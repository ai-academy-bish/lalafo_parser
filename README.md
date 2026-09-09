# lalafo.kg — Scraper & HuggingFace Dataset Builder

Scrapes [lalafo.kg](https://lalafo.kg) — Kyrgyzstan's largest *informal* classifieds
board — into clean, relational HuggingFace datasets: listings, sellers, cities and
images. **Three verticals ship today — real estate, used cars and mobile phones —
and adding the next is a YAML file, not a code change.**

Where the curated boards (house.kg) show the polished market, lalafo is the noisy
street-level one: bigger, messier, and far richer in raw signal — phone numbers,
seller reputation, five engagement counters, promotion campaigns, perceptual image
hashes. This project turns that chaos into a research-grade dataset.

| vertical | config | what it crawls | scale |
|---|---|---|---|
| **real estate** | `configs/realestate.yaml` | 77 leaves under «Недвижимость» — every property type, deal and region | ~78 000 ads |
| **cars** | `configs/cars.yaml` | 125 brand leaves under «Транспорт / Продажа авто» | ~55 000 ads |
| **phones** | `configs/phones.yaml` | 32 brand leaves under «Мобильные телефоны» — handsets only | ~8 400 ads |

* **~600 000+ images**, embedded as a HuggingFace `Image` feature (+ perceptual hash)
* Phone numbers, coordinates, prices (+ price history), absolute timestamps
* Seller reputation, complexes (ЖК), promotion flags — everything lalafo exposes
* **Resumable** — a crawl killed at 80% restarts at 80%
* Field names in English, values kept in the original language

## How it works (the one thing that is different)

lalafo.kg is a Next.js SPA behind a **Cloudflare Turnstile challenge** — there is no
HTML to scrape and plain HTTP gets a 403. The pipeline defeats this in two moves:

1. **Warm** a `cf_clearance` cookie once with a real browser
   ([`nodriver`](https://github.com/ultrafunkamsterdam/nodriver), headed under Xvfb) —
   it clears Turnstile in ~30 s.
2. **Replay** that cookie from [`curl_cffi`](https://github.com/lexiforest/curl_cffi)
   with a Chrome TLS fingerprint — a browserless, multiprocess client that hits the
   JSON API at full speed. The cookie is auto-refreshed when it ages out.

Everything else follows the house.kg engine's philosophy: strict layering,
append-only JSONL for resumability, one file for every site-specific constant.

### One engine, many sections

`constants.py` holds what is true of the **site** (endpoints, headers, geography).
What is true of one **section** — its leaf categories, its attribute map, its special
params — lives in a *taxonomy* YAML under `configs/categories/`. A run config names
the taxonomy it crawls:

```yaml
# configs/cars.yaml
categories_file: configs/categories/cars.yaml
```

That one line is the whole difference between a real-estate run and a car run. The
crawler, the parser, the storage layer and the dataset builder are identical.

## Documentation

| Document | For whom |
|---|---|
| **[`docs/lalafo_dataset.md`](docs/lalafo_dataset.md)** | **Anyone using the real-estate data.** Every field, every relation, real volumes, and every pitfall of an unmoderated board (negotiable prices are null; duplicates; mis-filed categories; PII). |
| **[`docs/cars_dataset.md`](docs/cars_dataset.md)** | **Anyone using the car data.** The same, for used cars — plus the traps specific to this branch: mixed KGS/USD pricing, multi-select attributes, missing mileage, `vin_status` is not a VIN. |
| **[`docs/phones_dataset.md`](docs/phones_dataset.md)** | **Anyone using the phone data.** The same, for handsets — measured over the full crawl. Its own traps: `model` collapsed from 31 per-brand params and unique only within a brand, five multi-select attributes, and missingness that is brand-structured (battery health is iPhone-only, `device_class` is Android-only). |
| **[`docs/code_guide.md`](docs/code_guide.md)** | **Anyone maintaining the scraper.** Module by module: the Cloudflare bypass, the capped-feed workaround, the multiprocessing detail stage, and how to add a vertical. Read it before changing anything. |

Each vertical's guide ships **with its dataset** as `DATASET_GUIDE.md` — declared by
the `guide:` key in the taxonomy, so a car dataset never carries the real-estate one.

## Quick start

```bash
make setup                    # venv (uv) + dependencies
make browser                  # Chrome + Xvfb for the Cloudflare warm-up (server-side)
make warmup                   # optional: pre-warm the cf_clearance cookie
make parsing_run LIMIT=200    # try a small crawl first
make validate                 # keys, foreign keys, images
make make_hf_dataset          # build the Parquet subsets
```

Then the full run (resumable — Ctrl-C and re-run any time):

```bash
make parsing_run
```

Every target takes `VERTICAL=` — it selects `configs/<name>.yaml`, defaulting to
`realestate`:

```bash
make parsing_run VERTICAL=cars LIMIT=200
make validate    VERTICAL=cars
make make_hf_dataset VERTICAL=cars
```

`make help` lists the verticals it finds in `configs/`.

## Requirements

* Python ≥ 3.10
* [`uv`](https://docs.astral.sh/uv/) (installed automatically by `make setup`)
* **Google Chrome** + **Xvfb** on the host (the Cloudflare warm-up drives a real browser)
* Disk for the images (~tens of GB for a full crawl)

## Commands

| Command | What it does |
|---|---|
| `make help` | Colourised command reference |
| `make setup` | Create `venv/` with `uv` and install dependencies |
| `make browser` | Check/prepare Chrome + Xvfb for the warm-up |
| `make warmup` | Warm/refresh the Cloudflare cookie only |
| `make parsing_run` | Scrape (resumable). `LIMIT=N` for a smaller run |
| `make validate` | Integrity checks: primary keys, foreign keys, images |
| `make make_hf_dataset` | Build the Parquet subsets, and push if configured |
| `make categories` | Generate a taxonomy from the live category tree (see below) |
| `make clean` | Remove `data/`, `hf_dataset/` and `logs/` |

All of them accept `VERTICAL=<name>` (or `CONFIG=<path>` to be explicit).

## Configuration

Configs live in [`configs/`](configs/), split in two:

```
configs/
├── realestate.yaml          run config — *how* to crawl
├── cars.yaml
└── categories/
    ├── realestate.yaml      taxonomy — *what* to crawl
    └── cars.yaml
```

A **run config** holds the scope, the Cloudflare session, worker/process counts,
images and where the dataset goes:

```yaml
categories_file: configs/categories/cars.yaml   # which vertical

scope:
  property_types: [car]
  deals: [sale]
  max_listings: null          # null = crawl everything

multiprocessing:
  enabled: true
  processes: null             # null = os.cpu_count()

storage:
  root: data_cars             # give every vertical its own root

dataset:
  stream_upload: true         # see below — needed when images exceed free disk
  hub:
    push: true
    repo_id: your-name/lalafo-kg-cars
```

Omit `property_types` / `deals` entirely to crawl every leaf the taxonomy defines.

A **taxonomy** holds the leaf categories and the attribute map:

```yaml
vertical: cars
site_path: /kyrgyzstan/avtomobili-s-probegom

params:                       # param-id -> English column
  62: year
  56: mileage_km

categories:                   # leaf id -> classification
  1608: {type: car, deal: sale, brand: "Toyota", name: "Toyota"}
```

`type` and `deal` are the two classification axes (columns `property_type` and
`deal`). **Any other key is a free-form label** that becomes its own column — that is
how each car listing gets a `brand`, taken from the category rather than parsed out
of the title. A param not listed under `params:` is never dropped, only
transliterated, so a taxonomy with `params: {}` already produces a usable dataset.

The HuggingFace token is read from the environment (`HF_TOKEN`) or from
`hf auth login` — **never put it in the YAML.**

### Publishing an image set larger than your free disk

By default the builder writes every image shard, then uploads the folder — so it
needs free disk equal to the whole corpus. The car set is ~73 GiB across ~157
shards, which does not fit on a box with 64 GB free.

`dataset.stream_upload: true` uploads each shard the moment it is written and
deletes it locally, so **peak disk is one shard (~500 MB)** instead of the whole set.
Shard names and contents are identical either way — boundaries are planned up front
from the files' sizes, not discovered while staging.

It also makes a long push **resumable**: shards already present in the repo are
skipped, so a run interrupted at shard 100 of 157 picks up where it left off rather
than re-uploading 50 GB.

Streaming requires `hub.push` (it deletes each shard right after sending it); a
config with one and not the other fails at load time rather than destroying data.

### Adding a vertical

No code — generate the taxonomy from lalafo's live category tree, then point a run
config at it:

```bash
make categories ROOT=1625 OUT=configs/categories/moto.yaml \
                NAME=moto TYPE=moto DEAL=sale LABEL=subcategory
cp configs/cars.yaml configs/moto.yaml     # edit categories_file + storage.root
make parsing_run VERTICAL=moto LIMIT=200
```

Review the generated `type`/`deal` before a full run: the generator stamps what you
passed onto every leaf, because whether a branch splits by deal is a judgement it
cannot make for you. `docs/code_guide.md` §8 has the full checklist.

## Output

```
data/            (storage.root — data_cars/ for the car vertical)
  raw/         discovered.jsonl, listings.jsonl, users.jsonl,
               complexes.jsonl, cities.jsonl, images.jsonl
  images/      image files (uuid4 names)
  state/       cf_session.json + the browser profile
hf_dataset/      (dataset.output_dir)
  data/*.parquet    one subset per table + sharded image subset
  README.md         dataset card
logs/          one log file per run
```

A subset with no rows is skipped, so the car dataset simply ships no `complexes`
table — residential complexes are a real-estate concept.

### Loading the dataset

```python
from datasets import load_dataset

ads    = load_dataset("<repo>", "listings", split="train")
users  = load_dataset("<repo>", "users",    split="train")
images = load_dataset("<repo>", "images",   split="train")   # decoded PIL images
```

## Ethics

lalafo listings contain contact PII (phone numbers, sometimes e-mail). This is a
research artefact; handle it per your data-protection obligations and don't
redistribute the raw contact fields without cause. The scraper is polite (one warmed
session, modest concurrency) and touches only public listing pages.
