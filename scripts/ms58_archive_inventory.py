"""Inventory completed ms58 artifacts before remote output cleanup."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def inventory(root, names):
    entries = {}
    for name in sorted(names):
        source = root / name
        if not source.is_dir():
            raise ValueError(f"Missing artifact directory {source}")
        for directory, dirs, files in os.walk(source, followlinks=False):
            for child in sorted(dirs + files):
                path = Path(directory) / child
                relative = str(path.relative_to(root))
                if path.is_symlink():
                    entries[relative] = {"kind": "symlink", "target": os.readlink(path)}
                elif path.is_file():
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    entries[relative] = {"kind": "file", "bytes": path.stat().st_size,
                                         "sha256": digest.hexdigest()}
                elif path.is_dir():
                    entries[relative] = {"kind": "directory"}
                else:
                    raise ValueError(f"Unsupported artifact type {path}")
    return {"schema_version": 1, "directories": sorted(names),
            "file_count": sum(row["kind"] == "file" for row in entries.values()),
            "total_bytes": sum(row.get("bytes", 0) for row in entries.values()),
            "entries": dict(sorted(entries.items()))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--directories", nargs="+", required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.root, args.directories), indent=2, sort_keys=True))
