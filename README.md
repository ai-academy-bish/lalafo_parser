# lalafo.kg — Scraper & HuggingFace Dataset Builder

Scrapes the **real-estate section of [lalafo.kg](https://lalafo.kg/kyrgyzstan/nedvizhimost)** —
Kyrgyzstan's largest *informal* classifieds board — into a clean, relational
HuggingFace dataset: listings, sellers, residential complexes, cities and images.

Where the curated boards (house.kg) show the polished market, lalafo is the noisy
street-level one: bigger, messier, and far richer in raw signal — phone numbers,
seller reputation, five engagement counters, promotion campaigns, perceptual image
hashes. This project turns that chaos into a research-grade dataset.

* **~78 000 real-estate listings** across every property type, deal and region
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

## Documentation

| Document | For whom |
|---|---|
| **[`docs/lalafo_dataset.md`](docs/lalafo_dataset.md)** | **Anyone using the data.** Every field, every relation, real volumes, and every pitfall of an unmoderated board (negotiable prices are null; duplicates; mis-filed categories; PII). |
| **[`docs/code_guide.md`](docs/code_guide.md)** | **Anyone maintaining the scraper.** Module by module: the Cloudflare bypass, the capped-feed workaround, the multiprocessing detail stage. Read it before changing anything. |

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
| `make clean` | Remove `data/`, `hf_dataset/` and `logs/` |

## Configuration

Everything lives in [`config.yaml`](config.yaml) — property types, deals, regions,
the Cloudflare session, worker/process counts, images, and where the dataset goes.

```yaml
scope:
  property_types: [apartment, house, commercial, land, room, garage, newbuild]
  deals: [sale, rent, daily_rent]
  max_listings: null          # null = crawl everything

multiprocessing:
  enabled: true
  processes: null             # null = os.cpu_count()

dataset:
  hub:
    push: true
    repo_id: your-name/lalafo-kg-realestate
```

The HuggingFace token is read from the environment (`HF_TOKEN`) or from
`hf auth login` — **never put it in the YAML.**

## Output

```
data/
  raw/         discovered.jsonl, listings.jsonl, users.jsonl,
               complexes.jsonl, cities.jsonl, images.jsonl
  images/      image files (uuid4 names)
  state/       cf_session.json + the browser profile
hf_dataset/
  data/*.parquet    one subset per table + sharded image subset
  README.md         dataset card
logs/          one log file per run
```

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
