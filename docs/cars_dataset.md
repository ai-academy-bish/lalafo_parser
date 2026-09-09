# lalafo.kg — Cars: Complete Dataset Guide

The **single source of truth** for the lalafo.kg used-car dataset: what the source
is, how every field is obtained, how the tables relate, and — most importantly —
every trap of an *unmoderated* board that will silently corrupt an analysis if you
do not know about it.

The companion [`code_guide.md`](code_guide.md) describes the code, and
[`lalafo_dataset.md`](lalafo_dataset.md) is the same document for the real-estate
vertical. Both datasets come from **one engine over two taxonomy files**, so the
core schema below is deliberately identical — only the classification axes and the
attribute columns differ.

> **Where the numbers come from.** Percentages and ranges in §5–§6 were measured on a
> sample of **600 ads drawn across the 20 largest brands** in September 2026. They
> describe the shape of the data, not the exact contents of your crawl — the board
> changes daily, and the sample is brand-weighted, not uniform.

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
Its transport section («Транспорт», category **1501**) carries ~427 000 ads across 15
branches. **This dataset covers exactly one of them: «Продажа авто» (category
1502)** — the used-car market, ~55 000 live ads.

Technically it is a Next.js SPA behind a **Cloudflare Turnstile challenge**; all data
comes from a JSON API reached via a warmed `cf_clearance` cookie (see the code guide).
Two API endpoints matter:

* **feed** `/api/search/v3/feed/search?category_id=<leaf>&page=N&per-page=40` — a page
  of listings (used only to discover ids; it is capped, see §3);
* **detail** `/api/search/v3/feed/details/<id>` — the full ad (~60 fields, the
  complete attribute list, the phone number).

### The leaves are brands, not deals

This is the one structural difference from the real-estate vertical. There, a leaf
category encodes *property type × deal* («Продажа квартир», «Аренда домов»). Here,
all 125 leaves under 1502 are **manufacturers** — Toyota (1608), Mercedes-Benz
(1585), Honda (1570), … So:

| column | value | where it comes from |
|---|---|---|
| `property_type` | always `car` | the taxonomy |
| `deal` | always `sale` | the taxonomy |
| `brand` | `Toyota`, `Kia`, … | **the leaf category** |
| `category_name` | same as `brand` | the leaf's own name |

`brand` is a taxonomy *label*, which is why it exists here and not in the real-estate
dataset. It comes from the category the seller filed under — never parsed out of the
title — so it is exactly as reliable as that choice, and no more (§6.7).

`property_type` and `deal` are constant, so they are useless as filters *within* this
dataset. Keep them anyway: they are what lets you `UNION` these listings with the
real-estate ones and still tell a car sale from a flat rental.

### What is deliberately **not** here

Sibling branches under «Транспорт» are separate verticals, each a taxonomy file away
(`make categories`, see the code guide §8). None of them is in this dataset:

| branch | id | ads | note |
|---|---:|---:|---|
| Автозапчасти | 1620 | ~220 000 | parts — by far the largest branch |
| Автоуслуги | 2155 | ~50 000 | services, not goods |
| Аксессуары и тюнинг | 1616 | ~35 000 | |
| Шины и диски | 1621 | ~26 000 | |
| Коммерческий транспорт | 1725 | ~13 000 | trucks; **different param ids** (§7) |
| Велосипеды | 1483 | ~12 600 | |
| Мототехника | 1625 | ~6 300 | |
| Сельхозтехника | 4680 | ~3 200 | |
| **Скупка авто** | **5831** | ~1 550 | **"wanted" ads — buyers, not sellers** |

The last one matters most: «Скупка авто» is the car equivalent of real estate's
`buy_request`. It is excluded, so **every row here is an offer to sell.** That is why
`deal` has no `buy_request` value in this vertical.

### Regions

lalafo scopes everything to `country_id=12` (Kyrgyzstan) and returns all cities in one
feed — there is no per-oblast crawl. The dataset stores `city` / `city_id` per ad and
a best-effort `region` (oblast) derived from the city name. **`region` is null for
about a quarter of car ads** — noticeably worse than for real estate, because car
sellers are spread across small Chui villages that the city→oblast lookup does not
list (§6.8).

