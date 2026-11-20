import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hotspot_manager import nm


class TerseTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(nm.split_terse("a:b:c"), ["a", "b", "c"])

    def test_escaped_colon_and_backslash(self):
        self.assertEqual(nm.split_terse(r"my\:net:dev:802-11-wireless"),
                         ["my:net", "dev", "802-11-wireless"])

    def test_empty_field(self):
        self.assertEqual(nm.split_terse("name::wifi"), ["name", "", "wifi"])


class ChannelTests(unittest.TestCase):
    OUTPUT = """
	* 2412 MHz [1] (20.0 dBm)
	  Maximum TX power: 20.0 dBm
	  Channel widths: 20MHz HT40+
	* 2437 MHz [6] (20.0 dBm)
	  Maximum TX power: 20.0 dBm
	  Channel widths: 20MHz HT40- HT40+
	* 2467 MHz [12] (disabled)
	* 2472 MHz [13] (no IR)
	  Maximum TX power: 20.0 dBm
	* 5180 MHz [36] (20.0 dBm)
	  Maximum TX power: 20.0 dBm
	* 5260 MHz [52] (20.0 dBm)
	  Radar detection
	* 5700 MHz [100] (disabled)
    """

    def test_parses_enabled_channels(self):
        channels = nm.parse_channels(self.OUTPUT)
        self.assertEqual(channels["2.4"], [1, 6])
        self.assertEqual(channels["5"], [36, 52])

    def test_skips_disabled_and_no_ir(self):
        channels = nm.parse_channels(self.OUTPUT)
        self.assertNotIn(12, channels["2.4"])
        self.assertNotIn(13, channels["2.4"])
        self.assertNotIn(100, channels["5"])

    def test_empty_output(self):
        self.assertEqual(nm.parse_channels(""), {"2.4": [], "5": [], "6": []})

    def test_six_ghz_band(self):
        output = """
	* 5955.0 MHz [1] (12.0 dBm)
	  Channel widths: 20MHz 40MHz 80MHz 160MHz
	* 5975.0 MHz [5] (12.0 dBm)
	* 6115.0 MHz [33] (disabled)
	* 6135.0 MHz [37] (12.0 dBm)
        """
        channels = nm.parse_channels(output)
        self.assertEqual(channels["6"], [1, 5, 37])
        self.assertEqual(channels["2.4"], [])
        self.assertEqual(channels["5"], [])


class StationTests(unittest.TestCase):
    OUTPUT = """Station aa:bb:cc:dd:ee:ff (on wlp1s0ap)
	authorized:	yes
	signal:  	-48 [-52, -50] dBm
	signal avg:	-49 [-53, -51] dBm
	rx bitrate:	433.3 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 1
	tx bitrate:	390.0 MBit/s VHT-MCS 8 80MHz short GI VHT-NSS 1
	connected time:	120 seconds
Station 11:22:33:44:55:66 (on wlp1s0ap)
	signal:  	-70 dBm
	rx bitrate:	144.4 MBit/s
	connected time:	4 seconds
"""

    def test_two_stations(self):
        stations = nm.parse_station_dump(self.OUTPUT)
        self.assertEqual([s.mac for s in stations], ["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"])
        self.assertEqual(stations[0].signal, -48)
        self.assertEqual(stations[0].rx_rate, "433.3 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 1")
        self.assertEqual(stations[1].signal, -70)
        self.assertEqual(stations[1].rx_rate, "144.4 MBit/s")

    def test_neighbors(self):
        output = ("10.42.0.112 dev wlp1s0ap lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
                  "10.42.0.113 dev wlp1s0ap lladdr 11:22:33:44:55:66 STALE\n"
                  "10.42.0.1 dev wlp1s0ap FAILED\n")
        neighbors = nm.parse_neighbors(output)
        self.assertEqual(neighbors["aa:bb:cc:dd:ee:ff"], "10.42.0.112")
        self.assertEqual(neighbors["11:22:33:44:55:66"], "10.42.0.113")
        self.assertEqual(len(neighbors), 2)


class IfaceInfoTests(unittest.TestCase):
    def test_ap(self):
        output = """Interface wlp1s0ap
	ifindex 22
	wdev 0x5
	addr ca:bc:fe:a1:bf:39
	ssid limonata
	type AP
	channel 6 (2437 MHz), width: 40 MHz, center1: 2437 MHz
"""
        info = nm.parse_iface_info(output)
        self.assertEqual(info["ssid"], "limonata")
        self.assertEqual(info["channel"], 6)
        self.assertEqual(info["frequency"], 2437)


class ValidationTests(unittest.TestCase):
    def test_empty_ssid(self):
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="", password="12345678"))

    def test_short_password(self):
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="net", password="short"))

    def test_open_needs_no_password(self):
        nm.validate(nm.Profile(ssid="net", security=nm.SEC_OPEN, password=""))

    def test_bad_cidr(self):
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="net", password="12345678", ipv4_cidr="10.0.0.300/24"))

    def test_bad_dhcp_range(self):
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="net", password="12345678", dhcp_range="10.0.0.1"))

    def test_channel_requires_band(self):
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="net", password="12345678", channel=6))

    def test_six_ghz_needs_wpa3(self):
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="net", password="12345678",
                                   band=nm.BAND_6, channel=37))

    def test_six_ghz_needs_channel(self):
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="net", password="12345678", security=nm.SEC_WPA3,
                                   band=nm.BAND_6))

    def test_six_ghz_reports_unavailable_channels(self):
        channels = {"2.4": [1], "5": [36], "6": []}
        with self.assertRaises(nm.NMError) as ctx:
            nm.validate(nm.Profile(ssid="net", password="12345678", security=nm.SEC_WPA3,
                                   band=nm.BAND_6, channel=37), channels)
        self.assertIn("6 GHz is unavailable", str(ctx.exception))

    def test_six_ghz_ok(self):
        channels = {"2.4": [1], "5": [36], "6": [37]}
        nm.validate(nm.Profile(ssid="net", password="12345678", security=nm.SEC_WPA3,
                               band=nm.BAND_6, channel=37), channels)

    def test_ttl_range(self):
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="net", password="12345678", ttl=300))

    def test_country_code(self):
        nm.validate(nm.Profile(ssid="net", password="12345678", country="US"))
        with self.assertRaises(nm.NMError):
            nm.validate(nm.Profile(ssid="net", password="12345678", country="USA"))


class QrTests(unittest.TestCase):
    def test_wpa(self):
        self.assertEqual(nm.wifi_qr_string("cafe", "secret123", nm.SEC_WPA2),
                         "WIFI:T:WPA;S:cafe;P:secret123;;")

    def test_open(self):
        self.assertEqual(nm.wifi_qr_string("cafe", "", nm.SEC_OPEN),
                         "WIFI:T:nopass;S:cafe;P:;;")

    def test_escapes_special_characters(self):
        self.assertEqual(nm.wifi_qr_string("a;b", 'p:"x,y\\z', nm.SEC_WPA3),
                         'WIFI:T:WPA;S:a\\;b;P:p\\:\\"x\\,y\\\\z;;')


if __name__ == "__main__":
    unittest.main()
