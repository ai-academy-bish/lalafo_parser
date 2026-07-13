# lalafo.kg — Complete Dataset Guide

The **single source of truth** for the lalafo.kg real-estate dataset: what the
source is, how every field is obtained, how the tables relate, and — most
importantly — every trap of an *unmoderated* board that will silently corrupt an
analysis if you do not know about it.

The companion [`code_guide.md`](code_guide.md) describes the code. This describes the
data.

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
Its real-estate section («Недвижимость», category **2029**) is a live, unmoderated
market: private sellers and agents, договорные prices, re-posts, keyword-stuffed
titles. It is messier than the curated boards and much closer to the real
street-level market — which is exactly its research value.

Technically it is a Next.js SPA behind a **Cloudflare Turnstile challenge**; all data
comes from a JSON API reached via a warmed `cf_clearance` cookie (see the code guide).
Two API endpoints matter:

* **feed** `/api/search/v3/feed/search?category_id=<leaf>&page=N&per-page=40` — a page
  of listings (used only to discover ids; it is capped, see §3);
* **detail** `/api/search/v3/feed/details/<id>` — the full ad (~61 fields, the
  complete attribute list, the phone number).

### Property types and deals

The crawler walks the leaf categories under 2029. Each leaf's name gives a
best-effort `property_type` and `deal`:

| `property_type` | `deal` |
|---|---|
| `apartment`, `house`, `room`, `commercial`, `land`, `garage`, `newbuild`, `resort` | `sale`, `rent`, `daily_rent`, `buy_request`, `rent_request`, `other` |

`buy_request` / `rent_request` are people *looking* («Куплю», «Сниму»), not offers —
exclude them if you only want listings.

### Regions

lalafo scopes everything to `country_id=12` (Kyrgyzstan) and returns **all cities of
the country** in one feed — there is no per-oblast crawl. The dataset stores
`city` / `city_id` per ad and a best-effort `region` (oblast) derived from the city
name; `region` is null for towns not in the lookup.

---

## 2. Volumes

The site advertises **~78 000** real-estate listings. The exact crawled total depends
on the feed cap and city-partitioning (§3); expect the low-to-mid tens of thousands
per full run, dominated by apartments (sale + rent). Illustrative leaf sizes measured
during development: apartment-sale ~11 000, land-sale ~8 500.

> ### ⚠️ The board is overwhelmingly Bishkek
> As with every Kyrgyz board, ~90 % of listings are Bishkek/Chui. Per-region
> statistics for small oblasts rest on few rows — do not over-read them.

---

## 3. How the crawler works

### Crawl by leaf category
The feed **caps every query** (~10 000 rows for the `category` feed, ~11 000 for
`ppv-category`). The parent category returns far fewer than its true size, so the
crawler never queries it — it walks **each leaf category separately**, which also
means `property_type`/`deal` are known from the stream, not guessed from text.

