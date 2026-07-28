#!/usr/bin/env bash
#
# Download the newest Claude Desktop release, rebuild it as an RPM, and install
# it. Safe to re-run: it exits early when the installed version is already the
# newest, and reuses a previously built RPM for that version.
#
# The RPM this produces never updates itself -- Anthropic ships updates through
# an apt repository that Fedora cannot consume -- so this script is the upgrade
# path. Run it periodically.
#
# Usage:
#   ./update-claude-desktop.sh [-f|--force] [-n|--no-install] [-o OUTPUT_DIR]
#
#   -f, --force       Rebuild and reinstall even if already up to date
#   -n, --no-install  Build only; skip the install step (no sudo needed)
#   -o, --output DIR  Where to keep built RPMs (default: this script's directory)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build-claude-desktop-rpm.sh"
PKG=claude-desktop

# Print the comment block at the top of this file as the usage message.
usage() {
    awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "${BASH_SOURCE[0]}"
}

FORCE=0
INSTALL=1
OUTDIR="$HERE"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--force)      FORCE=1; shift ;;
        -n|--no-install) INSTALL=0; shift ;;
        -o|--output)     OUTDIR="$2"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

mkdir -p "$OUTDIR"
OUTDIR="$(cd "$OUTDIR" && pwd)"

[[ -x "$BUILD" ]] || { echo "missing or non-executable: $BUILD" >&2; exit 1; }

# --- what is installed, and what is available? -------------------------------
# rpm -q prints "package ... is not installed" on stdout, so key off its exit
# status rather than the captured text.
if ! installed="$(rpm -q --qf '%{VERSION}' "$PKG" 2>/dev/null)"; then
    installed=""
fi

echo "==> Checking for updates"
latest="$("$BUILD" --print-latest)"

echo "    installed: ${installed:-<none>}"
echo "    latest:    $latest"

if [[ "$installed" == "$latest" && $FORCE -eq 0 ]]; then
    echo "==> Already up to date."
    exit 0
fi

# --- make sure we can build ---------------------------------------------------
# The build needs rpm-build; we are about to ask for sudo anyway, so offer to
# install it rather than failing. LOCAL_ROOT users are already sorted.
if [[ -z "${LOCAL_ROOT:-}" ]] && ! command -v rpmbuild >/dev/null 2>&1; then
    if [[ $INSTALL -eq 0 ]]; then
        echo "rpmbuild not found -- install rpm-build, or set LOCAL_ROOT" >&2
        exit 1
    fi
    echo "==> rpmbuild not found; installing rpm-build"
    sudo dnf install -y rpm-build binutils
fi

# --- build (or reuse) ---------------------------------------------------------
# Release is always 1; the dist tag varies with the Fedora release, so glob it.
find_rpm() {
    find "$OUTDIR" -maxdepth 1 -name "$PKG-$latest-1.*.$(uname -m).rpm" \
        -print -quit 2>/dev/null
}

rpm_file="$(find_rpm)"
if [[ -n "$rpm_file" && $FORCE -eq 0 ]]; then
    echo "==> Reusing already-built $(basename "$rpm_file")"
else
    "$BUILD" "$OUTDIR"
    rpm_file="$(find_rpm)"
    [[ -n "$rpm_file" ]] || { echo "build did not produce an RPM for $latest" >&2; exit 1; }
fi

if [[ $INSTALL -eq 0 ]]; then
    echo "==> Built (not installing): $rpm_file"
    exit 0
fi

# --- install ------------------------------------------------------------------
echo "==> Installing $(basename "$rpm_file")"
if [[ "$installed" == "$latest" ]]; then
    # Same version, so --force got us here; install/upgrade would be a no-op.
    sudo dnf reinstall -y "$rpm_file"
else
    # dnf install upgrades in place when a newer version of an installed
    # package is given as a local file.
    sudo dnf install -y "$rpm_file"
fi

echo "==> Installed $(rpm -q --qf '%{VERSION}-%{RELEASE}' "$PKG")"
echo "    Launch 'Claude' from your application menu, or run: claude-desktop"
