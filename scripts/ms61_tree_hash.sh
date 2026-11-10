#!/usr/bin/env bash
# Git-free content hash of the cpp/ and src/ trees of a Kayros source directory.
# Hash = sha256 of the sorted "sha256sum" listing (relative paths, C locale, __pycache__ excluded).
# The same command runs locally (ms61_sync_source.sh) and on the node (ms61_build.sh), so the two values are comparable.
set -euo pipefail
src=${1:?usage: ms61_tree_hash.sh SOURCE_DIR}
cd "$src"
find cpp src -type f ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1
