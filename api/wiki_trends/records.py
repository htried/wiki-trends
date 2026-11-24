# SPDX-License-Identifier: GPL-2.0-or-later
"""Flatten Wikimedia Enterprise Pageviews records (the `pageviews` schema) into what the site stores and serves."""

from __future__ import annotations

import math
from datetime import datetime
from urllib.parse import unquote


def key(title: str) -> str:
    """Page title as a key: underscores, first letter upper case (MediaWiki's canonical form for these wikis)."""
    t = title.strip().replace(" ", "_")
    return t[:1].upper() + t[1:] if t else t


def hour_of(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) // 3600


def _hour_view(rec: dict, hour: int | None) -> dict:
    """The record's entry for the hour itself (views can also hold longer periods)."""
    views = rec.get("views") or []
    for v in views:
        p = v.get("period") or {}
        if hour is not None and p.get("start") and hour_of(p["start"]) == hour and p.get("end") and hour_of(p["end"]) == hour:
            return v
    for v in views:
        if "hour" in (v.get("comment") or "").lower():
            return v
    return views[0] if views else {}


def _count_stats(views: list[dict]) -> dict:
    for v in views:
        for s in v.get("statistics") or []:
            if s.get("variable") == "count":
                return s.get("values") or {}
    return {}


def _num(x) -> float | None:
    """A finite number or None (MariaDB rejects NaN and infinity, which JSON parsing lets through)."""
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None


def _first_with(views: list[dict], field: str) -> dict:
    return next((v for v in views if v.get(field)), {})


def flatten(rec: dict, hour: int | None = None) -> dict:
    """One record -> the fields the site uses. `hour` (hours since epoch) picks the hourly view when several are given.

    The hour's count, the 96-hour statistics, the daily geography and the monthly source pages may sit in one `views`
    entry (the README's example) or each in its own (the live API): each is taken from the hour's entry if it has it,
    otherwise from the first entry that does."""
    v = _hour_view(rec, hour)
    views = [v] + [x for x in rec.get("views") or [] if x is not v]
    st = _count_stats(views)
    geo, src = _first_with(views, "geo_distributions"), _first_with(views, "source_pages")
    sig = rec.get("signals") or {}
    title = rec.get("name") or ""
    if not title and rec.get("url"):
        title = unquote(rec["url"].rsplit("/wiki/", 1)[-1])
    return {
        "title": key(title),
        "page_id": rec.get("identifier"),
        "qid": (rec.get("main_entity") or {}).get("identifier"),
        "count": int(v.get("count") or 0),
        "proportion": _num(v.get("project_proportion")),
        "mean": _num(st.get("mean")),
        "stddev": _num(st.get("stddev")),
        "ratio": _num(st.get("ratio")),
        "zscore": _num(st.get("zscore")),
        "trending_abs": bool(sig.get("is_trending_absolute")),
        "trending_rel": bool(sig.get("is_trending_project_proportion")),
        "is_new": bool(sig.get("is_new_page")),
        "geo": [{"country": g.get("country_code"), "proportion": g.get("proportion")} for g in geo.get("geo_distributions") or []
                if g.get("country_code")],
        "geo_total": geo.get("geo_distributions_total"),
        "sources": [{"title": key(s.get("name") or ""), "proportion": s.get("proportion")} for s in src.get("source_pages") or []
                    if s.get("name")],
        "sources_total": src.get("source_pages_total"),
        "geo_period": geo.get("period"),  # the day the geography describes
        "sources_period": src.get("period"),  # the month the source pages (clickstream) describe
    }
