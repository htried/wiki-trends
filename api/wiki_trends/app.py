# SPDX-License-Identifier: GPL-2.0-or-later
"""The wiki-trends API (Flask, WSGI: Toolforge's python webservice serves `app`).

GET /v1/projects
    the projects served, with the hours loaded for each
GET /v1/trending/{project}?hour=YYYY-MM-DDTHH&k=50&spark=72
    the top-k trending pages of a project for an hour (default: the latest loaded), each with its hourly views over the
    `spark` hours ending at that hour
GET /v1/pages/{project}/{title}?hour=YYYY-MM-DDTHH&hours=72&k=10
    an anchor page: its hourly views; its "entity" (the k most-viewed pages it links to or that send it readers, summed);
    each of those pages against its own 96-hour norm and against the anchor's clickstream history; geography
GET /v1/health

Every response is JSON, cacheable, and open to other origins (the frontend and any bot call it the same way).
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from . import deploy, gate
from . import links as linkmod
from . import store
from .config import is_demo, projects, retention_hours, wiki_host
from .records import flatten, key
from .wme import NotAvailable, WMEClient, WMEError

app = Flask(__name__)
app.json.sort_keys = False
gate.install(app)
deploy.install(app)
_client: WMEClient | None = None
_fetch_lock = threading.Lock()

K_CHOICES = (10, 50, 100)
UNUSUAL_Z = 3.0


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@app.errorhandler(ApiError)
def _api_error(e: ApiError):
    return jsonify({"error": e.message}), e.status


@app.after_request
def _headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers.setdefault("Cache-Control", "public, max-age=300")
    return resp


def client() -> WMEClient:
    global _client
    if _client is None:
        _client = WMEClient()
    return _client


def iso_hour(h: int) -> str:
    return datetime.fromtimestamp(h * 3600, timezone.utc).strftime("%Y-%m-%dT%H:00Z")


def parse_hour(s: str | None, default: int | None) -> int:
    if not s:
        if default is None:
            raise ApiError(404, "no data loaded yet")
        return default
    try:
        t = datetime.strptime(s.rstrip("Z")[:13], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ApiError(400, "hour must look like 2026-11-16T15") from None
    return int(t.timestamp()) // 3600


def check_project(project: str) -> str:
    if project not in projects():
        raise ApiError(404, f"unknown project {project!r}; available: {', '.join(projects())}")
    return project


def int_arg(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(request.args.get(name, default))
    except ValueError:
        raise ApiError(400, f"{name} must be an integer") from None
    if not lo <= v <= hi:
        raise ApiError(400, f"{name} must be between {lo} and {hi}")
    return v


def page_url(project: str, title: str) -> str:
    return f"https://{wiki_host(project)}/wiki/{title}"


def spark(ser: dict[int, int], h0: int, h1: int) -> list[int | None]:
    """Views per hour in [h0, h1]; None where the page was below the batch's storage threshold (or the hour is missing)."""
    return [ser.get(h) for h in range(h0, h1 + 1)]


# ---------------------------------------------------------------------------------------------------------------- routes

@app.get("/v1/health")
def health():
    return jsonify({"ok": True})


@app.get("/v1/projects")
def list_projects():
    out = []
    for p in projects():
        th, bh = store.loaded_hours(p, "trending"), store.loaded_hours(p, "batch")
        out.append({"project": p, "host": wiki_host(p),
                    "trending_hours": {"first": iso_hour(th[0]), "last": iso_hour(th[-1]), "count": len(th)} if th else None,
                    "batch_hours": {"first": iso_hour(bh[0]), "last": iso_hour(bh[-1]), "count": len(bh)} if bh else None})
    return jsonify({"projects": out, "retention_hours": retention_hours(), "demo": is_demo()})


@app.get("/v1/trending/<project>")
def trending(project: str):
    check_project(project)
    loaded = store.loaded_hours(project, "trending")
    hour = parse_hour(request.args.get("hour"), loaded[-1] if loaded else None)
    k = int_arg("k", 50, 1, 1000)
    n_spark = int_arg("spark", 72, 0, retention_hours())
    if hour not in loaded:
        _fetch_trending(project, hour)  # an hour outside the local window: ask WME once and keep it
    items = store.trending_list(project, hour, k)
    series = store.series(project, [r["title"] for r in items], hour - n_spark + 1, hour) if n_spark else {}
    resp = jsonify({
        "project": project, "hour": iso_hour(hour), "k": k, "spark_hours": n_spark,
        "items": [{"rank": r["rank"], "title": r["title"], "url": page_url(project, r["title"]), "qid": r["qid"],
                   "views": r["count"], "zscore": r["zscore"], "ratio": r["ratio"], "project_share": r["proportion"],
                   "signals": {"absolute": r["trending_abs"], "relative": r["trending_rel"], "new_page": r["is_new"]},
                   **({"spark": spark(series.get(r["title"], {}), hour - n_spark + 1, hour)} if n_spark else {})}
                  for r in items],
    })
    if loaded and hour != loaded[-1]:
        resp.headers["Cache-Control"] = "public, max-age=86400"  # past hours do not change
    return resp


def _fetch_trending(project: str, hour: int) -> None:
    with _fetch_lock:
        if store.is_loaded(project, hour, "trending"):
            return
        if is_demo():
            raise ApiError(404, f"no demo data for {project} at {iso_hour(hour)}; the demo covers the last 72 hours")
        try:
            recs = [flatten(r, hour) for r in client().trending(project, hour, 1000)]
        except NotAvailable:
            raise ApiError(404, f"no trending data for {project} at {iso_hour(hour)}") from None
        except WMEError as e:
            raise ApiError(502, f"upstream error: {e}") from None
        store.write_trending(project, hour, recs)


