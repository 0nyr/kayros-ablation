"""Verify the immutable classic checker before importing its evaluation code."""

import hashlib
from importlib import metadata
import json
from pathlib import Path

CHECKER_COMMIT = "425dff351fdd9522ea6bb8c537dd416dfcd19614"
CHECKER_TREE = "e22c65ea7b13c342653b947b6066b78b45313e517ad8ac3af09efaaa66e91334"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checker_identity() -> dict:
    distribution = metadata.distribution("mamut-routing-lib")
    if distribution.version != "0.11.0":
        raise RuntimeError(f"Required checker 0.11.0, found {distribution.version}")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    if direct.get("vcs_info", {}).get("commit_id") != CHECKER_COMMIT:
        raise RuntimeError("Checker installation does not identify the pinned Git commit")
    package = Path(distribution.locate_file("mamut_routing_lib")).resolve()
    files = {p.relative_to(package).as_posix(): sha256(p)
             for p in sorted(package.rglob("*.py"))}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != CHECKER_TREE or len(files) != 23:
        raise RuntimeError("Installed checker Python files differ from the pinned source")
    # Reject a shadowing package on sys.path before importing evaluation functions.
    import importlib.util
    spec = importlib.util.find_spec("mamut_routing_lib")
    if spec is None or Path(spec.origin).resolve() != package / "__init__.py":
        raise RuntimeError("The imported checker would shadow the verified installation")
    return {"package": "mamut-routing-lib", "version": distribution.version,
            "commit": CHECKER_COMMIT, "python_files": len(files),
            "installed_python_tree_sha256": digest}