### Partition oversize leaves by city
When a leaf's `totalCount` exceeds the cap, the crawler re-queries it **per
`city_id`** and unions the results — each city subset fits under the cap, recovering
the tail the single query hides. (~90 % Bishkek, so this is mostly "Bishkek, then the
rest".)

### Two resumable stages
1. **Discovery** writes every id it finds to `discovered.jsonl` (with its stream
   classification).
2. **Details** fetches every discovered id not yet in `listings.jsonl`.
Kill either at any point and re-run: it resumes.

### One warmed session, many processes
A browser clears Cloudflare once; the detail stage then runs `curl_cffi` across a
process pool. No throttling was observed (50 rapid requests all 200; ~30 detail
records/s at 10 workers in testing).

---

## 4. The data model

Four tabular subsets + an image subset, linked by lalafo's own natural keys.

```
listings ──┬── user_id     ──→ users
           ├── complex_id  ──→ complexes     (residential complex / ЖК)
           └── city_id     ──→ cities

images   ───── listing_id  ──→ listings.ad_id
```

### Keys

| Table | PK | Origin | Stable? |
|---|---|---|---|
| `listings` | `ad_id` | lalafo's ad id | yes |
| `users` | `user_id` | lalafo's user id | yes |
| `complexes` | `complex_id` | the ЖК attribute `value_id` | yes |
| `cities` | `city_id` | lalafo's city id | yes |
| `images` | `foto_id` | uuid4 (our own) | per crawl |

All natural keys are lalafo's, so **two snapshots can be diffed and joined** on
`ad_id` — re-crawl to build a price/views time series.

### Why users/complexes/cities have no separate crawl
lalafo embeds the full `user` object and the ЖК/city in **every** listing payload, so
these tables are built by de-duplicating what the listings already carry. There is no
public seller-profile or reviews API — **there is no reviews table** (unlike
house.kg). Seller reputation is captured instead by `response_rate` / `response_time`
on `users`.

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
| `category_id` | int | lalafo's leaf category |
| `property_type` | str | from the category: `apartment`, `house`, … |
| `deal` | str | from the category: `sale`, `rent`, `daily_rent`, `buy_request`, `rent_request`, `other` |
| `category_name` | str | the leaf's Russian name |

**Content**

| `title` · `description` | str | ad headline and free text (original). Titles carry rooms/area/series; descriptions carry terms and contact preferences |

**Price**

| Field | Type | Description |
|---|---|---|
| `price` | float | numeric price. **Null for negotiable ads** |
| `old_price` | float | previous price if the seller changed it (price history within one ad) |
| `currency` · `symbol` | str | e.g. `USD` / `$` |
| `price_type` | int | lalafo's price kind code |
| `is_negotiable` | bool | «договорная» — explains most null prices |
| `national_price` · `national_currency` | float/str | lalafo's normalisation to KGS |

**Geography**

| `city` · `city_id` | str/int | FK → cities |
| `region` | str | best-effort oblast; null if the city is unlisted |
| `lat` · `lng` | float | coordinates |
| `is_hide_house_number` | bool | seller hid the exact address |

**Seller & contact** (see also `users`)

| Field | Type | Description |
|---|---|---|
| `user_id` | int | FK → users |
| `origin_user_id` | int | original poster (differs when re-posted by an agency) |
| `user_ids` | list[int] | associated accounts |
| `username` | str | display name on the ad |
| `mobile` | str | **phone number, plaintext** |
| `email` | str | seller e-mail when present (PII — see §6) |
| `device_id` | str | posting device id when exposed |
| `hide_phone` · `hide_chat` | bool | contact preferences |

**Complex** — `complex_id` (FK → complexes) · `complex_name` (ЖК display name).

**Engagement** — `views`, `impressions`, `favorite_count`, `callers_count`,
`writers_count`, `response_type`. Five counters where house.kg had one.

**Status & monetisation** — `status_id`, `is_vip`, `is_premium`, `is_select`,
`is_ppv`, `is_paid_posting`, `is_private_ad`, `is_identity`, `is_freedom`,
`campaign_types` (paid-promotion types running on the ad).

**Time** — `created_time` / `updated_time` (**absolute Unix seconds**) with ISO
mirrors `created_date` / `updated_date`. No relative-date decoding needed.

**Media** — `foto_ids` (list → images subset), `image_count`.

**Characteristics (flattened from `params`)** — which appear depends on the property
type. Known ones get clean English columns; unmapped ones are transliterated. Common
apartment columns: `rooms`, `area_m2`, `kitchen_area_m2`, `floor`, `floors_total`,
`series`, `condition`, `wall_material`, `heating`, `year_built`, `offer_type`
(«Собственник»/«Агент»), `deal_terms`, `legal_documents`, `furniture`, `parking`,
`amenities`, `layout`, `finishing`, `district`, `developer`, `complex_name`. The
untouched list is in **`params_raw`** (JSON) — nothing is lost.

### 5.2 `users`

| Field | Type | Description |
|---|---|---|
| `user_id` | int | **PK** |
| `user_hash` | str | lalafo's account hash |
| `username` · `avatar` | str | display name, avatar URL |
| `pro` | bool | a business/pro account |
| `is_banned` · `is_deleted` | bool | account state |
| `response_rate` | int | % of messages answered — the reputation signal |
| `response_time` | int | typical response time (seconds) |
| `response_info` | str | the human-readable version («Отвечает на 93% сообщений…») |
| `ads_count` | int | **derived** — listings by this user in the dataset |

### 5.3 `complexes` (ЖК)

| `complex_id` (PK, the attribute value_id) · `name` · `listing_count` (derived). |

### 5.4 `cities`

| `city_id` (PK) · `name` · `region` (best-effort oblast) · `listing_count` (derived). |

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

---

## 6. Pitfalls

Every item is a real property of this unmoderated source. The naive approach is
wrong.

### 6.1 🔴 Prices are not comparable across deals
A `sale` price is a total; `rent` is monthly; `daily_rent` is nightly. Never average
across `deal`. And filter `property_type` too.

### 6.2 🔴 `price` is often null — that means "negotiable", not zero
A large share of ads are «договорная» (`is_negotiable = true`) and carry no price.
Dropping or zero-filling them both bias the result; model them explicitly.

### 6.3 🔴 The data is noisy and duplicated
lalafo is unmoderated: the same flat is re-posted by several agencies, titles are
keyword-stuffed, and ads are mis-filed into the wrong category. Dedupe on image
`p_hash` or `(title, price, user_id)`; trust `property_type`/`deal` (from the
category) over the title — but know even the category is sometimes wrong.

### 6.4 🔴 Contact PII is present
`mobile` (almost always) and `email` (sometimes) are real personal data, as are
`user_hash` and `device_id`. Treat the dataset accordingly; do not redistribute raw
contact fields without cause or a lawful basis.

### 6.5 🟡 `origin_user_id` ≠ `user_id`
When an agency re-posts a private ad, `user_id` is the poster and `origin_user_id` the
original author. For "who really owns this listing" analyses, mind the difference.

### 6.6 🟡 Coverage is capped without city-partitioning
See §3. A big leaf crawled as a single feed query tops out around 10–11 k; enable
`partition_oversize_by_city` for the tail.

### 6.7 🟢 Timestamps are absolute
`created_time`/`updated_time` are Unix seconds (ISO mirrors provided) — no relative
dates to resolve, unlike house.kg.

---

## 7. Known limitations

| Limitation | Detail |
|---|---|
| **No reviews** | lalafo exposes no public seller-review API. Reputation is `response_rate`/`response_time` only. |
| **No public profile** | Seller fields come from the `user` object embedded in listings; there is no richer profile endpoint. |
| **Feed cap** | Discovery is bounded by the per-query cap; city-partitioning mitigates but does not guarantee 100 %. |
| **`region` is best-effort** | Derived from the city name; null for towns outside the lookup. |
| **Snapshot, not history** | Each crawl is point-in-time; `views`/prices change. Keys are stable, so re-crawl and diff. |
| **Category noise** | Some ads are mis-filed by the seller; `property_type`/`deal` inherit that error. |

---

## 8. Recipes

```python
from datasets import load_dataset

ads    = load_dataset("<repo>", "listings", split="train")
users  = load_dataset("<repo>", "users",    split="train")
images = load_dataset("<repo>", "images",   split="train")
```

**Median $/m² of Bishkek apartments for sale (priced only):**

```python
import statistics
rows = [
    r for r in ads
    if r["deal"] == "sale" and r["property_type"] == "apartment"
    and r["city"] == "Бишкек" and r["price"] and r["area_m2"]
]
def m2(v):
    try: return float(str(v).split()[0].replace(",", "."))
    except: return None
vals = [r["price"]/m2(r["area_m2"]) for r in rows if m2(r["area_m2"])]
print(statistics.median(vals))
```

**Pro (agency) vs private share:**

```python
by_user = {u["user_id"]: u for u in users}
pro = sum(1 for r in ads if by_user.get(r["user_id"], {}).get("pro"))
print(pro / len(ads))
```

**Deduplicate re-posted ads by cover-image hash:**

```python
seen, unique = set(), []
for im in images:
    if im["is_main"]:
        if im["p_hash"] in seen:
            continue
        seen.add(im["p_hash"]); unique.append(im["ad_id"])
```

**Negotiable-price rate by property type:**

```python
from collections import Counter
tot, neg = Counter(), Counter()
for r in ads:
    tot[r["property_type"]] += 1
    if r["is_negotiable"]:
        neg[r["property_type"]] += 1
for t in tot:
    print(t, round(neg[t]/tot[t], 2))
```
