#!/usr/bin/env bash
# Run on Kenzae. Copies a local Kayros worktree to nancy.g5k:~/ms61-bpc/sources/<name>/ (no .git, no build/, no __pycache__).
# Records the local HEAD, the dirty flag, the dirty file list and the cpp/+src/ content hash in SOURCE_ID next to the copy.
# The remote build/ directory is left alone here. ms61_build.sh always removes build/cplex and builds from scratch.
set -euo pipefail
local_tree=$(realpath "${1:?usage: ms61_sync_source.sh LOCAL_WORKTREE NAME}")
name=${2:?usage: ms61_sync_source.sh LOCAL_WORKTREE NAME}
[[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "Invalid name: $name" >&2; exit 2; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
remote=nancy.g5k
ssh_opts=(-S none -o ConnectTimeout=15)
rev=$(git -C "$local_tree" rev-parse HEAD)
status=$(git -C "$local_tree" status --porcelain --untracked-files=normal)
dirty=false; [[ -n "$status" ]] && dirty=true
local_hash=$("$script_dir/ms61_tree_hash.sh" "$local_tree")
ssh "${ssh_opts[@]}" "$remote" "mkdir -p ms61-bpc/sources/$name"
rsync -a --delete -e "ssh ${ssh_opts[*]}" \
    --exclude=.git --exclude=/build/ --exclude=__pycache__ --exclude=/.venv/ --exclude=/SOURCE_ID \
    "$local_tree/" "$remote:ms61-bpc/sources/$name/"
remote_hash=$(ssh "${ssh_opts[@]}" "$remote" 'bash -s' -- "ms61-bpc/sources/$name" < "$script_dir/ms61_tree_hash.sh")
if [[ "$remote_hash" != "$local_hash" ]]; then
    echo "Content hash mismatch after rsync: local $local_hash remote $remote_hash" >&2
    exit 1
fi
{
    printf 'source_rev=%s\n' "$rev"
    printf 'dirty=%s\n' "$dirty"
    printf 'local_worktree=%s\n' "$local_tree"
    printf 'synced_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'content_sha256_cpp_src=%s\n' "$local_hash"
    if [[ "$dirty" == true ]]; then
        printf 'dirty_files:\n'
        printf '%s\n' "$status" | sed 's/^/  /'
    fi
} | ssh "${ssh_opts[@]}" "$remote" "cat > ms61-bpc/sources/$name/SOURCE_ID"
echo "Synced $local_tree -> $remote:~/ms61-bpc/sources/$name"
echo "  HEAD $rev dirty=$dirty content_sha256_cpp_src=$local_hash"
