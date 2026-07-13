"""Domain constants + every API contract detail — patch here when lalafo changes.

lalafo.kg is a Next.js SPA behind a Cloudflare Turnstile *managed challenge*: the
HTML and the frontend ``/api/*`` proxy are both challenged.  There is no HTML to
scrape.  All data comes from one JSON API, reached by warming a ``cf_clearance``
cookie once with a real browser (``session.py``) and then replaying it with a
Chrome-fingerprinted HTTP client (``http_client.py``).

Everything site-specific lives here, grouped so that when lalafo moves an endpoint
or renames an attribute the fix is one file:

* ``Api``       — hosts, paths, query knobs, the mandatory app headers;
* ``CATEGORIES``— the real-estate leaf categories (the crawl streams);
* ``PARAM_MAP`` — attribute param-id → English column (synonyms collapse);
* ``REGIONS`` / ``CITY_REGION`` — best-effort oblast for a city;
* ``ImageCdn``  — the poster CDN.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

BASE_URL = "https://lalafo.kg"

#: Kyrgyzstan.  Every request is scoped to this country; ids for other countries
#: are deliberately never sent.
COUNTRY_ID = 12


class Api:
    """Endpoint templates and the header set the app always sends.

    The feed is capped (~10 000 rows for the ``category`` feed, ~11 000 for the
    ``ppv-category`` feed) *per query*, which is why the crawler walks one leaf
    category at a time and partitions oversize leaves by city — see the crawler.
    """

    #: Paginated listing feed for one category.  ``per-page`` is hyphenated (an
    #: underscore is silently ignored and you get the default page size).
    FEED = "/api/search/v3/feed/search"

    #: Full ad, including the complete ``params`` list and the seller phone.  The
    #: feed carries only a *partial* ``params`` list, so every kept ad is refetched
    #: here.
    DETAIL = "/api/search/v3/feed/details/{id}"

    #: Category tree (via the Next.js proxy).  Used to regenerate ``CATEGORIES``.
    CATEGORY_TREE = "/api/proxy/catalog/v3/categories/tree"

    #: Attribute schema (enum values) for a category — documentation / mapping aid.
    PARAM_FILTER = "/api/catalog/v3/params/filter?category_id={id}"

    #: Max page size the feed honours.
    PER_PAGE = 40

    #: Headers the API expects.  ``device``/``country-id``/``language`` are the app
    #: identity; the User-Agent is filled in from the warmed browser session so it
    #: matches the ``cf_clearance`` fingerprint.
    @staticmethod
    def headers(user_agent: str) -> dict[str, str]:
        return {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://lalafo.kg/kyrgyzstan/nedvizhimost",
            "device": "pc",
            "country-id": str(COUNTRY_ID),
            "language": "ru_RU",
        }

    @classmethod
    def feed_url(cls, category_id: int, page: int, *, city_id: int | None = None) -> str:
        url = (
            f"{cls.FEED}?category_id={category_id}&expand=url"
            f"&page={page}&per-page={cls.PER_PAGE}&with_feed_banner=true"
        )
        if city_id is not None:
            url += f"&city_id={city_id}"
        return url

    @classmethod
    def detail_url(cls, ad_id: int | str) -> str:
        return cls.DETAIL.format(id=ad_id) + "?expand=url"


class ImageCdn:
    """Poster image CDN (open, not behind Cloudflare)."""

    #: Host rotates (img1..img7); the path is what matters.
    HOST_MARKER = "lalafo.com/i/posters/"


# ---------------------------------------------------------------------------
# Crawl streams: the real-estate leaf categories under «Недвижимость» (id 2029)
# ---------------------------------------------------------------------------
#
# Regenerate with `make categories` (fetches CATEGORY_TREE and re-classifies).
# Each value is (property_type, deal, russian_name).  property_type/deal are a
# best-effort classification of the leaf's name — the russian name is kept so a
# consumer can always reclassify.
#
# property_type ∈ apartment | house | room | commercial | land | garage |
#                 newbuild | resort
# deal          ∈ sale | rent | daily_rent | buy_request | rent_request | other

REAL_ESTATE_ROOT = 2029

CATEGORIES: dict[int, tuple[str, str, str]] = {
    2033: ("house", "rent", "Долгосрочная аренда домов"),
    2034: ("house", "daily_rent", "Посуточная аренда домов"),
    2035: ("house", "buy_request", "Куплю дом"),
    2036: ("house", "rent_request", "Сниму дом"),
    2038: ("land", "sale", "Продажа участков"),
    2039: ("land", "rent", "Аренда участков"),
    2041: ("apartment", "rent_request", "Сниму квартиру"),
    2042: ("apartment", "buy_request", "Куплю квартиру"),
    2044: ("apartment", "rent", "Долгосрочная аренда квартир"),
    2045: ("apartment", "daily_rent", "Посуточная аренда квартир"),
    2046: ("apartment", "sale", "Продажа квартир"),
    2048: ("room", "buy_request", "Куплю комнату"),
    2049: ("room", "rent_request", "Сниму комнату"),
    2050: ("room", "sale", "Продажа комнат"),
    2052: ("room", "rent", "Долгосрочная аренда комнат"),
    2053: ("room", "daily_rent", "Гостиницы, отели (Посуточная аренда комнат)"),
    2056: ("commercial", "sale", "Продажа офисов"),
    2057: ("commercial", "sale", "Продажа магазинов"),
    2058: ("commercial", "sale", "Продажа ресторанов, кафе"),
    2059: ("commercial", "sale", "Продажа складов и мастерских"),
    2060: ("commercial", "sale", "Продажа отелей и хостелов"),
    2061: ("commercial", "sale", "Продажа зданий"),
    2062: ("commercial", "sale", "Продажа цехов, заводов, фабрик"),
    2063: ("commercial", "sale", "Продажа другой коммерческой недвижимости"),
    2065: ("commercial", "rent", "Аренда складов и мастерских"),
    2066: ("commercial", "rent", "Аренда магазинов"),
    2067: ("commercial", "rent", "Аренда ресторанов и кафе"),
    2068: ("commercial", "rent", "Аренда офисов"),
    2069: ("commercial", "rent", "Аренда помещений под гостиничный бизнес"),
    2070: ("commercial", "rent", "Аренда зданий"),
    2071: ("commercial", "rent", "Аренда цехов, заводов и фабрик"),
    2072: ("commercial", "rent", "Аренда другой коммерческой недвижимости"),
    4560: ("newbuild", "sale", "Новостройки от застройщика"),
    5294: ("land", "buy_request", "Куплю земельный участок"),
    5298: ("garage", "rent_request", "Сниму гараж"),
    5299: ("garage", "rent", "Сдаю в аренду гараж"),
    5300: ("garage", "buy_request", "Куплю гараж"),
    5301: ("garage", "sale", "Продаю гараж"),
    5302: ("garage", "other", "Вагоны"),
    5303: ("garage", "other", "Паркинги"),
    5314: ("garage", "other", "Другое"),
    6039: ("commercial", "sale", "Продажа торговых контейнеров"),
    6040: ("commercial", "sale", "Продажа павильонов"),
    6041: ("commercial", "sale", "Продажа бутиков"),
    6042: ("commercial", "sale", "Продажа киосков, ларьков"),
    7090: ("commercial", "sale", "Продажа автобизнеса"),
    7091: ("commercial", "sale", "Продажа салонов красоты и кабинетов"),
    7092: ("commercial", "sale", "Продажа сельхоз предприятий"),
    7093: ("commercial", "sale", "Продажа медицинских центров"),
    7248: ("commercial", "rent", "Аренда бутиков"),
    7249: ("commercial", "rent", "Аренда павильонов"),
    8036: ("commercial", "rent", "Аренда кабинетов в салонах красоты"),
    8037: ("commercial", "rent", "Аренда медицинских и массажных кабинетов"),
    8038: ("commercial", "rent", "Аренда помещений для автобизнеса"),
    8039: ("commercial", "rent", "Аренда торговых контейнеров"),
    8040: ("commercial", "rent", "Аренда спортивных залов"),
    8041: ("commercial", "rent", "Аренда коворкинга"),
    8042: ("commercial", "rent", "Аренда учебных кабинетов"),
    8149: ("commercial", "sale", "Продажа помещений свободного назначения"),
    8150: ("commercial", "rent", "Аренда помещений свободного назначения"),
    8151: ("commercial", "rent_request", "Сниму коммерческую недвижимость"),
    12188: ("house", "sale", "Продажа барачных домов"),
    12189: ("house", "sale", "Продажа времянки"),
    12190: ("house", "sale", "Продажа таунхаусов"),
    12191: ("house", "sale", "Продажа дачи"),
    12193: ("house", "sale", "Продажа коттеджей и домов"),
    12194: ("house", "sale", "Продажа полдома"),
    12205: ("house", "sale", "Продажа модульных домов"),
    12666: ("room", "daily_rent", "Хостелы"),
    12743: ("house", "daily_rent", "Аренда коттеджей и домов на Иссык-Куле"),
    12767: ("apartment", "daily_rent", "Аренда квартир на Иссык-Куле"),
    12769: ("resort", "daily_rent", "Юрты на Иссык-Куле"),
    12771: ("house", "daily_rent", "Гостевые дома на Иссык-Куле"),
    12791: ("resort", "daily_rent", "Кемпинг на Иссык-Куле"),
    12792: ("room", "daily_rent", "Хостелы и койко-места на Иссык-Куле"),
    12798: ("room", "daily_rent", "Гостиницы на Иссык-Куле"),
    12885: ("resort", "daily_rent", "Детские лагеря на Иссык-Куле"),
}

#: All distinct property types / deals appearing above (for config validation).
PROPERTY_TYPES: tuple[str, ...] = tuple(sorted({v[0] for v in CATEGORIES.values()}))
DEALS: tuple[str, ...] = tuple(sorted({v[1] for v in CATEGORIES.values()}))


def categories_for(property_types: list[str], deals: list[str]) -> list[int]:
    """The leaf category ids matching a scope of property types and deals."""
    return [
        cid
        for cid, (pt, dl, _) in CATEGORIES.items()
        if pt in property_types and dl in deals
    ]


# ---------------------------------------------------------------------------
# Attribute params: param-id -> English column name
# ---------------------------------------------------------------------------
#
# Synonyms collapse: lalafo uses different param-ids for the same concept across
# categories (226 & 3299 are both "Этаж"; 228 & 3284 are both "Назначение"; 878 &
# 3278 are both "Дополнительно").  They map to one column so the dataset does not
# grow duplicate columns for one idea.  A param-id not listed here is NOT dropped —
# `parsers.listing` transliterates its Russian name into a column (house.kg rule:
# unmapped is not lost).  Add a line here to give such a column a clean name.

PARAM_MAP: dict[int, str] = {
    68: "furniture",
    69: "rooms",
    70: "area_m2",
    71: "land_area_sotka",
    226: "floor",
    3299: "floor",             # synonym
    228: "purpose",
    3284: "purpose",           # synonym
    351: "kitchen_area_m2",
    354: "parking",
    357: "district",
    867: "series",
    868: "legal_documents",
    870: "utilities",
    871: "heating",
    872: "condition",
    873: "wall_material",
    874: "year_built",
    878: "amenities",
    3278: "amenities",         # synonym
    2149: "offer_type",
    2218: "realtor_services",
    2665: "developer",
    2666: "deal_terms",
    3290: "finishing",
    3291: "layout",
    3294: "completion_date",
    3295: "rent_type",
    3296: "building_type",
    3509: "floors_total",
    5592: "complex_name",
}

#: The param whose ``value_id`` identifies a residential complex (ЖК).  This is the
#: foreign key that ties a listing to the ``complexes`` table.
COMPLEX_PARAM_ID = 5592
#: The param naming the developer / building company.
DEVELOPER_PARAM_ID = 2665
#: The param carrying the intra-city district.
DISTRICT_PARAM_ID = 357


# ---------------------------------------------------------------------------
# Geography: best-effort oblast for a city
# ---------------------------------------------------------------------------
#
# lalafo exposes `city` / `city_id` per ad but no oblast, and no cities catalog
# endpoint.  This table maps the well-known cities/towns (the overwhelming
# majority of rows) to the seven oblasts, mirroring the house.kg `region` field so
# analyses transfer.  An unlisted city gets region=None — documented, not guessed.

REGIONS: tuple[str, ...] = (
    "chui", "issyk_kul", "talas", "naryn", "jalal_abad", "osh", "batken",
)

CITY_REGION: dict[str, str] = {
    # Chui / Bishkek
    "Бишкек": "chui", "Кара-Балта": "chui", "Токмок": "chui", "Токмак": "chui",
    "Кант": "chui", "Кара-Джыгач": "chui", "Кой-Таш": "chui", "Лебединовка": "chui",
    "Сокулук": "chui", "Беловодское": "chui", "Военно-Антоновка": "chui",
    "Кок-Джар": "chui", "Ленинское": "chui", "Орто-Сай": "chui", "Пригородное": "chui",
    "Норус": "chui", "Беш-Кюнгей": "chui", "Петровка": "chui", "Ивановка": "chui",
    "Шопоков": "chui", "Кара-Суу": "chui",
    # Issyk-Kul
    "Каракол": "issyk_kul", "Чолпон-Ата": "issyk_kul", "Балыкчы": "issyk_kul",
    "Чок-Тал": "issyk_kul", "Бостери": "issyk_kul", "Кызыл-Суу": "issyk_kul",
    "Тамчы": "issyk_kul", "Григорьевка": "issyk_kul", "Кара-Ой": "issyk_kul",
    # Osh
    "Ош": "osh", "Узген": "osh", "Кара-Суу (Ош)": "osh", "Ноокат": "osh",
    # Jalal-Abad
    "Джалал-Абад": "jalal_abad", "Жалал-Абад": "jalal_abad", "Кербен": "jalal_abad",
    "Майлуу-Суу": "jalal_abad", "Токтогул": "jalal_abad", "Кок-Джангак": "jalal_abad",
    # Talas
    "Талас": "talas",
    # Naryn
    "Нарын": "naryn", "Ат-Башы": "naryn",
    # Batken
    "Баткен": "batken", "Сулюкта": "batken", "Кызыл-Кия": "batken", "Исфана": "batken",
}


def region_for(city: str | None) -> str | None:
    return CITY_REGION.get(city) if city else None


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

#: When a leaf's totalCount exceeds this, the feed is capped and the crawler
#: partitions the leaf by city to recover the tail.
FEED_CAP_THRESHOLD = 9000
