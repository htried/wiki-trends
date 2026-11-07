# wiki-trends

What is trending on Wikipedia right now, hour by hour, and what is moving with it. Built on the Wikimedia Enterprise
pageviews API (beta) for the seven Wikipedias of its initial release (de, en, es, fr, it, nl, pt).

- **Trending** (homepage): the top trending articles of a Wikipedia for any loaded UTC hour, with their last 72 hours.
- **Page explorer**: an anchor article, its *entity* (the top 10 / 50 / 100 most-viewed pages it links to or that send it
  readers, summed), each associated page against its own recent norm and against the anchor's clickstream history
  (which pages are unusually hot relative to how readers normally arrive), and where readers are (daily, by country).

## Layout

```
api/        Flask API + hourly ingest (Python 3.11)
  wiki_trends/wme.py      Wikimedia Enterprise client (login/refresh, trending, hourly batch download)
  wiki_trends/ingest.py   hourly job: top-1000 trending list + the full batch of every article, per project
  wiki_trends/store.py    SQLAlchemy Core: SQLite locally, ToolsDB (MariaDB) on Toolforge
  wiki_trends/app.py      the API (/v1/...)
  wiki_trends/demo.py     synthetic data for local frontend work
frontend/   static site (no build step); talks only to the API
```

The frontend and the API are separate: the frontend only calls `/v1`, so it can be hosted anywhere (set the API's URL in
`frontend/assets/config.js`), and anything else (a Bluesky bot, a notebook) can use the same endpoints.

## API

| endpoint | returns |
|---|---|
| `GET /v1/projects` | the projects served and the hours loaded for each |
| `GET /v1/trending/{project}?hour=2026-11-16T15&k=50&spark=72` | top-k trending pages for an hour (default: latest loaded), each with hourly views over the last `spark` hours |
| `GET /v1/pages/{project}/{title}?hour=…&hours=72&k=10` | anchor page: hourly views, entity (top-k associated pages summed, each with its norm and clickstream comparison), geography |
| `GET /v1/health` | `{"ok": true}` |

Responses are JSON, cacheable, and allow any origin. Hours are UTC. Missing hourly values (`null`) mean the page was below
the batch's threshold that hour.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt pytest
cd api
../.venv/bin/python -m pytest -q
# synthetic data, API and frontend on http://localhost:8790
DATABASE_URL=sqlite:///demo.sqlite3 WIKI_TRENDS_PROJECTS=enwiki ../.venv/bin/python -m wiki_trends.demo
DATABASE_URL=sqlite:///demo.sqlite3 WIKI_TRENDS_PROJECTS=enwiki WIKI_TRENDS_FRONTEND=$PWD/../frontend ../.venv/bin/flask --app wiki_trends.app run --port 8790
# real data (needs a Wikimedia Enterprise account)
WME_USERNAME=… WME_PASSWORD=… ../.venv/bin/python -m wiki_trends.ingest --projects enwiki --hours-back 6
```

## Deploy on Toolforge

One tool can serve both the API and the frontend (`WIKI_TRENDS_FRONTEND`); split them into two tools later if needed.

```bash
ssh login.toolforge.org && become wiki-trends
git clone <this repo> ~/wiki-trends
mkdir -p ~/www/python && ln -s ~/wiki-trends/api ~/www/python/src
webservice python3.11 shell -- sh -c 'python3 -m venv ~/www/python/venv && ~/www/python/venv/bin/pip install -r ~/www/python/src/requirements.txt'

# secrets and settings (never in the repo); ToolsDB credentials are in ~/replica.my.cnf
toolforge envvars create WME_USERNAME '…'
toolforge envvars create WME_PASSWORD '…'
toolforge envvars create DATABASE_URL 'mysql+pymysql://<user>:<password>@tools.db.svc.wikimedia.cloud/<user>__wikitrends?charset=utf8mb4'
toolforge envvars create WIKI_TRENDS_FRONTEND /data/project/wiki-trends/wiki-trends/frontend
toolforge envvars create WIKI_TRENDS_CONTACT 'https://wiki-trends.toolforge.org'

# the hourly ingest, and the web service
toolforge jobs run ingest --image python3.11 --schedule '20 * * * *' \
  --command 'cd ~/wiki-trends/api && ~/www/python/venv/bin/python -m wiki_trends.ingest'
webservice python3.11 start
```

Create the database once (`sql tools`, then `CREATE DATABASE <user>__wikitrends;`); tables are created on first start.
Storage: each project's full hourly batch is tens of thousands of rows (English: ~38,000), so seven projects at the
default 7-day retention are on the order of 15–20 million rows; lower `WIKI_TRENDS_RETENTION_HOURS` if ToolsDB space is
tight. Wikimedia Enterprise publishes an hour a few hours after it ends; the job picks up each hour on the first run after
it appears.

## Privacy and data

The frontend loads scripts only from this tool and Toolforge's CDN mirror (`tools-static.wmflabs.org`), and calls the
Wikipedia API only for title suggestions. The world map is Natural Earth (public domain). Pageview data comes from the
Wikimedia Enterprise pageviews API; check its terms before making the site public.
