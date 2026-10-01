#!/usr/bin/env bash
# Project Lantern — one-command demo.
#
#   ./demo.sh
#
# Builds the index if lantern.db is missing, then runs five showcase
# searches: a normal query, an exact-phrase query, a site:-scoped query,
# a misspelled query (spellcheck), and an exclusion query.
# Always exits 0.

set -u
cd "$(dirname "$0")"

echo "================================================================"
echo " Project Lantern — demo"
echo "================================================================="

if [ ! -f lantern.db ]; then
    echo "[demo] lantern.db not found — running the build pipeline first."
    python3 -m lantern.cli build
else
    echo "[demo] lantern.db found — skipping build."
fi

showcase() {
    local label="$1"; shift
    echo
    echo "----------------------------------------------------------------"
    echo " $label"
    echo "----------------------------------------------------------------"
    python3 -m lantern.cli search "$1" --n 5
}

showcase "1. Normal query"            "black hole"
showcase "2. Exact phrase"           '"sourdough hydration"'
showcase "3. Site-scoped query"      "site:cooking sourdough"
showcase "4. Misspelled query (did-you-mean)" "pythn list comprehensions"
showcase "5. Exclusion query"        "python -django"

echo
echo "================================================================="
echo " Demo complete. Try the web UI:  python3 -m lantern.cli serve"
echo "================================================================="
exit 0
