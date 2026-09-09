"""Parse a listing-detail JSON payload into records.

The detail endpoint returns ~61 top-level fields plus a ``params`` list and a
nested ``user``.  This module turns one such object into:

* a :class:`~lalafo_parser.models.Listing` (core fields + flattened characteristics),
* image descriptors (downloaded and keyed later by the crawler),
* the embedded seller (→ users table),
* the residential complex, if any (→ complexes table),
* the city (→ cities table).

The parser is bound to a :class:`~lalafo_parser.taxonomy.Taxonomy`, not to a
hard-coded category list: it is the taxonomy that says how a leaf classifies, which
param-ids get clean English names, and which params feed dimension tables.  Point it
at a different taxonomy and the same parser produces cars instead of flats.

House.kg rule kept: an attribute whose param-id is not in the taxonomy's param map
is **transliterated**, not dropped, so a new lalafo attribute still reaches the
dataset under a latinised name and can be promoted to a clean column later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..constants import region_for
from ..models import Complex, City, Listing, User
from ..taxonomy import Taxonomy
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
    """Detail JSON -> records, for one vertical.

    Takes the :class:`Taxonomy` that defines the vertical and a Transliterator so
    unmapped params are slugged rather than dropped.
    """

    def __init__(self, taxonomy: Taxonomy, translit: Transliterator | None = None) -> None:
        self.taxonomy = taxonomy
        self.translit = translit or Transliterator()

    def now(self) -> str:
        return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, payload: dict[str, Any]) -> ParsedAd | None:
        node = payload.get("data") if "data" in payload else payload
        if not isinstance(node, dict) or not node.get("id"):
            return None

        attrs, params_raw, extracted = self._attributes(node.get("params") or [])
        cat_id = node.get("category_id")
        leaf = self.taxonomy.classify(cat_id)

        national = node.get("national_price") or {}

        listing = Listing(
            ad_id=node["id"],
            url=self._abs_url(node.get("url")),
            pars_date=self.now(),
            category_id=cat_id,
            property_type=leaf.property_type if leaf else None,
            deal=leaf.deal if leaf else None,
            category_name=leaf.name if leaf else None,
            labels=dict(leaf.labels) if leaf else {},
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

    def _attributes(
        self, params: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        """Flatten params into (named columns, raw list, special extractions).

        An unmapped param becomes a transliterated column instead of being lost.
        """
        attrs: dict[str, Any] = {}
        raw: list[dict[str, Any]] = []
        extracted: dict[str, Any] = {}
        param_map = self.taxonomy.param_map
        complex_id = self.taxonomy.complex_param
        developer_id = self.taxonomy.developer_param
        district_id = self.taxonomy.district_param

        for p in params:
            pid = p.get("id")
            name = p.get("name")
            value = clean_text(p.get("value")) if isinstance(p.get("value"), str) else p.get("value")
            value_id = p.get("value_id")
            raw.append({"id": pid, "name": name, "value": value, "value_id": value_id})

            field_name = param_map.get(pid) or (self.translit.slugify(name) if name else None)
            if field_name:
                attrs.setdefault(field_name, value)

            # Special params are optional per vertical: a taxonomy that declares no
            # `complex` (cars) simply never matches, and the table stays empty.
            if complex_id is not None and pid == complex_id and value_id:
                extracted["complex_id"] = value_id
                extracted["complex_name"] = value
            elif developer_id is not None and pid == developer_id:
                extracted["developer"] = value
            elif district_id is not None and pid == district_id:
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
