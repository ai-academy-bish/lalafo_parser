"""Stage 1 — discover ad ids by walking each leaf category's feed.

The feed is capped per query (~10–11 k rows), far below a big leaf's true size, so:

* the crawler walks **one leaf category at a time** — the leaf's URL already tells
  us the property type and deal, which are therefore never guessed from text;
* when a leaf's ``totalCount`` exceeds the cap, it is **partitioned by city**: the
  same leaf is re-queried per ``city_id`` (each city subset fits under the cap),
  and the union recovers the tail the single query hides.

Discovered ids are written to their own ``discovered`` table, so this stage is
resumable and the (much heavier) detail stage can run separately.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from ..constants import CATEGORIES, FEED_CAP_THRESHOLD, Api
from ..http_client import ChallengeError, LalafoClient
from ..logging_utils import ProgressTracker, get_logger
from ..storage import Storage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Stream:
    """One (leaf category) crawl stream."""

    category_id: int
    property_type: str
    deal: str
    name: str


class IdCollector:
    """Walks the feed of every in-scope stream and records the ad ids found."""

    def __init__(self, client: LalafoClient, storage: Storage, progress: ProgressTracker,
                 *, page_workers: int = 10, max_pages: int | None = None,
                 partition_by_city: bool = True, ensure_session=None) -> None:
        self.client = client
        self.storage = storage
        self.progress = progress
        self.page_workers = page_workers
        self.max_pages = max_pages
        self.partition_by_city = partition_by_city
        #: callback the pipeline provides to re-harvest the cookie on a challenge
        self.ensure_session = ensure_session

    def collect(self, streams: list[Stream], max_listings: int | None = None) -> int:
        """Discover ids for every stream; returns how many *new* ids were stored."""
        self.progress.track("ids", len(streams), "discovering ids")
        new_total = 0
        for stream in streams:
            new_total += self._collect_stream(stream)
            self.progress.advance("ids")
            if max_listings and len(self.storage.discovered) >= max_listings:
                logger.info("reached max_listings=%d during discovery", max_listings)
                break
        self.progress.complete("ids")
        logger.info("discovery done: %d ids total (%d new this run)",
                    len(self.storage.discovered), new_total)
        return new_total

    # -- one stream --------------------------------------------------------

    def _collect_stream(self, stream: Stream) -> int:
        meta = self._page_meta(stream.category_id)
        if meta is None:
            logger.warning("stream %s (%d): no feed", stream.name, stream.category_id)
            return 0
        total, page_count = meta

        ids, cities = self._walk(stream.category_id, page_count, city_id=None)

        if total > FEED_CAP_THRESHOLD and self.partition_by_city and cities:
            logger.info("stream %s is oversize (%d) — partitioning by %d cities",
                        stream.name, total, len(cities))
            for city_id in cities:
                cmeta = self._page_meta(stream.category_id, city_id=city_id)
                if cmeta:
                    cids, _ = self._walk(stream.category_id, cmeta[1], city_id=city_id)
                    ids |= cids

        return self._store(ids, stream)

    def _page_meta(self, category_id: int, city_id: int | None = None):
        data = self._get(Api.feed_url(category_id, 1, city_id=city_id))
        if not data:
            return None
        meta = data.get("_meta", {})
        page_count = meta.get("pageCount", 1)
        if self.max_pages:
            page_count = min(page_count, self.max_pages)
        return meta.get("totalCount", 0), page_count

    def _walk(self, category_id: int, page_count: int,
              city_id: int | None) -> tuple[set[int], set[int]]:
        """Fetch every page in parallel; return (ad ids of this category, city ids)."""
        ids: set[int] = set()
        cities: set[int] = set()
        lock = threading.Lock()

        def grab(page: int) -> None:
            data = self._get(Api.feed_url(category_id, page, city_id=city_id))
            if not data:
                return
            local_ids, local_cities = [], []
            for item in data.get("items", []):
                if item.get("category_id") == category_id and item.get("id"):
                    local_ids.append(item["id"])
                if item.get("city_id"):
                    local_cities.append(item["city_id"])
            with lock:
                ids.update(local_ids)
                cities.update(local_cities)

        with ThreadPoolExecutor(max_workers=self.page_workers) as pool:
            list(as_completed([pool.submit(grab, p) for p in range(1, page_count + 1)]))
        return ids, cities

    def _store(self, ids: set[int], stream: Stream) -> int:
        new = 0
        for ad_id in ids:
            if self.storage.discovered.append({
                "ad_id": ad_id,
                "category_id": stream.category_id,
                "property_type": stream.property_type,
                "deal": stream.deal,
            }):
                new += 1
        logger.info("  %-42s total=%-6d new=%d", stream.name, len(ids), new)
        return new

    def _get(self, path: str):
        """Feed GET that re-harvests the cookie once on a challenge."""
        try:
            return self.client.get_json(path)
        except ChallengeError:
            if self.ensure_session:
                self.ensure_session(force=True)
                try:
                    return self.client.get_json(path)
                except ChallengeError:
                    return None
            return None
