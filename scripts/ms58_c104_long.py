"""Observe both explicit correction settings on C104 for one hour each."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys

from checker_contract import sha256
from ms58_correction_runs import physical_cores, run_lane
from probe_runner import atomic_json


assert os.environ.get('OAR_JOB_ID') == '7013762'
assert datetime.now(timezone.utc) < datetime.fromisoformat('2026-11-10T06:25:00+00:00')
root = Path.home() / 'ms58-bpca'
prior = root / 'outputs/resume-20261110/correction'
out = root / 'resume-20261110/c104-long'
out.mkdir(exist_ok=False)
for name in ['rows', 'launch-logs']:
    (out / name).mkdir()
scripts = out / 'frozen-scripts'
shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns('__pycache__'))
shutil.copytree(prior / 'frozen-patches', out / 'frozen-patches')
for setting in [0, 1]:
    manifest = json.loads((prior / f'correcting-{setting}-manifest.json').read_bytes())
    manifest['source_patch_files'] = {str(out / 'frozen-patches' / Path(path).name): digest
                                      for path, digest in manifest['source_patch_files'].items()}
    manifest['purpose'] = 'Exploratory simultaneous C104 correction controls at 3600 seconds'
    atomic_json(out / f'correcting-{setting}-manifest.json', manifest)
cores = physical_cores()
assert len(cores) >= 12
base = next(lane for lane in json.loads((prior / 'schedule.json').read_bytes())['lanes']
            if lane['instance'] == 'C104')
lanes = [{**base, 'horizon_s': 3600, 'cpu': cores[10 + setting], 'order': [setting]}
         for setting in [0, 1]]
atomic_json(out / 'schedule.json', {'lanes': lanes, 'scheduled_count': 2,
    'created_utc': datetime.now(timezone.utc).isoformat(), 'oar_job_id': os.environ['OAR_JOB_ID'],
    'driver_sha256': sha256(Path(__file__)),
    'interpretation': 'One simultaneous observation per setting on distinct physical cores. This is a diagnostic of the correction effect, not a randomized paired timing estimate or a soundness certificate.'})
with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(lambda lane: run_lane(lane, root, out, scripts), lanes))
atomic_json(out / 'complete.json', {'rows': [row for group in results for row in group]})
