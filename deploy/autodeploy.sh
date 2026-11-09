#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Fallback self-update for Toolforge, as a scheduled job (see README): pull main if it has new commits and reload the
# web service. Does nothing when already up to date. The GitHub webhook (/hooks/github) does the same thing on push.
set -euo pipefail
cd "$(dirname "$0")/../api"
export WIKI_TRENDS_RELOAD_FILE="${WIKI_TRENDS_RELOAD_FILE:-$HOME/www/python/reload}"
exec "${WIKI_TRENDS_VENV:-$HOME/www/python/venv}/bin/python" -m wiki_trends.deploy
