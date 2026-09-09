"""Stage 2 — fetch each discovered ad's detail, parse it, download its images.

This is the bulk of a run (one detail request per kept listing, plus images), so it
is where multiprocessing earns its keep.  The split is deliberate:

* **worker processes** do the network + parsing + image writing.  Image files are
  named with uuid4, so many processes writing into one flat directory never
  collide — no cross-process lock needed.
* the **parent process** is the single writer to the JSONL tables.  Workers return
  plain dicts; the parent appends them.  One writer means no file-lock dance and a
  consistent, resumable on-disk state.

Workers are given the *path* to the vertical's taxonomy rather than a loaded
object: ``Pool`` initargs must pickle cleanly, and re-reading one small YAML once
per process is free next to the network work it is about to do.

Session refresh is the parent's job.  Before each batch it re-warms the
``cf_clearance`` cookie if it has aged out; if a batch still hits challenges (cookie
died early), the parent force-refreshes and retries just that batch's failures.
Workers never open a browser — they re-read the refreshed cookie file on their next
task.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..constants import Api
from ..http_client import ChallengeError, LalafoClient
from ..logging_utils import ProgressTracker, get_logger
from ..parsers import ListingParser
from ..session import SessionStore
from ..storage import ImageStore, Storage
from ..taxonomy import Taxonomy

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Worker side (runs in each child process)
# ---------------------------------------------------------------------------

_W: dict[str, Any] = {}


def _init_worker(session_path: str, profile_dir: str, impersonate: str, timeout: int,
                 max_retries: int, images_dir: str, images_enabled: bool,
                 max_per_listing: int | None, prefer_webp: bool,
                 taxonomy_path: str, referer: str) -> None:
    store = SessionStore(Path(session_path), Path(profile_dir), warmup_url=referer)
    _W["client"] = LalafoClient(store, impersonate=impersonate, timeout=timeout,
                                max_retries=max_retries, referer=referer)
    _W["parser"] = ListingParser(Taxonomy.load(taxonomy_path))
    _W["images"] = ImageStore(Path(images_dir))
    _W["images_enabled"] = images_enabled
    _W["max_per_listing"] = max_per_listing
    _W["prefer_webp"] = prefer_webp


def _download_images(refs, ad_id: int) -> tuple[list[dict], list[str]]:
    client: LalafoClient = _W["client"]
    store: ImageStore = _W["images"]
    prefer_webp = _W["prefer_webp"]
    cap = _W["max_per_listing"]
    rows, foto_ids = [], []
    for ref in (refs[:cap] if cap else refs):
        url = (ref.webp_url if prefer_webp and ref.webp_url else ref.url)
        if not url:
            continue
        data = client.get_bytes(url)
        if not data:
            continue
        foto_id, path = store.save(data, url)
        foto_ids.append(foto_id)
        rows.append({
            "foto_id": foto_id,
            "listing_id": ad_id,
            "ad_id": ad_id,
            "image_id": ref.image_id,
            "url": ref.url,
            "webp_url": ref.webp_url,
            "width": ref.width,
            "height": ref.height,
            "is_main": ref.is_main,
            "is_cv_image": ref.is_cv_image,
            "p_hash": ref.p_hash,
            "file_name": path.name,
        })
    return rows, foto_ids


def _process_ad(ad_id: int) -> dict[str, Any]:
    """Fetch + parse + download one ad. Returns a status dict for the parent."""
    client: LalafoClient = _W["client"]
    parser: ListingParser = _W["parser"]
    try:
        payload = client.get_json(Api.detail_url(ad_id))
    except ChallengeError:
        return {"status": "challenge", "ad_id": ad_id}
    if not payload:
        return {"status": "missing", "ad_id": ad_id}

    parsed = parser.parse(payload)
    if not parsed:
        return {"status": "missing", "ad_id": ad_id}

    image_rows, foto_ids = ([], [])
    if _W["images_enabled"]:
        image_rows, foto_ids = _download_images(parsed.images, ad_id)
    parsed.listing.foto_ids = foto_ids
    parsed.listing.image_count = len(parsed.images)

    return {
        "status": "ok",
        "ad_id": ad_id,
        "listing": parsed.listing.to_dict(),
        "images": image_rows,
        "user": parsed.user.to_dict() if parsed.user else None,
        "complex": parsed.complex.to_dict() if parsed.complex else None,
        "city": parsed.city.to_dict() if parsed.city else None,
    }


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------

class ListingCrawler:
    """Drives the detail stage over the discovered ids."""

    def __init__(self, config, storage: Storage, progress: ProgressTracker,
                 session_store: SessionStore, ensure_session) -> None:
        self.config = config
        self.storage = storage
        self.progress = progress
        self.session_store = session_store
        self.ensure_session = ensure_session   # parent's refresh callback

    def crawl(self, max_listings: int | None = None) -> int:
        todo = self._todo(max_listings)
        if not todo:
            logger.info("no new listings to fetch (all %d already stored)",
                        len(self.storage.listings))
            return 0
        logger.info("fetching %d listings (%d already stored)",
                    len(todo), len(self.storage.listings))
        self.progress.track("listings", len(todo), "fetching listings")
        self.progress.track("images", None, "downloading images")

        written = 0
        batch = max(2000, self._processes() * 500)
        for group in _chunks(todo, batch):
            self.ensure_session()  # age-based refresh between batches
            written += self._run_batch(group)
        self.progress.complete("listings")
        self.progress.complete("images")
        logger.info("detail stage done: %d listings, %d images, %d users, %d complexes, %d cities",
                    len(self.storage.listings), len(self.storage.images),
                    len(self.storage.users), len(self.storage.complexes),
                    len(self.storage.cities))
        return written

    # -- batches -----------------------------------------------------------

    def _run_batch(self, ids: list[int]) -> int:
        results = self._map(ids)
        written, challenged = self._consume(results)
        if challenged:
            logger.warning("%d ads challenged — refreshing cookie and retrying",
                           len(challenged))
            self.ensure_session(force=True)
            again, still = self._consume(self._map(challenged))
            written += again
            if still:
                logger.warning("%d ads still challenged after refresh — left for next run",
                               len(still))
        return written

    def _map(self, ids: list[int]) -> Iterable[dict[str, Any]]:
        """Run _process_ad over ids, as processes or (fallback) threads."""
        mp = self.config.multiprocessing
        if mp.enabled and len(ids) > 1:
            with Pool(processes=self._processes(), initializer=_init_worker,
                      initargs=self._worker_args()) as pool:
                yield from pool.imap_unordered(_process_ad, ids, chunksize=mp.chunk_size)
        else:
            _init_worker(*self._worker_args())
            workers = mp.threads_per_process if not mp.enabled else self.config.http.workers
            with ThreadPoolExecutor(max_workers=workers) as pool:
                yield from pool.map(_process_ad, ids)

    def _consume(self, results: Iterable[dict[str, Any]]) -> tuple[int, list[int]]:
        written, challenged = 0, []
        for res in results:
            status = res["status"]
            if status == "challenge":
                challenged.append(res["ad_id"])
                continue
            self.progress.advance("listings")
            if status != "ok":
                continue
            self._write(res)
            written += 1
        return written, challenged

    def _write(self, res: dict[str, Any]) -> None:
        for row in res["images"]:
            self.storage.images.append(row)
            self.progress.advance("images")
        if res["user"]:
            self.storage.users.append(res["user"])
        if res["complex"]:
            self.storage.complexes.append(res["complex"])
        if res["city"]:
            self.storage.cities.append(res["city"])
        self.storage.listings.append(res["listing"])

    # -- helpers -----------------------------------------------------------

    def _todo(self, max_listings: int | None) -> list[int]:
        todo: list[int] = []
        for row in self.storage.discovered.rows():
            ad_id = row.get("ad_id")
            if ad_id is None or str(ad_id) in self.storage.listings:
                continue
            todo.append(int(ad_id))
            if max_listings and len(todo) >= max_listings:
                break
        return todo

    def _processes(self) -> int:
        import os
        return self.config.multiprocessing.processes or os.cpu_count() or 4

    def _worker_args(self) -> tuple:
        p = self.config.paths
        return (
            str(p.state / self.config.session.session_filename),
            str(p.state / self.config.session.profile_dirname),
            self.config.http.impersonate,
            self.config.http.timeout,
            self.config.http.max_retries,
            str(p.images),
            self.config.images.enabled,
            self.config.images.max_per_listing,
            self.config.images.prefer_webp,
            str(self.config.taxonomy_path),
            self.config.taxonomy.site_url,
        )


def _chunks(seq: list[int], size: int) -> Iterator[list[int]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]
