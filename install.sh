#!/bin/sh
set -eu

PREFIX=${PREFIX:-/usr/local}
DESTDIR=${DESTDIR:-}
LIBDIR="$PREFIX/lib/hotspot-manager"
BINDIR="$PREFIX/bin"
DATADIR="${XDG_DATA_DIR:-$PREFIX/share}"
LIBEXEC="$PREFIX/libexec"
SYSCONFDIR=${SYSCONFDIR:-/etc}
UNITDIR=${UNITDIR:-$PREFIX/lib/systemd/system}

ROOT=
if [ -n "$DESTDIR" ]; then
    ROOT=$DESTDIR
fi

run() {
    if [ -n "$ROOT" ] || [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo -- "$@"
    fi
}

echo "Installing hotspot-manager into $ROOT$PREFIX"

run install -d "$ROOT$LIBDIR/hotspot_manager"
run install -m644 src/hotspot_manager/__init__.py "$ROOT$LIBDIR/hotspot_manager/__init__.py"
run install -m644 src/hotspot_manager/main.py "$ROOT$LIBDIR/hotspot_manager/main.py"
run install -m644 src/hotspot_manager/nm.py "$ROOT$LIBDIR/hotspot_manager/nm.py"
run install -m644 src/hotspot_manager/window.py "$ROOT$LIBDIR/hotspot_manager/window.py"

run install -d "$ROOT$BINDIR"
run install -m755 data/hotspot-manager "$ROOT$BINDIR/hotspot-manager"

run install -d "$ROOT$DATADIR/applications"
run install -m644 data/io.github.hotspotmanager.Hotspot.desktop "$ROOT$DATADIR/applications/io.github.hotspotmanager.Hotspot.desktop"

run install -d "$ROOT$DATADIR/icons/hicolor/scalable/apps"
run install -m644 data/io.github.hotspotmanager.Hotspot.svg "$ROOT$DATADIR/icons/hicolor/scalable/apps/io.github.hotspotmanager.Hotspot.svg"

echo "Installing NetworkManager, polkit and sudo integration"

run install -d "$ROOT$LIBEXEC"
run install -m755 data/hotspot-manager-net-helper "$ROOT$LIBEXEC/hotspot-manager-net-helper"

run install -d "$ROOT$SYSCONFDIR/polkit-1/rules.d"
run install -m644 data/60-hotspot-manager.rules "$ROOT$SYSCONFDIR/polkit-1/rules.d/60-hotspot-manager.rules"

run install -d "$ROOT$SYSCONFDIR/sudoers.d"
run install -m440 data/hotspot-manager.sudoers "$ROOT$SYSCONFDIR/sudoers.d/zz-hotspot-manager"
run rm -f "$ROOT$SYSCONFDIR/sudoers.d/hotspot-manager"

run install -d "$ROOT$UNITDIR"
run install -m644 data/hotspot-ap-interface.service "$ROOT$UNITDIR/hotspot-ap-interface.service"

run install -d "$ROOT/var/lib/hotspot-manager"

if command -v gtk4-update-icon-cache >/dev/null 2>&1 && [ -z "$ROOT" ]; then
    run gtk4-update-icon-cache -q -t -f "$DATADIR/icons/hicolor" || true
fi
if command -v update-desktop-database >/dev/null 2>&1 && [ -z "$ROOT" ]; then
    run update-desktop-database "$DATADIR/applications" || true
fi

if [ -z "$ROOT" ] || [ "$ROOT" = "/" ]; then
    if command -v systemctl >/dev/null 2>&1; then
        run systemctl daemon-reload || true
        run systemctl restart polkit || true
        run systemctl enable hotspot-ap-interface.service || true
        run systemctl restart hotspot-ap-interface.service || true
    fi
fi

echo "Done. Launch Hotspot from your app grid."
