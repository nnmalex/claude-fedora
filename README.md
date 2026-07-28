# Claude Desktop for Fedora

Repackages Anthropic's official Claude Desktop `.deb` as an RPM.

Anthropic ships Claude Desktop for Linux as a Debian package only, distributed
through an apt repository. Fedora and RHEL are [explicitly not supported
yet](https://code.claude.com/docs/en/desktop-linux). These scripts fetch the
newest official package, verify it against Anthropic's signing key, and rebuild
it as a proper RPM with Fedora-native dependencies.

The binaries are unmodified. The only edit to the application payload is a
[one-line fix to Quick Entry](#the-quick-entry-fix), which cannot open at all on
a Wayland session as shipped; everything else is byte-identical to what
Anthropic publishes.

## Quick start

```bash
./update-claude-desktop.sh
```

That checks for a new release, builds it if needed, and installs it. It prompts
for `sudo` at the install step. Re-run it any time; it exits early when you are
already up to date.

Then launch **Claude** from your application menu, or run `claude-desktop`.

## Contents

| File | Purpose |
| --- | --- |
| `update-claude-desktop.sh` | Check for updates, build, install. The one you run. |
| `build-claude-desktop-rpm.sh` | Download + verify + build the RPM. No root needed. |
| `claude-desktop.spec` | The RPM spec. |
| `patch-quick-entry-wayland.py` | The Quick Entry fix. Applied to `app.asar` during `%prep`. |

## Requirements

- Fedora on `x86_64` or `aarch64` (Anthropic publishes no other architectures)
- `rpm-build`, `binutils`, `curl`, `gnupg2`
- ~2 GB of free disk space to build in

`update-claude-desktop.sh` offers to install `rpm-build` if it is missing, since
it needs `sudo` anyway.

## Updating

An RPM built this way **does not update itself**. Anthropic delivers updates
through their apt repository, which Fedora cannot consume, and the app has no
built-in updater on Linux. Re-running `./update-claude-desktop.sh` is the
upgrade path — it compares the installed version against the repository and
rebuilds only when there is something newer.

Useful flags:

```bash
./update-claude-desktop.sh --no-install     # build only, no sudo
./update-claude-desktop.sh --force          # rebuild + reinstall current version
./update-claude-desktop.sh -o ~/rpms        # keep built RPMs elsewhere
```

To build without installing anything at all:

```bash
./build-claude-desktop-rpm.sh ~/rpms
./build-claude-desktop-rpm.sh --print-latest   # just query the newest version
```

## Uninstall

```bash
sudo dnf remove claude-desktop
```

Per-user data in `~/.config/Claude` is left behind, as with any RPM. Remove it
by hand if you want a clean slate.

## How it works

### Verification

The `.deb` becomes a root-installed RPM containing a setuid-root binary, so it
is verified end to end before anything is unpacked:

1. Anthropic's signing key is downloaded and its fingerprint checked against
   `31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE`, the value published in
   Anthropic's documentation and pinned in the build script.
2. The repository's `InRelease` index is verified against that key.
3. The `Packages` index is checked against the SHA256 recorded in `InRelease`.
4. The downloaded `.deb` is checked against the SHA256 recorded in `Packages`.

Any break in that chain aborts the build.

### Packaging changes

The Debian package is not simply unpacked — several things in it are Debian
specific or actively wrong to carry onto Fedora:

- **The maintainer scripts are dropped entirely.** Upstream's `postinst` does
  two things: it installs an AppArmor userns profile, which is meaningless on
  SELinux systems, and it registers Anthropic's apt repository, which has no
  Fedora equivalent. Neither belongs in an RPM.
- **`Provides` are suppressed for the bundled libraries.** The app ships its own
  Electron/Chromium stack (`libEGL.so`, `libGLESv2.so`, `libffmpeg.so`, …).
  Without this, RPM would advertise them system-wide and they could be used to
  satisfy other packages' dependencies with Chromium's private copies. The
  matching auto-generated `Requires` are excluded too, since those libraries
  resolve through the binary's `RUNPATH`.
- **Dependencies are remapped to Fedora names** — `gtk3`, `nss`, `mesa-libgbm`,
  `libsecret`, `libnotify` and so on. The portal backend is a boolean dependency
  (`xdg-desktop-portal-kde or …-gtk or …-gnome`), and Debian's `Recommends`
  become RPM weak dependencies, including the qemu/edk2/virtiofsd set that
  Cowork's VM sandbox uses. Most shared-library dependencies are still generated
  automatically from `DT_NEEDED`; the spec lists only what Chromium `dlopen()`s
  at runtime plus the non-library requirements, which the generator cannot see.
- **Stripping and debuginfo are disabled.** There is no source to build
  debuginfo from, and stripping the ~200 MB Electron binary would break its
  embedded V8 snapshot.
- **`chrome-sandbox` keeps mode 4755.** On Ubuntu 24.04+ the AppArmor profile is
  the preferred sandbox path, but on Fedora — unrestricted user namespaces, no
  AppArmor — this setuid helper is the one Chromium actually uses.
- The Debian `copyright` file moves to `/usr/share/licenses/`, and the
  `lintian` overrides are removed.

### The Quick Entry fix

Quick Entry never opens on a native Wayland session — not from the
`Ctrl+Alt+Space` hotkey, not from the tray's *Open Quick Entry*. This is not
Fedora-specific, and it is not the hotkey: the GlobalShortcuts portal delivers
the accelerator, the overlay window gets created, and its page finishes
loading. What fails is the last step. The overlay is a `BrowserWindow` created
with `show: false`, and the app awaits its `ready-to-show` before calling
`show()` — but Ozone/Wayland does not emit that event for a window that has
never been mapped, so the await never settles. On X11 it arrives in about
150 ms and everything works.

`patch-quick-entry-wayland.py` races that await against a one-second timer, so
the overlay is shown even when the event does not arrive. The first `show()` is
what makes `ready-to-show` fire, so at most one activation per session can
reach the timeout, and on X11 the race still settles early exactly as before.

The edit is applied to `app.asar` in place and padded to the exact length of the
code it replaces, so every offset in the asar header stays valid and only the
affected file's integrity hashes need rewriting. It is anchored on a log message
inside the await; if upstream reworks that code the pattern stops matching and
the build fails, rather than quietly producing an unpatched package.

## Caveats

- **Claude Desktop for Linux is beta**, and Fedora is not a tested target;
  Anthropic tests Ubuntu 22.04+ and Debian 12+. This works, but it is off the
  supported path — don't report Fedora-specific bugs to Anthropic as if they
  shipped this.
- **No automatic updates.** See [Updating](#updating).
- Not everything is in the Linux build yet: Computer Use and dictation are
  unavailable regardless of distribution.
- Wayland does not let a client place its own windows, so the Quick Entry
  overlay appears wherever the compositor puts it and the remembered position
  is ignored. Under XWayland (`--ozone-platform=x11`) the position is honoured,
  but the global hotkey stops working, since that path uses X11 key grabs
  instead of the portal.

## Troubleshooting

**Building without installing `rpm-build`.** The build script honours a
`LOCAL_ROOT` environment variable pointing at an extracted (not installed)
`rpm-build` tree, for machines where you would rather not add build tooling:

```bash
mkdir -p /tmp/rpmroot && cd /tmp/rpmroot
dnf download --resolve --alldeps rpm-build
for f in *.rpm; do rpm2cpio "$f" | cpio -idmu --quiet; done
cp -rn /usr/lib/rpm/. usr/lib/rpm/

LOCAL_ROOT=/tmp/rpmroot ~/path/to/build-claude-desktop-rpm.sh
```

This also disables `check-buildroot` and `check-rpaths`, which are referenced by
absolute path and only exist in a real `redhat-rpm-config` install. They target
compiled software and are no-ops for a binary repackage.

**The build takes a long time.** Compressing the ~520 MB payload dominates. The
spec is built with multithreaded zstd (`w19T0.zstdio`); expect a few minutes.

**Not enough free space.** The build needs ~1.7 GB of scratch and uses the
output directory for it, not `/tmp` — Fedora mounts `/tmp` as tmpfs, so building
there consumes RAM and tends to hit its quota. Point it elsewhere with:

```bash
WORKDIR=/var/tmp ./update-claude-desktop.sh
```

The scratch directory is removed when the build finishes, including on failure.

**`unsupported architecture`.** Anthropic publishes `amd64` and `arm64` only.
