"""Persistence: append-only JSONL tables + an image store.

Resumability is the whole point.  A full real-estate crawl is tens of thousands of
listings and hundreds of thousands of images; it *will* be interrupted.  So:

* every record is appended to JSONL the moment it is parsed — nothing is held in
  memory until the end, and a hard kill loses at most the record in flight;
* on start-up each table indexes the keys it already holds and the crawler skips
  them, so a restart resumes instead of re-downloading;
* discovered ids are their own table, so id-discovery and detail-fetching resume
  independently.

``JsonlTable`` is unchanged from the house.kg engine — it never knew anything about
that site.  Only the set of tables and their keys is lalafo-specific.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .logging_utils import get_logger

logger = get_logger(__name__)


class JsonlTable:
    """An append-only JSONL file with a de-duplicating key index. Thread-safe."""

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        self.key = key
        self._lock = threading.Lock()
        self._keys: set[str] = set()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load_keys()

    def _load_keys(self) -> None:
        if not self.path.exists():
            return
        recovered = 0
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("skipping corrupt line in %s", self.path.name)
                    continue
                value = row.get(self.key)
                if value is not None:
                    self._keys.add(str(value))
                    recovered += 1
        if recovered:
            logger.info("resuming %s: %d existing rows", self.path.name, recovered)

    # -- reads -------------------------------------------------------------

    def __contains__(self, key: object) -> bool:
        return str(key) in self._keys

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def keys(self) -> set[str]:
        return set(self._keys)

    def rows(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

    # -- writes ------------------------------------------------------------

    def append(self, row: dict[str, Any]) -> bool:
        """Append unless the key is already present. Returns True if written."""
        value = row.get(self.key)
        if value is None:
            raise ValueError(f"row is missing key field {self.key!r}")
        value = str(value)
        with self._lock:
            if value in self._keys:
                return False
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
            self._keys.add(value)
        return True

    def extend(self, rows: Iterable[dict[str, Any]]) -> int:
        return sum(1 for row in rows if self.append(row))


class ImageStore:
    """Flat directory of images named with uuid4.

    Flat on purpose: the dataset ships images as an embedded HF `Image` feature, so
    directory structure carries no meaning — the FK in the `images` table does.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def path_for(self, foto_id: str, extension: str = ".jpg") -> Path:
        return self.directory / f"{foto_id}{extension}"

    def save(self, data: bytes, url: str) -> tuple[str, Path]:
        extension = ".jpg"
        for candidate in (".jpeg", ".png", ".webp", ".jpg"):
            if url.lower().split("?")[0].endswith(candidate):
                extension = candidate
                break
        foto_id = self.new_id()
        path = self.path_for(foto_id, extension)
        path.write_bytes(data)
        return foto_id, path

    def __len__(self) -> int:
        return sum(1 for p in self.directory.iterdir() if p.is_file())


class Storage:
    """The five tables + the discovered-ids table + the image store."""

    def __init__(self, raw_dir: Path, images_dir: Path) -> None:
        #: every ad id found in discovery, with its stream classification —
        #: makes discovery resumable and lets detail-fetching run separately
        self.discovered = JsonlTable(raw_dir / "discovered.jsonl", key="ad_id")

        self.listings = JsonlTable(raw_dir / "listings.jsonl", key="ad_id")
        self.users = JsonlTable(raw_dir / "users.jsonl", key="user_id")
        self.complexes = JsonlTable(raw_dir / "complexes.jsonl", key="complex_id")
        self.cities = JsonlTable(raw_dir / "cities.jsonl", key="city_id")
        self.images = JsonlTable(raw_dir / "images.jsonl", key="foto_id")
        self.image_store = ImageStore(images_dir)

    def summary(self) -> dict[str, int]:
        return {
            "discovered": len(self.discovered),
            "listings": len(self.listings),
            "users": len(self.users),
            "complexes": len(self.complexes),
            "cities": len(self.cities),
            "images": len(self.images),
        }
