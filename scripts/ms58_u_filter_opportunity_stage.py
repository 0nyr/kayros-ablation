"""Stage the exact dominance source archive and seven-patch inventory without building."""
import argparse
import json
from pathlib import Path
import shutil
import tarfile

from checker_contract import sha256
from ms58_dabia_screen import immutable_json


def stage(archive, pins_path, patch_directory, destination, patches_output):
    pins = json.loads(pins_path.read_bytes())
    if sha256(archive) != pins['archive_sha256']:
        raise ValueError('Source archive differs from pinned U-opportunity archive')
    expected = set(pins['baseline_patch_sha256']) | {pins['selected_full_patch_sha256'], pins['incremental_patch_sha256'], pins['parent_incremental_patch_sha256']}
    found = {}
    for path in sorted(patch_directory.rglob('*.patch')):
        value = sha256(path)
        if value in expected:
            found.setdefault(value, path)
    if set(found) != expected or len(expected) != 7:
        raise ValueError('Need four baseline patches, selected9d full patch and both diagnostic incremental patches')
    if destination.exists() or patches_output.exists():
        raise FileExistsError('Source and patch destinations must both be new')
    destination.mkdir(parents=True)
    try:
        prefix = 'kayros-' + pins['source_id'][:12]
        with tarfile.open(archive, 'r:gz') as tar:
            members = tar.getmembers()
            if any(not m.isfile() and not m.isdir() for m in members):
                raise ValueError('Source archive must contain only regular files and directories')
            for member in members:
                relative = Path(member.name)
                if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != prefix:
                    raise ValueError('Archive member escapes the pinned source prefix')
                stripped = Path(*relative.parts[1:])
                if stripped == Path('.'):
                    continue
                target = destination / stripped
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(member.mode)
        for relative, expected_hash in pins['files'].items():
            if sha256(destination / relative) != expected_hash:
                raise ValueError('Staged source differs from pinned archive contents: ' + relative)
        observed = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
        if observed != set(pins['files']):
            raise ValueError('Staged source has an unexpected file population')
        patches_output.mkdir(parents=True)
        for index, value in enumerate(sorted(found)):
            shutil.copyfile(found[value], patches_output / f'{index:02d}-{found[value].name}')
        immutable_json(destination / '.u-filter-opportunity-source-identity.json',
                       {'source_id': pins['source_id'], 'source_pins_sha256': sha256(pins_path),
                        'archive_sha256': sha256(archive), 'source_files': pins['files'],
                        'patches': {str(p.resolve()): sha256(p) for p in patches_output.glob('*.patch')}})
    except Exception:
        # Keep a failed staging directory as evidence. Never reuse it.
        raise
    return {'source': str(destination.resolve()), 'patches': str(patches_output.resolve()),
            'source_id': pins['source_id'], 'solver_cells_started': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--source-pins', type=Path, required=True)
    parser.add_argument('--patch-directory', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--patches-output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.archive, args.source_pins, args.patch_directory, args.destination, args.patches_output), indent=2))


if __name__ == '__main__':
    main()
