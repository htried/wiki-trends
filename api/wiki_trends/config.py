# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings, all from the environment (Toolforge: `toolforge envvars create NAME VALUE`; locally: a .env file you export).

WME_USERNAME / WME_PASSWORD   Wikimedia Enterprise account; the client logs in and refreshes its own access token
WME_ACCESS_TOKEN              alternatively, a ready access token (expires after 24 hours)
WME_API_BASE                  default https://api.enterprise.wikimedia.com/v2
WME_AUTH_BASE                 default https://auth.enterprise.wikimedia.com/v1
DATABASE_URL                  SQLAlchemy URL; default sqlite:///wiki_trends.sqlite3 (Toolforge: the ToolsDB MariaDB URL)
WIKI_TRENDS_PROJECTS          comma-separated project identifiers; default the initial WME release
WIKI_TRENDS_RETENTION_HOURS   hourly history kept per page (default 168 = 7 days)
WIKI_TRENDS_CONTACT           contact URL or address for the User-Agent sent to Wikimedia APIs
WIKI_TRENDS_DEMO              1: the data is synthetic (wiki_trends.demo); the site says so on every page
WIKI_TRENDS_PASSPHRASE        optional shared passphrase for the whole site (wiki_trends/gate.py)
WIKI_TRENDS_SECRET            optional key for signing the passphrase cookie
WIKI_TRENDS_WEBHOOK_SECRET    GitHub webhook secret for self-update on pushes to main (wiki_trends/deploy.py)
WIKI_TRENDS_RELOAD_FILE       file uWSGI watches for reloads (deploy/uwsgi.ini)
"""

from __future__ import annotations

import os

# the initial Wikimedia Enterprise pageviews release: seven Wikipedias, articles (namespace 0)
DEFAULT_PROJECTS = ["enwiki", "dewiki", "eswiki", "frwiki", "itwiki", "nlwiki", "ptwiki"]


def projects() -> list[str]:
    raw = os.environ.get("WIKI_TRENDS_PROJECTS", "")
    return [p.strip() for p in raw.split(",") if p.strip()] or list(DEFAULT_PROJECTS)


def batch_identifier(project: str) -> str:
    """enwiki -> enwiki_namespace_0 (WME batch identifier for a project's articles)."""
    return f"{project}_namespace_0"


def wiki_host(project: str) -> str:
    """enwiki -> en.wikipedia.org"""
    if not project.endswith("wiki"):
        raise ValueError(f"not a Wikipedia project identifier: {project}")
    return f"{project[:-4]}.wikipedia.org"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite:///wiki_trends.sqlite3")


def retention_hours() -> int:
    return int(os.environ.get("WIKI_TRENDS_RETENTION_HOURS", "168"))


def user_agent() -> str:
    contact = os.environ.get("WIKI_TRENDS_CONTACT", "https://toolsadmin.wikimedia.org/")
    return f"wiki-trends/0.1 ({contact})"


def is_demo() -> bool:
    return os.environ.get("WIKI_TRENDS_DEMO", "").strip().lower() in ("1", "true", "yes")
