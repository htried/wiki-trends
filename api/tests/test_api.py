# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import io
import json
import tarfile

import pytest

from wiki_trends import app as appmod
from wiki_trends import links as linkmod
from wiki_trends import store
from wiki_trends.ingest import ingest_hour
from wiki_trends.records import flatten, key
from wiki_trends.wme import NotAvailable, records_from_archive

H = 494_391  # an hour index (hours since 1970-01-01 UTC)

# the Pageviews record from the WME README, adapted to English titles
README_RECORD = {
    "name": "Deutschland", "identifier": 1497705,
    "is_part_of": {"identifier": "enwiki"}, "namespace": {"identifier": 0},
    "url": "https://en.wikipedia.org/wiki/Deutschland",
    "main_entity": {"identifier": "Q9482", "url": "https://www.wikidata.org/entity/Q9482"},
    "views": [{
        "comment": "views during the hour", "period": {"start": "2026-11-16T15:00:00Z", "end": "2026-11-16T15:59:59Z"},
        "count": 468, "project_proportion": 0.0011460083159064974,
        "statistics": [{"variable": "count", "values": {"mean": 470.28735632183907, "stddev": 361.0001902996979,
                                                        "ratio": 0.9951362580960528, "zscore": -0.0063361637564238816}}],
        "geo_distributions": [{"country_code": "AT", "proportion": 0.049}], "geo_distributions_total": 6840,
        "source_pages": [{"name": "Deutsche_Sprache", "proportion": 0.1507}], "source_pages_total": 39687,
    }],
    "signals": {"is_new_page": False, "is_trending_absolute": False, "is_trending_project_proportion": True},
}


def rec(name, count, z=0.0, mean=None, sources=(), geo=(), trending=False, hour=H):
    r = copy.deepcopy(README_RECORD)
    r["name"], r["identifier"] = name, abs(hash(name)) % 10**7
    v = r["views"][0]
    from datetime import datetime, timezone
    t = datetime.fromtimestamp(hour * 3600, timezone.utc)
    v["period"] = {"start": t.strftime("%Y-%m-%dT%H:00:00Z"), "end": t.strftime("%Y-%m-%dT%H:59:59Z")}
    v["count"] = count
    v["statistics"][0]["values"] = {"mean": mean if mean is not None else count, "stddev": 10.0, "ratio": 1.0, "zscore": z}
    v["source_pages"] = [{"name": s, "proportion": p} for s, p in sources]
    v["geo_distributions"] = [{"country_code": c, "proportion": p} for c, p in geo]
    r["signals"] = {"is_new_page": False, "is_trending_absolute": trending, "is_trending_project_proportion": False}
    return r


def test_flatten_readme_record():
    f = flatten(README_RECORD)
    assert f["title"] == "Deutschland" and f["page_id"] == 1497705 and f["qid"] == "Q9482"
    assert f["count"] == 468 and round(f["zscore"], 4) == -0.0063 and f["trending_rel"] and not f["trending_abs"]
    assert f["geo"] == [{"country": "AT", "proportion": 0.049}] and f["sources"] == [{"title": "Deutsche_Sprache", "proportion": 0.1507}]
    assert key("the odyssey") == "The_odyssey"


def test_archive_ndjson_and_array():
    for payload in ("\n".join(json.dumps(r) for r in [README_RECORD, README_RECORD]), json.dumps([README_RECORD, README_RECORD])):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = payload.encode()
            ti = tarfile.TarInfo("enwiki_namespace_0_0.ndjson")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
        buf.seek(0)
        assert [r["name"] for r in records_from_archive(buf)] == ["Deutschland", "Deutschland"]


