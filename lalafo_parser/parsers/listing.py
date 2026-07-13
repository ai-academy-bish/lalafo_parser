"""Parse a listing-detail JSON payload into records.

The detail endpoint returns ~61 top-level fields plus a ``params`` list and a
nested ``user``.  This module turns one such object into:

* a :class:`~lalafo_parser.models.Listing` (core fields + flattened characteristics),
* image descriptors (downloaded and keyed later by the crawler),
* the embedded seller (→ users table),
* the residential complex, if any (→ complexes table),
* the city (→ cities table).

House.kg rule kept: an attribute whose param-id is not in ``PARAM_MAP`` is
**transliterated**, not dropped, so a new lalafo attribute still reaches the
dataset under a latinised name and can be promoted to a clean column later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..constants import (
    CATEGORIES,
    COMPLEX_PARAM_ID,
    DEVELOPER_PARAM_ID,
    DISTRICT_PARAM_ID,
    PARAM_MAP,
    region_for,
)
from ..models import Complex, City, Listing, User
from ..utils import Transliterator, clean_text, epoch_to_iso, to_float


@dataclass(slots=True)
class ImageRef:
    """An image described by the ad, before it is downloaded."""

    image_id: int | None
    url: str | None
    webp_url: str | None
    width: int | None
    height: int | None
    is_main: bool | None
    is_cv_image: bool | None
    p_hash: str | None


@dataclass(slots=True)
class ParsedAd:
    """Everything one detail payload yields."""

    listing: Listing
    images: list[ImageRef] = field(default_factory=list)
    user: User | None = None
    complex: Complex | None = None
    city: City | None = None


class ListingParser:
    """Detail JSON -> records. Takes a Transliterator so unmapped params are slugged."""

    def __init__(self, translit: Transliterator | None = None) -> None:
        self.translit = translit or Transliterator()

    def now(self) -> str:
        return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, payload: dict[str, Any]) -> ParsedAd | None:
        node = payload.get("data") if "data" in payload else payload
        if not isinstance(node, dict) or not node.get("id"):
            return None

        attrs, params_raw, extracted = self._attributes(node.get("params") or [])
        cat_id = node.get("category_id")
        ptype, deal, cat_name = self._classify(cat_id)

        national = node.get("national_price") or {}

        listing = Listing(
            ad_id=node["id"],
            url=self._abs_url(node.get("url")),
            pars_date=self.now(),
            category_id=cat_id,
            property_type=ptype,
            deal=deal,
            category_name=cat_name,
            title=clean_text(node.get("title")),
            description=clean_text(node.get("description")),
            price=to_float(node.get("price")),
            old_price=to_float(node.get("old_price")),
            currency=node.get("currency"),
            symbol=node.get("symbol"),
            price_type=node.get("price_type"),
            is_negotiable=node.get("is_negotiable"),
            national_price=to_float(national.get("price")),
            national_currency=national.get("currency"),
            city=node.get("city"),
            city_id=node.get("city_id"),
            region=region_for(node.get("city")),
            lat=node.get("lat"),
            lng=node.get("lng"),
            is_hide_house_number=node.get("is_hide_house_number"),
            user_id=node.get("user_id"),
            origin_user_id=node.get("origin_user_id"),
            user_ids=node.get("user_ids"),
            username=clean_text(node.get("username")),
            mobile=node.get("mobile"),
            email=node.get("email"),
            device_id=node.get("device_id"),
            hide_phone=node.get("hide_phone"),
            hide_chat=node.get("hide_chat"),
            complex_id=extracted.get("complex_id"),
            complex_name=extracted.get("complex_name"),
            views=node.get("views"),
            impressions=node.get("impressions"),
            favorite_count=node.get("favorite_count"),
            callers_count=node.get("callers_count"),
            writers_count=node.get("writers_count"),
            response_type=node.get("response_type"),
            status_id=node.get("status_id"),
            is_vip=node.get("is_vip"),
            is_premium=node.get("is_premium"),
            is_select=node.get("is_select"),
            is_ppv=node.get("is_ppv"),
            is_paid_posting=node.get("is_paid_posting"),
            is_private_ad=node.get("is_private_ad"),
            is_identity=node.get("is_identity"),
            is_freedom=node.get("is_freedom"),
            campaign_types=node.get("available_campaign_types"),
            created_date=epoch_to_iso(node.get("created_time")),
            updated_date=epoch_to_iso(node.get("updated_time")),
            created_time=node.get("created_time"),
            updated_time=node.get("updated_time"),
            attrs=attrs,
            params_raw=params_raw,
        )

        images = self._images(node.get("images") or [])
        listing.image_count = len(images)

        return ParsedAd(
            listing=listing,
            images=images,
            user=self._user(node.get("user")),
            complex=self._complex(extracted),
            city=self._city(node),
        )

    # -- pieces ------------------------------------------------------------

    def _classify(self, category_id: Any) -> tuple[str | None, str | None, str | None]:
        entry = CATEGORIES.get(category_id)
        if entry:
            return entry[0], entry[1], entry[2]
        return None, None, None

    def _attributes(
        self, params: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        """Flatten params into (named columns, raw list, special extractions).

        An unmapped param becomes a transliterated column instead of being lost.
        """
        attrs: dict[str, Any] = {}
        raw: list[dict[str, Any]] = []
        extracted: dict[str, Any] = {}

        for p in params:
            pid = p.get("id")
            name = p.get("name")
            value = clean_text(p.get("value")) if isinstance(p.get("value"), str) else p.get("value")
            value_id = p.get("value_id")
            raw.append({"id": pid, "name": name, "value": value, "value_id": value_id})

            field_name = PARAM_MAP.get(pid) or (self.translit.slugify(name) if name else None)
            if field_name:
                attrs.setdefault(field_name, value)

            if pid == COMPLEX_PARAM_ID and value_id:
                extracted["complex_id"] = value_id
                extracted["complex_name"] = value
            elif pid == DEVELOPER_PARAM_ID:
                extracted["developer"] = value
            elif pid == DISTRICT_PARAM_ID:
                extracted["district"] = value

        return attrs, raw, extracted

    def _images(self, images: list[dict[str, Any]]) -> list[ImageRef]:
        refs: list[ImageRef] = []
        for im in images:
            refs.append(
                ImageRef(
                    image_id=im.get("id"),
                    url=im.get("original_url"),
                    webp_url=im.get("original_webp_url"),
                    width=im.get("width"),
                    height=im.get("height"),
                    is_main=im.get("is_main"),
                    is_cv_image=im.get("is_cv_image"),
                    p_hash=str(im["p_hash"]) if im.get("p_hash") is not None else None,
                )
            )
        return refs

    def _user(self, user: dict[str, Any] | None) -> User | None:
        if not user or not user.get("id"):
            return None
        return User(
            user_id=user["id"],
            user_hash=user.get("user_hash"),
            username=clean_text(user.get("username")),
            avatar=user.get("avatar"),
            pro=user.get("pro"),
            is_banned=user.get("is_banned"),
            is_deleted=user.get("is_deleted"),
            response_rate=user.get("response_rate"),
            response_time=user.get("response_time"),
            response_info=clean_text(user.get("response_info")),
        )

    def _complex(self, extracted: dict[str, Any]) -> Complex | None:
        cid = extracted.get("complex_id")
        if not cid:
            return None
        return Complex(complex_id=cid, name=extracted.get("complex_name"))

    def _city(self, node: dict[str, Any]) -> City | None:
        cid = node.get("city_id")
        if not cid:
            return None
        name = node.get("city")
        return City(city_id=cid, name=name, region=region_for(name))

    @staticmethod
    def _abs_url(url: str | None) -> str | None:
        if not url:
            return None
        return url if url.startswith("http") else f"https://lalafo.kg{url}"
