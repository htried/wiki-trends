# SPDX-License-Identifier: GPL-2.0-or-later
"""An optional shared passphrase in front of the whole site (pages and API), for previews.

On when WIKI_TRENDS_PASSPHRASE is set (Toolforge: `toolforge envvars create WIKI_TRENDS_PASSPHRASE`), off when it is
unset. Visitors enter it once on /login and get a cookie for 30 days; changing the passphrase signs everyone out. API
clients (e.g. a bot) can send it in the X-Wiki-Trends-Passphrase header instead. This keeps casual visitors out of a
preview; it is not access control for anything sensitive.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from html import escape
from urllib.parse import quote

from flask import Flask, jsonify, make_response, redirect, request

COOKIE = "wiki_trends_pass"
MAX_AGE = 30 * 86400
# reachable without the passphrase: the login page and what it needs, health checks, the deploy webhook
OPEN = ("/login", "/v1/health", "/hooks/github", "/assets/style.css", "/assets/favicon.svg", "/assets/vendor/codex/")


def passphrase() -> str | None:
    p = os.environ.get("WIKI_TRENDS_PASSPHRASE", "").strip()
    return p or None


def _token(p: str) -> str:
    secret = os.environ.get("WIKI_TRENDS_SECRET", "wiki-trends")
    return hmac.new(secret.encode(), ("pass:" + p).encode(), hashlib.sha256).hexdigest()


def _ok(p: str) -> bool:
    if hmac.compare_digest(request.cookies.get(COOKIE, ""), _token(p)):
        return True
    given = request.headers.get("X-Wiki-Trends-Passphrase", "")
    return bool(given) and hmac.compare_digest(given.encode(), p.encode())


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wikipedia Trends</title>
<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg">
<link rel="stylesheet" href="/assets/vendor/codex/codex-tokens.css">
<link rel="stylesheet" href="/assets/vendor/codex/codex.style.css">
<link rel="stylesheet" href="/assets/style.css">
</head><body>
<main style="max-width:420px;padding-top:12vh">
 <div class="panel"><h1>Wikipedia trends</h1>
  <p class="subtle">This preview needs a passphrase.</p>
  {error}
  <form method="post" action="/login">
   <input type="hidden" name="next" value="{next}">
   <div class="cdx-field"><div class="cdx-label"><label class="cdx-label__label" for="pass"><span class="cdx-label__label__text">Passphrase</span></label></div>
    <div class="cdx-field__control"><div class="cdx-text-input"><input id="pass" name="passphrase" type="password" class="cdx-text-input__input" autofocus autocomplete="current-password"></div></div></div>
   <p><button class="cdx-button cdx-button--action-progressive cdx-button--weight-primary" type="submit">Enter</button></p>
  </form></div>
</main></body></html>"""


def _safe_next(n: str | None) -> str:
    return n if n and n.startswith("/") and not n.startswith("//") else "/"


def install(app: Flask) -> None:
    @app.before_request
    def _gate():
        p = passphrase()
        if not p or request.path.startswith(OPEN) or _ok(p):
            return None
        if request.path.startswith("/v1/"):
            return jsonify({"error": "passphrase required"}), 401
        return redirect("/login?next=" + quote(request.full_path.rstrip("?")))

    @app.route("/login", methods=["GET", "POST"])
    def _login():
        p = passphrase()
        nxt = _safe_next(request.values.get("next"))
        if not p:
            return redirect(nxt)
        if request.method == "POST":
            if hmac.compare_digest(request.form.get("passphrase", "").strip().encode(), p.encode()):
                resp = make_response(redirect(nxt))
                resp.set_cookie(COOKIE, _token(p), max_age=MAX_AGE, httponly=True, secure=request.is_secure or
                                request.headers.get("X-Forwarded-Proto") == "https", samesite="Lax")
                return resp
            err = '<div class="cdx-message cdx-message--error" role="alert"><span class="cdx-message__icon"></span><div class="cdx-message__content">That passphrase is not right.</div></div>'
            return LOGIN_PAGE.format(error=err, next=escape(nxt)), 401
        return LOGIN_PAGE.format(error="", next=escape(nxt))
