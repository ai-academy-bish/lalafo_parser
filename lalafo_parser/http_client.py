"""Browserless HTTP client — curl_cffi carrying the warmed ``cf_clearance``.

`requests`/`httpx` are rejected by Cloudflare even *with* a valid cookie, because
the cookie is bound to a browser TLS fingerprint.  `curl_cffi` with
``impersonate="chrome…"`` reproduces that fingerprint, so the warmed cookie sails
through and we get the JSON API at full speed, no browser in the loop.

Thread-safety: `curl_cffi.Session` is not shared across threads, so each worker
thread lazily builds its own (a `threading.local`), mirroring how the house.kg
client handled `requests.Session`.

When the cookie finally expires the API answers the Cloudflare interstitial again.
The client raises `ChallengeError` so the pipeline can re-harvest and retry —
workers never open a browser themselves.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .constants import BASE_URL, Api
from .logging_utils import get_logger
from .session import SessionStore

logger = get_logger(__name__)


class ChallengeError(RuntimeError):
    """The API returned a Cloudflare challenge — the cookie needs re-harvesting."""


class LalafoClient:
    """Fetch JSON and image bytes from the API using the current session."""

    def __init__(self, session_store: SessionStore, *, impersonate: str = "chrome131",
                 timeout: int = 25, max_retries: int = 4, delay: float = 0.0,
                 referer: str = BASE_URL) -> None:
        self.session_store = session_store
        #: The section page the app would have come from — set per vertical from
        #: ``Taxonomy.site_url``.
        self.referer = referer
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_retries = max_retries
        self.delay = delay
        self._local = threading.local()

    # -- session -----------------------------------------------------------

    def _cr_session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            from curl_cffi import requests as cr
            s = cr.Session(impersonate=self.impersonate)
            self._local.session = s
        return s

    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        session = self.session_store.load()
        if not session:
            raise ChallengeError("no session available — warm it first")
        return session["cookies"], Api.headers(session["ua"], referer=self.referer)

    @staticmethod
    def _is_challenge(text: str) -> bool:
        head = text[:600].lower()
        return "just a moment" in head or "cf-challenge" in head or "challenge-platform" in head

    # -- requests ----------------------------------------------------------

    def get_json(self, path: str) -> dict[str, Any] | None:
        """GET a JSON endpoint. Returns the parsed body, or None for a 404.

        Raises ChallengeError when Cloudflare re-challenges (cookie expired).
        """
        url = path if path.startswith("http") else f"https://lalafo.kg{path}"
        for attempt in range(1, self.max_retries + 1):
            try:
                cookies, headers = self._auth()
                r = self._cr_session().get(
                    url, headers=headers, cookies=cookies, timeout=self.timeout
                )
                if r.status_code == 404:
                    return None
                if r.status_code == 200 and r.text[:1] in "{[":
                    if self.delay:
                        time.sleep(self.delay)
                    return r.json()
                if r.status_code == 403 or self._is_challenge(r.text):
                    raise ChallengeError(f"challenged on {url}")
            except ChallengeError:
                raise
            except Exception as exc:  # network hiccup, JSON parse, etc.
                logger.debug("GET %s failed (attempt %d): %s", url, attempt, exc)
            time.sleep(0.6 * attempt)
        logger.warning("giving up on %s after %d attempts", url, self.max_retries)
        return None

    def get_bytes(self, url: str) -> bytes | None:
        """GET raw bytes (images). The CDN is not behind Cloudflare."""
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self._cr_session().get(url, timeout=self.timeout)
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code == 404:
                    return None
            except Exception as exc:
                logger.debug("GET(bytes) %s failed (attempt %d): %s", url, attempt, exc)
            time.sleep(0.5 * attempt)
        return None