---

## 2. Volumes

The site advertises **~54 800** ads under «Продажа авто», spread very unevenly across
the 125 brand leaves:

| brand | ads | | brand | ads |
|---|---:|---|---|---:|
| Toyota | ~7 800 | | Lexus | ~2 000 |
| Mercedes-Benz | ~5 700 | | Nissan | ~1 800 |
| Honda | ~4 500 | | Audi | ~1 800 |
| Hyundai | ~4 400 | | Mazda | ~1 600 |
| Kia | ~4 300 | | ВАЗ (ЛАДА) | ~1 400 |
| Volkswagen | ~3 200 | | Chevrolet | ~1 400 |
| BMW | ~2 500 | | Mitsubishi | ~1 000 |
| Subaru | ~2 300 | | Ford | ~800 |
| Daewoo | ~2 200 | | *95 more brands* | *< 100 each* |

The long tail is real: **95 of the 125 brands have fewer than 100 ads**, and a
handful have none. Any per-brand model needs a minimum-count filter.

> ### ⚠️ The board is overwhelmingly Bishkek
> ~57 % of sampled car ads are Bishkek itself and ~69 % are Chui oblast overall.
> Ош is a distant second (~3 %). Per-region statistics for small oblasts rest on very
> few rows — do not over-read them.

---

## 3. How the crawler works

### Crawl by leaf category
The feed **caps every query** (~10 000 rows for the `category` feed, ~11 000 for the
`ppv-category` feed — the car branch serves the latter). So the crawler never queries
the parent category; it walks **each brand leaf separately**.

### City partitioning is rarely needed here
For a leaf whose `totalCount` exceeds `FEED_CAP_THRESHOLD` (9 000), the crawler
re-queries it per `city_id` and unions the results. **No car brand reaches that cap** —
the largest, Toyota, reports ~8 800 rows in the feed — so partitioning effectively
never fires in this vertical. (The feed's `totalCount` and the category tree's
`ads_count` disagree slightly — ~8 800 vs ~7 800 for Toyota. Neither is wrong; they
count promoted and expired rows differently. Use them as magnitudes, not as truth.) Leave `partition_oversize_by_city: true` on anyway: it costs one extra feed
request per leaf and protects you the day Toyota crosses 9 000.

This makes car coverage *better* than real-estate coverage, where several leaves do
exceed the cap.

### Two resumable stages
Stage 1 discovers ad ids per leaf into `discovered.jsonl`; stage 2 fetches each ad's
detail and images. Both skip what is already stored, so an interrupted crawl resumes.

### One warmed session, many processes
A single browser warm-up yields the `cf_clearance` cookie; worker processes replay it
with a Chrome TLS fingerprint. Workers never open a browser.

---

## 4. The data model

Three tabular subsets + an image subset, linked by lalafo's own natural keys.

```
listings ──┬── user_id     ──→ users
           └── city_id     ──→ cities

images   ───── listing_id  ──→ listings.ad_id
```

### Keys

| Table | PK | Origin | Stable? |
|---|---|---|---|
| `listings` | `ad_id` | lalafo's ad id | yes |
| `users` | `user_id` | lalafo's user id | yes |
| `cities` | `city_id` | lalafo's city id | yes |
| `images` | `foto_id` | uuid4 (our own) | per crawl |

All natural keys are lalafo's, so **two snapshots can be diffed and joined** on
`ad_id` — re-crawl to build a price/views time series.

### There is no `complexes` table
`complexes` (ЖК — residential complexes) is a real-estate concept, driven by attribute
param 5592. The car taxonomy declares no such param, so nothing ever populates it and
the builder skips the empty subset. `complex_id` and `complex_name` still exist as
columns on `listings` — always null — because the two verticals share one record
schema. Drop them, or use them as the join key when unioning with real estate.

### Why users/cities have no separate crawl
lalafo embeds the full `user` object and the city in **every** listing payload, so
these tables are built by de-duplicating what the listings already carry. There is no
public seller-profile or reviews API — **there is no reviews table**. Seller
reputation is `response_rate` / `response_time` on `users`.

---

