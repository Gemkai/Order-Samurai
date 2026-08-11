#!/usr/bin/env bash
# build_core_zip.sh — build the public order-samurai-core.zip from this pack.
#
# Base file list is `git ls-files` (tracked files only), which already keeps
# node_modules/, .git/, dashboard-ui/dist/, __pycache__/, and *.pyc out (see
# .gitignore) without us having to hand-maintain a duplicate exclude list.
# On top of that we drop internal-only material that should never ship
# publicly, and any Windows-only leftovers that don't belong in a Mac-first
# product pack.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

out_dir="$repo_root/dist"
out_zip="$out_dir/order-samurai-core.zip"
mkdir -p "$out_dir"
rm -f "$out_zip" "$out_zip.sha256"

# Exclusion patterns (grep -E, matched against each tracked path).
exclude_re='^docs/productization/|^docs/INTERNAL_STRATEGY_MONETIZATION\.md$|(^|/)__pycache__/|\.pyc$|\.ps1$|(^|/)\.env(\.|$)'

tmp_list="$(mktemp)"
trap 'rm -f "$tmp_list"' EXIT
git ls-files | grep -Ev "$exclude_re" > "$tmp_list"

zip -q -X "$out_zip" -@ < "$tmp_list"

# Standard `shasum -c` format ("<hash>  <filename>"), computed against the
# bare basename: install.sh downloads both files into the same temp dir and
# runs `shasum -a 256 -c order-samurai-core.zip.sha256` from there, so the
# recorded filename must match what lands on disk, not this build's absolute
# path. A hash-only sidecar (the prior form) fails that check outright with
# "no properly formatted SHA checksum lines found" on every real install.
(cd "$out_dir" && shasum -a 256 "$(basename "$out_zip")") > "$out_zip.sha256"

count="$(wc -l < "$tmp_list" | tr -d ' ')"
echo "Built $out_zip ($count files)" >&2
echo "SHA256: $(cat "$out_zip.sha256")" >&2
