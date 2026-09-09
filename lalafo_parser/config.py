"""Typed configuration, loaded from a run config under `configs/`.

Every knob the pipeline honours lives here as a dataclass, so configuration is
validated and discoverable rather than a dict of strings passed around.  A typo in
the YAML fails loudly at load time instead of being silently ignored.

A run config answers *how* to crawl (workers, images, storage, dataset).  *What* to
crawl comes from the taxonomy file it points at with ``categories_file`` — see
``taxonomy.py``.  Swapping that one line is what turns a real-estate run into a car
run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .constants import REGIONS
from .taxonomy import Taxonomy

#: Used when a run config names no taxonomy — keeps a bare `Config()` usable.
DEFAULT_CATEGORIES_FILE = "configs/categories/realestate.yaml"


@dataclass(slots=True)
class ScopeConfig:
    """What to crawl.

    The crawl walks one *leaf category* per stream; `property_types` × `deals`
    selects which leaves **out of the taxonomy the run config points at**.
    `regions` optionally restricts to certain oblasts (best-effort, by city) — by
    default every region of Kyrgyzstan is kept.

    `property_types` / `deals` default to everything the taxonomy defines, so a
    config that names neither crawls the whole vertical.  They cannot be validated
    at construction time (the taxonomy is not loaded yet), so `Config.load` calls
    `resolve()` — a typo still fails loudly, just one step later.
    """

    property_types: list[str] | None = None
    deals: list[str] | None = None
    regions: list[str] = field(default_factory=lambda: list(REGIONS))
    #: Stop after N listings (per run, across the scope). None = crawl everything.
    max_listings: int | None = None
    #: Cap feed pages per category stream. None = follow to the last page.
    max_pages_per_stream: int | None = None
    #: Split oversize leaves (totalCount over the feed cap) by city to recover the
    #: tail the single-query feed hides.  Costs extra requests; worth it for a
    #: complete crawl.
    partition_oversize_by_city: bool = True

    def __post_init__(self) -> None:
        # Regions are site-wide, so they *can* be checked immediately.
        unknown = set(self.regions) - set(REGIONS)
        if unknown:
            raise ValueError(
                f"unknown regions: {sorted(unknown)}. "
                f"Only Kyrgyzstan is supported: {sorted(REGIONS)}"
            )

    def resolve(self, taxonomy: Taxonomy) -> None:
        """Apply the taxonomy's defaults, then validate the selection against it."""
        if self.property_types is None:
            self.property_types = list(taxonomy.property_types)
        if self.deals is None:
            self.deals = list(taxonomy.deals)

        source = taxonomy.source.name
        unknown = set(self.property_types) - set(taxonomy.property_types)
        if unknown:
            raise ValueError(
                f"unknown property types: {sorted(unknown)}. "
                f"{source} defines: {list(taxonomy.property_types)}"
            )
        unknown = set(self.deals) - set(taxonomy.deals)
        if unknown:
            raise ValueError(
                f"unknown deals: {sorted(unknown)}. {source} defines: {list(taxonomy.deals)}"
            )
        if not self.property_types or not self.deals:
            raise ValueError("scope must enable at least one property type and deal")
        if not taxonomy.categories_for(self.property_types, self.deals):
            raise ValueError(
                f"scope selects no categories at all — no leaf in {source} has both "
                f"a listed property type and a listed deal"
            )


@dataclass(slots=True)
class SessionConfig:
    """Cloudflare Turnstile session warm-up.

    A real browser (nodriver) solves the challenge once and yields a
    `cf_clearance` cookie + User-Agent, persisted to `state/`.  The HTTP client
    replays it.  The cookie is short-lived, so it is re-harvested when it goes
    stale or the API starts returning challenges.
    """

    #: Re-harvest the cookie after this many seconds even without a 403.
    max_age: int = 1200
    #: Run the warm-up browser headless.  On a server this needs xvfb either way;
    #: nodriver passes Turnstile better with a real (headed-under-xvfb) Chrome.
    headless: bool = False
    #: Persisted Chrome profile dir (under state/), so repeat warm-ups are faster.
    profile_dirname: str = "cf_profile"
    #: File the cookie + UA are written to (under state/).
    session_filename: str = "cf_session.json"


@dataclass(slots=True)
class HttpConfig:
    """HTTP behaviour for the browserless (curl_cffi) client."""

    workers: int = 10
    timeout: int = 25
    max_retries: int = 4
    #: Extra pause between requests inside a worker.
    delay: float = 0.0
    #: curl_cffi TLS-impersonation target; must match the warmed Chrome major.
    impersonate: str = "chrome131"


@dataclass(slots=True)
class MultiprocessingConfig:
    """Process-level parallelism for the detail-fetch stage.

    Detail fetching is the bulk of a run (one request per kept listing).  It is
    network-bound, so processes each run their own thread pool; the parent stays
    the single writer to the JSONL tables (no cross-process file locking).
    """

    enabled: bool = True
    #: Worker processes. None = os.cpu_count().
    processes: int | None = None
    #: HTTP threads *inside* each process.
    threads_per_process: int = 8
    #: Listings dispatched to a worker at a time.
    chunk_size: int = 200