## 5. Field reference

Field names are English; values stay in the original Russian.

### 5.1 `listings`

**Identity & classification**

| Field | Type | Description |
|---|---|---|
| `ad_id` | int | **PK** — lalafo's ad id, stable across crawls |
| `url` | str | canonical ad URL |
| `pars_date` | str | when we scraped it (ISO-8601 UTC) |
| `category_id` | int | lalafo's brand leaf (1608 = Toyota, …) |
| `property_type` | str | always `car` in this vertical |
| `deal` | str | always `sale` in this vertical |
| `brand` | str | **the manufacturer, from the category** — see §1 |
| `category_name` | str | the leaf's name (same string as `brand`) |

**Content** — `title` · `description` (original Russian). lalafo auto-composes most
car titles from the attributes («Toyota RAV4: 2020 г., 2.5 л, Автомат, Бензин,
Кроссовер»), so the title is largely redundant with the columns — but not always, and
sellers do edit it.

**Price**

| Field | Type | Description |
|---|---|---|
| `price` | float | numeric price, **in `currency`** — present on ~96 % of car ads |
| `old_price` | float | previous price if the seller changed it (price history within one ad) |
| `currency` · `symbol` | str | **mixed: ~55 % `KGS`, ~44 % `USD`** — see §6.1 |
| `price_type` | int | lalafo's price kind code |
| `is_negotiable` | bool | «договорная» — only ~3 % of car ads, unlike real estate |
| `national_price` · `national_currency` | float/str | KGS normalisation — **only filled for non-KGS ads** (§6.1) |

**Geography** — `city` · `city_id` (FK → cities) · `region` (best-effort oblast, null
~24 % of the time) · `lat` · `lng` (present on essentially every ad) ·
`is_hide_house_number`.

**Seller & contact** (see also `users`)

| Field | Type | Description |
|---|---|---|
| `user_id` | int | FK → users |
| `origin_user_id` | int | original poster (differs when re-posted by a dealer) |
| `user_ids` | list[int] | associated accounts |
| `username` | str | display name on the ad |
| `mobile` | str | **phone number, plaintext** — present on ~98 % of ads |
| `email` | str | seller e-mail when present (PII — see §6.5) |
| `device_id` | str | posting device id when exposed |
| `hide_phone` · `hide_chat` | bool | contact preferences |

**Complex** — `complex_id` · `complex_name`: **always null here** (see §4).

**Engagement** — five counters. Typical medians in the sample: `views` ~233,
`impressions` ~12 500, `writers_count` ~5, `favorite_count` ~7, `callers_count` ~3.
Also `response_type`. `impressions` runs ~50× `views` in the sample, which fits the
reading that one counts feed appearances and the other ad opens — lalafo documents
neither, so treat the interpretation as inference and the ratio as observed.

**Status & monetisation** — `status_id`, `is_vip`, `is_premium`, `is_select`,
`is_ppv`, `is_paid_posting`, `is_private_ad`, `is_identity`, `is_freedom`,
`campaign_types`. In the sample **~97 % of car ads carry `is_ppv`** (the branch is
served by the pay-per-view feed) and ~35 % `is_vip`, while `is_premium` was never set
— so `is_ppv` carries no signal here, and `is_vip` does.

**Time** — `created_time` / `updated_time` (**absolute Unix seconds**) with ISO
mirrors `created_date` / `updated_date`. No relative-date decoding needed.

**Media** — `foto_ids` (list → images subset), `image_count`. Cars are photographed
well: mean ~8.9 images per ad, median 8, max 53 observed.

### 5.2 Car attributes (flattened from `params`)

Mapped in [`configs/categories/cars.yaml`](../configs/categories/cars.yaml). Fill
rates are from the sample; **every one of these can be null**.

