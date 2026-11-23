"""Polite HTTP client with an on-disk raw cache and a fetch log.

Rules every connector inherits:

* robots.txt is read once per host and honoured (a missing robots.txt, HTTP 4xx, means no restriction).
  A disallowed URL raises :class:`RobotsDisallowed`; connectors never work around it.
* Requests to each host are spaced by a minimum interval (SEC fair access: at most 10 requests/second).
* The generic User-Agent names the project only. SEC EDGAR requires a declared contact, which is read
  from the ``SEC_USER_AGENT`` environment variable at run time and never written to disk or logs.
* Every response body is stored gzip-compressed under ``data/raw/<kind>/`` and indexed in
  ``data/raw/_fetch_log.jsonl`` (url, UTC fetch time, status, bytes, sha256), so any number in a built
  dataset can be traced back to the exact bytes it came from.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .paths import RAW

GENERIC_UA = "industrial-intelligence/0.1 (open-source research pipeline; public data only)"
FETCH_LOG = RAW / "_fetch_log.jsonl"
RETRY_STATUS = {429, 500, 502, 503, 504}

# minimum seconds between requests to one host
HOST_INTERVAL = {
    "www.sec.gov": 0.15,
    "data.sec.gov": 0.15,
    "efts.sec.gov": 0.25,
    "comtradeapi.un.org": 1.2,
    "alfred.stlouisfed.org": 0.6,
    "fred.stlouisfed.org": 0.6,
    "www.eia.gov": 0.3,
    "api.usaspending.gov": 0.4,
    "api.ted.europa.eu": 1.0,
    "www.tdlr.texas.gov": 2.0,
}
DEFAULT_INTERVAL = 0.5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcstamp() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class RobotsDisallowed(Exception):
    """robots.txt of the host disallows the URL for our user agent."""


class FetchError(Exception):
    def __init__(self, status: int, url: str, body: bytes = b""):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.url = url
        self.body = body


def _is_sec(host: str) -> bool:
    return host == "sec.gov" or host.endswith(".sec.gov")


class Fetcher:
    """Cached, throttled, robots-aware fetcher shared by all connectors."""

    def __init__(self, offline: bool = False):
        self.offline = offline
        self.session = requests.Session()
        self.session.headers.update({"Accept-Encoding": "gzip, deflate"})
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.n_network = 0
        self.n_cache = 0
        self._index = self._load_index()

    # ------------------------------------------------------------------ identity
    @staticmethod
    def user_agent(host: str) -> str:
        if _is_sec(host):
            ua = os.environ.get("SEC_USER_AGENT", "").strip()
            if not ua:
                if host == "www.sec.gov":
                    raise RuntimeError("www.sec.gov needs a declared contact: set SEC_USER_AGENT ('Name email').")
                return GENERIC_UA
            return ua
        return GENERIC_UA

    # ------------------------------------------------------------------ robots
    def robots_allowed(self, url: str) -> bool:
        p = urllib.parse.urlsplit(url)
        host = p.netloc
        if host not in self._robots:
            rp = None
            if not self.offline:
                try:
                    r = self.session.get(f"{p.scheme}://{host}/robots.txt", timeout=30,
                                         headers={"User-Agent": self.user_agent(host)})
                    if r.status_code < 400 and "text" in (r.headers.get("Content-Type") or "text"):
                        rp = urllib.robotparser.RobotFileParser()
                        rp.parse(r.text.splitlines())
                except requests.RequestException:
                    rp = None
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(GENERIC_UA, url)

    # ------------------------------------------------------------------ cache
    @staticmethod
    def _load_index() -> dict:
        """One fetch log per raw kind (data/raw/<kind>/_fetch_log.jsonl), so parallel collectors never share a file."""
        idx: dict = {}
        logs = sorted(RAW.glob("*/_fetch_log.jsonl")) + ([FETCH_LOG] if FETCH_LOG.exists() else [])
        for log in logs:
            with log.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("status") == 200 and rec.get("path"):
                        idx[rec["key"]] = rec
        return idx

    @staticmethod
    def cache_key(url: str, body: dict | str | None = None) -> str:
        if body is None:
            return url
        payload = body if isinstance(body, str) else json.dumps(body, sort_keys=True)
        return url + "#POST:" + hashlib.sha1(payload.encode()).hexdigest()

    def _store(self, key: str, url: str, kind: str, status: int, data: bytes) -> dict:
        h = hashlib.sha1(key.encode()).hexdigest()
        folder = RAW / kind / h[:2]
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"{h}.gz"
        p.write_bytes(gzip.compress(data))
        rec = {"key": key, "url": url, "kind": kind, "path": p.relative_to(RAW).as_posix(), "fetched_at": utcstamp(),
               "status": status, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        with self._lock:
            with (RAW / kind / "_fetch_log.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            self._index[key] = rec
        return rec

    def cached(self, url: str, body: dict | str | None = None) -> dict | None:
        return self._index.get(self.cache_key(url, body))

    def read_cached(self, rec: dict) -> bytes:
        return gzip.decompress((RAW / rec["path"]).read_bytes())

    # ------------------------------------------------------------------ network
    def _throttle(self, host: str) -> None:
        gap = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
        with self._lock:
            wait = self._last.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def get(self, url: str, kind: str, *, max_age_hours: float | None = None, allow_404: bool = False,
            post_json: dict | None = None, post_form: dict | None = None, headers: dict | None = None,
            timeout: float = 120, retries: int = 5) -> bytes | None:
        """Return the body of `url` (from cache when fresh enough). None for an allowed 404.

        max_age_hours=None: cached copies never expire (immutable documents such as filings and archives).
        """
        body = post_json if post_json is not None else post_form
        key = self.cache_key(url, body)
        rec = self._index.get(key)
        if rec is not None:
            fresh = max_age_hours is None or (
                utcnow() - datetime.strptime(rec["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                < timedelta(hours=max_age_hours))
            if fresh or self.offline:
                self.n_cache += 1
                return self.read_cached(rec)
        if self.offline:
            raise FileNotFoundError(f"offline and not cached: {url}")
        if not self.robots_allowed(url):
            raise RobotsDisallowed(url)
        host = urllib.parse.urlsplit(url).netloc
        hdr = {"User-Agent": self.user_agent(host)}
        if headers:
            hdr.update(headers)
        delay = 2.0
        for attempt in range(retries + 1):
            self._throttle(host)
            try:
                if post_json is not None:
                    r = self.session.post(url, json=post_json, headers=hdr, timeout=timeout)
                elif post_form is not None:
                    r = self.session.post(url, data=post_form, headers=hdr, timeout=timeout)
                else:
                    r = self.session.get(url, headers=hdr, timeout=timeout)
            except requests.RequestException:
                if attempt == retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            self.n_network += 1
            if r.status_code == 200:
                self._store(key, url, kind, 200, r.content)
                return r.content
            if r.status_code == 404 and allow_404:
                return None
            if r.status_code in RETRY_STATUS and attempt < retries:
                ra = r.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.isdigit() else delay)
                delay = min(delay * 2, 120)
                continue
            raise FetchError(r.status_code, url, r.content[:2000])
        raise FetchError(-1, url)

    def get_json(self, url: str, kind: str, **kw):
        data = self.get(url, kind, **kw)
        return None if data is None else json.loads(data)

    def get_text(self, url: str, kind: str, encoding: str = "utf-8", **kw) -> str | None:
        data = self.get(url, kind, **kw)
        return None if data is None else data.decode(encoding, errors="replace")


def save_raw_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
