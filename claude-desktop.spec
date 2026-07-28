%global appdir  /usr/lib/claude-desktop

# Upstream's .deb architecture name for the arch we are building on.
%ifarch aarch64
%global debarch arm64
%else
%global debarch amd64
%endif

# Prebuilt upstream binaries: nothing to compile, nothing to strip.
# Stripping the 200 MB Electron binary would break its embedded V8 snapshot
# references, and there is no source to build debuginfo from.
%global debug_package %{nil}
%global __os_install_post %{nil}
%global _build_id_links none

# The app bundles its own Chromium/Electron libraries. They must not be
# advertised to the rest of the system as if they satisfied libEGL/libGLESv2/etc.
%global __provides_exclude_from ^%{appdir}/.*$

# ...and the bundled libs the main binary links against are resolved via its
# RUNPATH, not by any system package, so they must not become Requires.
%global __requires_exclude ^(libffmpeg\\.so|libEGL\\.so|libGLESv2\\.so|libvk_swiftshader\\.so|libvulkan\\.so).*$

Name:           claude-desktop
Version:        1.24012.9
Release:        1%{?dist}
Summary:        Desktop application for Claude.ai

# Anthropic's application code is proprietary; the bundled Electron/Chromium
# stack carries its own licenses (see LICENSES.chromium.html in %%{appdir}).
License:        LicenseRef-Anthropic-Proprietary AND MIT AND BSD-3-Clause
URL:            https://claude.ai
Source0:        claude-desktop_%{version}_%{debarch}.deb
Source1:        patch-quick-entry-wayland.py

# Upstream publishes amd64 and arm64 only. The RPM's architecture follows the
# build host, so each arch must be built natively -- there is no cross path.
ExclusiveArch:  aarch64 x86_64

BuildRequires:  binutils
BuildRequires:  python3
BuildRequires:  tar
BuildRequires:  xz

# Most shared-library deps are picked up automatically from DT_NEEDED.
# Listed here are the ones Chromium dlopen()s at runtime plus the non-library
# runtime requirements, which the generator cannot see.
Requires:       gtk3
Requires:       libnotify
Requires:       libsecret
Requires:       libXtst
Requires:       libdrm
Requires:       mesa-libgbm
Requires:       nss
Requires:       xdg-utils
Requires:       xdg-desktop-portal
Requires:       (xdg-desktop-portal-kde or xdg-desktop-portal-gtk or xdg-desktop-portal-gnome)

Recommends:     alsa-lib
Recommends:     ca-certificates
Recommends:     libayatana-appindicator-gtk3
Recommends:     (gnome-keyring or kf6-kwallet)
# Cowork runs its sandbox in a VM.
%ifarch aarch64
Recommends:     qemu-system-aarch64
Recommends:     edk2-aarch64
%else
Recommends:     qemu-system-x86
Recommends:     edk2-ovmf
%endif
Recommends:     virtiofsd

%description
Claude is an AI assistant from Anthropic. The desktop application provides the
Chat, Cowork and Claude Code experiences in a single native window, including
parallel sessions, visual diff review, an integrated terminal and editor, and
live app preview.

This package is repackaged from Anthropic's official Debian package for
%{debarch}, with one fix applied so that Quick Entry opens on native Wayland.
Because it does not come from Anthropic's apt repository, it does not update
itself -- rebuild from a newer .deb to upgrade.

%prep
%setup -q -c -T
ar x %{SOURCE0}
tar -xf data.tar.xz

# The one change to the application payload: Quick Entry's overlay never opens
# on a native Wayland session, because it waits on a 'ready-to-show' that Ozone
# only delivers once the window is mapped. See the script for the details; it
# aborts the build rather than patch anything it does not recognise.
python3 %{SOURCE1} usr/lib/claude-desktop/resources/app.asar

%install
cp -a usr %{buildroot}/

# Debian packaging metadata that means nothing on Fedora.
rm -rf %{buildroot}%{_datadir}/lintian

# Relocate the Debian copyright file to Fedora's license directory.
install -Dpm 0644 usr/share/doc/%{name}/copyright \
    %{buildroot}%{_licensedir}/%{name}/copyright
rm -rf %{buildroot}%{_datadir}/doc

# Chromium's setuid sandbox helper. Ubuntu 24.04+ prefers an AppArmor userns
# profile, but on Fedora (SELinux, unrestricted userns) this SUID helper is the
# sandbox path that actually gets used, so the mode matters.
chmod 4755 %{buildroot}%{appdir}/chrome-sandbox

%files
%license %{_licensedir}/%{name}/copyright
%{_bindir}/%{name}
%{appdir}
%{_datadir}/applications/com.anthropic.Claude.desktop
%{_datadir}/icons/hicolor/*/apps/%{name}.png

%changelog
* Mon Jul 27 2026 Repackaged locally <claude@alex.vnkr.me> - 1.24012.9-1
- Repackage Anthropic's official claude-desktop_1.24012.9_arm64.deb for Fedora
- Drop the Debian maintainer scripts: the AppArmor userns profile does not
  apply on Fedora, and the apt repository registration has no equivalent
- Suppress Provides for the bundled Electron/Chromium libraries
- Bound Quick Entry's wait on 'ready-to-show', which Ozone/Wayland never
  delivers for an unmapped window, so the overlay opens on a Wayland session
