#!/usr/bin/env bash
#
# Repackage Anthropic's official claude-desktop .deb as an RPM for Fedora.
#
# Anthropic publishes Debian packages only. This fetches the newest one from
# their apt repository, verifies it, and rebuilds it with claude-desktop.spec.
#
# Usage:
#   ./build-claude-desktop-rpm.sh [OUTPUT_DIR]   # build into OUTPUT_DIR (default: cwd)
#   ./build-claude-desktop-rpm.sh --print-latest # print newest upstream version, exit
#
# Env:
#   LOCAL_ROOT   Build against an extracted (not installed) rpm-build tree.
#   WORKDIR      Parent for the build scratch directory. Defaults to OUTPUT_DIR,
#                because the build needs ~1.7 GB and Fedora's /tmp is tmpfs.
#
# Requires: rpm-build, binutils, curl, gnupg2

set -euo pipefail

REPO="https://downloads.claude.ai/claude-desktop/apt/stable"
KEY_URL="https://downloads.claude.ai/claude-desktop/key.asc"
# Published at https://code.claude.com/docs/en/desktop-linux
KEY_FPR="31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEC="$HERE/claude-desktop.spec"
# Everything the spec pulls in as SourceN besides the .deb itself.
SOURCES=("$HERE/patch-quick-entry-wayland.py" "$HERE/recolor-tray-icon.py")

case "$(uname -m)" in
    aarch64) DEBARCH=arm64 ;;
    x86_64)  DEBARCH=amd64 ;;
    *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

# Print the comment block at the top of this file as the usage message.
usage() {
    awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "${BASH_SOURCE[0]}"
}

PRINT_ONLY=0
OUTDIR="$PWD"
case "${1:-}" in
    --print-latest) PRINT_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    "") ;;
    *) mkdir -p "$1"; OUTDIR="$(cd "$1" && pwd)" ;;
esac

# Metadata only (key, InRelease, Packages) -- a few hundred KB, /tmp is fine.
# The bulky build scratch is created later, on disk. See WORK below.
META="$(mktemp -d)"
WORK=""
cleanup() {
    rm -rf "$META"
    [[ -n "$WORK" ]] && rm -rf "$WORK"
    # An EXIT trap that ends on a failing command sets the script's exit status.
    return 0
}
trap cleanup EXIT

log() { [[ $PRINT_ONLY -eq 1 ]] || echo "$@"; }

# Resolve the newest package in the repository, verifying the whole trust chain
# on the way: pinned fingerprint -> signed InRelease -> Packages hash -> .deb
# SHA256. Anything weaker and we would be repackaging an unauthenticated blob
# into a root-installed, setuid-carrying RPM.
#
# Sets: VERSION, FILENAME, SHA256
resolve_latest() {
    cd "$META"

    log "==> Verifying repository signature"
    curl -fsSL "$KEY_URL" -o key.asc
    curl -fsSL "$REPO/dists/stable/InRelease" -o InRelease

    export GNUPGHOME="$META/gnupg"
    mkdir -p "$GNUPGHOME"
    chmod 700 "$GNUPGHOME"

    local got_fpr
    got_fpr="$(gpg --show-keys --with-colons key.asc | awk -F: '/^fpr:/{print $10; exit}')"
    if [[ "$got_fpr" != "$KEY_FPR" ]]; then
        echo "signing key fingerprint mismatch:" >&2
        echo "  expected $KEY_FPR" >&2
        echo "  got      ${got_fpr:-<none>}" >&2
        exit 1
    fi

    gpg --quiet --import key.asc
    gpg --verify InRelease >/dev/null 2>&1 \
        || { echo "InRelease signature invalid" >&2; exit 1; }
    log "    key $KEY_FPR, InRelease signature OK"

    curl -fsSL "$REPO/dists/stable/main/binary-$DEBARCH/Packages" -o Packages

    local want
    want="$(sha256sum Packages | cut -d' ' -f1)"
    grep -qE "^ $want +[0-9]+ main/binary-$DEBARCH/Packages\$" InRelease \
        || { echo "Packages index does not match signed InRelease" >&2; exit 1; }
    log "    Packages index matches InRelease"

    # Each stanza lists Version, Filename and SHA256 in that order; pasting them
    # onto one line lets sort -V pick the newest by version number.
    local newest
    newest="$(grep -E '^(Version|Filename|SHA256):' Packages \
        | paste - - - | sort -V | tail -n1)"
    [[ -n "$newest" ]] || { echo "no package found for $DEBARCH" >&2; exit 1; }

    VERSION="$(cut -f1 <<<"$newest" | cut -d' ' -f2)"
    FILENAME="$(cut -f2 <<<"$newest" | cut -d' ' -f2)"
    SHA256="$(cut -f3 <<<"$newest" | cut -d' ' -f2)"
}

