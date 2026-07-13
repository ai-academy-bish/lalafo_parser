"""Dataset card (README.md) for the HuggingFace repository."""

from __future__ import annotations


def build_card(counts: dict[str, int], subsets: list[str]) -> str:
    """Render the YAML front-matter (subset configs) plus a usage guide."""
    configs = "".join(
        "  - config_name: {name}\n"
        "    data_files:\n"
        "      - split: train\n"
        "        path: data/{pattern}\n".format(
            name=name,
            pattern=f"{name}-*.parquet" if name == "images" else f"{name}.parquet",
        )
        for name in subsets
    )

    rows = "\n".join(
        f"| `{name}` | {counts.get(name, 0):,} | {DESCRIPTIONS[name]} |" for name in subsets
    )

    return f"""---
language:
  - ru
  - ky
license: other
task_categories:
  - tabular-regression
  - image-classification
tags:
  - real-estate
  - kyrgyzstan
  - lalafo
configs:
{configs}---

# lalafo.kg — Kyrgyzstan Real Estate (the informal market)

Every real-estate listing scraped from [lalafo.kg](https://lalafo.kg/kyrgyzstan/nedvizhimost),
the largest *informal* classifieds board in Kyrgyzstan — messier and larger than the
curated boards, and closer to the real street-level market. **Field names are
English; values are kept in the original language (Russian).**

## Subsets

| subset | rows | description |
|---|---:|---|
{rows}

## Relations

```
listings.user_id     -> users.user_id
listings.complex_id  -> complexes.complex_id   (residential complex / ЖК)
listings.city_id     -> cities.city_id
images.listing_id    -> listings.ad_id
```

Keys are lalafo's own ids (`ad_id`, `user_id`, `city_id`, and the complex attribute
`value_id`), so they are **stable across crawls** — two snapshots can be diffed and
joined.

## Usage

```python
from datasets import load_dataset

ads    = load_dataset("<repo>", "listings", split="train")
users  = load_dataset("<repo>", "users",    split="train")
images = load_dataset("<repo>", "images",   split="train")   # decoded PIL images
images[0]["image"]
```

## Read before you analyse

* **Prices are not comparable across deals.** `deal` is `sale` / `rent` /
  `daily_rent` / `buy_request` / `rent_request`. A sale price is a total, a rent
  price a rate; never average across them. Filter on `deal` (and `property_type`).
* **`price` is often null** — a large share of ads are «договорная» (negotiable),
  flagged by `is_negotiable`. Do not treat null as zero.
* **The data is noisy.** lalafo is unmoderated: duplicate re-posts, keyword-stuffed
  titles, and mis-filed categories are common. `p_hash` on images helps dedupe
  near-identical ads; `property_type`/`deal` come from the category, not the text.
* **Contact PII is present** (`mobile`, sometimes `email`). Handle per your ethics
  board / data-protection obligations; this is a research artefact.
* **The board is Bishkek-centric.** `region` is a best-effort oblast from the city
  name and is null for unlisted towns.
* **Timestamps are absolute** Unix seconds (`created_time`/`updated_time`) with ISO
  mirrors (`created_date`/`updated_date`) — no relative-date decoding needed.
* **Attributes are open-ended.** Known params are English columns; an unmapped param
  is transliterated (e.g. `naznachenie`). The untouched list is in `params_raw`.

The full field-by-field guide, with every pitfall in the source, is in
`DATASET_GUIDE.md`.
"""


DESCRIPTIONS: dict[str, str] = {
    "listings": "one row per advertisement (all property types, deals and regions)",
    "users": "sellers (ad authors), with reputation (response_rate / response_time)",
    "complexes": "residential complexes (ЖК), keyed by lalafo's attribute value_id",
    "cities": "city/town dimension with best-effort oblast",
    "images": "listing images, embedded as a HF `Image` feature (+ perceptual hash)",
}
