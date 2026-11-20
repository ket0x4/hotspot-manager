import os
import socket
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import nm

SECURITY_LABELS = {
    nm.SEC_WPA2: "WPA2 Personal",
    nm.SEC_WPA3: "WPA3 Personal",
    nm.SEC_OPEN: "Open",
}
SECURITY_ORDER = [nm.SEC_WPA2, nm.SEC_WPA3, nm.SEC_OPEN]

BAND_LABELS = {nm.BAND_AUTO: "Automatic", nm.BAND_24: "2.4 GHz",
               nm.BAND_5: "5 GHz", nm.BAND_6: "6 GHz"}
BAND_ORDER = [nm.BAND_AUTO, nm.BAND_24, nm.BAND_5, nm.BAND_6]

WIDTH_VALUES = [0, 1, 2, 3, 4]
WIDTH_LABELS = ["Automatic", "20 MHz", "40 MHz", "80 MHz", "160 MHz"]

IPV6_ORDER = ["ignore", "auto", "shared"]
IPV6_LABELS = ["Ignore", "Automatic", "Shared"]

UPLINK_KINDS = {
    "802-11-wireless": "Wi-Fi",
    "gsm": "Mobile data",
    "cdma": "Mobile data",
    "ethernet": "Ethernet",
    "bluetooth": "Bluetooth",
}

UPLINK_PHRASES = {
    "802-11-wireless": "Wi-Fi",
    "gsm": "mobile data",
    "cdma": "mobile data",
    "ethernet": "the Ethernet connection",
    "bluetooth": "Bluetooth",
}


def signal_icon(signal):
    if signal >= -55:
        return "network-wireless-signal-excellent-symbolic"
    if signal >= -67:
        return "network-wireless-signal-good-symbolic"
    if signal >= -78:
        return "network-wireless-signal-ok-symbolic"
    return "network-wireless-signal-weak-symbolic"


def band_label(frequency):
    if frequency < 3000:
        return "2.4 GHz"
    if frequency < 5925:
        return "5 GHz"
    return "6 GHz"


class HotspotWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Hotspot")
        self.set_default_size(400, 820)

        self.profile = None
        self._snapshot = nm.Snapshot()
        self._client_rows = {}
        self._channels = {"2.4": [], "5": [], "6": []}
        self._channel_values = [0]
        self._busy = False
        self._polling = False
        self._loading = False
        self._syncing = False
        self._reveal_password = False

        self._build_actions()
        self._build_ui()
        self._load_initial()

        GLib.timeout_add_seconds(3, self._tick)

    # ------------------------------------------------------------------ setup

    def _build_actions(self):
        for name, callback in (
            ("qr", self._show_qr),
            ("copy-password", self._copy_password),
            ("revert", self._revert),
            ("apply", self._apply),
            ("about", self._show_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _build_ui(self):
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Hotspot"))

        self.spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER)
        header.pack_end(self.spinner)

        self.apply_button = Gtk.Button(label="Apply", visible=False, valign=Gtk.Align.CENTER)
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", lambda *_: self.activate_action("win.apply", None))
        header.pack_end(self.apply_button)

        menu = Gio.Menu()
        menu.append("Show QR code", "win.qr")
        menu.append("Copy password", "win.copy-password")
        menu.append("Revert changes", "win.revert")
        menu.append("About Hotspot", "win.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     tooltip_text="Menu", valign=Gtk.Align.CENTER)
        header.pack_end(menu_button)

        self.toggle = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Enable hotspot")
        self.toggle.connect("notify::active", self._on_toggle)
        header.pack_end(self.toggle)

        self.stack = Adw.ViewStack()
        self.stack.add_titled_with_icon(self._build_status_page(), "status", "Hotspot",
                                        "network-wireless-hotspot-symbolic")
        self.stack.add_titled_with_icon(self._build_settings_page(), "settings", "Settings",
                                        "preferences-system-symbolic")

        switcher = Adw.ViewSwitcherBar(stack=self.stack)
        switcher.set_reveal(True)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.stack)
        toolbar.add_bottom_bar(switcher)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)

    # ------------------------------------------------------------- status page

    def _build_status_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(24)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.hero.add_css_class("card")
        self.hero.add_css_class("hero-card")
        self.hero.set_halign(Gtk.Align.FILL)

        self.hero_icon = Gtk.Image(icon_name="network-wireless-hotspot-symbolic",
                                   pixel_size=56)
        self.hero_icon.add_css_class("hero-icon")
        self.hero.append(self.hero_icon)

        self.hero_ssid = Gtk.Label()
        self.hero_ssid.add_css_class("hero-ssid")
        self.hero_ssid.set_wrap(True)
        self.hero.append(self.hero_ssid)

        self.hero_state = Gtk.Label()
        self.hero_state.add_css_class("dim-label")
        self.hero_state.set_wrap(True)
        self.hero.append(self.hero_state)

        self.hero_button = Gtk.Button()
        self.hero_button.add_css_class("pill")
        self.hero_button.add_css_class("suggested-action")
        self.hero_button.set_halign(Gtk.Align.CENTER)
        self.hero_button.set_margin_top(12)
        self.hero_button.connect("clicked", self._on_hero_button)
        self.hero.append(self.hero_button)

        box.append(self.hero)

        details = Adw.PreferencesGroup(title="Details")
        self.ssid_row = Adw.ActionRow(title="Network name")
        self.ssid_copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy network name",
                                    valign=Gtk.Align.CENTER)
        self.ssid_copy.add_css_class("flat")
        self.ssid_copy.connect("clicked", lambda *_: self._copy(self._profile().ssid, "Network name copied"))
        self.ssid_row.add_suffix(self.ssid_copy)
        details.add(self.ssid_row)

        self.password_row = Adw.ActionRow(title="Password")
        self.password_label = Gtk.Label(selectable=False)
        self.password_label.add_css_class("dim-label")
        self.password_row.add_suffix(self.password_label)
        self.reveal_button = Gtk.Button(icon_name="view-reveal-symbolic", tooltip_text="Show password",
                                        valign=Gtk.Align.CENTER)
        self.reveal_button.add_css_class("flat")
        self.reveal_button.connect("clicked", self._toggle_password_visibility)
        self.password_row.add_suffix(self.reveal_button)
        self.password_copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy password",
                                        valign=Gtk.Align.CENTER)
        self.password_copy.add_css_class("flat")
        self.password_copy.connect("clicked", lambda *_: self._copy(self._profile().password, "Password copied"))
        self.password_row.add_suffix(self.password_copy)
        details.add(self.password_row)

        self.security_row = Adw.ActionRow(title="Security")
        details.add(self.security_row)

        self.band_row = Adw.ActionRow(title="Band and channel")
        details.add(self.band_row)

        self.address_row = Adw.ActionRow(title="Hotspot address")
        details.add(self.address_row)

        self.uplink_row = Adw.ActionRow(title="Internet connection")
        details.add(self.uplink_row)

        box.append(details)

        self.clients_group = Adw.PreferencesGroup(title="Connected devices",
                                                  description="Devices using this hotspot")
        self.empty_row = Adw.ActionRow(title="No devices connected",
                                       subtitle="Join the hotspot to see devices here")
        self.clients_group.add(self.empty_row)
        box.append(self.clients_group)

        clamp = Adw.Clamp(maximum_size=560, tightening_threshold=420)
        clamp.set_child(box)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(clamp)
        return scroller

    # ----------------------------------------------------------- settings page

    def _build_settings_page(self):
        page = Adw.PreferencesPage()

        network = Adw.PreferencesGroup(title="Network")
        self.ssid_entry = Adw.EntryRow(title="Network name")
        self.ssid_entry.connect("changed", self._on_changed)
        network.add(self.ssid_entry)

        self.security_combo = Adw.ComboRow(title="Security")
        self.security_combo.set_model(Gtk.StringList.new([SECURITY_LABELS[s] for s in SECURITY_ORDER]))
        self.security_combo.connect("notify::selected", self._on_security_changed)
        network.add(self.security_combo)

        self.password_entry = Adw.PasswordEntryRow(title="Password")
        self.password_entry.connect("changed", self._on_changed)
        network.add(self.password_entry)

        generate_row = Adw.ActionRow(title="New password",
                                     subtitle="Create a random 12 character password")
        generate_button = Gtk.Button(label="Generate", valign=Gtk.Align.CENTER)
        generate_button.connect("clicked", self._generate_password)
        generate_row.add_suffix(generate_button)
        generate_row.set_activatable_widget(generate_button)
        self.generate_row = generate_row
        network.add(generate_row)

        self.hidden_switch = Adw.SwitchRow(title="Hidden network",
                                           subtitle="Do not broadcast the network name")
        self.hidden_switch.connect("notify::active", self._on_changed)
        network.add(self.hidden_switch)
        page.add(network)

        radio = Adw.PreferencesGroup(title="Radio")
        self.band_combo = Adw.ComboRow(title="Band")
        self.band_combo.set_model(Gtk.StringList.new([BAND_LABELS[b] for b in BAND_ORDER]))
        self.band_combo.connect("notify::selected", self._on_band_changed)
        radio.add(self.band_combo)

        self.channel_combo = Adw.ComboRow(title="Channel")
        self.channel_combo.set_model(Gtk.StringList.new(["Automatic"]))
        self.channel_combo.connect("notify::selected", self._on_channel_changed)
        radio.add(self.channel_combo)

        self.width_combo = Adw.ComboRow(title="Channel width")
        self.width_combo.set_model(Gtk.StringList.new(WIDTH_LABELS))
        self.width_combo.connect("notify::selected", self._on_changed)
        radio.add(self.width_combo)

        self.isolation_switch = Adw.SwitchRow(
            title="Client isolation",
            subtitle="Stop connected devices from reaching each other")
        self.isolation_switch.connect("notify::active", self._on_changed)
        radio.add(self.isolation_switch)

        self.pmf_combo = Adw.ComboRow(title="Management frame protection")
        self.pmf_combo.set_model(Gtk.StringList.new(["Default", "Optional", "Required"]))
        self.pmf_combo.connect("notify::selected", self._on_changed)
        radio.add(self.pmf_combo)
        page.add(radio)

        sharing = Adw.PreferencesGroup(
            title="Sharing",
            description="Connecting devices receive an address through shared mode")
        self.ipv4_entry = Adw.EntryRow(title="IPv4 address")
        self.ipv4_entry.connect("changed", self._on_changed)
        self.ipv4_entry.set_tooltip_text("Leave empty for automatic (10.42.0.1/24)")
        sharing.add(self.ipv4_entry)

        self.dhcp_entry = Adw.EntryRow(title="DHCP range")
        self.dhcp_entry.connect("changed", self._on_changed)
        self.dhcp_entry.set_tooltip_text("Two addresses, for example 10.42.0.10,10.42.0.200")
        sharing.add(self.dhcp_entry)

        self.lease_entry = Adw.EntryRow(title="DHCP lease time")
        self.lease_entry.connect("changed", self._on_changed)
        self.lease_entry.set_tooltip_text("For example 1h or 30m")
        sharing.add(self.lease_entry)

        self.ipv6_combo = Adw.ComboRow(title="IPv6")
        self.ipv6_combo.set_model(Gtk.StringList.new(IPV6_LABELS))
        self.ipv6_combo.connect("notify::selected", self._on_changed)
        sharing.add(self.ipv6_combo)

        self.uplink_switch = Adw.SwitchRow(
            title="Keep Wi-Fi while sharing",
            subtitle="Broadcast from a second virtual interface so the current Wi-Fi stays connected")
        self.uplink_switch.connect("notify::active", self._on_changed)
        sharing.add(self.uplink_switch)

        self.ttl_entry = Adw.EntryRow(title="Tethering TTL")
        self.ttl_entry.connect("changed", self._on_changed)
        self.ttl_entry.set_tooltip_text(
            "Rewrite the TTL of traffic from hotspot clients, for example 65. "
            "Leave empty to keep packets untouched")
        sharing.add(self.ttl_entry)
        page.add(sharing)

        regulatory = Adw.PreferencesGroup(
            title="Regulatory",
            description="The country sets which Wi-Fi channels and power levels are allowed")
        self.country_entry = Adw.EntryRow(title="Country code")
        self.country_entry.connect("changed", self._on_changed)
        self.country_entry.set_max_length(2)
        self.country_entry.set_tooltip_text("Two letter code, for example US. Empty means automatic")
        regulatory.add(self.country_entry)

        self.regulatory_row = Adw.ActionRow(title="Radio regulatory domain")
        self.regulatory_row.set_subtitle("—")
        regulatory.add(self.regulatory_row)
        page.add(regulatory)

        startup = Adw.PreferencesGroup(title="Startup")
        self.autoconnect_switch = Adw.SwitchRow(title="Start hotspot automatically",
                                                subtitle="Turn the hotspot on when the phone boots")
        self.autoconnect_switch.connect("notify::active", self._on_changed)
        startup.add(self.autoconnect_switch)
        page.add(startup)

        return page

    # ------------------------------------------------------------ initial data

    def _load_initial(self):
        try:
            if not nm.profile_exists():
                nm.create_profile(socket.gethostname() or "Hotspot")
            self.profile = nm.read_profile()
        except nm.NMError as exc:
            self._error("Could not read the hotspot settings", exc)
            self.profile = nm.Profile(ssid="Hotspot", password=nm.generate_password())

        try:
            self._channels = nm.available_channels()
        except nm.NMError:
            pass

        self._load_settings(self.profile)
        self._render_status(self._snapshot)
        self.refresh_async()
        GLib.idle_add(self._clear_focus)

    def _clear_focus(self):
        self.set_focus(None)
        return False

    def _profile(self):
        return self.profile or nm.Profile()

    def _load_settings(self, profile):
        self._loading = True
        self.ssid_entry.set_text(profile.ssid)
        self.security_combo.set_selected(SECURITY_ORDER.index(profile.security))
        self.password_entry.set_text(profile.password)
        self.hidden_switch.set_active(profile.hidden)
        self.band_combo.set_selected(BAND_ORDER.index(profile.band))
        self._set_channels(profile.band, profile.channel)
        self.width_combo.set_selected(WIDTH_VALUES.index(profile.channel_width)
                                      if profile.channel_width in WIDTH_VALUES else 0)
        self.isolation_switch.set_active(profile.ap_isolation)
        self.pmf_combo.set_selected({0: 0, 2: 1, 3: 2}.get(profile.pmf, 0))
        self.ipv4_entry.set_text(profile.ipv4_cidr)
        self.dhcp_entry.set_text(profile.dhcp_range)
        self.lease_entry.set_text(profile.dhcp_lease_time)
        self.ipv6_combo.set_selected(IPV6_ORDER.index(profile.ipv6)
                                     if profile.ipv6 in IPV6_ORDER else 0)
        self.uplink_switch.set_active(profile.keep_uplink)
        self.ttl_entry.set_text(str(profile.ttl) if profile.ttl else "")
        self.country_entry.set_text(profile.country)
        self.autoconnect_switch.set_active(profile.autoconnect)
        self._update_security_sensitivity()
        self._loading = False
        self._set_dirty(False)
        self._update_regulatory_row()

    def _update_regulatory_row(self):
        try:
            global_country, phy_country, self_managed = nm.reg_state()
        except nm.NMError:
            self.regulatory_row.set_subtitle("—")
            return
        parts = []
        if global_country and global_country != "00":
            parts.append(f"system {global_country}")
        else:
            parts.append("system world (00)")
        if self_managed and phy_country:
            parts.append(f"radio {phy_country}, firmware managed")
        if not self._channels.get("6"):
            parts.append("6 GHz disabled by firmware")
        self.regulatory_row.set_subtitle(" · ".join(parts))

    # ------------------------------------------------------------ dirty state

    def _on_changed(self, *_):
        if self._loading:
            return
        self._set_dirty(True)

    def _on_security_changed(self, *_):
        if self._loading:
            return
        self._update_security_sensitivity()
        self._set_dirty(True)

    def _update_security_sensitivity(self):
        secure = SECURITY_ORDER[self.security_combo.get_selected()] != nm.SEC_OPEN
        self.password_entry.set_sensitive(secure)
        self.generate_row.set_sensitive(secure)

    def _set_dirty(self, dirty):
        self.apply_button.set_visible(bool(dirty))

    def _on_band_changed(self, *_):
        if self._loading:
            return
        band = BAND_ORDER[self.band_combo.get_selected()]
        if band == nm.BAND_6:
            self.security_combo.set_selected(SECURITY_ORDER.index(nm.SEC_WPA3))
        self._set_channels(band, 0)
        self._set_dirty(True)

    def _on_channel_changed(self, *_):
        if self._loading:
            return
        self.width_combo.set_sensitive(self._selected_channel() != 0)
        self._set_dirty(True)

    def _selected_channel(self):
        index = self.channel_combo.get_selected()
        if 0 <= index < len(self._channel_values):
            return self._channel_values[index]
        return 0

    def _set_channels(self, band, selected):
        self._channel_values = [0] + (self._channels.get(band, []) if band != nm.BAND_AUTO else [])
        labels = ["Automatic"] + [f"Channel {c}" for c in self._channel_values[1:]]
        if band == nm.BAND_6 and not self._channel_values[1:]:
            labels = ["No 6 GHz channels available"]
        self.channel_combo.set_model(Gtk.StringList.new(labels))
        self.channel_combo.set_sensitive(len(self._channel_values) > 1)
        self.channel_combo.set_selected(
            self._channel_values.index(selected) if selected in self._channel_values else 0)
        self.width_combo.set_sensitive(self._selected_channel() != 0)

    def _generate_password(self, *_):
        self.password_entry.set_text(nm.generate_password())
        self._set_dirty(True)

    # ----------------------------------------------------------- apply/revert

    def _collect_profile(self):
        security = SECURITY_ORDER[self.security_combo.get_selected()]
        band = BAND_ORDER[self.band_combo.get_selected()]
        ttl_text = self.ttl_entry.get_text().strip()
        return nm.Profile(
            ssid=self.ssid_entry.get_text().strip(),
            password=self.password_entry.get_text(),
            security=security,
            band=band,
            channel=self._selected_channel(),
            channel_width=WIDTH_VALUES[self.width_combo.get_selected()],
            hidden=self.hidden_switch.get_active(),
            ap_isolation=self.isolation_switch.get_active(),
            pmf=[0, 2, 3][self.pmf_combo.get_selected()],
            ipv4_cidr=self.ipv4_entry.get_text().strip(),
            dhcp_range=self.dhcp_entry.get_text().strip(),
            dhcp_lease_time=self.lease_entry.get_text().strip(),
            ipv6=IPV6_ORDER[self.ipv6_combo.get_selected()],
            autoconnect=self.autoconnect_switch.get_active(),
            keep_uplink=self.uplink_switch.get_active(),
            ttl=int(ttl_text) if ttl_text.isdigit() else 0,
            country=self.country_entry.get_text().strip().upper(),
        )

    def _apply(self, *_):
        if self._busy:
            return
        profile = self._collect_profile()
        try:
            nm.validate(profile, self._channels)
        except nm.NMError as exc:
            self._error("Check the hotspot settings", exc)
            return

        self._set_busy(True)
        self.apply_button.set_sensitive(False)

        def work():
            nm.apply_profile(profile)
            if nm.is_active():
                nm.activate()
            return nm.read_profile()

        def done(updated):
            self.profile = updated
            self._set_busy(False)
            self.apply_button.set_sensitive(True)
            self._load_settings(updated)
            self._toast("Hotspot settings applied")
            self.refresh_async()

        def failed(exc):
            self._set_busy(False)
            self.apply_button.set_sensitive(True)
            self._error("Could not apply the settings", exc)

        self._run(work, done, failed)

    def _revert(self, *_):
        if self._busy:
            return
        try:
            self.profile = nm.read_profile()
        except nm.NMError as exc:
            self._error("Could not read the hotspot settings", exc)
            return
        self._load_settings(self.profile)
        self._render_status(self._snapshot)
        self._toast("Changes reverted")

    # ---------------------------------------------------------------- toggling

    def _on_toggle(self, switch, _pspec):
        if self._syncing or self._busy:
            return
        self._toggle(switch.get_active())

    def _on_hero_button(self, *_):
        if self._busy:
            return
        self._toggle(not self._snapshot.active)

    def _toggle(self, enabled):
        self._set_busy(True)
        self._sync_toggle(enabled)
        self.hero_state.set_text("Turning on…" if enabled else "Turning off…")

        def done(_result):
            self._set_busy(False)
            self._toast("Hotspot turned on" if enabled else "Hotspot turned off")
            self.refresh_async()

        def failed(exc):
            self._set_busy(False)
            self._sync_toggle(not enabled)
            self._error("Could not start the hotspot" if enabled else "Could not stop the hotspot", exc)
            self.refresh_async()

        self._run(nm.activate if enabled else nm.deactivate, done, failed)

    def _sync_toggle(self, active):
        self._syncing = True
        self.toggle.set_active(active)
        self._syncing = False

    def _set_busy(self, busy):
        self._busy = busy
        self.spinner.set_visible(busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.toggle.set_sensitive(not busy)
        self.hero_button.set_sensitive(not busy)

    # --------------------------------------------------------------- refresh

    def _tick(self):
        self.refresh_async()
        return GLib.SOURCE_CONTINUE

    def refresh_async(self):
        if self._polling:
            return
        self._polling = True

        def worker():
            try:
                snap = nm.snapshot()
            except Exception:
                snap = None
            GLib.idle_add(self._apply_snapshot, snap)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_snapshot(self, snap):
        self._polling = False
        if snap is not None and not self._busy:
            self._snapshot = snap
            self._render_status(snap)
        return False

    def _render_status(self, snap):
        profile = self._profile()
        ssid = snap.ssid or profile.ssid
        self.hero_ssid.set_text(ssid or "Hotspot")

        if snap.active:
            kind = UPLINK_PHRASES.get(snap.uplink_type)
            count = len(snap.clients)
            parts = [f"Sharing {kind}"] if kind else ["On"]
            if count == 1:
                parts.append("1 device")
            elif count:
                parts.append(f"{count} devices")
            self.hero_state.set_text(" · ".join(parts))
            self.hero_button.set_label("Turn Off")
            self.hero.remove_css_class("hero-off")
        else:
            self.hero_state.set_text("Hotspot is off")
            self.hero_button.set_label("Turn On")
            self.hero.add_css_class("hero-off")

        self._sync_toggle(snap.active)

        security = SECURITY_LABELS.get(profile.security, profile.security)
        self.ssid_row.set_subtitle(ssid or "—")
        self.security_row.set_subtitle(security + (" · hidden" if profile.hidden else ""))

        if snap.active and snap.channel:
            self.band_row.set_subtitle(f"{band_label(snap.frequency)} · channel {snap.channel}")
        elif profile.band == nm.BAND_AUTO:
            self.band_row.set_subtitle("Automatic")
        elif profile.channel:
            self.band_row.set_subtitle(f"{BAND_LABELS[profile.band]} · channel {profile.channel}")
        else:
            self.band_row.set_subtitle(f"{BAND_LABELS[profile.band]} · automatic channel")

        self.address_row.set_subtitle(snap.address or "—")
        if snap.uplink:
            kind = UPLINK_KINDS.get(snap.uplink_type, snap.uplink_type or "")
            self.uplink_row.set_subtitle(f"{snap.uplink} ({kind})" if kind else snap.uplink)
        else:
            self.uplink_row.set_subtitle("No internet connection")

        self._render_password()
        self._render_clients(snap.clients if snap.active else [])

    def _render_password(self):
        profile = self._profile()
        if profile.security == nm.SEC_OPEN:
            self.password_label.set_text("None")
            self.reveal_button.set_visible(False)
            self.password_copy.set_visible(False)
            return
        self.reveal_button.set_visible(True)
        self.password_copy.set_visible(True)
        if self._reveal_password:
            self.password_label.set_text(profile.password or "—")
            self.reveal_button.set_icon_name("view-conceal-symbolic")
            self.reveal_button.set_tooltip_text("Hide password")
        else:
            self.password_label.set_text("•" * max(8, len(profile.password)))
            self.reveal_button.set_icon_name("view-reveal-symbolic")
            self.reveal_button.set_tooltip_text("Show password")

    def _toggle_password_visibility(self, *_):
        self._reveal_password = not self._reveal_password
        self._render_password()

    def _render_clients(self, clients):
        wanted = {client.mac: client for client in clients}
        for mac in list(self._client_rows):
            if mac not in wanted:
                row, _icon = self._client_rows.pop(mac)
                self.clients_group.remove(row)
        for mac, client in wanted.items():
            if mac not in self._client_rows:
                icon = Gtk.Image(valign=Gtk.Align.CENTER)
                row = Adw.ActionRow()
                row.add_prefix(icon)
                self.clients_group.add(row)
                self._client_rows[mac] = (row, icon)
            row, icon = self._client_rows[mac]
            row.set_title(client.ip or client.mac)
            details = [client.mac]
            if client.signal:
                details.append(f"{client.signal} dBm")
            if client.rx_rate:
                details.append(client.rx_rate)
            row.set_subtitle(" · ".join(details))
            icon.set_from_icon_name(signal_icon(client.signal))
        self.empty_row.set_visible(not clients)
        self.clients_group.set_title(
            f"Connected devices ({len(clients)})" if clients else "Connected devices")

    # ---------------------------------------------------------------- helpers

    def _run(self, work, on_done, on_error):
        def deliver(callback, value):
            callback(value)
            return False

        def worker():
            try:
                result = work()
            except Exception as exc:
                GLib.idle_add(deliver, on_error, exc)
            else:
                GLib.idle_add(deliver, on_done, result)

        threading.Thread(target=worker, daemon=True).start()

    def _toast(self, message):
        self.toasts.add_toast(Adw.Toast.new(message))

    def _copy(self, text, message):
        if not text:
            return
        Gdk.Display.get_default().get_clipboard().set(text)
        self._toast(message)

    def _copy_password(self, *_):
        profile = self._profile()
        if profile.security == nm.SEC_OPEN:
            self._toast("The hotspot has no password")
            return
        self._copy(profile.password, "Password copied")

    def _error(self, heading, exc):
        dialog = Adw.AlertDialog(heading=heading, body=str(exc))
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)

    def _show_qr(self, *_):
        profile = self._profile()
        path = os.path.join(GLib.get_tmp_dir(), "hotspot-manager-qr.png")
        try:
            subprocess.run(
                ["qrencode", "-t", "PNG", "-s", "8", "-m", "2", "-o", path,
                 nm.wifi_qr_string(profile.ssid, profile.password, profile.security)],
                check=True, capture_output=True, timeout=10)
            texture = Gdk.Texture.new_from_filename(path)
        except (OSError, subprocess.SubprocessError) as exc:
            self._error("Could not create the QR code", exc)
            return

        picture = Gtk.Picture(paintable=texture)
        picture.set_size_request(240, 240)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.append(picture)

        ssid_label = Gtk.Label(label=profile.ssid)
        ssid_label.add_css_class("title-2")
        ssid_label.set_wrap(True)
        box.append(ssid_label)

        hint = Gtk.Label(label="Scan with the other device's camera to join")
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        hint.set_justify(Gtk.Justification.CENTER)
        box.append(hint)

        copy_button = Gtk.Button(label="Copy password")
        copy_button.add_css_class("pill")
        copy_button.set_halign(Gtk.Align.CENTER)
        copy_button.set_margin_top(6)
        copy_button.connect("clicked", lambda *_: self._copy(profile.password, "Password copied"))
        box.append(copy_button)

        header = Adw.HeaderBar()
        dialog = Adw.Dialog()
        dialog.set_title("Join the hotspot")
        dialog.set_content_width(340)
        dialog.set_content_height(520)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(box)
        dialog.set_child(toolbar)
        dialog.present(self)

    def _show_about(self, *_):
        about = Adw.AboutDialog(
            application_name="Hotspot",
            application_icon="io.github.hotspotmanager.Hotspot",
            developer_name="Hotspot Manager",
            version="1.0",
            comments="Manage the Wi-Fi hotspot of your phone with NetworkManager.",
            license_type=Gtk.License.MIT_X11,
        )
        about.present(self)
