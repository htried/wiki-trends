# SPDX-License-Identifier: GPL-2.0-or-later
"""A page's outgoing article links, from the public MediaWiki Action API, redirects resolved, cached for a day."""

from __future__ import annotations

import requests

from . import store
from .config import is_demo, user_agent, wiki_host
from .records import key

MAX_AGE = 86400


def outlinks(project: str, title: str, session: requests.Session | None = None, max_links: int = 2000) -> list[str]:
    # demo data: only the demo's own link lists, never live ones (real links would mix real pages into fake data)
    cached = store.cached_links(project, title, 10**10 if is_demo() else MAX_AGE)
    if cached is not None or is_demo():
        return cached or []
    s = session or requests.Session()
    params = {"action": "query", "format": "json", "formatversion": 2, "titles": title.replace("_", " "), "redirects": 1,
              "generator": "links", "gplnamespace": 0, "gpllimit": "max"}
    out: list[str] = []
    for _ in range(20):
        r = s.get(f"https://{wiki_host(project)}/w/api.php", params=params, headers={"User-Agent": user_agent()}, timeout=30)
        r.raise_for_status()
        d = r.json()
        out += [key(p["title"]) for p in (d.get("query") or {}).get("pages", []) if not p.get("missing")]
        if "continue" not in d or len(out) >= max_links:
            break
        params.update(d["continue"])
    out = sorted(set(out) - {key(title)})
    store.store_links(project, title, out)
    return out
