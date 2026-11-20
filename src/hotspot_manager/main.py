import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk

from .window import HotspotWindow

CSS = """
.hero-card {
    padding: 26px 18px 22px 18px;
    border-radius: 18px;
}
.hero-icon {
    color: @accent_color;
}
.hero-off .hero-icon {
    color: @insensitive_fg_color;
}
.hero-ssid {
    font-size: 1.5rem;
    font-weight: 800;
}
.hero-card button.pill {
    padding: 10px 28px;
}
"""


class HotspotApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.hotspotmanager.Hotspot",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.connect("startup", self._on_startup)

    def _on_startup(self, _app):
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_activate(self):
        window = self.props.active_window
        if not window:
            window = HotspotWindow(self)
        window.present()


def main():
    return HotspotApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
