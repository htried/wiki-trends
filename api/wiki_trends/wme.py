"""Wikimedia Enterprise pageviews API client (v2, beta).

Endpoints used (all `Authorization: Bearer <access token>`):
  GET  /pageviews/trending/{identifier}/{k}/{date}/{hour}   top-k trending pages for an hour (k <= 1000), Pageviews records;
                                                            404 when the hour does not exist yet
  GET  /pageviews/batches/{date}/{hour}/{identifier}         info on one project's hourly batch (count, size, periods)
  GET  /pageviews/batches/{date}/{hour}/{identifier}/download
                                                            307 to a presigned URL (valid 60 s) for a gzipped tar of one
                                                            Pageviews record per article (NDJSON inside)
Auth: POST {auth}/login {username, password} -> access_token (24 h) and refresh_token; POST {auth}/token-refresh.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone

import requests

from .config import user_agent


class WMEError(RuntimeError):
    pass


class NotAvailable(WMEError):
    """The requested hour (or batch) does not exist yet."""


class WMEClient:
    def __init__(self, api_base: str | None = None, auth_base: str | None = None, session: requests.Session | None = None):
        self.api_base = (api_base or os.environ.get("WME_API_BASE", "https://api.enterprise.wikimedia.com/v2")).rstrip("/")
        self.auth_base = (auth_base or os.environ.get("WME_AUTH_BASE", "https://auth.enterprise.wikimedia.com/v1")).rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent()
        self._token = os.environ.get("WME_ACCESS_TOKEN")
        self._refresh = None
        self._expires = time.time() + 23 * 3600 if self._token else 0.0
        self._lock = threading.Lock()

    # ---- auth
    def _login(self) -> None:
        user, pw = os.environ.get("WME_USERNAME"), os.environ.get("WME_PASSWORD")
        if not (user and pw):
            raise WMEError("set WME_USERNAME and WME_PASSWORD (or WME_ACCESS_TOKEN)")
        r = self.session.post(f"{self.auth_base}/login", json={"username": user, "password": pw}, timeout=30)
        if not r.ok:
            raise WMEError(f"WME login failed: HTTP {r.status_code}")
        self._store(r.json())

    def _store(self, d: dict) -> None:
        self._token = d["access_token"]
        self._refresh = d.get("refresh_token", self._refresh)
        self._expires = time.time() + float(d.get("expires_in", 86400)) - 300

    def token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires:
                return self._token
            if self._refresh:
                r = self.session.post(f"{self.auth_base}/token-refresh",
                                      json={"username": os.environ.get("WME_USERNAME"), "refresh_token": self._refresh}, timeout=30)
                if r.ok:
                    self._store(r.json())
                    return self._token
            self._login()
            return self._token

    # ---- requests
    def _get(self, path: str, *, allow_redirects: bool = True, stream: bool = False) -> requests.Response:
        for attempt in range(4):
            r = self.session.get(f"{self.api_base}{path}", headers={"Authorization": f"Bearer {self.token()}"},
                                 allow_redirects=allow_redirects, stream=stream, timeout=120)
            if r.status_code == 401 and attempt == 0:
                self._expires = 0  # token rejected: log in again once
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                raise NotAvailable(_message(r))
            if r.status_code >= 400:
                raise WMEError(f"WME {path}: HTTP {r.status_code}: {_message(r)}")
            return r
        raise WMEError(f"WME {path}: retries exhausted (HTTP {r.status_code})")

    def trending(self, project: str, hour: int, k: int = 1000) -> list[dict]:
        """Top-k trending Pageviews records of a project for an hour (hours since 1970-01-01 00:00 UTC)."""
        if not 1 <= k <= 1000:
            raise ValueError("k must be between 1 and 1000")
        d, h = date_hour(hour)
        return self._get(f"/pageviews/trending/{project}_namespace_0/{k}/{d}/{h}").json()

    def batch_info(self, project: str, hour: int) -> dict:
        d, h = date_hour(hour)
        return self._get(f"/pageviews/batches/{d}/{h}/{project}_namespace_0").json()

    def batch_records(self, project: str, hour: int) -> Iterator[dict]:
        """Every article's Pageviews record for one project and hour, streamed from the batch archive."""
        d, h = date_hour(hour)
        r = self._get(f"/pageviews/batches/{d}/{h}/{project}_namespace_0/download", allow_redirects=False)
        url = r.headers.get("Location") if r.status_code in (301, 302, 303, 307, 308) else None
        # the presigned URL is short-lived and carries its own credentials: no Authorization header
        body = requests.get(url, stream=True, timeout=300, headers={"User-Agent": user_agent()}) if url else r
        if not body.ok:
            raise WMEError(f"batch download failed: HTTP {body.status_code}")
        yield from records_from_archive(body.raw if url else io.BytesIO(body.content))


def records_from_archive(fileobj) -> Iterator[dict]:
    """Pageviews records from a gzipped tar of NDJSON (or JSON array) files."""
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            data = f.read()
            text = data.decode("utf-8")
            if text.lstrip().startswith("["):
                yield from json.loads(text)
                continue
            for line in text.splitlines():
                if line.strip():
                    yield json.loads(line)


def date_hour(hour: int) -> tuple[str, str]:
    t = datetime.fromtimestamp(hour * 3600, timezone.utc)
    return t.strftime("%Y-%m-%d"), t.strftime("%H")


def _message(r: requests.Response) -> str:
    try:
        d = r.json()
        return str(d.get("message") or d)[:300]
    except ValueError:
        return r.text[:300]