resolve_latest

if [[ $PRINT_ONLY -eq 1 ]]; then
    echo "$VERSION"
    exit 0
fi

# The build needs roughly 1.7 GB of scratch: the .deb, its expanded payload, the
# buildroot copy and the finished RPM. Default to OUTDIR rather than /tmp --
# Fedora mounts /tmp as tmpfs, so building there burns RAM and hits its quota.
mkdir -p "$OUTDIR"
base="${WORKDIR:-$OUTDIR}"
mkdir -p "$base"
avail_kb="$(df -Pk "$base" | awk 'NR==2 {print $4}')"
if (( avail_kb < 2 * 1024 * 1024 )); then
    printf 'not enough free space in %s: %d MB available, need ~2 GB\n' \
        "$base" "$((avail_kb / 1024))" >&2
    echo "set WORKDIR=/path/with/space to build elsewhere" >&2
    exit 1
fi
WORK="$(mktemp -d "$base/.claude-rpm-build.XXXXXX")"

deb="$(basename "$FILENAME")"
echo "==> Downloading claude-desktop $VERSION ($DEBARCH)"
curl -fL# "$REPO/$FILENAME" -o "$WORK/$deb"
echo "$SHA256  $WORK/$deb" | sha256sum -c - >/dev/null \
    || { echo "checksum mismatch on $deb" >&2; exit 1; }
echo "    SHA256 OK"

echo "==> Building RPM"
top="$WORK/rpmbuild"
mkdir -p "$top"/{BUILD,RPMS,SRPMS,BUILDROOT}

# Match the spec's Version to whatever the repository is serving today.
spec="$WORK/claude-desktop.spec"
sed -E "s/^Version:([[:space:]]+).*/Version:\\1$VERSION/" "$SPEC" > "$spec"

# _sourcedir is WORK (see below), so the scripts have to sit next to the .deb.
cp "${SOURCES[@]}" "$WORK/"

declare -a defines=(
    --define "_topdir $top"
    # Point at the downloaded .deb in place rather than copying 160 MB into
    # SOURCES/.
    --define "_sourcedir $WORK"
    # Default is single-threaded zstd -19; on a ~520 MB payload that takes well
    # over ten minutes. T0 uses all cores at the same compression level.
    --define "_binary_payload w19T0.zstdio"
)

rpmbuild_bin=rpmbuild
if [[ -n "${LOCAL_ROOT:-}" ]]; then
    rpmbuild_bin="$LOCAL_ROOT/usr/bin/rpmbuild"
    export RPM_CONFIGDIR="$LOCAL_ROOT/usr/lib/rpm"
    export PATH="$LOCAL_ROOT/usr/bin:$PATH"
    defines+=(--define "_rpmconfigdir $LOCAL_ROOT/usr/lib/rpm")
    # check-buildroot / check-rpaths are invoked by absolute path and only exist
    # in a real redhat-rpm-config install. They target compiled software and are
    # no-ops for a binary repack.
    defines+=(--define "__arch_install_post %{nil}")
fi

command -v "$rpmbuild_bin" >/dev/null 2>&1 \
    || { echo "rpmbuild not found -- install rpm-build, or set LOCAL_ROOT" >&2; exit 1; }

"$rpmbuild_bin" -bb "${defines[@]}" "$spec"

find "$top/RPMS" -name '*.rpm' -exec cp -v {} "$OUTDIR/" \;
echo "==> Done: $OUTDIR/claude-desktop-$VERSION-1.*.rpm"
