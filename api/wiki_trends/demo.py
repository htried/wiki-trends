# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic data for working on the frontend without WME credentials: three days of hourly records for English
Wikipedia, built around one story (a film adaptation of The Odyssey: "The Odyssey" moving with "Matt Damon"), plus
ordinary pages, clickstream sources, geography, and cached article links. Never use for anything but local development.

    DATABASE_URL=sqlite:///demo.sqlite3 python -m wiki_trends.demo
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime, timezone

from . import store
from .records import flatten

STORY = {  # page: (base views/hour, peak multiplier, hours after the story breaks when it peaks)
    "The_Odyssey": (600, 25, 2), "Matt_Damon": (900, 12, 1), "Christopher_Nolan": (1500, 6, 1), "Odysseus": (400, 9, 3),
    "Homer": (1200, 1.6, 4), "Iliad": (700, 2.2, 4), "Penelope": (150, 5, 5), "Tom_Holland": (2500, 2.5, 2),
    "Zendaya": (3000, 1.8, 2), "Ithaca": (300, 3, 6), "Trojan_War": (500, 2.4, 5), "Polyphemus": (80, 6, 6),
}
LINKS = {"The_Odyssey": ["Homer", "Iliad", "Odysseus", "Penelope", "Ithaca", "Trojan_War", "Polyphemus", "Ancient_Greek",
                         "Epic_poetry", "Telemachus", "Athena", "Poseidon"],
         "Matt_Damon": ["Christopher_Nolan", "Ben_Affleck", "Good_Will_Hunting", "The_Bourne_Identity_(2002_film)",
                        "Oppenheimer_(film)", "The_Martian_(film)"]}
SOURCES = {"The_Odyssey": [("Homer", 0.31), ("Iliad", 0.18), ("Odysseus", 0.12), ("Ancient_Greek_literature", 0.08),
                           ("Matt_Damon", 0.004), ("Epic_poetry", 0.05), ("Penelope", 0.03)],
           "Matt_Damon": [("Ben_Affleck", 0.21), ("Good_Will_Hunting", 0.12), ("Oppenheimer_(film)", 0.09),
                          ("The_Odyssey", 0.002), ("Christopher_Nolan", 0.05)]}
BACKGROUND = ["Ancient_Greek", "Epic_poetry", "Telemachus", "Athena", "Poseidon", "Ben_Affleck", "Good_Will_Hunting",
              "The_Bourne_Identity_(2002_film)", "Oppenheimer_(film)", "The_Martian_(film)", "Ancient_Greek_literature",
              "Taylor_Swift", "United_States", "Cleopatra", "World_War_II", "Albert_Einstein", "Moon", "Football",
              "Bitcoin", "Paris", "Volcano", "Eclipse", "Formula_One", "Olympic_Games", "Pizza", "Jazz"]
GEO = {"default": [("US", 0.42), ("GB", 0.12), ("IN", 0.10), ("CA", 0.06), ("AU", 0.05), ("DE", 0.03), ("PH", 0.03)],
       "Homer": [("US", 0.3), ("GR", 0.2), ("GB", 0.15), ("IN", 0.1)], "Matt_Damon": [("US", 0.55), ("GB", 0.1), ("CA", 0.08)]}


def record(title: str, hour: int, count: int, mean: float, std: float, trending: bool) -> dict:
    t = datetime.fromtimestamp(hour * 3600, timezone.utc)
    z = (count - mean) / std if std else 0.0
    return {"name": title, "identifier": abs(hash(title)) % 10**7, "url": f"https://en.wikipedia.org/wiki/{title}",
            "main_entity": {"identifier": f"Q{abs(hash(title)) % 10**6}"},
            "views": [{"comment": "views during the hour",
                       "period": {"start": t.strftime("%Y-%m-%dT%H:00:00Z"), "end": t.strftime("%Y-%m-%dT%H:59:59Z")},
                       "count": count, "project_proportion": count / 9_000_000,
                       "statistics": [{"variable": "count", "values": {"mean": mean, "stddev": std, "ratio": count / mean if mean else None, "zscore": z}}],
                       "geo_distributions": [{"country_code": c, "proportion": p} for c, p in GEO.get(title, GEO["default"])],
                       "geo_distributions_total": count * 20,
                       "source_pages": [{"name": s, "proportion": p} for s, p in SOURCES.get(title, [])],
                       "source_pages_total": count * 30}],
            "signals": {"is_new_page": False, "is_trending_absolute": trending and z > 3 and count > 100,
                        "is_trending_project_proportion": trending and z > 3}}


def main() -> None:
    rng = random.Random(3)
    store.engine()
    now = int(time.time()) // 3600 - 2
    hours = list(range(now - 71, now + 1))
    story_hour = now - 8
    base = {t: v[0] for t, v in STORY.items()} | {t: rng.choice([60, 150, 400, 900, 2500]) for t in BACKGROUND}
    hist: dict[str, list[int]] = {t: [] for t in base}
    for h in hours:
        recs = []
        for t, b in base.items():
            daily = 1 + 0.35 * math.sin(2 * math.pi * ((h % 24) - 9) / 24)
            v = b * daily * rng.uniform(0.85, 1.15)
            if t in STORY and h >= story_hour:
                mult, peak = STORY[t][1], STORY[t][2]
                dt = h - story_hour
                v *= 1 + (mult - 1) * (dt / peak if dt < peak else math.exp(-(dt - peak) / 10))
            past = hist[t][-96:] or [b]
            mean = sum(past) / len(past)
            std = (sum((x - mean) ** 2 for x in past) / max(1, len(past) - 1)) ** 0.5 or 1.0
            recs.append(record(t, h, int(v), mean, std, True))
            hist[t].append(int(v))
        flat = [flatten(r, h) for r in recs]
        store.write_hourly("enwiki", h, flat, meta={"geo_data": {"period": {"start": "demo"}},
                                                   "associated_pages": {"period": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31T23:59:59Z"}}})
        trend = sorted([f for f in flat if f["trending_abs"] or f["trending_rel"]], key=lambda f: -(f["zscore"] or 0))
        store.write_trending("enwiki", h, trend)
    for t, ls in LINKS.items():
        store.store_links("enwiki", t, ls)
    print(f"demo data: enwiki, {len(hours)} hours ending {datetime.fromtimestamp(now * 3600, timezone.utc):%Y-%m-%d %H}:00 UTC; "
          f"story breaks at {datetime.fromtimestamp(story_hour * 3600, timezone.utc):%H}:00")


if __name__ == "__main__":
    main()
