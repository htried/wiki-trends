"""A page's outgoing article links, from the public MediaWiki Action API, redirects resolved, cached for a day."""

from __future__ import annotations

import requests

from . import store
from .config import user_agent, wiki_host
from .records import key

MAX_AGE = 86400


def outlinks(project: str, title: str, session: requests.Session | None = None, max_links: int = 2000) -> list[str]:
    cached = store.cached_links(project, title, MAX_AGE)
    if cached is not None:
        return cached
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
