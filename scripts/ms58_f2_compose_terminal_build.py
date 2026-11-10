"""Reserved-node build and synthetic gates only for the f2 capture derivative."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tarfile

SOURCE = '53b93a31f2eccb81a1390ee4f7a4ec09afb805b4'
CONTROL = 'f2c9e205bd1ecc9afb45d5efe7fcb0df881f2b33'
INVENTORY_SHA = '0af3d68cb983eb6ff3c847c0c2bda346e907184e38b9800520fc999c11ca1bf3'
CPLEX_SHA = 'd2657ed57b8a34b1b5926f80c060f87da4caa4df49b6029aa0551d1c0b6ce179'
CHECKER = {'package':'mamut-routing-lib','version':'0.11.0','commit':'425dff351fdd9522ea6bb8c537dd416dfcd19614',
           'python_files':23,'installed_python_tree_sha256':'e22c65ea7b13c342653b947b6066b78b45313e517ad8ac3af09efaaa66e91334'}


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write_new(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def verify(source,files):
    observed={p.relative_to(source).as_posix():sha(p) for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if observed!=files:raise ValueError('Extracted derivative/reference source differs from its inventory')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--jobs',type=int,default=4)
    args=parser.parse_args()
    nodefile=Path(os.environ.get('OAR_NODEFILE','/missing'))
    host=socket.gethostname().split('.')[0]
    if (os.environ.get('OAR_JOB_ID')!=args.job_id or not host.startswith(('gros-','grvingt-','grappe-','grdix-'))
            or not nodefile.is_file() or host not in {n.split('.')[0] for n in nodefile.read_text().split()}):
        raise ValueError('Require a declared real reserved compute node')
    if not 1<=args.jobs<=4:raise ValueError('Use 1 through 4 build workers')
    if any(k.startswith(('KAYROS_','UPSTREAM_','PROBE_','MS58_DIAGNOSTIC_')) for k in os.environ):
        raise ValueError('Inherited algorithm/diagnostic overrides are forbidden during build')
    if any(os.environ.get(k) for k in ('CFLAGS','CXXFLAGS','CPPFLAGS','LDFLAGS')):
        raise ValueError('Inherited compiler/linker overrides are forbidden')
    bundle=args.bundle.resolve();identity_path=bundle/'source-identity.json'
    if sha(identity_path)!=INVENTORY_SHA:raise ValueError('Source inventory pin mismatch')
    identity=json.loads(identity_path.read_bytes())
    if identity['source_id']!=SOURCE or identity['control_source_id']!=CONTROL:raise ValueError('Source identity mismatch')
    for name,expected in identity['bundle_source_files'].items():
        if sha(bundle/name)!=expected:raise ValueError('Bundle source/patch changed: '+name)
    compiler=Path(shutil.which('g++')).resolve()
    version=subprocess.check_output([str(compiler),'--version'],text=True).splitlines()[0]
    if version!='g++ (Debian 14.2.0-19) 14.2.0':raise ValueError('Use the fixed f2 compiler for reserved builds')
    cplex=Path(os.environ['CPLEX_BIN']).resolve();header=Path(os.environ['CPLEX_INCLUDE'])/'ilcplex/cplex.h'
    boost=Path(os.environ['BOOST_BIN']).resolve()
    if sha(cplex)!=CPLEX_SHA:raise ValueError('CPLEX static library differs from the fixed control')
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    source=output/'source';source.mkdir()
    with tarfile.open(bundle/'source.tar.gz') as tar:tar.extractall(source,filter='data')
    verify(source,identity['files'])
    verify(bundle/'reference-goc',identity['reference_math_files'])
    record=dict(source_id=SOURCE,control_source_id=CONTROL,diagnostic_only=True,accepted_closure=False,
        source_inventory_sha256=INVENTORY_SHA,build_driver_sha256=sha(Path(__file__)),host=socket.gethostname(),
        oar_job_id=args.job_id,compiler=version,compiler_sha256=sha(compiler),
        dependencies={str(p):sha(p) for p in (cplex,header,boost)},commands=[],build_passed=False,
        native_gate_passed=False,routing_runs=0,created_utc=datetime.now(timezone.utc).isoformat())
    write_new(output/'build-started.json',record)
    environment=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',CC='gcc',CXX=str(compiler))
    build=output/'build'
    commands=[('configure',['cmake','-S',str(source/'code'),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_POLICY_VERSION_MINIMUM=3.5','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON']),
        ('compile',['cmake','--build',str(build),'--target','bp','-j',str(args.jobs)]),
        ('synthetic-gates',[shutil.which('python3'),str(source/'diagnostic/run_local_gates.py'),
        '--reference-goc',str(bundle/'reference-goc'),'--compiler',str(compiler),'--cplex-include',os.environ['CPLEX_INCLUDE'],
        '--output',str(output/'synthetic-gates')])]
    try:
        for name,command in commands:
            with (output/(name+'.stdout')).open('x') as out,(output/(name+'.stderr')).open('x') as err:
                process=subprocess.run(command,stdout=out,stderr=err,env=environment,timeout=1200)
            record['commands'].append(dict(name=name,command=command,returncode=process.returncode))
            if process.returncode:raise RuntimeError(name+' failed; original output retained')
        verify(source,identity['files'])
        gate=json.loads((output/'synthetic-gates/complete.json').read_bytes())
        if gate.get('all_checks_passed') is not True or gate.get('routing_runs')!=0:raise ValueError('Synthetic gates did not pass')
        binary=build/'bp';ldd=subprocess.check_output(['ldd',str(binary)],text=True);(output/'ldd.txt').write_text(ldd)
        libraries={str(Path(p).resolve()):sha(Path(p).resolve()) for p in re.findall(r'(/\S+)\s+\(',ldd)}
        runtime={str(binary):sha(binary),str(build/'CMakeCache.txt'):sha(build/'CMakeCache.txt'),
                 str(build/'compile_commands.json'):sha(build/'compile_commands.json'),**libraries}
        manifest=dict(schema_version=1,runtime_kind='native',source_id=SOURCE,control_source_id=CONTROL,
            backend='CPLEX',checker=CHECKER,checker_validation_status='runtime harness must verify pinned checker before any approved diagnostic',
            algorithm_eps=1e-5,expected_algorithm_settings={'label_correction':False},diagnostic_only=True,accepted_closure=False,
            runtime_files=runtime,source_patch_files={str(bundle/'diagnostic.patch'):sha(bundle/'diagnostic.patch')},
            source_archive_sha256=sha(bundle/'source.tar.gz'),source_inventory_sha256=INVENTORY_SHA,
            diagnostic_environment={'MS58_DIAGNOSTIC_SOURCE_INVENTORY_SHA256':INVENTORY_SHA,
                'MS58_DIAGNOSTIC_CAPTURE_PATH':'${CELL_DIRECTORY}/terminal-capture.json',
                'MS58_DIAGNOSTIC_RAW_INPUT_SHA256':'${PINNED_CHECKER_INPUT_SHA256}',
                'MS58_DIAGNOSTIC_NATIVE_INPUT_PATH':'${PINNED_ORIGINAL_NATIVE_INPUT_PATH}'},
            worker_environment={'UPSTREAM_PROBE_BINARY':str(binary),'UPSTREAM_SOURCE_ROOT':str(source)},
            purpose='Numerical reachability/terminal capture only. Never replaces fixed f2. No replay implementation.')
        write_new(output/'manifest.template.json',manifest)
        record.update(build_passed=True,native_gate_passed=True,binary_sha256=sha(binary),
            native_gate_sha256=sha(output/'synthetic-gates/complete.json'),manifest_template_sha256=sha(output/'manifest.template.json'))
    except Exception as error:record['error']=f'{type(error).__name__}: {error}'
    record['finished_utc']=datetime.now(timezone.utc).isoformat();write_new(output/'build-complete.json',record)
    print(json.dumps(record,indent=2));return int(not record['build_passed'] or not record['native_gate_passed'])

if __name__=='__main__':raise SystemExit(main())
