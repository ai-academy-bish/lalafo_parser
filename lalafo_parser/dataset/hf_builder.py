"""Build the HuggingFace dataset — one subset (config) per table.

Four tabular subsets (listings, users, complexes, cities) plus an embedded image
subset.  A subset with no rows is skipped, so a vertical that has no residential
complexes (cars) simply ships no ``complexes`` table.

Images are embedded as a HF `Image` feature and written as sharded Parquet rather
than shipped as loose files: a repo of hundreds of thousands of small files is
painfully slow to clone and load; embedded-and-sharded is the standard path and lets
a consumer do ``load_dataset("<repo>", "images", split="train")`` and get decoded
PIL images.

Derived counts (a user's ``ads_count``, a complex's / city's ``listing_count``) are
computed here in a single pass over the listings, so the crawler never has to hold
them in memory.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from datasets import Dataset, Features, Image, Value
from huggingface_hub import HfApi

from ..config import Config
from ..logging_utils import ProgressTracker, get_logger
from ..storage import Storage
from .card import build_card

logger = get_logger(__name__)

#: Tabular subsets, in card order.  ``images`` is appended when enabled.
TABLE_SUBSETS = ("listings", "users", "complexes", "cities")

#: Columns whose value is a nested list/dict — JSON-encoded to a string so Arrow
#: never has to infer a wobbly nested schema across sparse rows.
_JSON_COLUMNS = ("params_raw",)


class HFDatasetBuilder:
    """Converts the JSONL tables + image files into a HF dataset."""

    def __init__(self, config: Config, storage: Storage, progress: ProgressTracker) -> None:
        self.config = config
        self.storage = storage
        self.progress = progress
        self.out_dir = config.dataset_dir

    # -- public API --------------------------------------------------------

    def build(self) -> dict[str, int]:
        if self.out_dir.exists():
            shutil.rmtree(self.out_dir)
        (self.out_dir / "data").mkdir(parents=True, exist_ok=True)

        self._counts = self._derived_counts()

        counts: dict[str, int] = {}
        for name in TABLE_SUBSETS:
            counts[name] = self._build_table(name)

        if self.config.dataset.include_images:
            counts["images"] = self._build_images()

        self._write_card(counts)
        if self.config.dataset.hub.push:
            self._push(counts)

        logger.info("[bold green]dataset ready[/] -> %s", self.out_dir)
        return counts

    # -- derived counts ----------------------------------------------------

    def _derived_counts(self) -> dict[str, Counter]:
        users: Counter = Counter()
        complexes: Counter = Counter()
        cities: Counter = Counter()
        for row in self.storage.listings.rows():
            if row.get("user_id") is not None:
                users[str(row["user_id"])] += 1
            if row.get("complex_id") is not None:
                complexes[str(row["complex_id"])] += 1
            if row.get("city_id") is not None:
                cities[str(row["city_id"])] += 1
        return {"users": users, "complexes": complexes, "cities": cities}

    # -- tabular subsets ---------------------------------------------------

    def _build_table(self, name: str) -> int:
        rows = list(getattr(self.storage, name).rows())
        if not rows:
            logger.warning("subset %s is empty — skipped", name)
            return 0

        rows = [self._normalise(name, r) for r in rows]
        rows = self._align(rows)
        dataset = Dataset.from_list(rows)
        target = self.out_dir / "data" / f"{name}.parquet"
        dataset.to_parquet(target)

        size_mb = target.stat().st_size / 1e6
        logger.info("  %-10s %6d rows  %3d cols  %6.2f MB",
                    name, dataset.num_rows, len(dataset.column_names), size_mb)
        return dataset.num_rows

    def _normalise(self, name: str, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        for col in _JSON_COLUMNS:
            if col in row and not isinstance(row[col], str):
                row[col] = json.dumps(row[col], ensure_ascii=False) if row[col] else None
        # inject derived counts
        if name == "users":
            row["ads_count"] = self._counts["users"].get(str(row.get("user_id")), 0)
        elif name == "complexes":
            row["listing_count"] = self._counts["complexes"].get(str(row.get("complex_id")), 0)
        elif name == "cities":
            row["listing_count"] = self._counts["cities"].get(str(row.get("city_id")), 0)
        return row

    @staticmethod
    def _align(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Give every row the same keys (characteristics are sparse; Arrow needs one
        schema), filling gaps with None."""
        columns: dict[str, None] = {}
        for row in rows:
            for key in row:
                columns.setdefault(key, None)
        return [{key: row.get(key) for key in columns} for row in rows]

    # -- image subset ------------------------------------------------------

    def _build_images(self) -> int:
        rows = list(self.storage.images.rows())
        if not rows:
            logger.warning("no images to package")
            return 0
        image_dir = self.config.paths.images
        present = {p.name for p in image_dir.iterdir() if p.is_file()}
        rows = [r for r in rows if r.get("file_name") in present]

        features = Features({
            "foto_id": Value("string"),
            "listing_id": Value("int64"),
            "ad_id": Value("int64"),
            "image_id": Value("int64"),
            "url": Value("string"),
            "p_hash": Value("string"),
            "is_main": Value("bool"),
            "image": Image(),
        })

        limit = _parse_size(self.config.dataset.max_shard_size)
        out_dir = self.out_dir / "data"
        self.progress.track("dataset", len(rows), "packing images")

        staged: list[Path] = []
        batch: list[dict[str, Any]] = []
        batch_bytes = 0

        def flush() -> None:
            nonlocal batch, batch_bytes
            if not batch:
                return
            path = out_dir / f"images-part-{len(staged):05d}.parquet"
            Dataset.from_list(batch, features=features).to_parquet(path)
            staged.append(path)
            batch, batch_bytes = [], 0

        for row in rows:
            data = (image_dir / row["file_name"]).read_bytes()
            batch.append({
                "foto_id": row["foto_id"],
                "listing_id": row.get("listing_id"),
                "ad_id": row.get("ad_id"),
                "image_id": row.get("image_id"),
                "url": row.get("url"),
                "p_hash": row.get("p_hash"),
                "is_main": row.get("is_main"),
                "image": {"path": row["file_name"], "bytes": data},
            })
            batch_bytes += len(data)
            self.progress.advance("dataset")
            if batch_bytes >= limit:
                flush()
        flush()
        self.progress.complete("dataset")

        for index, path in enumerate(staged):
            path.rename(out_dir / f"images-{index:05d}-of-{len(staged):05d}.parquet")
        total_mb = sum(p.stat().st_size for p in out_dir.glob("images-*.parquet")) / 1e6
        logger.info("  %-10s %6d rows  %d shard(s)  %6.1f MB",
                    "images", len(rows), len(staged), total_mb)
        return len(rows)

    # -- card & hub --------------------------------------------------------

    def _write_card(self, counts: dict[str, int]) -> None:
        subsets = [n for n in (*TABLE_SUBSETS, "images") if counts.get(n)]
        card = build_card(counts, subsets, self.config.taxonomy)
        (self.out_dir / "README.md").write_text(card, encoding="utf-8")
        # Each vertical ships its *own* long-form guide, or none — never another
        # vertical's (a car dataset must not carry the real-estate guide).
        guide = self.config.taxonomy.guide
        if guide:
            path = self.config.project_root / guide
            if path.exists():
                shutil.copy(path, self.out_dir / "DATASET_GUIDE.md")
            else:
                logger.warning("taxonomy declares guide %s but it does not exist", path)

    def _push(self, counts: dict[str, int]) -> None:
        """Upload ``hf_dataset/`` verbatim so the README's ``configs:`` paths and the
        parquet files stay in sync (see the house.kg builder note — ``push_to_hub``
        rewrites the layout and desynchronises the card)."""
        hub = self.config.dataset.hub
        if not hub.repo_id:
            raise ValueError("dataset.hub.push is true but hub.repo_id is not set")
        api = HfApi(token=hub.token)
        api.create_repo(repo_id=hub.repo_id, repo_type="dataset",
                        private=hub.private, exist_ok=True)
        total_mb = sum(f.stat().st_size for f in self.out_dir.rglob("*") if f.is_file()) / 1e6
        logger.info("pushing %.0f MB to https://huggingface.co/datasets/%s", total_mb, hub.repo_id)
        api.upload_folder(
            folder_path=str(self.out_dir), repo_id=hub.repo_id, repo_type="dataset",
            commit_message=(
                f"Add lalafo {self.config.taxonomy.vertical} dataset "
                f"({counts.get('listings', 0)} listings)"
            ),
        )
        logger.info("[bold green]pushed[/] -> https://huggingface.co/datasets/%s", hub.repo_id)


def _parse_size(text: str) -> int:
    units = {"KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
    value = text.strip().upper()
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * factor)
    return int(value)
