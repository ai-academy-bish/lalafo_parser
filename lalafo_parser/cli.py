"""Command-line entry points (used by the Makefile targets).

    python -m lalafo_parser.cli --config config.yaml crawl --limit 200
    python -m lalafo_parser.cli warmup      # just refresh the Cloudflare cookie
    python -m lalafo_parser.cli build
    python -m lalafo_parser.cli validate

`--config` is a top-level flag and must precede the subcommand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .crawler import Pipeline
from .dataset import HFDatasetBuilder
from .logging_utils import ProgressTracker, get_logger, setup_logging
from .session import SessionStore
from .storage import Storage
from .validate import Validator

logger = get_logger(__name__)


def _load(args: argparse.Namespace) -> Config:
    config = Config.load(args.config)
    if getattr(args, "limit", None):
        config.scope.max_listings = args.limit
    if getattr(args, "processes", None):
        config.multiprocessing.processes = args.processes
    if getattr(args, "no_images", False):
        config.images.enabled = False
    if getattr(args, "no_mp", False):
        config.multiprocessing.enabled = False
    if getattr(args, "no_progress", False):
        config.logging.progress = False
    return config


def cmd_warmup(args: argparse.Namespace) -> int:
    config = _load(args)
    setup_logging(config.log_dir, config.logging.level, config.logging.color, run_name="warmup")
    paths = config.paths
    store = SessionStore(
        paths.state / config.session.session_filename,
        paths.state / config.session.profile_dirname,
        max_age=config.session.max_age,
        headless=config.session.headless,
    )
    store.ensure_fresh(force=True)
    logger.info("session ready (age %.0fs)", store.age())
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    config = _load(args)
    log_file = setup_logging(
        config.log_dir, config.logging.level, config.logging.color, run_name="crawl"
    )
    logger.info("logging to %s", log_file)
    Pipeline(config).run()
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    config = _load(args)
    log_file = setup_logging(
        config.log_dir, config.logging.level, config.logging.color, run_name="dataset"
    )
    logger.info("logging to %s", log_file)
    paths = config.paths
    storage = Storage(raw_dir=paths.raw, images_dir=paths.images)
    if not len(storage.listings):
        logger.error("no listings found in %s — run the crawler first", paths.raw)
        return 1
    with ProgressTracker(enabled=config.logging.progress) as progress:
        HFDatasetBuilder(config, storage, progress).build()
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    config = _load(args)
    setup_logging(config.log_dir, config.logging.level, config.logging.color, run_name="validate")
    paths = config.paths
    storage = Storage(raw_dir=paths.raw, images_dir=paths.images)
    return 0 if Validator(storage).run() else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lalafo", description="lalafo.kg real-estate scraper and dataset builder"
    )
    parser.add_argument("-c", "--config", default=str(Path("config.yaml")))
    sub = parser.add_subparsers(dest="command", required=True)

    warm = sub.add_parser("warmup", help="warm/refresh the Cloudflare cookie only")
    warm.set_defaults(func=cmd_warmup)

    crawl = sub.add_parser("crawl", help="discover ids, fetch details + images")
    crawl.add_argument("--limit", type=int, help="stop after N listings")
    crawl.add_argument("--processes", type=int, help="override worker processes")
    crawl.add_argument("--no-images", action="store_true", help="skip image downloads")
    crawl.add_argument("--no-mp", action="store_true", help="single-process (threads only)")
    crawl.add_argument("--no-progress", action="store_true")
    crawl.set_defaults(func=cmd_crawl)

    build = sub.add_parser("build", help="package the HuggingFace dataset")
    build.add_argument("--no-images", action="store_true")
    build.add_argument("--no-progress", action="store_true")
    build.set_defaults(func=cmd_build)

    validate = sub.add_parser("validate", help="check keys, foreign keys and images")
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
