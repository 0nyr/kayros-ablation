"""Stage the exact corrected integration archive and seven source patches without building."""
import argparse
import json
from pathlib import Path
import shutil
import tarfile

from checker_contract import sha256
from ms58_correction_batch_native import BATCH, SOURCE_IDENTITY_SHA
from ms58_dabia_screen import immutable_json


def stage(archive, inventory_path, patch_directory, root):
    if sha256(inventory_path) != SOURCE_IDENTITY_SHA:
        raise ValueError('Require the pinned corrected integration source inventory')
    pins = json.loads(inventory_path.read_bytes())
    if pins['source_id'] != BATCH or sha256(archive) != pins['archive_sha256']:
        raise ValueError('Archive or source identity mismatch')
    patches = {p.name: sha256(p) for p in patch_directory.glob('*.patch')}
    if len(patches) != 7 or patches != {p['filename']: p['sha256'] for p in pins['patch_stack']}:
        raise ValueError('Require the exact ordered seven-patch inventory')
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'sources/correction-on-domain-batch'
    source.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            relative = Path(member.name)
            if (not member.isfile() and not member.isdir() or relative.is_absolute() or '..' in relative.parts
                    or not relative.parts or relative.parts[0] != pins['archive_prefix']):
                raise ValueError('Archive must contain only regular files/directories under its pinned prefix')
            stripped = Path(*relative.parts[1:])
            if stripped == Path('.'):
                continue
            target = source / stripped
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as data, target.open('xb') as output:
                    shutil.copyfileobj(data, output)
                target.chmod(member.mode)
    observed = {p.relative_to(source).as_posix(): sha256(p) for p in source.rglob('*') if p.is_file()}
    if observed != pins['files']:
        raise ValueError('Extracted source file population or hashes differ from the final corrected tree')
    shutil.copytree(patch_directory, root / 'patches-correction-on-domain-batch')
    shutil.copyfile(inventory_path, root / 'source-inventory.json')
    immutable_json(root / 'stage-complete.json', dict(source_id=BATCH, source_inventory_sha256=SOURCE_IDENTITY_SHA,
        archive_sha256=sha256(archive), source_directory=str(source.resolve()), patches=patches,
        build_status='pending', native_gate_status='pending', solver_cells_started=0))
    return {'source_id': BATCH, 'root': str(root.resolve()), 'source_file_count': len(observed), 'patch_count': 7}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--source-inventory', type=Path, required=True)
    parser.add_argument('--patch-directory', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.archive, args.source_inventory, args.patch_directory, args.root), indent=2))


if __name__ == '__main__':
    main()
