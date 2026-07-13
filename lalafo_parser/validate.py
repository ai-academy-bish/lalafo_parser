"""Integrity checks the dataset promises.

Each check asserts an invariant a consumer relies on.  Run after a crawl, before
building.  A failure is a real bug (a broken foreign key, an image row with no
file), not a style nit.
"""

from __future__ import annotations

from .constants import DEALS, PROPERTY_TYPES
from .logging_utils import get_logger
from .storage import Storage

logger = get_logger(__name__)


class Validator:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.failures: list[str] = []

    def run(self) -> bool:
        self._primary_keys()
        self._foreign_keys()
        self._images()
        self._classification()
        self._prices()

        if self.failures:
            logger.error("[bold red]validation failed[/] — %d problem(s):", len(self.failures))
            for f in self.failures:
                logger.error("  ✗ %s", f)
            return False
        logger.info("[bold green]validation passed[/]")
        return True

    def _fail(self, msg: str) -> None:
        self.failures.append(msg)

    # -- checks ------------------------------------------------------------

    def _primary_keys(self) -> None:
        seen: set[str] = set()
        nulls = 0
        for row in self.storage.listings.rows():
            key = row.get("ad_id")
            if key is None:
                nulls += 1
                continue
            if str(key) in seen:
                self._fail(f"duplicate ad_id {key}")
            seen.add(str(key))
        if nulls:
            self._fail(f"{nulls} listings have a null ad_id")

    def _foreign_keys(self) -> None:
        users = self.storage.users.keys
        complexes = self.storage.complexes.keys
        cities = self.storage.cities.keys
        miss_u = miss_c = miss_city = 0
        for row in self.storage.listings.rows():
            uid = row.get("user_id")
            if uid is not None and str(uid) not in users:
                miss_u += 1
            cid = row.get("complex_id")
            if cid is not None and str(cid) not in complexes:
                miss_c += 1
            city = row.get("city_id")
            if city is not None and str(city) not in cities:
                miss_city += 1
        # A user/complex/city seen only on a listing that failed to store its entity
        # row is a real gap — but a small count can happen when a seller was deleted
        # mid-crawl. Report, don't necessarily fail the whole run on a handful.
        if miss_u:
            self._fail(f"{miss_u} listings reference a user_id absent from users")
        if miss_c:
            self._fail(f"{miss_c} listings reference a complex_id absent from complexes")
        if miss_city:
            self._fail(f"{miss_city} listings reference a city_id absent from cities")

    def _images(self) -> None:
        listings = self.storage.listings.keys
        orphan = 0
        for row in self.storage.images.rows():
            lid = row.get("listing_id")
            if lid is not None and str(lid) not in listings:
                orphan += 1
        if orphan:
            self._fail(f"{orphan} image rows reference a missing listing")

    def _classification(self) -> None:
        bad = 0
        for row in self.storage.listings.rows():
            if row.get("property_type") not in PROPERTY_TYPES:
                bad += 1
            elif row.get("deal") not in DEALS:
                bad += 1
        if bad:
            self._fail(f"{bad} listings have an unknown property_type/deal")

    def _prices(self) -> None:
        # sanity: negotiable ads may have null price; a non-negotiable ad with a
        # price should have a currency.
        bad = 0
        for row in self.storage.listings.rows():
            if row.get("price") is not None and not row.get("currency"):
                bad += 1
        if bad:
            self._fail(f"{bad} listings have a price but no currency")
