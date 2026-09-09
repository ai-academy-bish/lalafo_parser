"""The dataset records, as dataclasses. ``to_dict()`` is what JSONL/Parquet consume.

Five tables, linked by natural keys straight from lalafo's own namespace:

    listings ──┬── user_id      ──→ users
               ├── complex_id   ──→ complexes      (residential complex / ЖК)
               └── city_id      ──→ cities

    images   ───── listing_id   ──→ listings   (ad_id is the same join, human-readable)

Everything lalafo exposes is kept — ids, phone, e-mail, coordinates, the five
engagement counters, seller reputation, promotion flags, the full attribute list.
Nothing that cannot be re-derived is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Listing:
    """One ad.  ~40 declared fields + an open-ended ``attrs`` map of characteristics.

    ``to_dict`` flattens ``labels`` and then ``attrs`` into the row with
    ``setdefault``, so neither can clobber a core field — the listings table ends up
    with more columns than the dataclass declares, exactly as on house.kg.  Labels
    go first because they come from the category (authoritative) rather than from a
    seller-filled attribute.
    """

    # identity & provenance
    ad_id: int                       # lalafo's own ad id — stable across crawls (PK)
    url: str | None
    pars_date: str | None            # when we scraped it (ISO-8601 UTC)

    # classification (from the crawl stream = authoritative)
    category_id: int | None
    property_type: str | None
    deal: str | None
    category_name: str | None

    # content
    title: str | None
    description: str | None

    # price
    price: float | None
    old_price: float | None
    currency: str | None
    symbol: str | None
    price_type: int | None
    is_negotiable: bool | None
    national_price: float | None     # price in KGS, lalafo's normalisation
    national_currency: str | None

    # geography
    city: str | None
    city_id: int | None
    region: str | None               # best-effort oblast (from CITY_REGION)
    lat: float | None
    lng: float | None
    is_hide_house_number: bool | None

    # seller (see users table)
    user_id: int | None
    origin_user_id: int | None
    user_ids: list[int] | None
    username: str | None
    mobile: str | None               # phone number, plaintext (house.kg never had this)
    email: str | None
    device_id: str | None
    hide_phone: bool | None
    hide_chat: bool | None

    # complex / developer (see complexes table)
    complex_id: int | None
    complex_name: str | None

    # engagement
    views: int | None
    impressions: int | None
    favorite_count: int | None
    callers_count: int | None
    writers_count: int | None
    response_type: int | None

    # status & monetisation flags
    status_id: int | None
    is_vip: bool | None
    is_premium: bool | None
    is_select: bool | None
    is_ppv: bool | None
    is_paid_posting: bool | None
    is_private_ad: bool | None
    is_identity: bool | None
    is_freedom: bool | None
    campaign_types: list[str] | None

    # time
    created_date: str | None         # ISO from created_time
    updated_date: str | None
    created_time: int | None         # raw unix epoch
    updated_time: int | None

    # media
    foto_ids: list[str] = field(default_factory=list)
    image_count: int = 0

    # extra classification columns contributed by the taxonomy leaf (cars carry
    # `brand`; real estate carries none) — flattened into the row
    labels: dict[str, Any] = field(default_factory=dict)

    # open-ended characteristics (flattened into the row)
    attrs: dict[str, Any] = field(default_factory=dict)
    #: the untouched param list, for anything the flattening missed
    params_raw: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            k: getattr(self, k)
            for k in self.__slots__
            if k not in ("attrs", "labels")
        }
        # flatten labels, then characteristics, never overwriting a core field
        for key, value in self.labels.items():
            row.setdefault(key, value)
        for key, value in self.attrs.items():
            row.setdefault(key, value)
        return row


@dataclass(slots=True)
class Image:
    """One poster image.  Links a file to its listing."""

    foto_id: str                     # uuid4 (PK)
    listing_id: int                  # FK -> listings.ad_id
    ad_id: int                       # same as listing_id, explicit for joins on snapshots
    image_id: int | None             # lalafo's own image id
    url: str | None
    webp_url: str | None
    width: int | None
    height: int | None
    is_main: bool | None
    is_cv_image: bool | None
    p_hash: str | None               # perceptual hash — dedupe images across ads for free
    file_name: str | None = None     # raw JSONL only; not in the published Parquet

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


@dataclass(slots=True)
class User:
    """A seller.  Union of every ad author seen (lalafo has no public profile API,
    so the fields come from the ``user`` object embedded in each listing)."""

    user_id: int                     # PK
    user_hash: str | None
    username: str | None
    avatar: str | None
    pro: bool | None
    is_banned: bool | None
    is_deleted: bool | None
    response_rate: int | None
    response_time: int | None
    response_info: str | None
    ads_count: int = 0               # derived: listings by this user in the dataset

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


@dataclass(slots=True)
class Complex:
    """A residential complex (ЖК), keyed by the attribute value_id lalafo assigns it."""

    complex_id: int                  # PK — the param value_id
    name: str | None
    listing_count: int = 0           # derived

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


@dataclass(slots=True)
class City:
    """A city/town dimension, derived from the listings (no catalog endpoint)."""

    city_id: int                     # PK
    name: str | None
    region: str | None               # best-effort oblast
    listing_count: int = 0           # derived

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}
