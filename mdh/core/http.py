"""
Rate-limited, retrying HTTP client shared by every source.

Design goals
------------
* Never exceed a provider's published limit: each host gets a request budget
  (requests/minute, set to ~70% of the published limit in mdh/settings.py) and
  calls are spaced so the budget can't be exceeded, even in bursts.
* Recover gracefully when a provider pushes back anyway: 429/418/5xx and network
  errors are retried with exponential backoff + jitter, honouring Retry-After.
  After a 429 the host's budget is halved for the rest of the run.
* Hard monthly quotas (e.g. SoSoValue 10k/month) are counted on disk and the
  client refuses to go past the configured ceiling (QuotaExceeded).
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger("mdh.http")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
RETRY_STATUS = {408, 418, 425, 429, 500, 502, 503, 504, 520, 522, 524}


class QuotaExceeded(RuntimeError):
    pass


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status


class RateLimitedClient:
    def __init__(self, host_rpm: dict[str, float], monthly_quota: dict[str, int],
                 state_dir: Path, default_rpm: float = 30, max_retries: int = 5,
                 timeout: float = 30):
        self.host_rpm = dict(host_rpm)
        self.monthly_quota = monthly_quota
        self.default_rpm = default_rpm
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_call: dict[str, float] = {}
        self.session = requests.Session()
        # room for the archive thread pool (mdh/sources/binance_hist.py) without dropping connections
        adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32)
        self.session.mount("https://", adapter)
        self.session.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
        self.calls_this_run: dict[str, int] = {}
        state_dir.mkdir(parents=True, exist_ok=True)
        self._quota_file = state_dir / "http_quota.json"
        try:
            self._quota = json.loads(self._quota_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self._quota = {}

    # ------------------------------------------------------------------ budget
    def _pace(self, host: str) -> None:
        rpm = self.host_rpm.get(host, self.default_rpm)
        gap = 60.0 / rpm
        wait = self._last_call.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call[host] = time.monotonic()

    def _month_key(self, host: str) -> str:
        return f"{host}|{datetime.now(timezone.utc):%Y-%m}"

    def _check_quota(self, host: str) -> None:
        cap = self.monthly_quota.get(host)
        if cap is not None and self._quota.get(self._month_key(host), 0) >= cap:
            raise QuotaExceeded(f"{host}: monthly ceiling {cap} reached; skipping until next month")

    def _count(self, host: str) -> None:
        self.calls_this_run[host] = self.calls_this_run.get(host, 0) + 1
        if host in self.monthly_quota:
            k = self._month_key(host)
            self._quota[k] = self._quota.get(k, 0) + 1
            self._quota_file.write_text(json.dumps(self._quota, indent=1))

    def quota_used(self, host: str) -> int:
        return self._quota.get(self._month_key(host), 0)

    # ------------------------------------------------------------------ request
    def request(self, method: str, url: str, **kw) -> requests.Response:
        host = urlparse(url).netloc
        kw.setdefault("timeout", self.timeout)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._check_quota(host)
            self._pace(host)
            try:
                r = self.session.request(method, url, **kw)
                self._count(host)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                delay = min(60, 2 ** attempt) + random.uniform(0, 1)
                log.warning("%s %s -> %s; retry %d in %.1fs", method, host, type(e).__name__, attempt + 1, delay)
                time.sleep(delay)
                continue
            if r.status_code < 400:
                return r
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                if r.status_code in (418, 429):
                    # provider says slow down: halve our budget for this host for the rest of the run
                    self.host_rpm[host] = max(1.0, self.host_rpm.get(host, self.default_rpm) / 2)
                ra = r.headers.get("Retry-After")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else min(120, 2 ** (attempt + 1))
                delay += random.uniform(0, 1)
                log.warning("%s %s -> HTTP %d; retry %d in %.1fs", method, host, r.status_code, attempt + 1, delay)
                time.sleep(delay)
                continue
            raise HttpError(r.status_code, url, r.text)
        raise last_exc or RuntimeError(f"giving up on {url}")

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()
