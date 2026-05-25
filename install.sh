#!/usr/bin/env bash
# Idempotent installer. Symlinks bin/stack into $STACK_INSTALL_DIR
# (default ~/.local/bin) and probes prerequisites.

set -euo pipefail
IFS=$'\n\t'

force=0
for arg in "$@"; do
    case "$arg" in
        -f|--force) force=1 ;;
        -h|--help)
            cat <<'EOF'
Usage: install.sh [--force]

Installs the `stack` CLI by symlinking tools/stack/bin/stack into
$STACK_INSTALL_DIR (default: ~/.local/bin).

Options:
  --force, -f   Overwrite an existing symlink/file at the install location
EOF
            exit 0
            ;;
        *)
            echo "install.sh: unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

install_dir="${STACK_INSTALL_DIR:-$HOME/.local/bin}"
mkdir -p "$install_dir"

src_dir="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$src_dir/bin/stack"
[[ -x "$src" ]] || { echo "install.sh: $src not executable" >&2; exit 1; }

dest="$install_dir/stack"
if [[ -e "$dest" || -L "$dest" ]]; then
    if [[ -L "$dest" ]] && [[ "$(readlink "$dest")" == "$src" ]]; then
        echo "already installed: $dest -> $src"
    elif (( force )); then
        rm -f "$dest"
        ln -s "$src" "$dest"
        echo "overwrote: $dest -> $src"
    else
        echo "install.sh: $dest already exists (use --force to overwrite)" >&2
        echo "  current: $(ls -ld "$dest")" >&2
        exit 1
    fi
else
    ln -s "$src" "$dest"
    echo "installed: $dest -> $src"
fi

if ! command -v stack >/dev/null 2>&1; then
    cat <<EOF
Note: '$install_dir' is not on your PATH. Add this to your shell rc:
  export PATH="$install_dir:\$PATH"
Then start a new shell or 'source' your rc file.
EOF
fi

echo
echo "Prerequisite check:"

_probe() {
    local name="$1" hint="$2"; shift 2
    if "$@" >/dev/null 2>&1; then
        printf '  %-20s ok\n' "$name"
    else
        printf '  %-20s MISSING  -> %s\n' "$name" "$hint"
    fi
}

_probe git           "install git"                        git --version
_probe jq            "install jq"                          jq --version
_probe git-branchless "install git-branchless and run 'git branchless init'" git branchless --version
_probe az            "install Azure CLI and run 'az login'" az --version

# pycharm discovery: try PATH first, then known install locations.
_pycharm_found=0
for c in pycharm \
         /opt/pycharm-*/bin/pycharm.sh \
         /snap/pycharm-professional/current/bin/pycharm.sh \
         /usr/share/pycharm/bin/pycharm.sh \
         /Applications/PyCharm.app/Contents/MacOS/pycharm \
         /Applications/PyCharm\ Professional.app/Contents/MacOS/pycharm; do
    if command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]]; then
        printf '  %-20s ok (%s)\n' pycharm "$c"
        _pycharm_found=1
        break
    fi
done
if (( _pycharm_found == 0 )); then
    printf '  %-20s MISSING  -> install PyCharm and the JetBrains command-line launcher\n' "pycharm"
    echo "    Note: 'stack update', 'stack sync', 'stack land' will fail if PyCharm is unavailable."
fi