class FakeWME:
    def __init__(self, hours: dict[int, list[dict]]):
        self.hours = hours

    def trending(self, project, hour, k=1000):
        if hour not in self.hours:
            raise NotAvailable("no such hour")
        rs = [r for r in self.hours[hour] if r["signals"]["is_trending_absolute"]]
        return sorted(rs, key=lambda r: -r["views"][0]["statistics"][0]["values"]["zscore"])[:k]

    def batch_info(self, project, hour):
        if hour not in self.hours:
            raise NotAvailable("no such hour")
        return {"count": len(self.hours[hour]), "geo_data": {"period": {"start": "2026-11-14T00:00:00Z"}},
                "associated_pages": {"period": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31T23:59:59Z"}}}

    def batch_records(self, project, hour):
        if hour not in self.hours:
            raise NotAvailable("no such hour")
        yield from self.hours[hour]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    store.engine(f"sqlite:///{tmp_path}/t.sqlite3")
    monkeypatch.setenv("WIKI_TRENDS_PROJECTS", "enwiki")
    hours = {}
    for h in range(H - 5, H + 1):
        spike = h == H
        hours[h] = [
            rec("The_Odyssey", 5000 if spike else 300, z=9 if spike else 0, mean=300, trending=spike, hour=h,
                sources=[("Matt_Damon", 0.05), ("Homer", 0.30), ("Iliad", 0.20)], geo=[("US", 0.6), ("GB", 0.2)]),
            rec("Matt_Damon", 4000 if spike else 400, z=6 if spike else 0, mean=400, trending=spike, hour=h, geo=[("US", 0.9)]),
            rec("Homer", 900, z=0.1, mean=880, hour=h, geo=[("GR", 0.5), ("US", 0.5)]),
            rec("Iliad", 500, z=0.0, mean=500, hour=h),
            rec("Unrelated", 70, hour=h),
        ]
    fake = FakeWME(hours)
    for h in hours:
        ingest_hour(fake, "enwiki", h)
    monkeypatch.setattr(linkmod, "outlinks", lambda project, title, **kw: ["Homer", "Iliad", "Odysseus"])
    monkeypatch.setattr(appmod, "_client", fake)
    return appmod.app.test_client()


def test_trending_latest_hour(api):
    r = api.get("/v1/trending/enwiki?k=10&spark=6").get_json()
    assert r["hour"].startswith("2026") or r["hour"]
    titles = [i["title"] for i in r["items"]]
    assert titles == ["The_Odyssey", "Matt_Damon"]
    assert r["items"][0]["spark"] == [300, 300, 300, 300, 300, 5000] and r["items"][0]["signals"]["absolute"]


def test_trending_unknown_hour_is_404(api):
    assert api.get("/v1/trending/enwiki?hour=2020-01-01T00").status_code == 404
    assert api.get("/v1/trending/xxwiki").status_code == 404
    assert api.get("/v1/trending/enwiki?hour=nonsense").status_code == 400


def test_page_entity_clickstream_and_geo(api):
    r = api.get("/v1/pages/enwiki/The_Odyssey?k=3&hours=6").get_json()
    assert r["page"]["views"] == 5000 and r["page"]["spark"][-1] == 5000
    ms = {m["title"]: m for m in r["entity"]["members"]}
    # associated pages: links (Homer, Iliad; Odysseus has no views) and clickstream sources (Matt_Damon), by views
    assert list(ms) == ["Matt_Damon", "Homer", "Iliad"]
    assert r["entity"]["views"] == 4000 + 900 + 500 and r["entity"]["spark"][0] == 400 + 900 + 500
    assert ms["Matt_Damon"]["unusual"] and not ms["Homer"]["unusual"] and not ms["Matt_Damon"]["linked"]
    # Matt_Damon: 5% of incoming clicks historically, 4000/5400 of the source pages' views now
    md = ms["Matt_Damon"]
    assert md["clickstream_share"] == 0.05 and abs(md["current_source_share"] - 4000 / 5400) < 1e-9
    assert md["clickstream_deviation"] > 5 and ms["Homer"]["clickstream_deviation"] < 1
    geo = {g["country"]: g["proportion"] for g in r["geo"]["associated"]}
    assert abs(sum(geo.values()) - 1) < 1e-9 and max(geo, key=geo.get) == "US"
    assert r["geo"]["page"][0] == {"country": "US", "proportion": 0.6}


def test_page_not_loaded_hour(api):
    assert api.get("/v1/pages/enwiki/The_Odyssey?hour=2020-01-01T00").status_code == 404


def test_cors_and_projects(api):
    r = api.get("/v1/projects")
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    p = r.get_json()["projects"][0]
    assert p["project"] == "enwiki" and p["batch_hours"]["count"] == 6
