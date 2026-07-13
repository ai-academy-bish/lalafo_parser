"""Cloudflare session: warm a ``cf_clearance`` cookie, then hand it to the client.

lalafo.kg sits behind a Cloudflare Turnstile *managed challenge*.  Plain HTTP —
curl, httpx, even a valid cookie replayed by the wrong TLS stack — gets a 403
"Just a moment…".  What passes it is a real browser: **nodriver** (the
undetected-chromedriver successor) driving Google Chrome, headed under Xvfb.  It
clears the challenge in ~30 s and yields a ``cf_clearance`` cookie bound to that
browser's fingerprint and our IP.

The trick that makes a *fast* crawl possible: that cookie then works from
``curl_cffi`` with ``impersonate="chrome…"`` — a browserless client whose TLS
fingerprint matches Chrome.  So we warm once, then fetch tens of thousands of JSON
records without a browser in the loop.

The cookie is short-lived (~15–25 min).  ``SessionStore`` persists it to
``state/`` and re-harvests when it goes stale or the API starts returning
challenges.  Only one process harvests at a time (a lock file guards it); the
worker processes just re-read the file.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from .logging_utils import get_logger

logger = get_logger(__name__)

WARMUP_URL = "https://lalafo.kg/kyrgyzstan/nedvizhimost"


# ---------------------------------------------------------------------------
# Harvest (browser) — the only part that needs nodriver / a display
# ---------------------------------------------------------------------------

def harvest(session_path: Path, profile_dir: Path, headless: bool = False,
            timeout: int = 90) -> dict:
    """Pass Turnstile with nodriver and persist ``{cookies, ua}`` atomically.

    Must run where a display is available (``xvfb-run`` on a server).  Returns the
    session dict.  Raises RuntimeError if the challenge is not cleared.
    """
    import nodriver as uc  # imported lazily: only the harvester needs it

    async def _run() -> dict:
        browser = await uc.start(
            headless=headless,
            user_data_dir=str(profile_dir),
            browser_args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        tab = await browser.get(WARMUP_URL)
        ok = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(2)
            try:
                title = await tab.evaluate("document.title")
            except Exception:
                title = ""
            if title and "moment" not in title.lower() and "момент" not in title.lower():
                ok = True
                break
            try:
                await tab.verify_cf()  # nodriver's Turnstile helper (needs opencv)
            except Exception:
                pass
        cookies = await browser.cookies.get_all()
        ua = await tab.evaluate("navigator.userAgent")
        jar = {c.name: c.value for c in cookies}
        browser.stop()
        if not ok or "cf_clearance" not in jar:
            raise RuntimeError("Cloudflare challenge was not cleared")
        return {"cookies": jar, "ua": ua, "harvested_at": int(time.time())}

    session = uc.loop().run_until_complete(_run())
    _atomic_write(session_path, session)
    logger.info("[green]cf_clearance harvested[/] (%d cookies)", len(session["cookies"]))
    return session


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# SessionStore — read/refresh, shared by every worker
# ---------------------------------------------------------------------------

class SessionStore:
    """Owns the persisted session file and knows when to re-harvest.

    Cheap to construct in a worker: it only reads the file.  ``ensure_fresh`` (the
    part that launches a browser) is meant to be called from the *parent* process
    between work chunks — workers reload with ``load`` and never harvest.
    """

    def __init__(self, session_path: Path, profile_dir: Path,
                 max_age: int = 1200, headless: bool = False) -> None:
        self.session_path = session_path
        self.profile_dir = profile_dir
        self.max_age = max_age
        self.headless = headless
        self._lock_path = session_path.with_suffix(".lock")
        self._cache: dict | None = None
        self._mtime: float = 0.0

    # -- reads -------------------------------------------------------------

    def load(self) -> dict | None:
        """Current session, reloaded if the file changed underneath us."""
        if not self.session_path.exists():
            return None
        mtime = self.session_path.stat().st_mtime
        if self._cache is None or mtime != self._mtime:
            try:
                self._cache = json.loads(self.session_path.read_text(encoding="utf-8"))
                self._mtime = mtime
            except (json.JSONDecodeError, OSError):
                return self._cache
        return self._cache

    def age(self) -> float:
        s = self.load()
        if not s:
            return float("inf")
        return time.time() - s.get("harvested_at", 0)

    def is_fresh(self) -> bool:
        s = self.load()
        return bool(s and "cf_clearance" in s.get("cookies", {}) and self.age() < self.max_age)

    # -- refresh (parent only) ---------------------------------------------

    def ensure_fresh(self, force: bool = False) -> dict:
        """Harvest a new cookie if the current one is missing/stale.

        Guarded by a lock file so that if two processes ever race, only one opens
        a browser and the other waits for the result.
        """
        if not force and self.is_fresh():
            return self.load()  # type: ignore[return-value]

        with self._harvest_lock():
            # another holder may have refreshed while we waited for the lock
            if not force and self.is_fresh():
                return self.load()  # type: ignore[return-value]
            logger.info("warming Cloudflare session (this opens a browser)…")
            return harvest(self.session_path, self.profile_dir, self.headless)

    def _harvest_lock(self):
        store = self

        class _Lock:
            def __enter__(self_inner):
                while True:
                    try:
                        fd = os.open(str(store._lock_path),
                                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.close(fd)
                        return self_inner
                    except FileExistsError:
                        # stale lock (crashed harvester) -> reclaim after 3 min
                        try:
                            if time.time() - store._lock_path.stat().st_mtime > 180:
                                store._lock_path.unlink(missing_ok=True)
                                continue
                        except OSError:
                            pass
                        time.sleep(2)

            def __exit__(self_inner, *exc):
                store._lock_path.unlink(missing_ok=True)

        return _Lock()
