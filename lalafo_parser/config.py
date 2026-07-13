"""Typed configuration, loaded from `config.yaml`.

Every knob the pipeline honours lives here as a dataclass, so configuration is
validated and discoverable rather than a dict of strings passed around.  A typo in
the YAML fails loudly at load time instead of being silently ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .constants import DEALS, PROPERTY_TYPES, REGIONS


@dataclass(slots=True)
class ScopeConfig:
    """What to crawl.

    The crawl walks one *leaf category* per stream; `property_types` × `deals`
    selects which leaves.  `regions` optionally restricts to certain oblasts
    (best-effort, by city) — by default every region of Kyrgyzstan is kept.
    """

    property_types: list[str] = field(default_factory=lambda: list(PROPERTY_TYPES))
    deals: list[str] = field(default_factory=lambda: list(DEALS))
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
        unknown = set(self.property_types) - set(PROPERTY_TYPES)
        if unknown:
            raise ValueError(f"unknown property types: {sorted(unknown)}")
        unknown = set(self.deals) - set(DEALS)
        if unknown:
            raise ValueError(f"unknown deals: {sorted(unknown)}")
        unknown = set(self.regions) - set(REGIONS)
        if unknown:
            raise ValueError(
                f"unknown regions: {sorted(unknown)}. "
                f"Only Kyrgyzstan is supported: {sorted(REGIONS)}"
            )
        if not self.property_types or not self.deals:
            raise ValueError("scope must enable at least one property type and deal")


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
    """Where scraped data lands (relative to the project root)."""

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
    hub: HubConfig = field(default_factory=HubConfig)


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
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    multiprocessing: MultiprocessingConfig = field(default_factory=MultiprocessingConfig)
    images: ImageConfig = field(default_factory=ImageConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> Config:
        path = Path(path).resolve()
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        dataset_raw = dict(raw.get("dataset") or {})
        hub = HubConfig(**(dataset_raw.pop("hub", {}) or {}))
        return cls(
            project_root=path.parent,
            scope=ScopeConfig(**(raw.get("scope") or {})),
            session=SessionConfig(**(raw.get("session") or {})),
            http=HttpConfig(**(raw.get("http") or {})),
            multiprocessing=MultiprocessingConfig(**(raw.get("multiprocessing") or {})),
            images=ImageConfig(**(raw.get("images") or {})),
            storage=StorageConfig(**(raw.get("storage") or {})),
            dataset=DatasetConfig(hub=hub, **dataset_raw),
            logging=LoggingConfig(**(raw.get("logging") or {})),
        )

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
