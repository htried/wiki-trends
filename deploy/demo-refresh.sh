#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Regenerate the synthetic demo data so its 72 hours end near now (Toolforge scheduled job, daily; see README).
set -euo pipefail
cd "$(dirname "$0")/../api"
export WIKI_TRENDS_PROJECTS="${WIKI_TRENDS_PROJECTS:-enwiki}"
exec "${WIKI_TRENDS_VENV:-$HOME/www/python/venv}/bin/python" -m wiki_trends.demo --reset
