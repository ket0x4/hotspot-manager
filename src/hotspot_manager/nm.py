"""NetworkManager / iw / ip helpers for the hotspot manager."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field

CONNECTION = "Hotspot"
HELPER = "/usr/local/libexec/hotspot-manager-net-helper"
STATE_DIR = "/var/lib/hotspot-manager"
TTL_STATE = f"{STATE_DIR}/ttl"
COUNTRY_STATE = f"{STATE_DIR}/country"
SYSFS_NET = "/sys/class/net"

SEC_WPA2 = "wpa2"
SEC_WPA3 = "wpa3"
SEC_OPEN = "open"

BAND_AUTO = "auto"
BAND_24 = "2.4"
BAND_5 = "5"
BAND_6 = "6"

BAND_TO_NM = {BAND_AUTO: "", BAND_24: "bg", BAND_5: "a", BAND_6: "6GHz"}
NM_TO_BAND = {v: k for k, v in BAND_TO_NM.items()}
NM_TO_SEC = {"sae": SEC_WPA3, "none": SEC_OPEN, "owe": SEC_OPEN}

PMF_DEFAULT = 0
PMF_DISABLE = 1
PMF_OPTIONAL = 2
PMF_REQUIRED = 3

PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"

MOBILE_PREFIXES = ("rmnet", "qmapmux", "wwan", "ccmni", "pdp")
WIFI_PREFIXES = ("wl",)
ETHERNET_PREFIXES = ("en", "eth", "usb")

PROFILE_FIELDS = [
    "802-11-wireless.ssid",
    "802-11-wireless.band",
    "802-11-wireless.channel",
    "802-11-wireless.channel-width",
    "802-11-wireless.hidden",
    "802-11-wireless.ap-isolation",
    "802-11-wireless-security.key-mgmt",
    "802-11-wireless-security.psk",
    "802-11-wireless-security.pmf",
    "ipv4.addresses",
    "ipv4.shared-dhcp-range",
    "ipv4.shared-dhcp-lease-time",
    "ipv6.method",
    "connection.autoconnect",
    "connection.interface-name",
]


class NMError(RuntimeError):
    pass


@dataclass
class Profile:
    ssid: str = ""
    password: str = ""
    security: str = SEC_WPA2
    band: str = BAND_AUTO
    channel: int = 0
    channel_width: int = 0
    hidden: bool = False
    ap_isolation: bool = False
    pmf: int = PMF_DEFAULT
    ipv4_cidr: str = ""
    dhcp_range: str = ""
    dhcp_lease_time: str = ""
    ipv6: str = "ignore"
    autoconnect: bool = False
    keep_uplink: bool = True
    ttl: int = 0
    country: str = ""


@dataclass
class Client:
    mac: str
    signal: int = 0
    rx_rate: str = ""
    ip: str = ""


@dataclass
class Snapshot:
    active: bool = False
    iface: str = ""
    ssid: str = ""
    channel: int = 0
    frequency: int = 0
    address: str = ""
    uplink: str = ""
    uplink_type: str = ""
    clients: list[Client] = field(default_factory=list)


# ------------------------------------------------------------------ processes


def _cmd(argv, check=True, timeout=40):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise NMError(f"{argv[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise NMError(f"{' '.join(argv)} timed out") from exc
    if check and proc.returncode != 0:
        lines = (proc.stderr or proc.stdout).strip().splitlines()
        raise NMError(lines[-1] if lines else f"{' '.join(argv)} failed")
    return proc.stdout


def nmcli(*args, check=True, timeout=40):
    return _cmd(["nmcli", *args], check=check, timeout=timeout)


def helper(*args):
    return _cmd(["sudo", "-n", HELPER, *args])


# -------------------------------------------------------------------- profile


def split_terse(line):
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def get_values(fields, check=True):
    out = nmcli("--show-secrets", "-g", ",".join(fields),
                "connection", "show", CONNECTION, check=check)
    return out.splitlines()


def profile_exists():
    return nmcli("-g", "connection.id", "connection", "show",
                 CONNECTION, check=False).strip() != ""


def generate_password(length=12):
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def _read_text(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _read_int(path, fallback=0):
    try:
        return int(_read_text(path))
    except (TypeError, ValueError):
        return fallback


def _int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def read_profile():
    values = get_values(PROFILE_FIELDS)
    values += [""] * (len(PROFILE_FIELDS) - len(values))
    data = dict(zip(PROFILE_FIELDS, values))

    security = NM_TO_SEC.get(data["802-11-wireless-security.key-mgmt"], SEC_WPA2)
    iface = data["connection.interface-name"]
    lease = data["ipv4.shared-dhcp-lease-time"]

    return Profile(
        ssid=data["802-11-wireless.ssid"],
        password=data["802-11-wireless-security.psk"] if security != SEC_OPEN else "",
        security=security,
        band=NM_TO_BAND.get(data["802-11-wireless.band"], BAND_AUTO),
        channel=_int(data["802-11-wireless.channel"]),
        channel_width=_int(data["802-11-wireless.channel-width"]),
        hidden=data["802-11-wireless.hidden"] == "yes",
        ap_isolation=data["802-11-wireless.ap-isolation"] == "1",
        pmf=_int(data["802-11-wireless-security.pmf"], PMF_DEFAULT),
        ipv4_cidr=data["ipv4.addresses"],
        dhcp_range=data["ipv4.shared-dhcp-range"],
        dhcp_lease_time="" if lease in ("", "0", "-1") else lease,
        ipv6=data["ipv6.method"] or "ignore",
        autoconnect=data["connection.autoconnect"] == "yes",
        keep_uplink=iface.endswith("ap") if iface else True,
        ttl=_read_int(TTL_STATE),
        country=_read_text(COUNTRY_STATE),
    )


def create_profile(ssid):
    nmcli(
        "connection", "add", "type", "wifi", "con-name", CONNECTION,
        "ssid", ssid, "autoconnect", "no",
        "ipv4.method", "shared", "ipv6.method", "ignore",
    )


def validate(profile, channels=None):
    if not profile.ssid:
        raise NMError("The network name cannot be empty")
    if len(profile.ssid.encode()) > 32:
        raise NMError("The network name must be at most 32 bytes")
    if profile.security != SEC_OPEN and not 8 <= len(profile.password) <= 63:
        raise NMError("The password must be 8 to 63 characters long")
    if profile.band == BAND_6:
        if profile.security != SEC_WPA3:
            raise NMError("6 GHz networks must use WPA3 Personal")
        if channels is not None and not channels.get("6"):
            raise NMError(
                "6 GHz is unavailable on this phone: the Wi-Fi firmware regulatory "
                "database disables every 6 GHz channel")
        if not profile.channel:
            raise NMError("Choose a 6 GHz channel")
    if profile.ipv4_cidr:
        try:
            ipaddress.IPv4Interface(profile.ipv4_cidr)
        except ValueError as exc:
            raise NMError(f"Invalid IPv4 address: {profile.ipv4_cidr}") from exc
    if profile.dhcp_range:
        parts = profile.dhcp_range.split(",")
        if len(parts) != 2:
            raise NMError("The DHCP range must be two addresses separated by a comma")
        for part in parts:
            try:
                ipaddress.IPv4Address(part.strip())
            except ValueError as exc:
                raise NMError(f"Invalid DHCP range address: {part.strip()}") from exc
    if profile.channel and profile.band == BAND_AUTO:
        raise NMError("Choose a band before choosing a channel")
    if profile.ttl and not 1 <= profile.ttl <= 255:
        raise NMError("The TTL must be between 1 and 255")
    if profile.country and (len(profile.country) != 2 or not profile.country.isalpha()):
        raise NMError("The country code must be two letters, for example US")


def _security_args(profile):
    if profile.security == SEC_OPEN:
        return ["802-11-wireless-security.key-mgmt", "none"]
    if profile.security == SEC_WPA3:
        key_mgmt, pmf = "sae", PMF_REQUIRED
    else:
        key_mgmt = "wpa-psk"
        pmf = profile.pmf if profile.pmf in (PMF_OPTIONAL, PMF_REQUIRED) else PMF_DISABLE
    return [
        "802-11-wireless-security.key-mgmt", key_mgmt,
        "802-11-wireless-security.psk", profile.password,
        "802-11-wireless-security.pmf", str(pmf),
        "802-11-wireless-security.proto", "rsn",
        "802-11-wireless-security.pairwise", "ccmp",
        "802-11-wireless-security.group", "ccmp",
    ]


def _network_args(profile):
    wifi = wifi_iface()
    args = [
        "connection.interface-name", f"{wifi}ap" if profile.keep_uplink else wifi,
        "802-11-wireless.ssid", profile.ssid,
        "802-11-wireless.hidden", "yes" if profile.hidden else "no",
        "802-11-wireless.ap-isolation", "1" if profile.ap_isolation else "0",
        "connection.autoconnect", "yes" if profile.autoconnect else "no",
        "ipv4.method", "shared",
        "ipv4.addresses", profile.ipv4_cidr,
        "ipv4.shared-dhcp-range", profile.dhcp_range,
        "ipv4.shared-dhcp-lease-time", profile.dhcp_lease_time,
        "ipv6.method", profile.ipv6,
    ]
    args += _security_args(profile)
    if profile.channel and profile.band != BAND_AUTO:
        args += [
            "802-11-wireless.band", BAND_TO_NM[profile.band],
            "802-11-wireless.channel", str(profile.channel),
            "802-11-wireless.channel-width", str(profile.channel_width or 0),
        ]
    else:
        args += [
            "802-11-wireless.channel-width", "0",
            "802-11-wireless.channel", "",
            "802-11-wireless.band", "",
        ]
    return args


def apply_profile(profile):
    validate(profile)
    if not profile_exists():
        create_profile(profile.ssid)
    nmcli("connection", "modify", CONNECTION, *_network_args(profile))
    helper("ttl-set", str(profile.ttl or 0))
    set_country(profile.country)


# -------------------------------------------------------------------- devices


def active_devices():
    out = nmcli("-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active", check=False)
    result = []
    for line in out.splitlines():
        fields = split_terse(line)
        if len(fields) >= 3:
            result.append({"name": fields[0], "device": fields[1], "type": fields[2]})
    return result


def is_active():
    return any(conn["name"] == CONNECTION for conn in active_devices())


def wifi_iface():
    physical = [name for name in sorted(os.listdir(SYSFS_NET))
                if os.path.exists(f"{SYSFS_NET}/{name}/device")
                and os.path.exists(f"{SYSFS_NET}/{name}/phy80211")]
    if physical:
        return physical[0]

    out = nmcli("-t", "-f", "DEVICE,TYPE", "device", "status", check=False)
    for line in out.splitlines():
        fields = split_terse(line)
        if len(fields) >= 2 and fields[1] == "wifi" and not fields[0].endswith("ap"):
            return fields[0]
    raise NMError("No Wi-Fi interface found")


def ensure_ap_iface():
    wifi = wifi_iface()
    ap = f"{wifi}ap"
    if os.path.isdir(f"{SYSFS_NET}/{ap}"):
        return ap
    helper("ensure", wifi, ap)
    if not os.path.isdir(f"{SYSFS_NET}/{ap}"):
        raise NMError("Could not create the virtual access point interface")
    return ap


def activate():
    wifi = wifi_iface()
    profile = read_profile()
    expected = ensure_ap_iface() if profile.keep_uplink else wifi
    current = get_values(["connection.interface-name"], check=False)
    if (current[0] if current else "") != expected:
        nmcli("connection", "modify", CONNECTION, "connection.interface-name", expected)
    nmcli("connection", "up", CONNECTION, timeout=90)
    apply_ttl(profile, expected)


def deactivate():
    nmcli("connection", "down", CONNECTION, check=False)
    try:
        helper("ttl-clear")
    except NMError:
        pass


# ------------------------------------------------------------------ tethering TTL


def apply_ttl(profile, iface):
    if profile.ttl:
        helper("ttl-apply", iface, str(profile.ttl))
    else:
        helper("ttl-clear")


# ------------------------------------------------------------------- regulatory


def reg_state():
    """Return (global_country, phy_country, self_managed)."""
    out = _cmd(["iw", "reg", "get"], check=False)
    global_country = ""
    phy_country = ""
    self_managed = False
    in_phy = False
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("phy#"):
            in_phy = True
            self_managed = "self-managed" in stripped
        elif stripped.startswith("country "):
            code = stripped.split()[1].rstrip(":")
            if in_phy and not phy_country:
                phy_country = code
            elif not in_phy and not global_country:
                global_country = code
    return global_country, phy_country, self_managed


def set_country(code):
    code = (code or "").strip().upper()
    if not code:
        helper("reg-clear")
        return
    if len(code) != 2 or not code.isalpha():
        raise NMError("The country code must be two letters, for example US")
    helper("reg-set", code)


# ------------------------------------------------------------------- interface


def parse_iface_info(text):
    details = {"ssid": "", "channel": 0, "frequency": 0}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ssid "):
            details["ssid"] = stripped.split(None, 1)[1]
        elif stripped.startswith("channel "):
            match = re.match(r"channel (\d+) \((\d+) MHz\)", stripped)
            if match:
                details["channel"] = int(match.group(1))
                details["frequency"] = int(match.group(2))
    return details


def iface_details(iface):
    return parse_iface_info(_cmd(["iw", "dev", iface, "info"], check=False))


def ipv4_address(iface):
    out = _cmd(["ip", "-4", "-o", "addr", "show", "dev", iface], check=False)
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", out)
    return match.group(1) if match else ""


def default_uplink(connections=None):
    if connections is None:
        connections = active_devices()
    try:
        routes = json.loads(_cmd(["ip", "-j", "route", "get", "1.1.1.1"], check=False) or "[]")
    except (json.JSONDecodeError, TypeError):
        return "", ""
    device = routes[0].get("dev", "") if routes else ""
    if not device:
        return "", ""

    for conn in connections:
        if conn["device"] == device:
            return conn["name"], conn["type"]
    if device.startswith(MOBILE_PREFIXES):
        mobile = next((c for c in connections if c["type"] in ("gsm", "cdma")), None)
        return (mobile["name"], mobile["type"]) if mobile else ("Mobile data", "gsm")
    if device.startswith(WIFI_PREFIXES):
        return "Wi-Fi", "802-11-wireless"
    if device.startswith(ETHERNET_PREFIXES):
        return "Ethernet", "ethernet"
    return device, ""


# ---------------------------------------------------------------------- clients


def parse_neighbors(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if "lladdr" in parts:
            ip = parts[0]
            result[parts[parts.index("lladdr") + 1].lower()] = ip
    return result


def neighbor_ips(iface):
    return parse_neighbors(_cmd(["ip", "-4", "neigh", "show", "dev", iface], check=False))


def parse_station_dump(text):
    stations = []
    current = None
    for line in text.splitlines():
        match = re.match(r"Station ([0-9a-fA-F:]{17})", line.strip())
        if match:
            if current:
                stations.append(current)
            current = Client(mac=match.group(1).lower())
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("signal:"):
            signal = re.search(r"signal:\s*(-?\d+)", stripped)
            if signal:
                current.signal = int(signal.group(1))
        elif stripped.startswith("rx bitrate:"):
            current.rx_rate = stripped.split(":", 1)[1].strip()
    if current:
        stations.append(current)
    return stations


def clients(iface):
    if not iface or not os.path.isdir(f"{SYSFS_NET}/{iface}"):
        return []
    stations = parse_station_dump(_cmd(["iw", "dev", iface, "station", "dump"], check=False))
    ips = neighbor_ips(iface)
    for station in stations:
        station.ip = ips.get(station.mac, "")
    return stations


def snapshot():
    connections = active_devices()
    active = next((c for c in connections if c["name"] == CONNECTION), None)
    snap = Snapshot(active=active is not None)
    snap.uplink, snap.uplink_type = default_uplink(connections)
    if not active:
        return snap
    snap.iface = active["device"]
    details = iface_details(snap.iface)
    snap.ssid = details["ssid"]
    snap.channel = details["channel"]
    snap.frequency = details["frequency"]
    snap.address = ipv4_address(snap.iface)
    snap.clients = clients(snap.iface)
    return snap


# --------------------------------------------------------------------- channels


def parse_channels(text):
    channels = {"2.4": [], "5": [], "6": []}
    for line in text.splitlines():
        found = re.match(r"\s*\*\s*([\d.]+) MHz \[(\d+)\](.*)", line)
        if not found:
            continue
        freq = int(float(found.group(1)))
        channel, rest = int(found.group(2)), found.group(3)
        if "disabled" in rest or "no IR" in rest:
            continue
        if freq < 3000:
            band = "2.4"
        elif freq < 5925:
            band = "5"
        else:
            band = "6"
        channels[band].append(channel)
    return channels


def available_channels():
    out = _cmd(["iw", "dev", wifi_iface(), "info"], check=False)
    match = re.search(r"wiphy (\d+)", out)
    if not match:
        return {"2.4": [], "5": [], "6": []}
    return parse_channels(_cmd(["iw", f"phy{match.group(1)}", "channels"], check=False))


def wifi_qr_string(ssid, password, security):
    def escape(value):
        return "".join(f"\\{char}" if char in '\\;,:"' else char for char in value)

    kind = "nopass" if security == SEC_OPEN else "WPA"
    return f"WIFI:T:{kind};S:{escape(ssid)};P:{escape(password)};;"
