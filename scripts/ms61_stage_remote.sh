#!/usr/bin/env bash
# Run on Kenzae: copy the ms61 final-campaign tools to nancy.g5k:~/ms61-bpc/final-scripts/ in ONE ssh call (tar over ssh).
# Layout on nancy mirrors the repository, so the analysis finds its helpers and the frozen reference manifest:
#   final-scripts/scripts/     runner, workers, build, plan generator, remote launcher, status
#   final-scripts/analysis/    ms61_final_analysis.py and the helpers it imports
#   final-scripts/references/dabia2013-n100-v011-2026-11-09/manifest.json
#   final-scripts/plans/       the plan files given as extra arguments (e.g. configs/ms61/final.json)
# Usage: bash scripts/ms61_stage_remote.sh [PLAN.json ...]
# Prints the remote sha256 of every staged file, to compare with the local list it prints first.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
files=(scripts/ms61_run_cells.py scripts/ms61_kayros_worker.py scripts/ms61_lera_worker.py scripts/ms61_build.sh
       scripts/ms61_tree_hash.sh scripts/checker_contract.py scripts/ms61_final_plan.py scripts/ms61_remote_launch.sh
       scripts/ms61_status.sh analysis/ms61_final_analysis.py analysis/ms61_summary.py analysis/ms58_dabia_plots.py
       analysis/plot_style.py references/dabia2013-n100-v011-2026-11-09/manifest.json configs/ms61/final-duration-hints.json)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
tar cf - "${files[@]}" | tar xf - -C "$stage"
if (( $# )); then
    mkdir -p "$stage/plans"
    for plan in "$@"; do cp "$plan" "$stage/plans/"; done
fi
(cd "$stage" && find . -type f | sort | xargs sha256sum) > "$stage.local.sha256"
echo "local:"; sed 's/^/  /' "$stage.local.sha256"
read -r -a ssh_cmd <<<"${MS61_SSH:-ssh -S none -o ConnectTimeout=45 -o ServerAliveInterval=20 nancy.g5k}"
tar czf - -C "$stage" . | timeout 300 "${ssh_cmd[@]}" '
set -e
mkdir -p ~/ms61-bpc/final-scripts
tar xzf - -C ~/ms61-bpc/final-scripts
chmod +x ~/ms61-bpc/final-scripts/scripts/*.sh
cd ~/ms61-bpc/final-scripts && find . -type f ! -path "*/__pycache__/*" | sort | xargs sha256sum' > "$stage.remote.sha256"
if missing=$(comm -23 <(sort "$stage.local.sha256") <(sort "$stage.remote.sha256")) && [[ -z "$missing" ]]; then
    echo "staged and verified: $(wc -l < "$stage.local.sha256") files in nancy:~/ms61-bpc/final-scripts"
else
    echo "MISMATCH after staging:"; echo "$missing"; exit 1
fi
rm -f "$stage.local.sha256" "$stage.remote.sha256"
