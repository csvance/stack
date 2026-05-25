#!/usr/bin/env bash
# Remove the symlink installed by install.sh. Refuses to touch anything else.

set -euo pipefail
IFS=$'\n\t'

install_dir="${STACK_INSTALL_DIR:-$HOME/.local/bin}"
dest="$install_dir/stack"

if [[ ! -e "$dest" && ! -L "$dest" ]]; then
    echo "uninstall.sh: nothing to remove at $dest"
    exit 0
fi

src_dir="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
expected="$src_dir/bin/stack"

if [[ -L "$dest" ]] && [[ "$(readlink "$dest")" == "$expected" ]]; then
    rm -f "$dest"
    echo "removed: $dest"
else
    echo "uninstall.sh: $dest is not our symlink; refusing to remove" >&2
    echo "  expected target: $expected" >&2
    echo "  actual: $(ls -ld "$dest")" >&2
    exit 1
fi
