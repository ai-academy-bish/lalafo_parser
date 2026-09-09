"""Sequences the crawl stages and reports.

    Pipeline.run()
      ├── 0. warm the Cloudflare session (browser, once)     ← session.py
      ├── 1. IdCollector.collect()   discover ad ids per leaf stream
      └── 2. ListingCrawler.crawl()  fetch details + images (multiprocessing)

Users, complexes and cities are extracted from each listing's payload during
stage 2 (lalafo embeds them), so there is no separate network stage for them — the
derived counts are computed at dataset-build time.

Which streams get walked comes from ``config.taxonomy`` — the vertical's YAML — so
this class is identical for real estate, cars or anything else lalafo lists.

Each stage is a method, so a subclass can override one without touching the rest.
"""

from __future__ import annotations

from ..config import Config
from ..http_client import LalafoClient
from ..logging_utils import ProgressTracker, get_logger
from ..session import SessionStore
from ..storage import Storage
from .id_collector import IdCollector, Stream
from .listing_crawler import ListingCrawler

logger = get_logger(__name__)


class Pipeline:
    """Owns the session, storage and the two crawl stages."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.taxonomy = config.taxonomy
        self.paths = config.paths
        self.storage = Storage(raw_dir=self.paths.raw, images_dir=self.paths.images)
        self.session_store = SessionStore(
            session_path=self.paths.state / config.session.session_filename,
            profile_dir=self.paths.state / config.session.profile_dirname,
            max_age=config.session.max_age,
            headless=config.session.headless,
            warmup_url=self.taxonomy.site_url,
        )
        self.client = LalafoClient(
            self.session_store,
            impersonate=config.http.impersonate,
            timeout=config.http.timeout,
            max_retries=config.http.max_retries,
            delay=config.http.delay,
            referer=self.taxonomy.site_url,
        )

    # -- callback shared with the stages -----------------------------------

    def ensure_session(self, force: bool = False) -> None:
        self.session_store.ensure_fresh(force=force)

    # -- stages ------------------------------------------------------------

    def streams(self) -> list[Stream]:
        """The in-scope leaf categories, biggest-lever streams first is not needed —
        discovery is cheap; keep them in a stable id order for readable logs."""
        # `or ...` covers a Config built by hand and never resolved; Config.load
        # always fills these in from the taxonomy.
        scope = self.config.scope
        wanted = set(self.taxonomy.categories_for(
            scope.property_types or list(self.taxonomy.property_types),
            scope.deals or list(self.taxonomy.deals),
        ))
        out = []
        for cid in sorted(wanted):
            leaf = self.taxonomy.leaves[cid]
            out.append(Stream(cid, leaf.property_type, leaf.deal, leaf.name, leaf.labels))
        return out

    def warmup(self) -> None:
        logger.info("[bold]stage 0[/] — warming Cloudflare session")
        self.ensure_session()

    def discover(self, progress: ProgressTracker) -> None:
        logger.info("[bold]stage 1[/] — discovering ad ids")
        IdCollector(
            self.client, self.storage, progress,
            page_workers=self.config.http.workers,
            max_pages=self.config.scope.max_pages_per_stream,
            partition_by_city=self.config.scope.partition_oversize_by_city,
            ensure_session=self.ensure_session,
        ).collect(self.streams(), self.config.scope.max_listings)

    def crawl_listings(self, progress: ProgressTracker) -> None:
        logger.info("[bold]stage 2[/] — fetching listing details + images")
        ListingCrawler(
            self.config, self.storage, progress,
            self.session_store, self.ensure_session,
        ).crawl(self.config.scope.max_listings)

    # -- orchestration -----------------------------------------------------

    def run(self) -> dict[str, int]:
        before = self.storage.summary()
        logger.info("vertical [bold]%s[/] (%s) — %d leaf categories in scope",
                    self.taxonomy.vertical, self.taxonomy.source.name, len(self.streams()))
        logger.info("storage before: %s", before)

        self.warmup()
        with ProgressTracker(enabled=self.config.logging.progress) as progress:
            self.discover(progress)
            self.crawl_listings(progress)

        after = self.storage.summary()
        logger.info("[bold green]crawl complete[/] — storage: %s", after)
        return after