@dataclass(slots=True)
class ImageConfig:
    """Poster image downloading."""

    enabled: bool = True
    workers: int = 12
    #: Skip listings with more than this many images (None = all).
    max_per_listing: int | None = None
    #: Prefer the webp variant when the CDN offers it (smaller, same resolution).
    prefer_webp: bool = False


@dataclass(slots=True)
class StorageConfig:
    """Where scraped data lands (relative to the project root).

    Give each vertical its own ``root`` — two verticals sharing one ``data/`` would
    interleave their listings in the same JSONL tables.
    """

    root: str = "data"
    photos_dirname: str = "images"
    raw_dirname: str = "raw"
    state_dirname: str = "state"

    def resolve(self, project_root: Path) -> ResolvedStorage:
        root = (project_root / self.root).resolve()
        return ResolvedStorage(
            root=root,
            images=root / self.photos_dirname,
            raw=root / self.raw_dirname,
            state=root / self.state_dirname,
        )


@dataclass(slots=True)
class ResolvedStorage:
    """Absolute paths, created on first use."""

    root: Path
    images: Path
    raw: Path
    state: Path

    def mkdirs(self) -> None:
        for p in (self.root, self.images, self.raw, self.state):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(slots=True)
class HubConfig:
    """HuggingFace Hub push settings."""

    push: bool = False
    repo_id: str | None = None
    private: bool = True
    #: Token from the environment / `hf auth login`; never put it in YAML.
    token_env: str = "HF_TOKEN"

    @property
    def token(self) -> str | None:
        return os.environ.get(self.token_env)


@dataclass(slots=True)
class DatasetConfig:
    """How the HuggingFace dataset is built."""

    output_dir: str = "hf_dataset"
    #: Embed images as a HF `Image` feature, sharded, rather than shipping loose
    #: files.
    include_images: bool = True
    max_shard_size: str = "500MB"
    #: Upload each image shard as soon as it is written and delete it locally,
    #: instead of staging the whole set and uploading at the end.  Peak disk then
    #: stays at roughly one shard rather than the full image corpus — the only way
    #: to publish a set larger than the free space.  Requires ``hub.push``.
    stream_upload: bool = False
    hub: HubConfig = field(default_factory=HubConfig)

    def __post_init__(self) -> None:
        if self.stream_upload and not self.hub.push:
            raise ValueError(
                "dataset.stream_upload requires dataset.hub.push — streaming deletes "
                "each shard right after uploading it, so with push off the shards "
                "would simply be destroyed"
            )


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    dirname: str = "logs"
    progress: bool = True
    color: bool = True


@dataclass(slots=True)
class Config:
    """Root configuration object."""

    project_root: Path = field(default_factory=Path.cwd)
    #: The taxonomy file this run crawls — relative to the project root.  This one
    #: line is the difference between a real-estate run and a car run.
    categories_file: str = DEFAULT_CATEGORIES_FILE
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    multiprocessing: MultiprocessingConfig = field(default_factory=MultiprocessingConfig)
    images: ImageConfig = field(default_factory=ImageConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    #: Loaded lazily from ``categories_file`` and cached; use ``.taxonomy``.
    _taxonomy: Taxonomy | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> Config:
        path = Path(path).resolve()
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        dataset_raw = dict(raw.get("dataset") or {})
        hub = HubConfig(**(dataset_raw.pop("hub", {}) or {}))
        config = cls(
            project_root=_project_root(path),
            categories_file=str(raw.get("categories_file") or DEFAULT_CATEGORIES_FILE),
            scope=ScopeConfig(**(raw.get("scope") or {})),
            session=SessionConfig(**(raw.get("session") or {})),
            http=HttpConfig(**(raw.get("http") or {})),
            multiprocessing=MultiprocessingConfig(**(raw.get("multiprocessing") or {})),
            images=ImageConfig(**(raw.get("images") or {})),
            storage=StorageConfig(**(raw.get("storage") or {})),
            dataset=DatasetConfig(hub=hub, **dataset_raw),
            logging=LoggingConfig(**(raw.get("logging") or {})),
        )
        # Needs the taxonomy, so it cannot happen in ScopeConfig.__post_init__.
        config.scope.resolve(config.taxonomy)
        return config

    @property
    def taxonomy(self) -> Taxonomy:
        """The vertical being crawled (leaf categories, param map, special params)."""
        if self._taxonomy is None:
            self._taxonomy = Taxonomy.load(self.taxonomy_path)
        return self._taxonomy

    @property
    def taxonomy_path(self) -> Path:
        path = Path(self.categories_file)
        return path if path.is_absolute() else (self.project_root / path).resolve()

    @property
    def paths(self) -> ResolvedStorage:
        p = self.storage.resolve(self.project_root)
        p.mkdirs()
        return p

    @property
    def log_dir(self) -> Path:
        d = (self.project_root / self.logging.dirname).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def dataset_dir(self) -> Path:
        return (self.project_root / self.dataset.output_dir).resolve()


def _project_root(config_path: Path) -> Path:
    """The repo root — where ``data/``, ``logs/`` and ``hf_dataset/`` belong.

    Run configs live in ``configs/``, so the config's own directory is no longer the
    project root.  Walk up to the directory holding ``pyproject.toml``; fall back to
    the config's directory, which is what a root-level ``config.yaml`` has always
    resolved to.
    """
    for candidate in config_path.parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return config_path.parent
