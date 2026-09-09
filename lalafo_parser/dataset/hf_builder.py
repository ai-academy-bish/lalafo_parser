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

**Streaming upload.** By default every shard is written, then the whole folder is
uploaded — which needs free disk equal to the image corpus.  With
``dataset.stream_upload`` each shard is uploaded and deleted as soon as it is
written, so peak disk is one shard instead of the whole set.  That is what makes a
corpus larger than the free space publishable at all.  Shard *names* are identical
either way: boundaries are planned up front from the files' sizes on disk, so
nothing depends on having staged the set first.
"""

from __future__ import annotations

import json
import os
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

        dataset = self.config.dataset
        self._streaming = dataset.stream_upload and dataset.include_images
        self._api: HfApi | None = None
        self._already_uploaded: set[str] = set()
        if self._streaming:
            # The repo has to exist before the first shard goes up, and knowing what
            # is already there makes an interrupted 70 GB push resumable.
            self._api = self._open_repo()
            self._already_uploaded = self._remote_files(self._api)

        self._counts = self._derived_counts()

        counts: dict[str, int] = {}
        for name in TABLE_SUBSETS:
            counts[name] = self._build_table(name)

        if dataset.include_images:
            counts["images"] = self._build_images()

        self._write_card(counts)
        if dataset.hub.push:
            self._push(counts)

        logger.info("[bold green]dataset ready[/] -> %s", self.out_dir)
        return counts

    # -- hub plumbing (shared by streaming and the final push) -------------

    @property
    def _repo_id(self) -> str:
        """The push target, or a loud failure — the one place that check lives."""
        repo_id = self.config.dataset.hub.repo_id
        if not repo_id:
            raise ValueError("dataset.hub.push is true but hub.repo_id is not set")
        return repo_id

    def _open_repo(self) -> HfApi:
        hub = self.config.dataset.hub
        api = HfApi(token=hub.token)
        api.create_repo(repo_id=self._repo_id, repo_type="dataset",
                        private=hub.private, exist_ok=True)
        return api

    def _remote_files(self, api: HfApi) -> set[str]:
        """What the repo already holds — so a resumed run skips finished shards."""
        try:
            names = {Path(f).name for f in api.list_repo_files(
                repo_id=self._repo_id, repo_type="dataset")}
        except Exception as exc:  # brand-new or unreachable repo — nothing to skip
            logger.debug("could not list repo files (%s) — uploading everything", exc)
            return set()
        shards = {n for n in names if n.startswith("images-") and n.endswith(".parquet")}
        if shards:
            logger.info("repo already holds %d image shard(s) — they will be skipped",
                        len(shards))
        return shards

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

    #: Arrow schema of the image subset.
    _IMAGE_FEATURES = Features({
        "foto_id": Value("string"),
        "listing_id": Value("int64"),
        "ad_id": Value("int64"),
        "image_id": Value("int64"),
        "url": Value("string"),
        "p_hash": Value("string"),
        "is_main": Value("bool"),
        "image": Image(),
    })

    def _build_images(self) -> int:
        rows = list(self.storage.images.rows())
        if not rows:
            logger.warning("no images to package")
            return 0

        image_dir = self.config.paths.images
        # One scan gives both "is the file there" and "how big is it" — the sizes
        # are what let shard boundaries be planned before anything is written.
        with os.scandir(image_dir) as entries:
            sizes = {e.name: e.stat().st_size for e in entries if e.is_file()}
        rows = [r for r in rows if r.get("file_name") in sizes]
        if not rows:
            logger.warning("no image files on disk for the %d image rows", len(rows))
            return 0

        plan = self._shard_plan(rows, sizes)
        out_dir = self.out_dir / "data"
        total = len(plan)
        verb = "packing + uploading images" if self._streaming else "packing images"
        self.progress.track("dataset", len(rows), verb)

        written_mb = 0.0
        for index, chunk in enumerate(plan):
            name = f"images-{index:05d}-of-{total:05d}.parquet"
            if self._streaming and name in self._already_uploaded:
                logger.info("  shard %d/%d already in the repo — skipped", index + 1, total)
                self.progress.advance("dataset", len(chunk))
                continue

            path = out_dir / name
            Dataset.from_list(
                [self._image_record(r, image_dir) for r in chunk],
                features=self._IMAGE_FEATURES,
            ).to_parquet(path)
            self.progress.advance("dataset", len(chunk))
            written_mb += path.stat().st_size / 1e6

            if self._streaming:
                self._upload_shard(path, index, total)

        self.progress.complete("dataset")
        held = "uploaded" if self._streaming else "written"
        logger.info("  %-10s %6d rows  %d shard(s)  %6.1f MB %s",
                    "images", len(rows), total, written_mb, held)
        return len(rows)

    def _shard_plan(self, rows: list[dict[str, Any]],
                    sizes: dict[str, int]) -> list[list[dict[str, Any]]]:
        """Split rows into shards by accumulated image bytes, without reading them.

        Planning up front (rather than flushing as we go) is what lets a shard be
        named ``images-<i>-of-<n>`` on first write, so streaming and staging produce
        byte-identical file names.
        """
        limit = _parse_size(self.config.dataset.max_shard_size)
        plan: list[list[dict[str, Any]]] = []
        batch: list[dict[str, Any]] = []
        batch_bytes = 0
        for row in rows:
            batch.append(row)
            batch_bytes += sizes[row["file_name"]]
            if batch_bytes >= limit:
                plan.append(batch)
                batch, batch_bytes = [], 0
        if batch:
            plan.append(batch)
        return plan

    @staticmethod
    def _image_record(row: dict[str, Any], image_dir: Path) -> dict[str, Any]:
        return {
            "foto_id": row["foto_id"],
            "listing_id": row.get("listing_id"),
            "ad_id": row.get("ad_id"),
            "image_id": row.get("image_id"),
            "url": row.get("url"),
            "p_hash": row.get("p_hash"),
            "is_main": row.get("is_main"),
            "image": {
                "path": row["file_name"],
                "bytes": (image_dir / row["file_name"]).read_bytes(),
            },
        }

    def _upload_shard(self, path: Path, index: int, total: int) -> None:
        """Send one shard, then delete it — the point of streaming."""
        assert self._api is not None
        size_mb = path.stat().st_size / 1e6
        self._api.upload_file(
            path_or_fileobj=str(path),
            path_in_repo=f"data/{path.name}",
            repo_id=self._repo_id,
            repo_type="dataset",
            commit_message=f"Add {path.name}",
        )
        path.unlink()
        logger.info("  shard %d/%d uploaded (%.1f MB) and removed locally",
                    index + 1, total, size_mb)

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
        rewrites the layout and desynchronises the card).

        Under streaming the image shards are already up and deleted, so what is left
        here is the card, the guide and the tabular subsets."""
        api = self._api or self._open_repo()
        total_mb = sum(f.stat().st_size for f in self.out_dir.rglob("*") if f.is_file()) / 1e6
        what = "remaining files" if self._streaming else "dataset"
        logger.info("pushing %s (%.0f MB) to https://huggingface.co/datasets/%s",
                    what, total_mb, self._repo_id)
        api.upload_folder(
            folder_path=str(self.out_dir), repo_id=self._repo_id, repo_type="dataset",
            commit_message=(
                f"Add lalafo {self.config.taxonomy.vertical} dataset "
                f"({counts.get('listings', 0)} listings)"
            ),
        )
        logger.info("[bold green]pushed[/] -> https://huggingface.co/datasets/%s", self._repo_id)


def _parse_size(text: str) -> int:
    units = {"KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
    value = text.strip().upper()
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * factor)
    return int(value)
