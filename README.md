# Hotspot Manager

A GTK4 and libadwaita hotspot manager made for GNOME Mobile and Phosh. It talks to
NetworkManager, so it manages the same network the rest of the system sees.

All settings live in a single NetworkManager connection profile named `Hotspot`.

## Features

- Turn the hotspot on and off from the header switch or the large status card
- Live status: network name, password, security, band and channel, hotspot address,
  uplink connection and the devices currently connected with their signal strength
- Change network name, password, security (WPA2, WPA3 or open) and generate passwords
- Bands: automatic, 2.4 GHz, 5 GHz and 6 GHz, with fixed channel and channel width
  picked from the channels the Wi-Fi card reports as usable
- Hidden network, client isolation and management frame protection
- Sharing options: IPv4 address, DHCP range, DHCP lease time, IPv6 mode and a tethering
  TTL rewrite so hotspot traffic looks like it came from the phone
- Regulatory country override, applied to the system and persisted across reboots
- Keep the current Wi-Fi connection alive while broadcasting by sharing from a virtual
  access point interface (`wlp1s0ap`), or hand the radio to the hotspot completely
- QR code and one tap copy for joining, plus start on boot
- Settings are validated before they are written and applied through `nmcli`

## Install on the phone

Copy the repository to the phone and run the installer as your user:

```sh
scp -r hotspot_manager alarm@10.151.90.30:~/
ssh alarm@10.151.90.30
cd ~/hotspot_manager
./install.sh
```

The installer asks for the sudo password and then:

- installs the app into `/usr/local/lib/hotspot-manager` with a launcher in `/usr/local/bin`
- installs the desktop entry and icon so it appears in the app grid
- installs a polkit rule in `/etc/polkit-1/rules.d/60-hotspot-manager.rules` so users in
  `wheel` can manage NetworkManager connections. This is needed because Phosh has no
  polkit agent to answer password prompts
- installs a sudoers rule for `/usr/local/libexec/hotspot-manager-net-helper`, the only
  privileged helper. It creates the virtual AP interface, applies the nftables TTL rule
  and sets the regulatory country. Every argument it accepts is validated
- installs and enables `hotspot-ap-interface.service` so the AP interface and the saved
  country code exist before NetworkManager starts

Then open **Hotspot** from the app grid, or run `hotspot-manager`.

## Notes

- **6 GHz**: the Wi-Fi card reports 60 channels in the 6 GHz band, but the phone's
  firmware board data marks every one of them disabled, so the radio never offers a
  usable 6 GHz channel. The app lists the band and explains this when it is selected.
  The country override does not help because this radio's regulatory domain is
  self-managed by the firmware. Replacing the firmware regulatory database did not
  unlock it either.
- **WPA3** needs PMF, so the app forces RSN/CCMP with required PMF when WPA3 is selected.
  NetworkManager 1.58 has no WPA2+WPA3 transition mode, so the two are separate options.
- **Tethering TTL** rewrites the TTL of packets forwarded from hotspot clients in an
  nftables mangle table so upstream networks see the phone's own TTL. The table is
  removed when the hotspot stops.
- Sharing from a virtual interface needs the Wi-Fi card to support AP and managed mode
  at the same time. Use **Keep Wi-Fi while sharing** only if your card allows it; the
  phone's card does.
- If you turn **Keep Wi-Fi while sharing** off, the hotspot takes over the radio and the
  phone's own Wi-Fi uplink drops until the hotspot is turned off.
- Changing the network name, password or security while clients are connected
  re-activates the hotspot so the new settings take effect.
