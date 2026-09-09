"""Site-wide constants + every API contract detail — patch here when lalafo changes.

lalafo.kg is a Next.js SPA behind a Cloudflare Turnstile *managed challenge*: the
HTML and the frontend ``/api/*`` proxy are both challenged.  There is no HTML to
scrape.  All data comes from one JSON API, reached by warming a ``cf_clearance``
cookie once with a real browser (``session.py``) and then replaying it with a
Chrome-fingerprinted HTTP client (``http_client.py``).

What lives here is what is true of the **site**:

* ``Api``       — hosts, paths, query knobs, the mandatory app headers;
* ``REGIONS`` / ``CITY_REGION`` — best-effort oblast for a city;
* ``ImageCdn``  — the poster CDN;
* ``FEED_CAP_THRESHOLD`` — when a leaf needs partitioning by city.

What is true of one **section** of the site — its leaf categories, its attribute
param map, its special params — is *not* here: it lives in a taxonomy YAML under
``configs/categories/`` and is loaded by ``taxonomy.py``.  That is what makes a new
vertical (cars, electronics, jobs) a config change rather than a code change.
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

    #: Category tree (via the Next.js proxy).  Used to generate a taxonomy file.
    CATEGORY_TREE = "/api/proxy/catalog/v3/categories/tree"

    #: Attribute schema (enum values) for a category — documentation / mapping aid.
    PARAM_FILTER = "/api/catalog/v3/params/filter?category_id={id}"

    #: Max page size the feed honours.
    PER_PAGE = 40

    #: Headers the API expects.  ``device``/``country-id``/``language`` are the app
    #: identity; the User-Agent is filled in from the warmed browser session so it
    #: matches the ``cf_clearance`` fingerprint.  ``referer`` is the section being
    #: crawled (``Taxonomy.site_url``) — the app always sends the page you came from.
    @staticmethod
    def headers(user_agent: str, referer: str = BASE_URL) -> dict[str, str]:
        return {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": referer,
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
# Geography: best-effort oblast for a city
# ---------------------------------------------------------------------------
#
# lalafo exposes `city` / `city_id` per ad but no oblast, and no cities catalog
# endpoint.  This table maps the well-known cities/towns (the overwhelming
# majority of rows) to the seven oblasts, mirroring the house.kg `region` field so
# analyses transfer.  An unlisted city gets region=None — documented, not guessed.
#
# This is site-wide, not section-specific: a car ad and a flat ad carry the same
# `city` string.

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
