# lalafo.kg — Mobile Phones: Complete Dataset Guide

The **single source of truth** for the lalafo.kg handset dataset: what the source is,
how every field is obtained, how the tables relate, and — most importantly — every
trap of an *unmoderated* board that will silently corrupt an analysis if you do not
know about it.

The companion [`code_guide.md`](code_guide.md) describes the code;
[`lalafo_dataset.md`](lalafo_dataset.md) and [`cars_dataset.md`](cars_dataset.md) are
the same document for the other two verticals. All three come from **one engine over
three taxonomy files**, so the core schema is deliberately identical — only the
classification axes and the attribute columns differ.

> **Where the numbers come from.** Unlike the car guide's sampled figures, every
> percentage and range below is measured over the **complete crawl: all 8 160
> listings**, September 2026. It is still a snapshot — the board changes daily.

---

## Table of contents

1. [The source](#1-the-source)
2. [Volumes](#2-volumes)
3. [How the crawler works](#3-how-the-crawler-works)
4. [The data model](#4-the-data-model)
5. [Field reference](#5-field-reference)
6. [Pitfalls — read this before analysing](#6-pitfalls)
7. [Known limitations](#7-known-limitations)
8. [Recipes](#8-recipes)

---

## 1. The source

[lalafo.kg](https://lalafo.kg) is Kyrgyzstan's largest *informal* classifieds board.
Its electronics section («Техника и электроника», category **1317**) carries ~126 000
ads. **This dataset covers exactly one sub-branch: «Мобильные телефоны» (category
1360)** — handsets offered for sale, and nothing else.

Technically it is a Next.js SPA behind a **Cloudflare Turnstile challenge**; all data
comes from a JSON API reached via a warmed `cf_clearance` cookie (see the code guide).
Two API endpoints matter:

* **feed** `/api/search/v3/feed/search?category_id=<leaf>&page=N&per-page=40` — a page
  of listings (used only to discover ids; it is capped, see §3);
* **detail** `/api/search/v3/feed/details/<id>` — the full ad (~60 fields, the
  complete attribute list, the phone number).

### The leaves are brands

As in the car vertical, every leaf under 1360 is a **manufacturer** — Apple iPhone
(1361), Samsung (1369), Redmi (8434), … 32 of them. So:

| column | value | where it comes from |
|---|---|---|
| `property_type` | always `phone` | the taxonomy |
| `deal` | always `sale` | the taxonomy |
| `brand` | `Apple iPhone`, `Samsung`, … | **the leaf category** |
| `category_name` | same as `brand` | the leaf's own name |

`brand` is a taxonomy *label* — it comes from the category the seller filed under,
never parsed from the title. `property_type` and `deal` are constant and therefore
useless as filters *within* this dataset; keep them for unions with the other
verticals.

### What is deliberately **not** here

Each of these is a sibling branch, and each would be its own taxonomy file
(`make categories`, see the code guide §8):

| branch | id | ads | note |
|---|---:|---:|---|
| Аксессуары для мобильных телефонов | 1359 | ~6 000 | cases, chargers, headphones, SIM cards |
| **Скупка мобильных телефонов** | **6652** | ~970 | **"wanted" ads — buyers, not sellers** |
| Планшеты | 4720 | ~825 | tablets |
| Настольные ПК и комплектующие | 1341 | ~18 600 | desktops |
| Ноутбуки и аксессуары | 1343 | ~7 700 | laptops |
| Видеоигры и приставки | 1396 | ~4 800 | consoles |
| Смарт-часы | 7925 | ~1 100 | smartwatches |

«Скупка» matters most: it is the phone equivalent of real estate's `buy_request`, and
it is excluded — so **every row here is an offer to sell.**

### Regions

lalafo scopes everything to `country_id=12` (Kyrgyzstan) and returns all cities in one
feed. The dataset stores `city` / `city_id` per ad and a best-effort `region` (oblast)
derived from the city name; `region` is null for **19 %** of rows (§6.9).

---

## 2. Volumes

**8 160 listings**, 37 627 images (~4.7 GB), 6 605 sellers, 175 cities.

The brand distribution is steeper than in the car vertical — Apple alone is nearly
half the board:

| brand | ads | share | | brand | ads |
|---|---:|---:|---|---|---:|
| Apple iPhone | 3 696 | 45.3 % | | Honor | 121 |
| Samsung | 1 271 | 15.6 % | | Vivo | 93 |
| Redmi | 1 170 | 14.3 % | | Nokia | 87 |
| Poco | 511 | 6.3 % | | Huawei | 86 |
| Другие мобильные телефоны | 344 | 4.2 % | | Google | 85 |
| Xiaomi | 307 | 3.8 % | | Tecno | 71 |

**18 of the 32 brands have fewer than 50 ads.** Anything per-brand needs a
minimum-count filter, and four brands (Apple, Samsung, Redmi, Poco) are 81 % of the
data.

> ### ⚠️ The board is overwhelmingly Bishkek
> Бишкек is 4 571 ads (56 %); Chui oblast overall is 5 440 (67 %). Ош is a distant
> second at 585 (7 %). Naryn has 22 rows in the whole dataset — do not compute
> per-oblast statistics for the small ones.

---

## 3. How the crawler works

### Crawl by leaf category
The feed **caps every query** (~10 000 rows for the `category` feed, ~11 000 for the
`ppv-category` feed — this branch is served by the latter). The crawler never queries
the parent; it walks **each brand leaf separately**.

### City partitioning never fires here
A leaf whose `totalCount` exceeds `FEED_CAP_THRESHOLD` (9 000) is re-queried per
`city_id`. The largest leaf, Apple iPhone, reports ~3 900 — less than half the cap —
so partitioning is dead code in this vertical. It stays enabled because it costs one
feed request per leaf when unneeded.

### Coverage
Discovery found 8 160 ids and **every one of them was fetched** (`discovered.jsonl`
and `listings.jsonl` are both 8 160). Re-querying the 32 leaves afterwards reported
8 732 live ads, so this snapshot is ~94 % of the branch: ads appear and expire between
the discovery pass and any later count. Re-run the crawl to top it up — it is
resumable and skips what it already has.

### Two resumable stages, one warmed session
Stage 1 discovers ad ids per leaf; stage 2 fetches details and images in worker
processes. A single browser warm-up yields the `cf_clearance` cookie; workers replay
it with a Chrome TLS fingerprint and never open a browser.

---

## 4. The data model

Three tabular subsets + an image subset, linked by lalafo's own natural keys.

```
listings ──┬── user_id     ──→ users
           └── city_id     ──→ cities

images   ───── listing_id  ──→ listings.ad_id
```

| Table | PK | Origin | Stable? |
|---|---|---|---|
| `listings` | `ad_id` | lalafo's ad id | yes |
| `users` | `user_id` | lalafo's user id | yes |
| `cities` | `city_id` | lalafo's city id | yes |
| `images` | `foto_id` | uuid4 (our own) | per crawl |

All natural keys are lalafo's, so **two snapshots can be diffed and joined** on
`ad_id` — re-crawl to build a price/views time series.

### There is no `complexes` table
`complexes` (ЖК) is a real-estate concept driven by attribute param 5592. The phone
taxonomy declares no special params, so nothing populates it and the builder skips the
empty subset. `complex_id` / `complex_name` still exist as always-null columns on
`listings`, because all three verticals share one record schema.

### Why users/cities have no separate crawl
lalafo embeds the full `user` object and the city in **every** listing payload, so
these tables are de-duplicated from what the listings already carry. There is no
public seller-profile or reviews API — **there is no reviews table**.

---

## 5. Field reference

Field names are English; values stay in the original Russian.

### 5.1 `listings` — core

**Identity & classification** — `ad_id` (PK), `url`, `pars_date`, `category_id`,
`property_type` (always `phone`), `deal` (always `sale`), **`brand`**, `category_name`.

**Content** — `title` · `description`. lalafo auto-composes most titles from the
attributes, so the title is largely redundant with the columns — but sellers edit it,
and it is where "срочно", "торг" and swap offers live.

**Price**

| Field | Type | Description |
|---|---|---|
| `price` | float | numeric price, in `currency` — present on **88 %** of ads |
| `old_price` | float | previous price if the seller changed it |
| `currency` · `symbol` | str | **93 % `KGS`**, 65 ads `USD`, 491 with none |
| `is_negotiable` | bool | «договорная» — **11 %** of ads |
| `national_price` | float | KGS normalisation — **only filled for non-KGS ads** (§6.8) |

Median KGS price is **18 000 сом**; the 65 USD ads have a median of $1 000.

**Geography** — `city` · `city_id` (FK) · `region` (null 19 %) · `lat` · `lng`
(present on 8 026 of 8 160) · `is_hide_house_number`.

**Seller & contact** — `user_id` (FK), `origin_user_id`, `user_ids`, `username`,
`mobile` (**83 %** — lower than the car board's 96 %), `email` (**27 %**), `device_id`,
`hide_phone`, `hide_chat`.

**Engagement** — medians over the full crawl: `views` 177, `impressions` 7 984,
`writers_count` 8, `favorite_count` 4, `callers_count` 3. Phone buyers message far
more than they call — `writers_count` runs ahead of `callers_count`, the reverse of
what a big-ticket vertical looks like.

**Status & monetisation** — `is_vip` (146 ads), `is_ppv` (740), `is_premium` (never
set), plus `status_id`, `is_select`, `is_paid_posting`, `is_private_ad`, `is_identity`,
`is_freedom`, `campaign_types`. Note the *feed* is `ppv-category` but only 9 % of ads
actually carry `is_ppv` — the feed type is not the ad flag.

**Time** — `created_time` / `updated_time` (absolute Unix seconds) with ISO mirrors.

**Media** — `foto_ids`, `image_count`. Mean 4.6 images per ad, median 4, max 43 — half
the car board's richness, and **52 ads have no image at all**.

### 5.2 Phone attributes (flattened from `params`)

Mapped in [`configs/categories/phones.yaml`](../configs/categories/phones.yaml).
Fill rates are over all 8 160 rows; **every one of these can be null**, and several are
missing in a *structured* way (§6.7).

| Column | Fill | Russian param | Values |
|---|---:|---|---|
| `model` | 95 % | Модель | **collapsed from 31 per-brand params** (§6.1); 876 distinct |
| `color` | 88 % | Цвет | collapsed from 2 ids (Apple / rest); Apple names are 36 % Latin-script (§6.5) |
| `storage` | 78 % | Объем памяти | **carries its unit**: `256 ГБ`, `1 ТБ`, `< 2 ГБ` (§6.4) |
| `technical_condition` | 70 % | Техническое состояние | **multi-select**: Идеальное, Хорошее, Трещины, Царапины, Требуется ремонт |
| `condition` | 63 % | Состояние | Б/у (4 307), Новый (899) |
| `package_contents` | 57 % | Комплектация | **multi-select**, 74 combinations: Чехол, Коробка, Кабель, Зарядное устройство… |
| `sim` | 57 % | SIM-карты | **multi-select**: `2 SIM`, `1 SIM, eSIM`, `eSIM` |
| `ram` | 47 % | Оперативная память | **carries its unit**: `8 ГБ`; Apple-poor (§6.7) |
| `delivery` | 46 % | Доставка | **multi-select**: Самовывоз, Платная доставка, Бесплатная доставка |
| `imei_status` | 46 % | Регистрация IMEI | **a flag, not an IMEI**: `Зарегистрирован` / `Не зарегистрирован` |
| `installment` | 42 % | Рассрочка | `Без рассрочки` / `В рассрочку` |
| `device_class` | 37 % | Класс | **multi-select**, and **never set on Apple** (§6.7): Флагман, Бюджетный, Игровой, Камерофон, Безрамочный, Кнопочный |
| `battery_health_pct` | 30 % | Состояние аккумулятора, % | a bare number — and **Apple-only** (§6.7); needs cleaning (§6.3) |

Anything lalafo adds later arrives automatically as a transliterated column; the
untouched param list is in **`params_raw`** (JSON) — nothing is lost.

### 5.3 `users`

`user_id` (PK), `user_hash`, `username`, `avatar`, `pro`, `is_banned`, `is_deleted`,
`response_rate`, `response_time`, `response_info`, `ads_count` (derived).

6 605 sellers for 8 160 ads — this board is mostly individuals, not shops. Only **90
accounts are flagged `pro`**, and only 38 sellers have 10+ listings, though the largest
single seller has **160**.

### 5.4 `cities`

`city_id` (PK), `name`, `region`, `listing_count` (derived).

### 5.5 `images`

`foto_id` (PK), `listing_id` · `ad_id` (FK), `image_id`, `url` · `webp_url`,
`width` · `height`, `is_main`, `is_cv_image`, `p_hash` (perceptual hash),
`image` (HF `Image` feature, published Parquet only).

---

## 6. Pitfalls

Every item below was verified against the full crawl. The naive approach is wrong.

### 6.1 🔴 `model` is collapsed from 31 params — and is only unique *within* a brand
lalafo gives **every brand leaf its own «Модель» param id and its own value list**:
183 for Apple, 225 for Samsung, 230 for Xiaomi, 3672 for Redmi, and so on for all 31.
The taxonomy collapses them into one `model` column, exactly as the real-estate
taxonomy collapses 226 and 3299 into `floor`. Without that you would get 31
near-empty columns instead of one useful one.

The consequence: **`model` is not a global key.** 14 model names are used by more than
one brand — `M7` is both a Poco and a Tecno, `C65` is both a Poco and a Realme, `X7`
is both an Oppo and a Poco. Always group by `(brand, model)`:

```python
key = (row["brand"], row["model"])       # not row["model"]
```

`Другая модель` (87 ads) is a catch-all across five brands, not a model.

### 6.2 🔴 Five attributes are multi-select, joined by `", "` — and `value_id` lies
`technical_condition`, `device_class`, `package_contents`, `sim` and `delivery` let
the seller tick several options. The API returns one alphabetically-sorted,
comma-joined string:

```
technical_condition = "Идеальное, Хорошее"
sim                 = "1 SIM, eSIM"
delivery            = "Платная доставка, Самовывоз"
```

So `technical_condition == "Идеальное"` drops the 328 ads that are "Идеальное,
Хорошее". Split instead:

```python
conds = set(str(row["technical_condition"] or "").split(", "))
```

The cost is large here, and it is worst for the rarer classes. Counting
`device_class` by exact match versus splitting on `", "`:

| class | exact match | split | missed |
|---|---:|---:|---:|
| Безрамочный | 687 | 1 026 | 33 % |
| Флагман | 651 | 993 | 34 % |
| Камерофон | 319 | 678 | **52 %** |
| Игровой | 235 | 678 | **65 %** |

Sellers routinely tick three or four classes at once, so the more a class co-occurs
with others, the more exact matching erases it.

And `value_id` in `params_raw` does **not** disambiguate them: on this crawl one
`device_class` id covered 18 different display strings and one `package_contents` id
covered 25. `storage` and `condition`, by contrast, are genuinely single-select —
each display string has its own id. Parse the string; do not key on `value_id`.

### 6.3 🔴 `battery_health_pct` is free numeric input, and it shows
Present on 30 % of ads, median 86. But the observed range is **4 to 123 659 000** —
someone typed an IMEI into the percentage box. Eleven values are below 50 and one is
above 100. Clamp before use:

```python
bh = num(row["battery_health_pct"])
bh = bh if bh and 0 < bh <= 100 else None
```

### 6.4 🔴 `storage` and `ram` carry their units, and the units are mixed
Values are strings like `256 ГБ`, `8 ГБ`, `1 ТБ`, `< 2 ГБ`, `< 16 ГБ`. Sorting them
lexically is nonsense — `1 ТБ` lands first and `64 ГБ` after `512 ГБ`. Parse to a
number of GB, and decide what to do with the two `<` buckets:

```python
def gb(v):
    if not v: return None
    s = str(v).replace("<", "").strip()
    n = float(s.split()[0])
    return n * 1024 if "ТБ" in s else n
```

They are named `storage` / `ram`, not `storage_gb` / `ram_gb`, precisely so the unit
is not assumed. Do not "fix" the names.

### 6.5 🔴 Apple colour names are a third English
Of 3 319 Apple ads with a colour, **1 216 (36 %) use the Latin marketing name** —
`Deep Purple`, `Natural Titanium`, `Space Gray`, `Black Titanium` — while the rest use
Russian (`Черный`, `Белый`, `Серебристый`). Grouping by `color` splits the same phone
into two buckets. Normalise before counting, or restrict colour analysis to a single
language family and say so.

### 6.6 🔴 Contact PII is present
`mobile` (83 % of ads) and `email` (27 %) are real personal data, as are `user_hash`
and `device_id`. Note the e-mail rate is far higher than on the car board. Treat the
dataset accordingly; do not redistribute raw contact fields without a lawful basis.

### 6.7 🟡 Missingness is brand-structured, not random
This is the subtlest trap in the dataset. Three attributes are missing **because of
which brand the ad is**, not at random:

| column | Apple | Samsung | Redmi | Poco | Xiaomi |
|---|---:|---:|---:|---:|---:|
| `battery_health_pct` | **67 %** | **0 %** | **0 %** | **0 %** | **0 %** |
| `device_class` | **0 %** | 68 % | 61 % | 73 % | 75 % |
| `ram` | 25 % | 65 % | 63 % | 71 % | 74 % |

`battery_health_pct` is an **iPhone-only field** — lalafo does not offer it on Android
leaves. "Median battery health by brand" therefore returns one row, not thirty-two,
and "median battery health" overall is really "median iPhone battery health".
`device_class` is the mirror image: Android-only. `ram` is simply rarer on Apple ads,
because Apple does not market RAM.

Any imputation or "drop rows with nulls" step will silently delete a whole brand.
Check fill rate *per brand* before modelling.

### 6.8 🟡 `currency` is nearly all KGS — but the `national_price` rule still applies
93 % of ads are KGS, 65 are USD, and 491 carry no currency at all. lalafo fills
`national_price` **only when the ad is not already in KGS** (62 of the 65 USD ads; 0 of
the 7 604 KGS ones). The normalisation is the same as in the car dataset even though
it matters less here:

```python
kgs = row["national_price"] if row["currency"] != "KGS" else row["price"]
```

Drop the no-currency ads from price work rather than assuming a unit.

### 6.9 🟡 `region` is null for 19 %, and 52 ads have no image
`region` is derived from the city name via a fixed lookup; small settlements are not
in it. Use `city` directly when you need geography. Separately, 52 listings carry zero
images — filter them out of any vision work rather than discovering it mid-training.

### 6.10 🟢 Timestamps are absolute
`created_time` / `updated_time` are Unix seconds with ISO mirrors — no relative dates
to resolve.

---

## 7. Known limitations

| Limitation | Detail |
|---|---|
| **Handsets only** | Category 1360. Accessories, buy-requests, tablets, laptops, desktops, consoles and smartwatches are separate verticals — see §1. |
| **No IMEI** | `imei_status` is a flag. There is no device-history or blacklist data. |
| **No reviews** | lalafo exposes no public seller-review API. Reputation is `response_rate` / `response_time` only. |
| **Apple-skewed** | 45 % of rows are one brand; four brands are 81 %. Balance or stratify before training anything. |
| **Brand-structured nulls** | See §6.7 — three columns are effectively brand-exclusive. |
| **`region` is best-effort** | Derived from the city name; null 19 % of the time. |
| **Snapshot, not history** | Each crawl is point-in-time; `views` and prices change. Keys are stable, so re-crawl and diff. |
| **Category noise** | Some phones are mis-filed by the seller; `brand` inherits that error. `Другие мобильные телефоны` (344 ads) is a genuine catch-all leaf, not a manufacturer — exclude it from per-brand comparisons. |

---

## 8. Recipes

```python
from datasets import load_dataset

ads    = load_dataset("<repo>", "listings", split="train")
users  = load_dataset("<repo>", "users",    split="train")
images = load_dataset("<repo>", "images",   split="train")
```

**The helpers every recipe below assumes (§6.3, §6.4, §6.8):**

```python
def num(v):
    try: return float(str(v).replace(",", ".").split()[0])
    except (TypeError, ValueError): return None

def kgs(r):
    return r["national_price"] if r["currency"] != "KGS" else r["price"]

def gb(v):
    if not v: return None
    s = str(v).replace("<", "").strip()
    n = num(s)
    return n * 1024 if n and "ТБ" in s else n

def health(r):
    bh = num(r["battery_health_pct"])
    return bh if bh and 0 < bh <= 100 else None
```

**Median price by (brand, model) — the only correct grouping (§6.1):**

```python
import statistics
from collections import defaultdict

by_model = defaultdict(list)
for r in ads:
    p = kgs(r)
    if p and r["model"] and r["model"] != "Другая модель":
        by_model[(r["brand"], r["model"])].append(p)

for key, prices in sorted(by_model.items(), key=lambda kv: -len(kv[1]))[:15]:
    print(f"{key[0]:<14} {key[1]:<20} n={len(prices):<4} {statistics.median(prices):>9,.0f} KGS")
```

**Does storage carry a price premium, within one model?**

```python
rows = defaultdict(lambda: defaultdict(list))
for r in ads:
    p, g = kgs(r), gb(r["storage"])
    if p and g and r["brand"] == "Apple iPhone" and r["model"]:
        rows[r["model"]][g].append(p)

for model, tiers in rows.items():
    if len(tiers) >= 2 and sum(len(v) for v in tiers.values()) >= 20:
        line = "  ".join(f"{int(g)}GB={statistics.median(v):,.0f}"
                         for g, v in sorted(tiers.items()) if len(v) >= 3)
        if line: print(f"{model:<22} {line}")
```

**Battery health vs price — remember this is iPhone-only (§6.7):**

```python
pairs = [(health(r), kgs(r)) for r in ads]
pairs = [(h, p) for h, p in pairs if h and p]
print(f"n={len(pairs)}  (all Apple — no Android leaf offers this field)")
buckets = defaultdict(list)
for h, p in pairs:
    buckets[min(int(h // 5) * 5, 100)].append(p)
for band in sorted(buckets):
    if len(buckets[band]) >= 20:
        print(f"{band}-{band+4}%  n={len(buckets[band]):<4} median={statistics.median(buckets[band]):,.0f}")
```

**Expand a multi-select attribute (§6.2):**

```python
from collections import Counter

classes = Counter()
for r in ads:
    for c in str(r["device_class"] or "").split(", "):
        if c:
            classes[c] += 1
print(classes.most_common())     # counts each phone once per class it claims
```

**Check fill rate per brand before you model anything (§6.7):**

```python
tot, filled = Counter(), Counter()
col = "device_class"
for r in ads:
    tot[r["brand"]] += 1
    if r[col]:
        filled[r["brand"]] += 1
for b, t in tot.most_common(8):
    print(f"{b:<26} {filled[b] * 100 // t:>3}%  ({filled[b]}/{t})")
```

**A brand-classification training set** — the category is a free label, but mind the
45 % Apple skew (§7) and the 52 image-less ads (§6.9):

```python
main = {im["ad_id"]: im for im in images if im["is_main"]}
pairs = [(main[r["ad_id"]]["image"], r["brand"]) for r in ads if r["ad_id"] in main]
```
