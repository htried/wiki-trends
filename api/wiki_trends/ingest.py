# SPDX-License-Identifier: GPL-2.0-or-later
"""Hourly ingest from Wikimedia Enterprise: the top-1000 trending list and the full batch of every article, per project.

    python -m wiki_trends.ingest                       # the last 12 hours not yet loaded (run hourly)
    python -m wiki_trends.ingest --hours-back 72       # backfill three days
    python -m wiki_trends.ingest --projects enwiki --trending-only

WME publishes an hour some hours after it ends; hours that are not out yet are skipped and picked up by the next run.
History older than WIKI_TRENDS_RETENTION_HOURS is pruned.
"""

from __future__ import annotations

import argparse
import logging
import time

from . import store
from .config import projects, retention_hours
from .records import flatten
from .wme import NotAvailable, WMEClient, WMEError

log = logging.getLogger("wiki_trends.ingest")


def ingest_hour(client: WMEClient, project: str, hour: int, *, trending_only: bool = False) -> dict:
    done = {}
    if not store.is_loaded(project, hour, "trending"):
        try:
            recs = [flatten(r, hour) for r in client.trending(project, hour, 1000)]
            store.write_trending(project, hour, recs)
            done["trending"] = len(recs)
        except NotAvailable:
            done["trending"] = "not available"
    if not trending_only and not store.is_loaded(project, hour, "batch"):
        try:
            info = client.batch_info(project, hour)
            n = store.write_hourly(project, hour, (flatten(r, hour) for r in client.batch_records(project, hour)),
                                   meta={k: info.get(k) for k in ("count", "size", "date_modified", "geo_data", "associated_pages")})
            done["batch"] = n
        except NotAvailable:
            done["batch"] = "not available"
    return done


def run(hours_back: int, only: list[str] | None = None, trending_only: bool = False) -> None:
    client = WMEClient()
    now = int(time.time()) // 3600
    for project in only or projects():
        for hour in range(now - 1, now - 1 - hours_back, -1):  # newest first: the latest hour matters most
            try:
                res = ingest_hour(client, project, hour, trending_only=trending_only)
            except WMEError as e:
                log.warning("%s %d: %s", project, hour, e)
                continue
            if res:
                log.info("%s %d: %s", project, hour, res)
        store.prune(project, now - retention_hours())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours-back", type=int, default=12)
    ap.add_argument("--projects", help="comma-separated, default all configured")
    ap.add_argument("--trending-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(args.hours_back, args.projects.split(",") if args.projects else None, args.trending_only)


if __name__ == "__main__":
    main()