| Column | Fill | Russian param | Values |
|---|---:|---|---|
| `model` | 96 % | Модель | free-ish enum, ~195 distinct in-sample; `Другая модель` for unlisted |
| `year` | 99 % | Год | numeric-as-string, observed 1974–2026 |
| `body_type` | 95 % | Кузов | 12 options: Седан, Кроссовер, Хэтчбэк, Универсал, Внедорожник, Минивэн, Пикап, Бус, Купе, Фургон, Лифтбек, Фастбек |
| `color` | 96 % | Цвет | enum |
| `fuel` | 91 % | Топливо | **multi-select** (§6.2): Бензин, Дизель, Электромобиль, Гибрид, Газ |
| `transmission` | 89 % | Коробка передач | Автомат, Механика, Вариатор, Робот, Типтроник |
| `registration_country` | 89 % | Страна учета | overwhelmingly `Кыргызстан` (~97 %) |
| `engine_volume_l` | 85 % | Объем двигателя | litres, numeric-as-string; median 2.0 |
| `drive` | 83 % | Привод | Передний, Задний, `4WD, полный`, `AWD, полный` — commas here are **part of the name** (§6.2) |
| `steering_wheel` | 83 % | Руль | Слева (~88 %), Справа |
| `condition` | 77 % | Состояние | Б/у, Новый |
| `technical_condition` | 73 % | Техническое состояние | **multi-select**: Идеальное, Хорошее, Аварийное, Битый, На запчасти |
| `availability` | 63 % | Наличие | В наличии, На заказ |
| `mileage_km` | **60 %** | Пробег (км.) | kilometres, numeric-as-string; median ~150 000 |
| `payment_terms` | 58 % | Расчет | **multi-select**: Оплата наличными, Возможен обмен, Обмена нет, Кредит, Рассрочка |
| `customs_cleared` | 55 % | Растаможка | Растаможен, Не растаможен |
| `vin_status` | 35 % | VIN код | **a flag, not a VIN**: `с VIN кодом` / `без VIN кода` |
| `battery_capacity` | 11 % | Емкость батареи | **a band, not a number**: `До 30 кВт·ч`, `91–100 кВт·ч`, `Более 100 кВт·ч`; EVs/hybrids only |

Anything lalafo adds later arrives automatically as a transliterated column; the
untouched param list is in **`params_raw`** (JSON) — nothing is lost.

### 5.3 `users`

| Field | Type | Description |
|---|---|---|
| `user_id` | int | **PK** |
| `user_hash` | str | lalafo's account hash |
| `username` · `avatar` | str | display name, avatar URL |
| `pro` | bool | a business/dealer account |
| `is_banned` · `is_deleted` | bool | account state |
| `response_rate` | int | % of messages answered — the reputation signal |
| `response_time` | int | typical response time (seconds) |
| `response_info` | str | the human-readable version |
| `ads_count` | int | **derived** — listings by this user in the dataset |

`ads_count` is the cheap dealer detector: a private seller has one or two cars, a
dealer has dozens. Cross-check with `pro`.

### 5.4 `cities`

`city_id` (PK) · `name` · `region` (best-effort oblast) · `listing_count` (derived).

### 5.5 `images`

| Field | Type | Description |
|---|---|---|
| `foto_id` | str | **PK** — uuid4 |
| `listing_id` · `ad_id` | int | FK → listings.ad_id |
| `image_id` | int | lalafo's image id |
| `url` · `webp_url` | str | original CDN URLs (host rotates; bytes here are durable) |
| `width` · `height` | int | resolution |
| `is_main` · `is_cv_image` | bool | cover flag; whether it is a CV-generated image |
| `p_hash` | str | **perceptual hash** — dedupe near-identical images across ads |
| `image` | Image | HF `Image` feature — decodes straight to PIL (published Parquet only) |

Car photos are a genuinely good vision corpus: ~9 images per ad from multiple angles,
with the brand available as a free label from the category.

---

## 6. Pitfalls

Every item below is a real property of this source, verified in the sample. The naive
approach is wrong.

### 6.1 🔴 `currency` is mixed, and `national_price` does not save you
About **55 % of car ads are priced in KGS and 44 % in USD**. Averaging `price` across
them is meaningless. The obvious fix — "just use `national_price`" — is a trap:
**lalafo fills `national_price` only when the ad is not already in KGS.** In the
sample it was set on 261 of 263 USD ads and on **0 of 327 KGS ads**.

```python
kgs = row["national_price"] if row["currency"] != "KGS" else row["price"]
```

