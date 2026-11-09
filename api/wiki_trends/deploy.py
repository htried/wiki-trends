# SPDX-License-Identifier: GPL-2.0-or-later
"""Self-update from GitHub: on a push to main, pull the new code and reload the web service.

POST /hooks/github   GitHub webhook (content type application/json, events: push), signed with the shared secret in
                     WIKI_TRENDS_WEBHOOK_SECRET. Pushes to other branches and other events are ignored. The update runs in
                     the background (GitHub waits only 10 seconds): `git pull --ff-only`, `pip install -r
                     requirements.txt` if that file changed, then touch WIKI_TRENDS_RELOAD_FILE, which uWSGI watches
                     (touch-reload in deploy/uwsgi.ini) to reload the app with the new code. Log: WIKI_TRENDS_DEPLOY_LOG.

`python -m wiki_trends.deploy` runs the same update from the command line or a scheduled job (the fallback when the web
container has no git, or the webhook is not set up): it pulls only when origin/main has new commits.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

REPO = Path(os.environ.get("WIKI_TRENDS_REPO", Path(__file__).resolve().parents[2]))
_lock = threading.Lock()


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n"
    path = os.environ.get("WIKI_TRENDS_DEPLOY_LOG")
    if path:
        with open(path, "a") as f:
            f.write(line)
    else:
        sys.stderr.write(line)


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True, timeout=120).stdout.strip()


def update(only_if_behind: bool = False) -> str:
    """Pull main and reload. Returns what happened."""
    if not shutil.which("git"):
        return "git is not available here; run `python -m wiki_trends.deploy` from a scheduled job instead"
    with _lock:
        try:
            before = _git("rev-parse", "HEAD")
            if only_if_behind:
                _git("fetch", "-q", "origin", "main")
                if _git("rev-parse", "origin/main") == before:
                    return "up to date"
            req = REPO / "api" / "requirements.txt"
            req_before = req.read_bytes() if req.exists() else b""
            _git("pull", "-q", "--ff-only", "origin", "main")
            after = _git("rev-parse", "HEAD")
            if after == before:
                return "up to date"
            if req.exists() and req.read_bytes() != req_before:
                pip = Path(os.environ.get("WIKI_TRENDS_VENV", Path.home() / "www/python/venv")) / "bin" / "pip"
                subprocess.run([str(pip), "install", "-q", "-r", str(req)], check=True, timeout=600)
            reload_file = os.environ.get("WIKI_TRENDS_RELOAD_FILE")
            if reload_file:
                Path(reload_file).touch()
            msg = f"updated {before[:8]} -> {after[:8]}" + (", reloading" if reload_file else " (set WIKI_TRENDS_RELOAD_FILE to reload)")
        except subprocess.CalledProcessError as e:
            msg = f"update failed: {' '.join(e.cmd[:4])}: {(e.stderr or '').strip()[:300]}"
        except Exception as e:  # never take the web service down over a deploy
            msg = f"update failed: {e}"
        _log(msg)
        return msg


def verify(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def install(app: Flask) -> None:
    @app.post("/hooks/github")
    def _github():
        secret = os.environ.get("WIKI_TRENDS_WEBHOOK_SECRET")
        if not secret:
            return jsonify({"error": "webhook not configured"}), 404
        if not verify(secret, request.get_data(), request.headers.get("X-Hub-Signature-256", "")):
            return jsonify({"error": "bad signature"}), 403
        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return jsonify({"ok": True, "pong": True})
        payload = request.get_json(silent=True) or {}
        if event != "push" or payload.get("ref") != "refs/heads/main":
            return jsonify({"ok": True, "ignored": f"{event} {payload.get('ref', '')}".strip()}), 202
        threading.Thread(target=update, daemon=True).start()
        return jsonify({"ok": True, "deploying": payload.get("after", "")[:8]}), 202


if __name__ == "__main__":
    print(update(only_if_behind=True))
