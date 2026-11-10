#!/usr/bin/env bash
# ms61_status.sh RUN: status of an ms61 run in one ssh call from Kenzae (or run it directly on the nancy frontend).
# RUN is a name under ~/ms61-bpc/runs/ or an absolute remote path.
# Prints: cells finished by status against the plan, unfinished claims per host, held cells, runner liveness per host
# (pgrep through oarsh when the job is still running, otherwise the age of the runner log), the last runner log lines
# and the tail of the latest launch report. Read-only.
# Environment on Kenzae: MS61_SSH (default "ssh -S none -o ConnectTimeout=45 -o ServerAliveInterval=20 nancy.g5k"),
# MS61_STATUS_TAIL (log lines per host, default 2), MS61_STATUS_PGREP=0 to skip the oarsh liveness probe.
set -euo pipefail
run=${1:?usage: ms61_status.sh RUN}
if ! command -v oarstat >/dev/null 2>&1; then
    read -r -a ssh_cmd <<<"${MS61_SSH:-ssh -S none -o ConnectTimeout=45 -o ServerAliveInterval=20 nancy.g5k}"
    exec timeout 300 "${ssh_cmd[@]}" "MS61_STATUS_TAIL=${MS61_STATUS_TAIL:-2} MS61_STATUS_PGREP=${MS61_STATUS_PGREP:-1} bash -s -- '$run'" <"${BASH_SOURCE[0]}"
fi
out=$run
[[ "$out" = /* ]] || out="$HOME/ms61-bpc/runs/$out"
[[ -d "$out/cells" ]] || { echo "No run at $out"; exit 2; }
echo "== $out at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 - "$out" <<'PY'
import json, sys, time
from collections import Counter, defaultdict
from pathlib import Path
out = Path(sys.argv[1])
raw = json.loads((out / 'plan.json').read_bytes())
cells = raw if isinstance(raw, list) else raw['cells']
status, by_arm, unfinished, missing = Counter(), defaultdict(Counter), defaultdict(list), 0
closures = Counter()
for cell in cells:
    d = out / 'cells' / cell['id']
    arm = cell.get('arm', '?')
    if (d / 'cell.json').exists():
        try:
            r = json.loads((d / 'cell.json').read_bytes())
        except ValueError:
            status['unreadable'] += 1
            continue
        status[r.get('status')] += 1
        by_arm[arm][r.get('status')] += 1
        if r.get('closure_s') is not None and r.get('status') == 'completed':
            closures[arm] += 1
    elif d.exists():
        owner = {}
        try:
            owner = json.loads((d / 'claim.json').read_bytes())
        except (OSError, ValueError):
            pass
        unfinished[owner.get('host', 'unclaimed')].append(cell['id'])
    else:
        missing += 1
total = len(cells)
done = sum(status.values())
print(f"plan {total} cells: {done} finished {dict(status)}, {sum(map(len, unfinished.values()))} running or stale, {missing} not started")
for arm in sorted(by_arm):
    print(f"  {arm:<24} {dict(by_arm[arm])}  closures {closures[arm]}")
for host in sorted(unfinished):
    ids = unfinished[host]
    print(f"  claimed by {host}: {len(ids)} ({', '.join(ids[:4])}{' ...' if len(ids) > 4 else ''})")
held = set()
for f in (out / 'runners').glob('*.done.json'):
    try:
        held |= set(json.loads(f.read_bytes()).get('held_for_resume') or [])
    except ValueError:
        pass
held = {c for c in held if not (out / 'cells' / c / 'cell.json').exists()}
if held:
    print(f"  held for a later resume (launch window closed): {len(held)}")
PY
echo
echo "== runners"
job=$(python3 -c 'import json,sys,glob; js=[json.load(open(f)).get("oar_job_id") for f in glob.glob(sys.argv[1]+"/runners/*.json") if not f.endswith(".done.json")]; js=[j for j in js if j]; print(max(js, key=int) if js else "")' "$out")
state=""
[[ -n "$job" ]] && state=$(oarstat -s -j "$job" 2>/dev/null | sed 's/.*: *//')
echo "latest job $job: ${state:-unknown}"
now=$(date +%s)
for log in "$out"/runners/*.log; do
    [[ -e "$log" ]] || continue
    host=$(basename "$log" .log)
    [[ "$host" == launch-* ]] && continue
    age=$(( now - $(stat -c %Y "$log") ))
    live="log age ${age}s"
    [[ -e "$out/runners/$host.done.json" ]] && live="done ($(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["finished_utc"][11:19], "ran", d["ran"], d["status_counts"])' "$out/runners/$host.done.json"))"
    if [[ "$state" == Running && "${MS61_STATUS_PGREP:-1}" == 1 && ! -e "$out/runners/$host.done.json" ]]; then
        n=$(OAR_JOB_ID=$job timeout 40 oarsh "$host" "pgrep -fc '[m]s61_run_cells.py --plan $out/plan.json'" </dev/null 2>/dev/null || true)
        live="$live, runner processes: ${n:-?}"
    fi
    echo "-- $host: $live"
    tail -n "${MS61_STATUS_TAIL:-2}" "$log" | cut -c1-160 | sed 's/^/   /'
done
latest=$(ls -t "$out"/runners/launch-*.log 2>/dev/null | head -1 || true)
if [[ -n "$latest" ]]; then
    echo
    echo "== latest launch report $(basename "$latest")"
    tail -n 6 "$latest"
fi
