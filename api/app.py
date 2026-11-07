"""Toolforge entry point: the python webservice (uWSGI) serves `app` from $HOME/www/python/src/app.py."""

from wiki_trends.app import create_app

app = create_app()
