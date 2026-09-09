"""Dataset card (README.md) for the HuggingFace repository.

The card is rendered from the vertical's taxonomy, so a car dataset does not ship a
real-estate card: the title, the tags, the section link, the `deal` vocabulary and
the relations block all come from the taxonomy that was crawled.
"""

from __future__ import annotations

from ..taxonomy import Taxonomy


def build_card(counts: dict[str, int], subsets: list[str], taxonomy: Taxonomy) -> str:
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

    relations = ["listings.user_id     -> users.user_id"]
    if "complexes" in subsets:
        relations.append(
            "listings.complex_id  -> complexes.complex_id   (residential complex / ЖК)"
        )
    relations.append("listings.city_id     -> cities.city_id")
    relations.append("images.listing_id    -> listings.ad_id")

    deals = ", ".join(f"`{d}`" for d in taxonomy.deals)
    if len(taxonomy.deals) == 1:
        deal_note = (
            f"* **Every row here is {deals}.** `deal` is constant in this vertical, so it\n"
            "  is a filter you do not need — but keep it when unioning with another\n"
            "  vertical's listings, where it is what tells a sale from a rent."
        )
    else:
        deal_note = (
            f"* **Prices are not comparable across deals.** `deal` is one of {deals}.\n"
            "  A sale price is a total, a rent price a rate; never average across them.\n"
            "  Filter on `deal` (and `property_type`) first."
        )

    guide_note = (
        "\n\nThe full field-by-field guide, with every pitfall in the source, is in\n"
        "`DATASET_GUIDE.md`."
        if taxonomy.guide
        else ""
    )

    label_note = ""
    if taxonomy.label_names:
        names = taxonomy.label_names
        labels = ", ".join(f"`{name}`" for name in names)
        verb, noun = ("comes", "column") if len(names) == 1 else ("come", "columns")
        label_note = (
            f"\n* **{labels} {verb} from the category, not the text.** lalafo files this\n"
            f"  branch by {labels}, so the {noun} is exactly as reliable as the seller's\n"
            f"  choice of category — and is never parsed out of a title."
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
  - {taxonomy.vertical}
  - kyrgyzstan
  - lalafo
configs:
{configs}---

# lalafo.kg — {taxonomy.title}

Scraped from [lalafo.kg]({taxonomy.site_url}), the largest *informal* classifieds
board in Kyrgyzstan — messier and larger than the curated boards, and closer to the
real street-level market. **Field names are English; values are kept in the original
language (Russian).**

## Subsets

| subset | rows | description |
|---|---:|---|
{rows}

## Relations

```
{chr(10).join(relations)}
```

Keys are lalafo's own ids (`ad_id`, `user_id`, `city_id`), so they are **stable
across crawls** — two snapshots can be diffed and joined.

## Usage

```python
from datasets import load_dataset

ads    = load_dataset("<repo>", "listings", split="train")
users  = load_dataset("<repo>", "users",    split="train")
images = load_dataset("<repo>", "images",   split="train")   # decoded PIL images
images[0]["image"]
```

## Read before you analyse

{deal_note}
* **`price` is often null** — a large share of ads are «договорная» (negotiable),
  flagged by `is_negotiable`. Do not treat null as zero.
* **The data is noisy.** lalafo is unmoderated: duplicate re-posts, keyword-stuffed
  titles, and mis-filed categories are common. `p_hash` on images helps dedupe
  near-identical ads; `property_type`/`deal` come from the category, not the text.{label_note}
* **Contact PII is present** (`mobile`, sometimes `email`). Handle per your ethics
  board / data-protection obligations; this is a research artefact.
* **The board is Bishkek-centric.** `region` is a best-effort oblast from the city
  name and is null for unlisted towns.
* **Timestamps are absolute** Unix seconds (`created_time`/`updated_time`) with ISO
  mirrors (`created_date`/`updated_date`) — no relative-date decoding needed.
* **Attributes are open-ended.** Params mapped in `{taxonomy.source.name}` become
  English columns; an unmapped param is transliterated (e.g. `naznachenie`). The
  untouched list is in `params_raw`.{guide_note}
"""


DESCRIPTIONS: dict[str, str] = {
    "listings": "one row per advertisement (every category, deal and region in scope)",
    "users": "sellers (ad authors), with reputation (response_rate / response_time)",
    "complexes": "residential complexes (ЖК), keyed by lalafo's attribute value_id",
    "cities": "city/town dimension with best-effort oblast",
    "images": "listing images, embedded as a HF `Image` feature (+ perceptual hash)",
}