Do that before any price analysis. If you need USD instead, invert it with a rate of
your choosing — the dataset does not carry one, and the KGS/USD rate moves.

### 6.2 🔴 Some attributes are multi-select, joined by `", "` — and `value_id` lies
`fuel`, `technical_condition` and `payment_terms` let the seller tick several options.
The API returns them as one alphabetically-sorted, comma-joined string:

```
fuel                = "Бензин, Газ"
technical_condition = "Идеальное, Хорошее"
payment_terms       = "Возможен обмен, Кредит, Оплата наличными, Рассрочка"
```

So `fuel == "Бензин"` silently drops every petrol car that also runs on LPG. Split
instead:

```python
fuels = set(str(row["fuel"] or "").split(", "))
if "Бензин" in fuels: ...
```

The cost of getting this wrong is measurable. In the 600-ad sample, exact matching
finds 389 petrol cars and 15 gas cars; splitting finds **409 and 30**. That is 5 % of
petrol cars and *half* of all LPG cars quietly missing from a naive count.

**But do not split every column.** In `drive` and `body_type` the comma is *part of
the option name* — `"4WD, полный"` is one value, not two. The reliable test is
`value_id` in `params_raw`: single-select params give each display string its own id,
multi-select ones do not.

Which brings the second half of the trap: **for a multi-select param, `value_id` in
`params_raw` identifies only one of the selected options.** In the sample, id `7070`
was attached to `Бензин`, `Бензин, Газ`, `Бензин, Дизель` *and*
`Бензин, Гибрид, Электромобиль` alike. Never key a multi-select attribute on
`value_id`; parse the string.

### 6.3 🔴 `mileage_km` is missing on 40 % of ads — and wrong on some of the rest
Only ~60 % of car ads state mileage at all, and the field is free numeric input, so it
contains impossible values in both directions: the sample held **5 ads over
1 000 000 km** and 7 under 100 km. Filter to a plausible band (say 1 000–500 000) and
treat the missing 40 % as missing — not as zero, and not as "new".

`engine_volume_l` has the same shape: 14 of 515 values were under 0.5 L.

### 6.4 🔴 `vin_status` is not a VIN, `battery_capacity` is not a number
Two columns whose Russian labels promise more than they deliver:

* `vin_status` ∈ {`с VIN кодом`, `без VIN кода`} — it says *whether* the seller claims
  a VIN, never the VIN itself. lalafo does not expose VINs.
* `battery_capacity` is a bucket label (`До 30 кВт·ч`, `91–100 кВт·ч`,
  `Более 100 кВт·ч`), not a kWh figure. Parse it to an interval, not a float. Present
  on ~11 % of ads — EVs and hybrids only.

They are named this way on purpose; do not "fix" the names back.

### 6.5 🔴 Contact PII is present
`mobile` (on ~98 % of ads) and `email` (sometimes) are real personal data, as are
`user_hash` and `device_id`. Treat the dataset accordingly; do not redistribute raw
contact fields without cause or a lawful basis.

### 6.6 🟡 Dealers dominate, and re-posts inflate counts
A dealer lot posts each car individually and re-posts it when it goes stale, so the
same physical car appears several times with different `ad_id`s. Dedupe on cover-image
`p_hash` or on `(brand, model, year, price, user_id)` before counting "cars on the
market". Use `users.ads_count` and `users.pro` to separate dealers from private
sellers — the two populations price differently.

### 6.7 🟡 `brand` is the seller's filing choice
`brand` comes from the category, which is far more reliable than parsing a title — but
it is still the seller's click. Mis-filed cars exist, and `Другая модель` (~1 % of
ads) means the model was not in lalafo's list, not that the model is unknown. The
brand `Другие автомобили` (leaf 1612, ~900 ads) is a genuine catch-all, not a
manufacturer — exclude it from per-brand comparisons.

### 6.8 🟡 `region` is null for ~24 % of car ads
Derived from the city name via a fixed lookup. Car sellers cluster in small Chui
settlements (Новопавловка, Лебединовка, …) that the lookup does not carry, so the null
rate is markedly higher than in the real-estate dataset. Use `city` directly when you
need geography, and treat `region` as a coarse convenience.

