# SPDX-License-Identifier: GPL-2.0-or-later
"""The passphrase gate, the GitHub deploy webhook and demo mode."""
import hashlib
import hmac
import json
import subprocess
import time

from wiki_trends import deploy, links as linkmod, store

from test_api import api  # noqa: F401  (fixture)

REAL_OUTLINKS = linkmod.outlinks  # the api fixture replaces it


def test_gate_off_by_default(api):
    assert api.get("/v1/projects").status_code == 200


def test_gate_cookie_and_header(api, monkeypatch):
    monkeypatch.setenv("WIKI_TRENDS_PASSPHRASE", "odyssey")
    assert api.get("/v1/projects").status_code == 401
    assert api.get("/v1/health").status_code == 200
    r = api.get("/page.html?title=X")
    assert r.status_code == 302 and r.headers["Location"].startswith("/login?next=/page.html")
    assert api.get("/login").status_code == 200
    assert api.post("/login", data={"passphrase": "wrong"}).status_code == 401
    r = api.post("/login", data={"passphrase": " odyssey ", "next": "//evil.example"})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert api.get("/v1/projects").status_code == 200  # cookie set
    api.delete_cookie("wiki_trends_pass")
    assert api.get("/v1/projects", headers={"X-Wiki-Trends-Passphrase": "odyssey"}).status_code == 200
    monkeypatch.setenv("WIKI_TRENDS_PASSPHRASE", "changed")  # a new passphrase signs everyone out
    assert api.get("/v1/projects").status_code == 401


def _sig(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook(api, monkeypatch):
    calls = []
    monkeypatch.setattr(deploy, "update", lambda: calls.append(1))
    body = json.dumps({"ref": "refs/heads/main", "after": "abcdef123456"}).encode()
    assert api.post("/hooks/github", data=body).status_code == 404  # not configured
    monkeypatch.setenv("WIKI_TRENDS_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("WIKI_TRENDS_PASSPHRASE", "odyssey")  # the gate does not block the webhook
    h = {"Content-Type": "application/json", "X-GitHub-Event": "push"}
    assert api.post("/hooks/github", data=body, headers={**h, "X-Hub-Signature-256": _sig("nope", body)}).status_code == 403
    r = api.post("/hooks/github", data=body, headers={**h, "X-Hub-Signature-256": _sig("s3cret", body)})
    assert r.status_code == 202 and r.get_json()["deploying"] == "abcdef12"
    other = json.dumps({"ref": "refs/heads/feature"}).encode()
    r = api.post("/hooks/github", data=other, headers={**h, "X-Hub-Signature-256": _sig("s3cret", other)})
    assert r.status_code == 202 and "ignored" in r.get_json()
    ping = api.post("/hooks/github", data=b"{}", headers={**h, "X-GitHub-Event": "ping", "X-Hub-Signature-256": _sig("s3cret", b"{}")})
    assert ping.get_json()["pong"]
    time.sleep(0.1)
    assert calls == [1]


def test_update_pulls_and_reloads(tmp_path, monkeypatch):
    def git(cwd, *a):
        subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)
    origin, clone, work = tmp_path / "origin.git", tmp_path / "clone", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    for d in (work,):
        git(d, "config", "user.email", "t@example.org"); git(d, "config", "user.name", "t")
    (work / "a").write_text("1"); git(work, "add", "a"); git(work, "commit", "-qm", "one"); git(work, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    reload = tmp_path / "reload"
    monkeypatch.setattr(deploy, "REPO", clone)
    monkeypatch.setenv("WIKI_TRENDS_RELOAD_FILE", str(reload))
    monkeypatch.setenv("WIKI_TRENDS_DEPLOY_LOG", str(tmp_path / "deploy.log"))
    assert deploy.update(only_if_behind=True) == "up to date" and not reload.exists()
    (work / "a").write_text("2"); git(work, "commit", "-qam", "two"); git(work, "push", "-q", "origin", "HEAD:main")
    assert deploy.update(only_if_behind=True).startswith("updated") and reload.exists()
    assert (clone / "a").read_text() == "2"


def test_demo_mode(api, monkeypatch):
    monkeypatch.setenv("WIKI_TRENDS_DEMO", "1")
    assert api.get("/v1/projects").get_json()["demo"] is True
    assert api.get("/v1/trending/enwiki?hour=2020-01-01T00").status_code == 404  # never asks WME
    monkeypatch.setattr(linkmod.requests, "Session", lambda: (_ for _ in ()).throw(AssertionError("live fetch")))
    assert REAL_OUTLINKS("enwiki", "Page_with_no_demo_links") == []
    store.store_links("enwiki", "The_Odyssey", ["Homer"])
    assert REAL_OUTLINKS("enwiki", "The_Odyssey") == ["Homer"]


def test_demo_builds_and_rolls_forward(tmp_path, monkeypatch):
    from wiki_trends import app as appmod, demo
    monkeypatch.setenv("WIKI_TRENDS_DEMO", "1")
    monkeypatch.setattr(linkmod.requests, "Session", lambda: (_ for _ in ()).throw(AssertionError("live fetch")))
    store.engine(f"sqlite:///{tmp_path}/demo.sqlite3")
    monkeypatch.setattr(demo, "_built", None)
    t0 = 1_794_000_000
    monkeypatch.setattr(demo.time, "time", lambda: t0)
    c = appmod.app.test_client()
    assert c.get("/v1/trending/enwiki?k=50").get_json()["items"]
    peak = time.strftime("%Y-%m-%dT%H", time.gmtime(t0 - 8 * 3600))  # the story breaks 8 hours before the last hour
    r = c.get(f"/v1/trending/enwiki?k=50&hour={peak}").get_json()
    assert "The_Odyssey" in [i["title"] for i in r["items"]]
    assert c.get("/v1/pages/enwiki/The_Odyssey").status_code == 200
    first = store.loaded_hours("enwiki", "batch")
    assert len(first) == 72 and first[-1] == t0 // 3600 - 2
    monkeypatch.setattr(demo.time, "time", lambda: t0 + 3 * 3600)  # three hours later: rolled forward, still 72 hours
    c.get("/v1/projects")
    later = store.loaded_hours("enwiki", "batch")
    assert len(later) == 72 and later[-1] == first[-1] + 3
