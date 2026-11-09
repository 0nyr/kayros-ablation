"""Inventory a pinned native upstream build for the common probe launcher."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
from checker_contract import checker_identity, sha256


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--patch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cplex-library',type=Path,required=True)
    args=parser.parse_args()
    source=args.source_root.resolve();binary=args.binary.resolve()
    runtime={str(binary):sha256(binary)}
    runtime.update({str(p.resolve()):sha256(p) for p in (source/'instances/dabia_et_al_2013').glob('*.json') if p.name not in ('index.json','solutions.json')})
    runtime[str((source/'experiments/bp.json').resolve())]=sha256(source/'experiments/bp.json')
    linked={}
    for filename in re.findall(r'(/[^\s()]+)',subprocess.check_output(['ldd',str(binary)],text=True)):
        p=Path(filename)
        if p.is_file():linked[str(p.resolve())]=sha256(p)
    runtime.update(linked)
    source_files={str(p.relative_to(source)):sha256(p) for p in sorted((source/'code').rglob('*')) if p.is_file()}
    source_digest=hashlib.sha256(json.dumps(source_files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    report={'schema_version':1,'runtime_kind':'native','source_commit':'cabc56ad9cdb22bed81a8a31f1671d40152055e6',
            'checker':checker_identity(),'source_tree_sha256':source_digest,'source_files':source_files,
            'runtime_files':runtime,'dynamic_libraries':linked,
            'source_patch_files':{str(args.patch.resolve()):sha256(args.patch)},
            'cplex_static_library':{'path':str(args.cplex_library.resolve()),'sha256':sha256(args.cplex_library)},
            'worker_environment':{'UPSTREAM_PROBE_BINARY':str(binary),'UPSTREAM_SOURCE_ROOT':str(source)},
            'python':platform.python_version(),
            'compiler':subprocess.check_output(['g++','--version'],text=True).splitlines()[0]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