### 6.9 🟢 Timestamps are absolute
`created_time` / `updated_time` are Unix seconds with ISO mirrors — no relative dates
to resolve.

---

## 7. Known limitations

| Limitation | Detail |
|---|---|
| **One branch only** | «Продажа авто» (1502). Parts, services, moto, trucks and «Скупка авто» are separate verticals — see §1. |
| **No VINs** | `vin_status` is a flag. There is no vehicle-history data of any kind. |
| **No reviews** | lalafo exposes no public seller-review API. Reputation is `response_rate` / `response_time` only. |
| **No public profile** | Seller fields come from the `user` object embedded in listings. |
| **Truck params differ** | If you add «Коммерческий транспорт» later, its param ids are *not* these: mileage is 2063 not 56, transmission 2065 not 64, and brand/model are 2084/2462. Give that branch its own `params:` block. |
| **`region` is best-effort** | Derived from the city name; null ~24 % of the time (§6.8). |
| **Snapshot, not history** | Each crawl is point-in-time; `views` and prices change. Keys are stable, so re-crawl and diff. |
| **Category noise** | Some cars are mis-filed by the seller; `brand` inherits that error. |

---

## 8. Recipes

```python
from datasets import load_dataset

ads    = load_dataset("<repo>", "listings", split="train")
users  = load_dataset("<repo>", "users",    split="train")
images = load_dataset("<repo>", "images",   split="train")
```

**A comparable KGS price (do this first — §6.1):**

```python
def kgs(r):
    return r["national_price"] if r["currency"] != "KGS" else r["price"]
```

**Median price by brand, with a minimum-count filter (§2):**

```python
import statistics
from collections import defaultdict

by_brand = defaultdict(list)
for r in ads:
    p = kgs(r)
    if p:
        by_brand[r["brand"]].append(p)

for brand, prices in sorted(by_brand.items(), key=lambda kv: -len(kv[1])):
    if len(prices) >= 50:                     # the tail is 95 brands deep
        print(f"{brand:<16} n={len(prices):<5} median={statistics.median(prices):,.0f} KGS")
```

**Depreciation curve — price against age, cleaned (§6.3):**

```python
def num(v):
    try: return float(str(v).replace(",", ".").split()[0])
    except (TypeError, ValueError): return None

rows = []
for r in ads:
    p, year, km = kgs(r), num(r["year"]), num(r["mileage_km"])
    if p and year and km and 1_000 <= km <= 500_000 and 1990 <= year <= 2026:
        rows.append((2026 - year, km, p))

by_age = defaultdict(list)
for age, _, p in rows:
    by_age[age].append(p)
for age in sorted(by_age):
    if len(by_age[age]) >= 30:
        print(age, round(statistics.median(by_age[age])))
```

**Expand a multi-select attribute (§6.2):**

```python
from collections import Counter

fuels = Counter()
for r in ads:
    for f in str(r["fuel"] or "").split(", "):
        if f:
            fuels[f] += 1
print(fuels.most_common())      # counts each car once per fuel it accepts
```

**Dealers vs private sellers** — note `ads_count` counts listings *in the dataset*,
so on a partial crawl the threshold is measuring your sample, not the market. Cross-
check with `users.pro`:

```python
by_user = {u["user_id"]: u for u in users}
dealer  = lambda r: (by_user.get(r["user_id"], {}).get("ads_count") or 0) >= 10

for label, keep in (("dealer", True), ("private", False)):
    prices = [kgs(r) for r in ads if dealer(r) is keep and kgs(r)]
    print(label, len(prices), round(statistics.median(prices)))
```

**Deduplicate re-posted cars by cover-image hash (§6.6):**

```python
seen, unique = set(), []
for im in images:
    if im["is_main"]:
        if im["p_hash"] in seen:
            continue
        seen.add(im["p_hash"])
        unique.append(im["ad_id"])
```

**A brand-classification training set** — the category gives you a free label:

```python
main = {im["ad_id"]: im for im in images if im["is_main"]}
pairs = [(main[r["ad_id"]]["image"], r["brand"]) for r in ads if r["ad_id"] in main]
```
