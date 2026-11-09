# SPDX-License-Identifier: GPL-2.0-or-later
"""Storage (SQLAlchemy Core): SQLite locally, ToolsDB (MariaDB) on Toolforge.

hourly      one row per (project, hour, page): count, share of project views, 96-hour statistics, trending signals
trending    the top-1000 trending list per (project, hour), by rank
page_meta   each page's latest geographic distribution (daily) and source pages (clickstream, monthly)
links       cached outgoing article links per page (MediaWiki API)
ingested    which (project, hour, kind) are loaded
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable

from sqlalchemy import (Boolean, Column, Float, Integer, MetaData, String, Table, Text, and_, create_engine, delete, func,
                        insert, select)
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.engine import Engine

from .config import database_url

md = MetaData()
# titles are case-sensitive: MariaDB's default collation is not, so use a binary one there
TITLE = String(255).with_variant(VARCHAR(255, collation="utf8mb4_bin"), "mysql", "mariadb")
hourly = Table(
    "hourly", md,
    Column("project", String(16), primary_key=True), Column("hour", Integer, primary_key=True),
    Column("title", TITLE, primary_key=True), Column("count", Integer), Column("proportion", Float), Column("mean", Float),
    Column("stddev", Float), Column("ratio", Float), Column("zscore", Float), Column("trending_abs", Boolean),
    Column("trending_rel", Boolean), Column("is_new", Boolean),
)
trending = Table(
    "trending", md,
    Column("project", String(16), primary_key=True), Column("hour", Integer, primary_key=True),
    Column("rank", Integer, primary_key=True), Column("title", TITLE), Column("page_id", Integer), Column("qid", String(32)),
    Column("count", Integer), Column("zscore", Float), Column("ratio", Float), Column("proportion", Float),
    Column("trending_abs", Boolean), Column("trending_rel", Boolean), Column("is_new", Boolean),
)
page_meta = Table(
    "page_meta", md,
    Column("project", String(16), primary_key=True), Column("title", TITLE, primary_key=True), Column("hour", Integer),
    Column("page_id", Integer), Column("qid", String(32)), Column("geo", Text), Column("geo_total", Integer),
    Column("sources", Text), Column("sources_total", Integer),
)
links = Table(
    "links", md,
    Column("project", String(16), primary_key=True), Column("title", TITLE, primary_key=True), Column("links", Text),
    Column("fetched", Integer),
)
ingested = Table(
    "ingested", md,
    Column("project", String(16), primary_key=True), Column("hour", Integer, primary_key=True),
    Column("kind", String(16), primary_key=True), Column("records", Integer), Column("at", Integer), Column("meta", Text),
)

_engine: Engine | None = None


def engine(url: str | None = None) -> Engine:
    global _engine
    if url is not None or _engine is None:
        _engine = create_engine(url or database_url(), pool_pre_ping=True, pool_recycle=280)
        md.create_all(_engine)
    return _engine


def _replace(conn, table: Table, rows: list[dict], keys: list[str]) -> None:
    """Upsert by primary key, portably: delete the keys, then insert."""
    if not rows:
        return
    for r in rows:
        conn.execute(delete(table).where(and_(*[table.c[k] == r[k] for k in keys])))
    conn.execute(insert(table), rows)


# ---- writes (ingest)
def write_trending(project: str, hour: int, recs: list[dict], meta: dict | None = None) -> None:
    rows = [{"project": project, "hour": hour, "rank": i + 1, **{k: r[k] for k in (
        "title", "page_id", "qid", "count", "zscore", "ratio", "proportion", "trending_abs", "trending_rel", "is_new")}}
        for i, r in enumerate(recs)]
    with engine().begin() as c:
        c.execute(delete(trending).where(and_(trending.c.project == project, trending.c.hour == hour)))
        if rows:
            c.execute(insert(trending), rows)
        _mark(c, project, hour, "trending", len(rows), meta)
    write_meta(project, hour, recs)


def write_hourly(project: str, hour: int, recs: Iterable[dict], meta: dict | None = None, chunk: int = 5000) -> int:
    n = 0
    with engine().begin() as c:
        c.execute(delete(hourly).where(and_(hourly.c.project == project, hourly.c.hour == hour)))
        buf, metas = [], []
        for r in recs:
            buf.append({"project": project, "hour": hour, **{k: r[k] for k in (
                "title", "count", "proportion", "mean", "stddev", "ratio", "zscore", "trending_abs", "trending_rel", "is_new")}})
            if r["geo"] or r["sources"]:
                metas.append(r)
            if len(buf) >= chunk:
                c.execute(insert(hourly), buf)
                n += len(buf)
                buf = []
        if buf:
            c.execute(insert(hourly), buf)
            n += len(buf)
        _mark(c, project, hour, "batch", n, meta)
    write_meta(project, hour, metas)
    return n


def write_meta(project: str, hour: int, recs: list[dict]) -> None:
    rows = [{"project": project, "title": r["title"], "hour": hour, "page_id": r.get("page_id"), "qid": r.get("qid"),
             "geo": json.dumps(r["geo"]), "geo_total": r.get("geo_total"), "sources": json.dumps(r["sources"]),
             "sources_total": r.get("sources_total")} for r in recs if r["geo"] or r["sources"]]
    if not rows:
        return
    with engine().begin() as c:
        # keep the newest record per page
        have = {t: h for t, h in c.execute(select(page_meta.c.title, page_meta.c.hour).where(and_(
            page_meta.c.project == project, page_meta.c.title.in_([r["title"] for r in rows]))))}
        rows = [r for r in rows if have.get(r["title"], -1) <= hour]
        for i in range(0, len(rows), 2000):
            part = rows[i:i + 2000]
            c.execute(delete(page_meta).where(and_(page_meta.c.project == project,
                                                   page_meta.c.title.in_([r["title"] for r in part]))))
            c.execute(insert(page_meta), part)


def _mark(conn, project: str, hour: int, kind: str, n: int, meta: dict | None) -> None:
    _replace(conn, ingested, [{"project": project, "hour": hour, "kind": kind, "records": n, "at": int(time.time()),
                               "meta": json.dumps(meta or {})}], ["project", "hour", "kind"])


def prune(project: str, keep_from_hour: int) -> None:
    with engine().begin() as c:
        for t in (hourly, trending, ingested):
            c.execute(delete(t).where(and_(t.c.project == project, t.c.hour < keep_from_hour)))


# ---- reads (API)
def loaded_hours(project: str, kind: str) -> list[int]:
    with engine().connect() as c:
        return [h for (h,) in c.execute(select(ingested.c.hour).where(and_(
            ingested.c.project == project, ingested.c.kind == kind)).order_by(ingested.c.hour))]


def is_loaded(project: str, hour: int, kind: str) -> bool:
    with engine().connect() as c:
        return c.execute(select(func.count()).select_from(ingested).where(and_(
            ingested.c.project == project, ingested.c.hour == hour, ingested.c.kind == kind))).scalar() > 0


def ingest_meta(project: str, hour: int, kind: str) -> dict:
    with engine().connect() as c:
        row = c.execute(select(ingested.c.meta).where(and_(
            ingested.c.project == project, ingested.c.hour == hour, ingested.c.kind == kind))).first()
    return json.loads(row[0]) if row and row[0] else {}


def trending_list(project: str, hour: int, k: int) -> list[dict]:
    with engine().connect() as c:
        rows = c.execute(select(trending).where(and_(trending.c.project == project, trending.c.hour == hour))
                         .order_by(trending.c.rank).limit(k)).mappings().all()
    return [dict(r) for r in rows]


def series(project: str, titles: list[str], h0: int, h1: int) -> dict[str, dict[int, int]]:
    """title -> {hour: views} for hours in [h0, h1] (pages below the batch's storage threshold are absent)."""
    out: dict[str, dict[int, int]] = {t: {} for t in titles}
    if not titles:
        return out
    with engine().connect() as c:
        for i in range(0, len(titles), 500):
            q = select(hourly.c.title, hourly.c.hour, hourly.c["count"]).where(and_(
                hourly.c.project == project, hourly.c.title.in_(titles[i:i + 500]), hourly.c.hour.between(h0, h1)))
            for t, h, n in c.execute(q):
                out[t][h] = n
    return out


def at_hour(project: str, titles: list[str], hour: int) -> dict[str, dict]:
    out = {}
    with engine().connect() as c:
        for i in range(0, len(titles), 500):
            q = select(hourly).where(and_(hourly.c.project == project, hourly.c.hour == hour,
                                          hourly.c.title.in_(titles[i:i + 500])))
            for r in c.execute(q).mappings():
                out[r["title"]] = dict(r)
    return out


def meta(project: str, title: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(select(page_meta).where(and_(page_meta.c.project == project, page_meta.c.title == title))).mappings().first()
    if not r:
        return None
    d = dict(r)
    d["geo"] = json.loads(d["geo"] or "[]")
    d["sources"] = json.loads(d["sources"] or "[]")
    return d


def metas(project: str, titles: list[str]) -> dict[str, dict]:
    out = {}
    with engine().connect() as c:
        for i in range(0, len(titles), 500):
            for r in c.execute(select(page_meta).where(and_(page_meta.c.project == project,
                                                            page_meta.c.title.in_(titles[i:i + 500])))).mappings():
                d = dict(r)
                d["geo"] = json.loads(d["geo"] or "[]")
                out[d["title"]] = d
    return out


def cached_links(project: str, title: str, max_age: int) -> list[str] | None:
    with engine().connect() as c:
        r = c.execute(select(links.c.links, links.c.fetched).where(and_(links.c.project == project, links.c.title == title))).first()
    if r and time.time() - r[1] < max_age:
        return json.loads(r[0])
    return None


def store_links(project: str, title: str, ls: list[str]) -> None:
    with engine().begin() as c:
        _replace(c, links, [{"project": project, "title": title, "links": json.dumps(ls), "fetched": int(time.time())}],
                 ["project", "title"])
