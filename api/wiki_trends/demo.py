# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic data for working on the frontend without WME credentials: three days of hourly records for English
Wikipedia, built around one story (a film adaptation of The Odyssey: "The Odyssey" moving with "Matt Damon"), plus
ordinary pages, clickstream sources, geography, and cached article links. For local development and previews only.

With WIKI_TRENDS_DEMO=1 the API needs nothing else: each process builds the data in its own SQLite file on local disk
on the first request and rolls it forward every hour (the 72 hours end two hours ago, the story breaks 4 hours before
that). The site says the data is synthetic and never mixes in live data. To write it into a database instead:

    DATABASE_URL=sqlite:///demo.sqlite3 python -m wiki_trends.demo [--reset]
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import math
import os
import random
import threading
import time
import zlib
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


def _id(title: str, mod: int) -> int:  # stable across runs, unlike hash()
    return int(hashlib.md5(title.encode()).hexdigest()[:12], 16) % mod


def record(title: str, hour: int, count: int, mean: float, std: float, trending: bool) -> dict:
    t = datetime.fromtimestamp(hour * 3600, timezone.utc)
    z = (count - mean) / std if std else 0.0
    return {"name": title, "identifier": _id(title, 10**7), "url": f"https://en.wikipedia.org/wiki/{title}",
            "main_entity": {"identifier": f"Q{_id(title, 10**6)}"},
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


# hundreds of ordinary pages, each with its own small spikes now and then, so every hour has a full trending list
_POOL = """
Taylor_Swift Beyoncé Bad_Bunny Billie_Eilish Drake_(musician) Rihanna Adele Ed_Sheeran Lady_Gaga Dua_Lipa Harry_Styles
Olivia_Rodrigo Ariana_Grande Kendrick_Lamar The_Weeknd Sabrina_Carpenter Chappell_Roan SZA Doja_Cat Post_Malone
Elton_John Paul_McCartney Bob_Dylan Bruce_Springsteen Madonna Shakira Coldplay Radiohead The_Beatles Queen_(band)
Pink_Floyd Metallica BTS Blackpink Fleetwood_Mac ABBA Oasis_(band) Daft_Punk Nirvana_(band) Led_Zeppelin
Leonardo_DiCaprio Brad_Pitt Margot_Robbie Cillian_Murphy Florence_Pugh Timothée_Chalamet Pedro_Pascal Emma_Stone
Ryan_Gosling Jennifer_Lawrence Scarlett_Johansson Keanu_Reeves Tom_Cruise Meryl_Streep Denzel_Washington Viola_Davis
Natalie_Portman Anne_Hathaway Robert_Downey_Jr. Jenna_Ortega Austin_Butler Anya_Taylor-Joy Paul_Mescal Ayo_Edebiri
Barbie_(film) Oppenheimer_(film) Dune:_Part_Two Inception Interstellar_(film) The_Dark_Knight Titanic_(1997_film)
Avatar_(2009_film) The_Godfather Pulp_Fiction Parasite_(2019_film) Everything_Everywhere_All_at_Once Gladiator_(2000_film)
The_Lord_of_the_Rings Star_Wars Harry_Potter Game_of_Thrones Breaking_Bad The_Bear_(TV_series) Stranger_Things
Succession_(TV_series) The_Last_of_Us_(TV_series) Severance_(TV_series) Wednesday_(TV_series) Squid_Game Shōgun_(2024_miniseries)
The_Office_(American_TV_series) Friends Saturday_Night_Live The_Simpsons Doctor_Who Sherlock_(TV_series) Fallout_(American_TV_series)
Lionel_Messi Cristiano_Ronaldo Kylian_Mbappé Erling_Haaland Jude_Bellingham Mohamed_Salah Lamine_Yamal Vinícius_Júnior
LeBron_James Stephen_Curry Victor_Wembanyama Caitlin_Clark Patrick_Mahomes Travis_Kelce Shohei_Ohtani Aaron_Judge
Serena_Williams Novak_Djokovic Carlos_Alcaraz Jannik_Sinner Coco_Gauff Aryna_Sabalenka Roger_Federer Rafael_Nadal
Lewis_Hamilton Max_Verstappen Charles_Leclerc Lando_Norris Simone_Biles Usain_Bolt Michael_Phelps Tiger_Woods
Scottie_Scheffler Rory_McIlroy Connor_McDavid Wayne_Gretzky Virat_Kohli Sachin_Tendulkar Tom_Brady Michael_Jordan
FIFA_World_Cup UEFA_Champions_League Premier_League Super_Bowl Wimbledon_Championships Tour_de_France NBA_Finals
World_Series Formula_One Cricket_World_Cup Real_Madrid_CF FC_Barcelona Manchester_United_F.C. Liverpool_F.C. Arsenal_F.C.
Chelsea_F.C. Manchester_City_F.C. Bayern_Munich Paris_Saint-Germain_F.C. Inter_Miami_CF Los_Angeles_Lakers Golden_State_Warriors
Kansas_City_Chiefs Dallas_Cowboys New_York_Yankees Los_Angeles_Dodgers Boston_Celtics New_England_Patriots
Albert_Einstein Isaac_Newton Marie_Curie Charles_Darwin Nikola_Tesla Alan_Turing Ada_Lovelace Stephen_Hawking
Richard_Feynman Galileo_Galilei Niels_Bohr Rosalind_Franklin Carl_Sagan Jane_Goodall Leonardo_da_Vinci Michelangelo
Vincent_van_Gogh Pablo_Picasso Frida_Kahlo Claude_Monet Rembrandt Salvador_Dalí Andy_Warhol Johannes_Vermeer
William_Shakespeare Jane_Austen Charles_Dickens Leo_Tolstoy Fyodor_Dostoevsky Virginia_Woolf George_Orwell Franz_Kafka
Ernest_Hemingway Toni_Morrison Gabriel_García_Márquez J._R._R._Tolkien Agatha_Christie Mark_Twain Emily_Dickinson Sylvia_Plath
Napoleon Julius_Caesar Alexander_the_Great Genghis_Khan Cleopatra Queen_Victoria Elizabeth_I Henry_VIII Joan_of_Arc
Abraham_Lincoln George_Washington Winston_Churchill Mahatma_Gandhi Nelson_Mandela Martin_Luther_King_Jr. Marcus_Aurelius
Charlemagne Catherine_the_Great Peter_the_Great Ramesses_II Tutankhamun Hannibal Augustus Confucius Socrates Plato Aristotle
World_War_I World_War_II Cold_War Roman_Empire Byzantine_Empire Ottoman_Empire Mongol_Empire British_Empire French_Revolution
American_Civil_War Industrial_Revolution Renaissance Ancient_Egypt Ancient_Rome Ancient_Greece Vikings Black_Death
Titanic Apollo_11 Moon_landing Fall_of_the_Berlin_Wall Battle_of_Hastings D-Day Pompeii Great_Wall_of_China Machu_Picchu
Stonehenge Eiffel_Tower Colosseum Taj_Mahal Petra Angkor_Wat Acropolis_of_Athens Chichen_Itza Mount_Everest Grand_Canyon
Amazon_rainforest Sahara Antarctica Pacific_Ocean Niagara_Falls Yellowstone_National_Park Great_Barrier_Reef Mount_Fuji
Sun Moon Mars Jupiter Saturn Venus Mercury_(planet) Neptune Pluto Milky_Way Black_hole Big_Bang Solar_eclipse Aurora
Halley's_Comet James_Webb_Space_Telescope Hubble_Space_Telescope International_Space_Station SpaceX NASA Voyager_1
Artificial_intelligence ChatGPT Large_language_model Machine_learning Quantum_computing Bitcoin Ethereum Blockchain
Internet World_Wide_Web Python_(programming_language) Linux Wikipedia Google Apple_Inc. Microsoft Amazon_(company) Nvidia
Tesla,_Inc. Meta_Platforms Netflix OpenAI TikTok YouTube Instagram Reddit Elon_Musk Jeff_Bezos Bill_Gates Steve_Jobs
Mark_Zuckerberg Sam_Altman Jensen_Huang Warren_Buffett Taylor_Swift_masters_dispute iPhone PlayStation_5 Nintendo_Switch
Minecraft Fortnite Grand_Theft_Auto_VI The_Legend_of_Zelda Pokémon Super_Mario Elden_Ring Tetris Chess Go_(game)
COVID-19_pandemic Influenza Measles Climate_change Global_warming El_Niño Hurricane Tornado Earthquake Volcano Tsunami
DNA CRISPR Vaccine Penicillin Photosynthesis Evolution Dinosaur Tyrannosaurus Mammoth Shark Octopus Honey_bee Monarch_butterfly
Coffee Tea Chocolate Pizza Sushi Croissant Olive_oil Wine Beer Whisky Bread Rice Pasta Tomato Avocado Honey Cheese
Thanksgiving Christmas Halloween Diwali Ramadan Lunar_New_Year Hanukkah Easter Day_of_the_Dead Oktoberfest Carnival
United_States United_Kingdom France Germany Italy Spain Japan China India Brazil Canada Australia Mexico Russia Ukraine
Nigeria Egypt South_Africa Argentina Indonesia Turkey Greece Iceland Norway Sweden Ireland Poland Portugal New_Zealand
London Paris New_York_City Tokyo Rome Berlin Istanbul Cairo Mumbai Lagos Los_Angeles Chicago Sydney Toronto Mexico_City
Venice Florence Barcelona Amsterdam Prague Vienna Kyoto Singapore Hong_Kong Dubai Rio_de_Janeiro Buenos_Aires Athens
Olympic_Games Nobel_Prize Academy_Awards Grammy_Awards Eurovision_Song_Contest Met_Gala Cannes_Film_Festival Time_Person_of_the_Year
""".split()
_POOL = [t for t in dict.fromkeys(_POOL) if t not in STORY]


def _u(*key) -> float:
    """A uniform number in [0, 1) fixed by `key` (a cheap, stable hash: seeding a Random per value is too slow)."""
    x = zlib.crc32(repr(key).encode())
    x = ((x ^ (x >> 16)) * 0x45D9F3B) & 0xFFFFFFFF
    return ((x ^ (x >> 16)) & 0xFFFFFF) / 0x1000000


@functools.lru_cache(maxsize=None)
def _spike(t: str, d: int) -> tuple[int, float, int, float] | None:
    """A page's spike on day `d`, if it has one (25% of days): start hour, size (2.5-25 times usual views), hours to
    peak (1-4), fade (2-8 hours)."""
    r = random.Random(f"{t}|day{d}")
    if r.random() >= 0.25:
        return None
    return d * 24 + r.randrange(24), r.uniform(2.5, 25), r.randint(1, 4), r.uniform(2, 8)


def _bumps(t: str, h: int) -> float:
    """Multiplier from a page's own short spikes, the same in every run."""
    m = 1.0
    for d in range(h // 24 - 2, h // 24 + 1):
        sp = _spike(t, d)
        if sp and h >= sp[0]:
            start, mult, peak, fade = sp
            dt = h - start
            m += (mult - 1) * (dt / peak if dt < peak else math.exp(-(dt - peak) / fade))
    return m


def generate(now: int) -> None:
    """Write the 72 demo hours ending at hour `now` (hours since 1970) and drop older ones. Everything is seeded by page
    and hour, so an hour looks the same every time it is generated. Each hour's statistics (mean, standard deviation)
    describe the page's usual views over the previous 96 hours, without its spikes."""
    hours = list(range(now - 71, now + 1))
    story_hour = now - 4
    base = {t: v[0] for t, v in STORY.items()} | {t: random.Random(t).choice([40, 80, 150, 300, 600, 1200, 2500])
                                                  for t in BACKGROUND + _POOL}
    hist: dict[str, list[float]] = {t: [] for t in base}  # usual views, for the running 96-hour mean and deviation
    s1 = dict.fromkeys(base, 0.0)
    s2 = dict.fromkeys(base, 0.0)
    for h in range(hours[0] - 96, now + 1):
        recs = []
        daily = 1 + 0.35 * math.sin(2 * math.pi * ((h % 24) - 9) / 24)
        for t, b in base.items():
            v = usual = b * daily * (0.85 + 0.3 * _u(t, h))
            if t in STORY:
                if h >= story_hour:
                    mult, peak = STORY[t][1], STORY[t][2]
                    dt = h - story_hour
                    v *= 1 + (mult - 1) * (dt / peak if dt < peak else math.exp(-(dt - peak) / 10))
            elif t not in LINKS:
                v *= _bumps(t, h)
            if h >= hours[0]:
                mean = s1[t] / 96
                std = max(0.0, (s2[t] - 96 * mean * mean) / 95) ** 0.5 or 1.0
                recs.append(record(t, h, int(v), mean, std, True))
            hist[t].append(usual)
            s1[t] += usual
            s2[t] += usual * usual
            if len(hist[t]) > 96:
                old = hist[t][-97]
                s1[t] -= old
                s2[t] -= old * old
        if h < hours[0]:
            continue
        flat = [flatten(r, h) for r in recs]
        store.write_hourly("enwiki", h, flat, meta={"geo_data": {"period": {"start": "demo"}},
                                                   "associated_pages": {"period": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31T23:59:59Z"}}})
        trend = sorted([f for f in flat if f["trending_abs"] or f["trending_rel"]], key=lambda f: -(f["zscore"] or 0))
        store.write_trending("enwiki", h, trend)
    store.prune("enwiki", hours[0])
    for t, ls in LINKS.items():
        store.store_links("enwiki", t, ls)
    for t in _POOL:  # every other page links to a dozen others, so the page explorer has something to show
        store.store_links("enwiki", t, random.Random(f"links|{t}").sample(_POOL, 12))


_lock = threading.Lock()
_built: tuple[int, int] | None = None


def ensure_current() -> None:
    """Build the demo data in this process's database, or roll it forward when a new hour has started."""
    global _built
    want = (os.getpid(), int(time.time()) // 3600 - 2)
    if _built == want:
        return
    with _lock:
        if _built != want:
            generate(want[1])
            _built = want


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reset", action="store_true", help="delete everything in the database first")
    args = ap.parse_args()
    eng = store.engine()
    if args.reset:
        store.md.drop_all(eng)
        store.md.create_all(eng)
    now = int(time.time()) // 3600 - 2
    story_hour = now - 4
    generate(now)
    hours = range(now - 71, now + 1)
    print(f"demo data in {eng.url.render_as_string(hide_password=True)}: enwiki, {len(hours)} hours ending {datetime.fromtimestamp(now * 3600, timezone.utc):%Y-%m-%d %H}:00 UTC; "
          f"story breaks at {datetime.fromtimestamp(story_hour * 3600, timezone.utc):%H}:00")


if __name__ == "__main__":
    main()
