"""lalafo_parser — scraper and HuggingFace dataset builder for lalafo.kg.

lalafo.kg is a Next.js SPA behind a Cloudflare Turnstile challenge; there is no HTML
to scrape.  A real browser (nodriver) warms a ``cf_clearance`` cookie once, then a
Chrome-fingerprinted HTTP client (curl_cffi) replays it against the JSON API at full
speed.  Everything else follows the house.kg engine's philosophy: strict layering,
append-only JSONL for resumability, one file for every site-specific constant.

The engine itself is **section-agnostic**.  Which part of lalafo a run crawls — its
leaf categories, its attribute map, its special params — comes from a *taxonomy*
YAML under ``configs/categories/``, named by the run config's ``categories_file``.
Real estate and cars are two such files over one unchanged pipeline; adding
electronics or jobs is another file, not another branch of code.

Layout
------
    config.py        typed configuration (dataclasses, loaded from configs/*.yaml)
    taxonomy.py      a vertical: leaf categories, param map, special params (YAML)
    catalog.py       renders a taxonomy file from lalafo's live category tree
    constants.py     API contract + geography — what is true of the *site*
    session.py       Cloudflare warm-up (nodriver) + cf_clearance provider
    http_client.py   curl_cffi client that replays the warmed cookie
    models.py        dataset records (Listing, User, Complex, City, Image)
    storage.py       append-only JSONL tables + image store (resumability)
    parsers/         JSON payload -> records (the only modules that know the schema)
    crawler/         id discovery + multiprocessing detail fetch + pipeline
    dataset/         HuggingFace packaging (Parquet subsets, embedded images)
    logging_utils/   rich console + file logging, multi-track progress bars
    utils/           transliteration, number extraction, epoch->ISO
"""

from .config import Config
from .crawler import Pipeline
from .dataset import HFDatasetBuilder
from .storage import Storage
from .taxonomy import Leaf, Taxonomy
from .validate import Validator

__version__ = "1.1.0"

__all__ = [
    "Config", "HFDatasetBuilder", "Leaf", "Pipeline", "Storage", "Taxonomy",
    "Validator", "__version__",
]