@app.get("/v1/pages/<project>/<path:title>")
def page(project: str, title: str):
    check_project(project)
    title = key(title)
    loaded = store.loaded_hours(project, "batch")
    hour = parse_hour(request.args.get("hour"), loaded[-1] if loaded else None)
    if hour not in loaded:
        raise ApiError(404, f"hour {iso_hour(hour)} is not loaded; loaded: {iso_hour(loaded[0])} to {iso_hour(loaded[-1])}"
                       if loaded else "no hourly data loaded yet")
    hours = int_arg("hours", 72, 6, retention_hours())
    k = int_arg("k", 10, 1, 100)
    h0 = hour - hours + 1

    me = store.at_hour(project, [title], hour).get(title)
    meta = store.meta(project, title) or {"geo": [], "sources": [], "geo_total": None, "sources_total": None}
    # associated pages: the page's own article links, plus the pages that send it readers (clickstream)
    try:
        out_links = linkmod.outlinks(project, title)
    except Exception:  # the MediaWiki API being unavailable should not take the page down
        out_links = []
    src_share = {s["title"]: s["proportion"] for s in meta["sources"]}
    candidates = sorted((set(out_links) | set(src_share)) - {title})
    now = store.at_hour(project, candidates, hour)
    top = sorted(now.values(), key=lambda r: -r["count"])[:k]
    members = [r["title"] for r in top]
    ser = store.series(project, [title] + members, h0, hour)

    # entity: the anchor's k most-viewed associated pages, summed, against the sum of their 96-hour means
    entity = [sum(ser[m].get(h, 0) for m in members) for h in range(h0, hour + 1)]
    expected = sum(r["mean"] or 0 for r in top)
    # clickstream: each source page's share of this hour's views among the anchor's source pages, against its share of
    # the anchor's incoming clicks last month
    src_now = {m: now[m]["count"] for m in src_share if m in now}
    src_sum = sum(src_now.values()) or 0
    src_hist = sum(src_share[m] for m in src_now) or 0

    def member(r: dict) -> dict:
        t = r["title"]
        hist = src_share.get(t)
        cur = (src_now[t] / src_sum) if (hist is not None and src_sum and t in src_now) else None
        exp = (hist / src_hist) if (hist is not None and src_hist) else None
        dev = (cur / exp) if (cur is not None and exp) else None
        return {"title": t, "url": page_url(project, t), "views": r["count"], "mean": r["mean"], "zscore": r["zscore"],
                "ratio": r["ratio"], "linked": t in out_links, "clickstream_share": hist, "current_source_share": cur,
                "clickstream_deviation": dev, "unusual": (r["zscore"] or 0) >= UNUSUAL_Z,
                "trending": bool(r["trending_abs"] or r["trending_rel"]), "spark": spark(ser.get(t, {}), h0, hour)}

    # geography: the anchor's own, and the associated pages' together (each weighted by its views this hour)
    geo_assoc: dict[str, float] = {}
    mm = store.metas(project, members)
    for r in top:
        for g in (mm.get(r["title"]) or {}).get("geo", []):
            geo_assoc[g["country"]] = geo_assoc.get(g["country"], 0.0) + r["count"] * (g["proportion"] or 0)
    tot = sum(geo_assoc.values()) or 1.0
    batch_meta = store.ingest_meta(project, hour, "batch")
    return jsonify({
        "project": project, "hour": iso_hour(hour), "hours": hours, "k": k,
        "page": {"title": title, "url": page_url(project, title), "qid": meta.get("qid"),
                 "views": me["count"] if me else None, "mean": me["mean"] if me else None, "zscore": me["zscore"] if me else None,
                 "ratio": me["ratio"] if me else None, "project_share": me["proportion"] if me else None,
                 "signals": {"absolute": me["trending_abs"], "relative": me["trending_rel"], "new_page": me["is_new"]} if me else None,
                 "spark": spark(ser.get(title, {}), h0, hour)},
        "entity": {"members": [member(r) for r in top], "candidates": len(candidates), "with_views": len(now),
                   "spark": entity, "views": entity[-1] if entity else 0, "expected": expected,
                   "ratio": (entity[-1] / expected) if expected else None,
                   "sources_total": meta.get("sources_total"), "clickstream_period": (batch_meta.get("associated_pages") or {}).get("period")},
        "geo": {"page": meta["geo"], "page_total": meta.get("geo_total"),
                "associated": sorted([{"country": c, "proportion": v / tot} for c, v in geo_assoc.items()], key=lambda x: -x["proportion"]),
                "period": (batch_meta.get("geo_data") or {}).get("period")},
    })


# optional: serve the static frontend from the same origin (WIKI_TRENDS_FRONTEND=path/to/frontend). The frontend only
# talks to /v1, so it can equally be hosted on its own.
_FRONTEND = os.environ.get("WIKI_TRENDS_FRONTEND")
if _FRONTEND:
    from flask import send_from_directory

    @app.get("/")
    def _index():
        return send_from_directory(_FRONTEND, "index.html")

    @app.get("/<path:path>")
    def _static(path: str):
        return send_from_directory(_FRONTEND, path)


def create_app():
    store.engine()
    return app


if __name__ == "__main__":
    store.engine()
    app.run(port=8000, debug=True)
