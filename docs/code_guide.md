# Code Guide — for maintainers

This document explains **how the lalafo.kg scraper is built and why**, module by
module. Read it before changing anything.

The companion document, [`lalafo_dataset.md`](lalafo_dataset.md), describes the
*data*. This one describes the *code that produces it*.

The engine is a direct descendant of the house.kg scraper — same layering, same
append-only-JSONL resumability, same "one file for every site constant" rule. What
is genuinely new is the **Cloudflare bypass** and the **multiprocessing detail
stage**; those get the most attention below.

---

## Table of contents

1. [Design principles](#1-design-principles)
2. [Package layout](#2-package-layout)
3. [The Cloudflare problem, and how it is solved](#3-the-cloudflare-problem-and-how-it-is-solved)
4. [Execution flow](#4-execution-flow)
5. [Module reference](#5-module-reference)
6. [The feed cap, and the per-leaf + city workaround](#6-the-feed-cap)
7. [Multiprocessing](#7-multiprocessing)
8. [Common maintenance tasks](#8-common-maintenance-tasks)
9. [Failure modes and debugging](#9-failure-modes-and-debugging)

---

## 1. Design principles

Six rules shaped every decision. If you change the code, keep them.

### 1.1 Constants live in one place — and site ≠ section
Everything true of the **site** (endpoints, headers, geography) is in `constants.py`.
Everything true of one **section** — its leaf categories, its param map, its special
params — is in a *taxonomy* YAML under `configs/categories/`, loaded by
`taxonomy.py`. lalafo **will** move an endpoint or restructure a branch; either way
the fix is one file, not a hunt through the crawler.

The split is what makes the engine section-agnostic: real estate and cars are two
YAML files over one unchanged pipeline. Adding electronics or jobs is a new file,
not a new branch of code. See §8.

### 1.2 Never lose data you cannot re-derive
An attribute whose param-id is unmapped is **transliterated**, not dropped. The
untouched param list is kept in `params_raw`. Every id lalafo exposes — `ad_id`,
`user_id`, `city_id`, `device_id`, `origin_user_id`, image ids, perceptual hashes —
is stored. The dataset is a superset of what any single analysis needs.

### 1.3 Resumability is the architecture
Records are appended to JSONL the instant they are parsed; nothing accumulates in
memory; every table indexes its keys on start-up and the crawler skips what it
already has. Discovery and detail-fetching are **separate** resumable stages, with
their own tables (`discovered` vs `listings`).

### 1.4 The crawl stream is the source of truth for classification
`property_type` and `deal` come from the **leaf category** we fetched, never inferred
from page text. lalafo is unmoderated — titles lie, categories are mis-filed — so the
category id is the only trustworthy signal, and even it is imperfect (see the dataset
guide).

### 1.5 Parsers never touch the network; the crawler never parses
`parsers/` take a decoded JSON object and return records. `crawler/` do all the I/O.
If you find yourself calling the HTTP client inside a parser, you are in the wrong
module.

### 1.6 One browser, then none
The browser (nodriver) is expensive and fragile, so it is used for exactly one thing
— warming a cookie — and never in the hot path. Everything after that is
`curl_cffi`. See §3.

---

## 2. Package layout

```
lalafo_parser/
├── constants.py       API contract + geography — what is true of the *site*  ← patch here
├── taxonomy.py        a vertical: leaf categories, param map, special params ← the YAML
├── catalog.py         renders a taxonomy file from lalafo's live category tree
├── config.py          typed configuration (dataclasses ← configs/<vertical>.yaml)
├── session.py         Cloudflare warm-up (nodriver) + cf_clearance provider ← the bypass
├── http_client.py     curl_cffi client that replays the warmed cookie
├── models.py          the dataset records
├── storage.py         append-only JSONL tables + image store               ← resume
├── validate.py        integrity checks
├── cli.py             entry points (warmup / crawl / build / validate / categories)
│
├── utils/             site-agnostic helpers
│   ├── text.py        Transliterator, clean_text
│   ├── numbers.py     to_int, to_float, parse_price
│   └── time.py        epoch_to_iso
│
├── parsers/           JSON payload -> records — the only modules that know the schema
│   └── listing.py     ListingParser (detail JSON -> Listing + images + user + complex + city)
│
├── crawler/           stages + orchestration — the only modules that do I/O
│   ├── id_collector.py    Stage 1  (feed pagination, city partitioning)
│   ├── listing_crawler.py Stage 2  (multiprocessing details + images)
│   └── pipeline.py        sequencing + the shared session-refresh callback
│
├── dataset/           HuggingFace packaging
│   ├── hf_builder.py  Parquet subsets, derived counts, embedded images
│   └── card.py        the dataset README
│
└── logging_utils/     rich console + file logging, multi-track progress bars

configs/                run configs — one per vertical (how to crawl)
├── realestate.yaml
├── cars.yaml
└── categories/         taxonomies — one per vertical (what to crawl)
    ├── realestate.yaml
    └── cars.yaml
```

---

## 3. The Cloudflare problem, and how it is solved

lalafo.kg is a **Next.js SPA behind a Cloudflare Turnstile managed challenge**. Both
the HTML and the frontend `/api/*` proxy answer a bare request with `403 "Just a
moment…"`. There is no HTML to parse, and no header trick that gets JSON out of it.

What was measured during recon (all failed): `curl`, `httpx`, `httpx` + a valid
`cf_clearance` cookie, `curl_cffi` + cookie, Playwright (headless and real headed
Chrome). The blocker is **automation detection**, not IP reputation (the recon IP was
residential and still failed).

What works is **`nodriver`** — the undetected-chromedriver successor — driving Google
Chrome headed under Xvfb. It clears Turnstile in ~30 s and yields a `cf_clearance`
cookie. The decisive follow-on discovery: that cookie then works from **`curl_cffi`
with `impersonate="chrome131"`**, because curl_cffi reproduces Chrome's TLS/JA3
fingerprint, which is what the cookie is bound to. So:

```
nodriver (once, ~30 s)  ─►  cf_clearance cookie  ─►  curl_cffi, browserless, many workers
        ▲                                                     │
        └──────────────  re-harvest on 403 / age  ◄───────────┘
```

`session.py` owns this. `harvest()` is the only code that imports nodriver.
`SessionStore` persists `{cookies, ua, harvested_at}` to `state/cf_session.json`,
reports staleness, and re-harvests under a lock so that — even with many worker
processes — only one browser ever opens. **Workers never harvest**; they re-read the
refreshed file (the store reloads on mtime change) and retry.

> ### Two rules that keep the bypass working
> 1. **`impersonate` must match the warmed Chrome major.** nodriver installs/uses a
>    current Chrome; `http.impersonate` (default `chrome131`) must track it. A stale
>    impersonate target reintroduces the 403.
> 2. **The warm-up needs a display.** On a server that means `xvfb-run` (the Makefile
>    wraps the crawl/warmup targets). nodriver passes Turnstile far more reliably
>    headed-under-Xvfb than truly headless.

---

## 4. Execution flow

```
cli.crawl
  └── Pipeline.run()
        │
        ├── 0. warmup()              SessionStore.ensure_fresh()  → cf_clearance
        │
        ├── 1. IdCollector.collect() for each in-scope leaf category:
        │        • fetch page 1 → _meta (totalCount, pageCount)
        │        • fetch every page, collect ad ids of THIS category
        │        • oversize leaf → re-query per city_id (see §6)
        │        → discovered.jsonl  (ad_id + property_type + deal)   ← RESUME
        │
        └── 2. ListingCrawler.crawl()
                 • todo = discovered − listings                        ← RESUME
                 • per batch: refresh cookie if aged, then a process pool
                   fetches details + downloads images
                 • workers return dicts; the PARENT is the single JSONL writer
                 → listings.jsonl, images.jsonl, users.jsonl,
                   complexes.jsonl, cities.jsonl

cli.build → HFDatasetBuilder.build()
                 • JSONL → Parquet, one subset per table
                 • derived counts (ads_count, listing_count) in one pass
                 • images → embedded Image feature, streamed into ~500 MB shards

cli.validate → Validator.run()  (primary keys, foreign keys, images, classification)
```

Users, complexes and cities are **extracted from each listing payload** during stage
2 (lalafo embeds the `user` object and the ЖК/city in every ad), so there is no
separate network stage for them — a big saving over house.kg's entity crawl.

---

## 5. Module reference

### 5.1 `constants.py`
Pure data, **site-wide only**. `Api` holds the endpoint templates and the mandatory
headers (`device: pc`, `country-id: 12`, `language: ru_RU`) — note `feed_url` uses
**`per-page`** (hyphenated; an underscore is ignored). `CITY_REGION` is the
best-effort oblast lookup (a car ad and a flat ad carry the same `city` string, so
geography belongs here). `FEED_CAP_THRESHOLD` drives the city partitioning of §6.

### 5.1a `taxonomy.py` — what a vertical *is*
`Taxonomy.load(path)` reads one YAML and yields the crawl streams. Three blocks:

* `categories` — leaf id → `type` (the `property_type` column), `deal`, `name`, plus
  any number of free-form **labels**. Real estate declares none; cars declare
  `brand`, which becomes a `brand` column on every car listing. Labels are flattened
  into the row ahead of `attrs`, so the category always wins over a same-named param.
* `params` — param-id → English column, **collapsing synonyms** (226 & 3299 are both
  "Этаж"). Section-scoped on purpose: lalafo reuses one concept under different ids
  per branch — mileage is 56 for cars but 2063 for trucks.
* `special_params` — params that feed a dedicated column or dimension table
  (`complex` / `developer` / `district`). Omitted where the concept does not exist,
  so cars never match one and the `complexes` table simply stays empty.

Plus the metadata that identifies the vertical: `vertical`, `title`, `root_category`,
`site_path` (used as both the Cloudflare warm-up page and the API `Referer`) and the
optional `guide` — the long-form dataset doc shipped as `DATASET_GUIDE.md`.

`property_types` and `deals` are *derived* from the file, and `ScopeConfig` is
validated against them — a typo in a run config still fails loudly, just after the
taxonomy loads rather than at import time.

### 5.1b `catalog.py`
Fetches `Api.CATEGORY_TREE` and renders a taxonomy skeleton for any root — this is
`make categories`. It stamps the `type`/`deal` you pass onto every leaf, because the
axes cannot be inferred from a name; that judgement stays with you.

### 5.2 `config.py`
Typed configuration; a typo in the YAML raises at load time. `categories_file` names
the taxonomy — the one line that separates a real-estate run from a car run.
`project_root` is found by walking up to `pyproject.toml`, so configs can live in
`configs/` while `data/`, `logs/` and `hf_dataset/` stay at the repo root. Other
sections vs house.kg: `SessionConfig` (cookie max-age, headless, profile dir) and
`MultiprocessingConfig` (processes, threads-per-process, chunk size).

### 5.3 `session.py` — the bypass
Covered in §3. Key pieces: `harvest()` (nodriver, the only browser code),
`SessionStore.load/is_fresh/ensure_fresh`, and the lock that serialises harvesting
across processes.

### 5.4 `http_client.py`
`LalafoClient` wraps `curl_cffi`. `curl_cffi.Session` is not thread-safe, so each
worker thread lazily builds its own (a `threading.local`), exactly as the house.kg
client did with `requests.Session`. On a 403 / challenge body it raises
`ChallengeError` so the caller can trigger a re-harvest — the client itself never
opens a browser. `get_bytes` fetches images (the CDN is *not* behind Cloudflare).

### 5.5 `models.py`
Dataclasses; `to_dict()` feeds JSONL/Parquet. `Listing` declares ~50 core fields and
carries an open-ended `attrs` dict that `to_dict` flattens into the row with
`setdefault` (a characteristic can never clobber a core field) — the same trick as
house.kg's `Listing`. `Image`, `User`, `Complex`, `City` are the other tables.

### 5.6 `storage.py`
`JsonlTable` is **unchanged** from the house.kg engine — append-only, key-indexed,
thread-safe, corrupt-final-line-tolerant. `Storage` wires the lalafo tables and their
keys (`ad_id`, `user_id`, `complex_id`, `city_id`, `foto_id`) plus the `discovered`
table that makes id-discovery resumable independently of detail-fetching.

### 5.7 `parsers/listing.py` — the core
Constructed with the `Taxonomy` for the vertical. `parse(payload)` turns one detail
object into a `ParsedAd` (listing + image refs + user + complex + city).
`_attributes` is the important method: it flattens `params` into named columns via
`taxonomy.param_map`, transliterates the unmapped ones, keeps the raw list, and
extracts whichever special params the taxonomy declares (the complex FK from param
5592's `value_id`, the developer, the district). Classification is
`taxonomy.classify(category_id)` — the leaf carries `property_type`, `deal`, `name`
and its labels.

Note for §7: workers get the taxonomy's **path**, not the object — `Pool` initargs
must pickle, and re-reading one small YAML per process is free.

### 5.8 `crawler/` — see §6 and §7.

### 5.9 `dataset/hf_builder.py`
One Parquet subset per table + the embedded, sharded image subset (peak *memory* =
one ~500 MB shard, regardless of the ~tens of GB total — same streaming trick as
house.kg's photo builder). Derived counts (`users.ads_count`,
`complexes.listing_count`, `cities.listing_count`) are computed in one pass over the
listings. Nested `params_raw` is JSON-encoded to a string so Arrow never has to infer
a wobbly nested schema. An empty subset is skipped, so cars ship no `complexes`.

**Shard planning.** `_shard_plan` splits the image rows into shards by accumulated
*file size on disk*, read from the single `os.scandir` pass that also decides which
rows have a file at all. Planning before writing is what lets a shard be named
`images-<i>-of-<n>.parquet` on first write — the builder no longer stages under a
temporary name and renames at the end.

**Streaming upload** (`dataset.stream_upload`). Peak *disk* is the other constraint,
and by default it is the whole corpus: every shard is written, then `upload_folder`
sends the lot. With streaming, `_upload_shard` sends each shard and unlinks it right
away, so peak disk is one shard. Consequences worth knowing before you touch this:

* the repo must exist **before** the first shard, so `build()` calls `_open_repo()`
  up front rather than leaving it to `_push`;
* `_remote_files()` lists the repo once and shards already there are skipped, which
  is what makes a 73 GB push resumable. This is only sound because shard *n* is
  deterministic — same JSONL order, same size-based boundaries;
* `_push` still runs at the end, but by then it is only carrying the card, the guide
  and the tabular subsets;
* streaming without `hub.push` would delete shards that were never sent, so
  `DatasetConfig.__post_init__` rejects that combination at load time.

---

## 6. The feed cap

**The single most important thing to understand about coverage.** The search feed is
**capped per query**: the `category` feed returns at most 10 000 rows, the
`ppv-category` feed ~11 000 — regardless of the true count. The parent category
«Недвижимость» (2029) reports ~16 k via the feed while the site advertises ~78 k.

So the crawler never queries the parent. It walks **each leaf category separately**
(the taxonomy's `categories`), and for a leaf whose `totalCount` exceeds `FEED_CAP_THRESHOLD`
(9 000) with `partition_oversize_by_city` on, it **re-queries the leaf per
`city_id`** — each city subset fits under the cap, and the union recovers the tail
the single query hides. Because ~90 % of the board is Bishkek, in practice this means
"Bishkek, then everything else", and it lifts coverage of the big leaves
(apartment-sale, apartment-rent) from ~85 % toward completeness.

How much this matters is per vertical. Real estate has several leaves over the cap;
the car branch has none — its biggest, Toyota, is ~8.8 k — so partitioning almost
never fires there. Leave it on regardless: it costs one extra feed request per leaf
when it is not needed.

`IdCollector._walk` pages a (leaf, city) combination in parallel and keeps only ids
whose `category_id` matches the leaf (the feed injects promoted ads from other
categories — those are dropped).

---

## 7. Multiprocessing

The detail stage is network-bound and dominates a run, so it is where processes earn
their keep. The split is deliberate:

* **Worker processes** (`_process_ad`, module-level so it pickles) do the network,
  parsing, and image writing. Image files use uuid4 names, so many processes writing
  into one flat directory never collide — no cross-process lock.
* The **parent process** is the *single writer* to the JSONL tables. Workers return
  plain dicts; `_consume` appends them. One writer means no file-lock dance and a
  consistent, resumable on-disk state.

Workers are initialised once per process (`_init_worker` builds a `LalafoClient`,
`ListingParser`, `ImageStore`). Set `multiprocessing.enabled: false` to fall back to
a single-process thread pool (useful for debugging — tracebacks are not marshalled
across a process boundary).

**Session refresh under multiprocessing** is the parent's job. Before each batch the
parent calls `ensure_session()` (age-based re-harvest). If a batch still hits
challenges (the cookie died early), the parent force-refreshes and retries just that
batch's failed ids; the workers pick up the new cookie file on their next task. A
worker never opens a browser.

---

## 8. Common maintenance tasks

**lalafo moved an endpoint / changed headers** → edit `constants.Api`. Nothing else.

**lalafo restructured the categories** → regenerate the taxonomy:
`make categories ROOT=<id> OUT=configs/categories/<vertical>.yaml --force`, then diff
it against the old file and re-apply your `type`/`deal` split and `params` map.

**Adding a vertical** (the common case now) — no code, two files:

1. `make categories ROOT=<root id> OUT=configs/categories/<name>.yaml NAME=<name>
   TYPE=<type> DEAL=<deal> [LABEL=<label key>]` — renders every leaf under that root.
   Find the root id in `Api.CATEGORY_TREE`; the top-level ones are listed in §8 of
   the dataset guide.
2. Hand-edit `type`/`deal` where the branch really does split by deal, and fill in
   `params` for the ids worth clean names (unmapped ones still arrive transliterated,
   so a first crawl works with `params: {}`).
3. Copy `configs/cars.yaml` to `configs/<name>.yaml`, point `categories_file` at the
   new taxonomy, and give it **its own `storage.root`** — two verticals sharing one
   `data/` would interleave their listings in the same JSONL tables.
4. `make parsing_run VERTICAL=<name> LIMIT=200` to try it.
5. When the data is understood, write `docs/<name>_dataset.md` and declare it as
   `guide:` in the taxonomy. It then ships with that dataset as `DATASET_GUIDE.md`,
   and the card links to it. A vertical with no `guide:` simply ships none — what it
   must never do is ship another vertical's.

**A new attribute appeared** → it arrives as a transliterated column automatically.
To give it a clean name, add one line to the vertical's `params:` block; check first
whether it is a synonym of an existing concept and, if so, map it to the **same**
English field.

**The 403s came back** → the `impersonate` target drifted from the installed Chrome
major, or Turnstile got harder. Bump `http.impersonate`; confirm nodriver still
clears the challenge (`make warmup`).

**Adding a field to `listings`** → add it to the `Listing` dataclass, populate it in
`ListingParser.parse`, document it in `lalafo_dataset.md`. Old JSONL rows lack it;
the builder's `_align` fills them with None.

---

## 9. Failure modes and debugging

| Symptom | Likely cause |
|---|---|
| **Every request 403 / "Just a moment"** | Cookie stale or `impersonate` ≠ warmed Chrome major. Run `make warmup`; check `http.impersonate`. |
| **Warm-up hangs on "Один момент…"** | nodriver could not clear Turnstile — no display (need `xvfb`), or `opencv` missing (its `verify_cf` checkbox helper), or Chrome missing. |
| **Coverage far below the site's count** | The feed cap (§6). Enable `partition_oversize_by_city`, and remember the parent category is always capped — crawl leaves. |
| **`price` null for many ads** | Correct — «договорная» (negotiable). Check `is_negotiable`, don't treat null as zero. |
| **Duplicate-looking listings** | lalafo is unmoderated; re-posts are real. Dedupe on `image` `p_hash` or `(title, price, user_id)`. |
| **Workers hang / no progress** | A batch is mid-refresh (parent opening a browser) — normal, brief. If persistent, the cookie can't be renewed (display/Chrome gone). |
| **`cannot pickle` in the pool** | Something non-picklable leaked into `_process_ad`'s return (e.g. a dataclass instead of `.to_dict()`). Return plain dicts only. |
| **Arrow schema error at build** | A new nested column. Add it to `_JSON_COLUMNS` in `hf_builder`, or flatten it. |

**Where to look:** `logs/<run>_<timestamp>.log` keeps `DEBUG` — every retry, every
404, every give-up, even when the console showed only `INFO`.
