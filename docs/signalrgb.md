# SignalRGB on the Zoom75 TIGA

Two plugins, one per USB device, both in [`plugin/`](../plugin):

| File | Device | Firmware needed |
|---|---|---|
| `Meletrix_Zoom75_TIGA_SignalRGB.js` | `1EA7:CEDD`, the keyboard on the **USB cable** | patch v1 or v2 |
| `Meletrix_Zoom75_TIGA_24G_SignalRGB.js` | `6D66:8888`, the **2.4G receiver** | patch v2 |

Install both: SignalRGB uses whichever device is connected. (Two files because SignalRGB
matches a plugin by `VendorId()` / `ProductId()`, and documents an array only for
`ProductId()`.)

- [Install](#install)
- [On the cable](#on-the-cable)
- [Over 2.4G](#over-24g)
- [Troubleshooting](#troubleshooting)
- [How the plugins work](#how-the-plugins-work)
- [LED layout](#led-layout)
- [Known limits](#known-limits)

## Install

1. Exit SignalRGB from its tray icon (closing the window only hides it).
2. Copy both plugin files into the `WhirlwindFX\Plugins` folder inside **your Documents
   folder**. SignalRGB's docs give `%USERPROFILE%\Documents\WhirlwindFX\Plugins`, or
   `...\OneDrive\Documents\...` when OneDrive redirects Documents. The folder name is
   localised (e.g. `Documenti` on an Italian Windows); SignalRGB creates it on first start.
   From PowerShell in the repository folder, this asks Windows where Documents really is:

   ```powershell
   Copy-Item plugin\Meletrix_Zoom75_TIGA_SignalRGB.js, plugin\Meletrix_Zoom75_TIGA_24G_SignalRGB.js -Destination (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'WhirlwindFX\Plugins')
   ```

3. Start SignalRGB. The keyboard shows up as **Meletrix Zoom75 TIGA** on the cable, or
   **Meletrix Zoom75 TIGA 2.4G** through the receiver, with Meletrix as the publisher. (Before
   version 1.2.0 the names ended in "(SignalRGB patch)".)

Plugins in that folder survive SignalRGB updates. Close VIA and the Meletrix Console while
SignalRGB runs: they share the keyboard's raw HID interface (SignalRGB's docs say the same
for every QMK keyboard). Close the Wuque flashing tool too; it locks that interface while open.

Each device page has two options: **Lighting Mode** (Canvas or Forced) and **Forced Color**.
There is no "shutdown colour": the keyboard returns to its own effect 2 s after SignalRGB
stops, and at once when SignalRGB exits normally.

## On the cable

- About **50 frames per second**, smooth (the keyboard accepts one packet every 2 ms, and a
  frame is 10 packets).
- LEDs are capped at 60 % brightness on USB power, as for the keyboard's own effects.
- PC sleep and wake work: the keyboard shows its own effect while the PC sleeps, and
  SignalRGB takes over again after wake.

## Over 2.4G

Needs patch v2 on the keyboard and the 2.4G plugin.

1. Unplug the cable, press **Fn+V** (2.4G mode). Leave the receiver plugged into the PC.
2. **After plugging the cable in or out, press Fn+V again.** Plugging in switches the keyboard
   to USB mode, and unplugging does not switch it back: the radio link stays up, but the
   keyboard then treats every packet as a VIA command. The 2.4G plugin notices, pauses and
   shows a notice; after Fn+V it picks up again by itself within about 10 s. If it doesn't,
   press Fn+V once more (once, the first Fn+V after an unplug did not take; the second did).
3. Start SignalRGB (or keep it running).

With the cable plugged in while SignalRGB runs, the cable plugin takes over (smooth) and the
2.4G plugin pauses.

**What to expect** (measured, see [hardware-results.md](hardware-results.md)):

- The receiver carries about **one packet every 80–100 ms**, a tenth of a frame. The plugin
  therefore sends only the 9-key groups that changed, at most one per frame. A single key
  change shows within about 0.2 s, a full-board change sweeps across in about 0.8–1 s, a still
  scene is refreshed every 1.5 s. **Best for static and slow scenes**; moving effects follow
  in visible steps.
- Typing while SignalRGB streams is normal.
- On battery the LEDs are capped at 20 % brightness, as for the keyboard's own effects.
- **Battery:** with nothing streaming, the keyboard sleeps (LEDs and LCD off) about 11–13 min
  after the last key press; a key press wakes it, and the 2.4G link is back about 20 s later.
  **While SignalRGB streams, it does not sleep**, so it uses battery the whole time SignalRGB
  runs, like a keyboard in constant use with its LEDs on. Use the cable, or close SignalRGB,
  to save battery. Details: [technical/power-and-sleep.md](technical/power-and-sleep.md).

## Troubleshooting

SignalRGB's log (`%LOCALAPPDATA%\WhirlwindFX\SignalRgb\Logs`) shows every plugin message as a
line starting with `[TIGA]`: the handshake, mode on/off, shutdown and any problem. Nothing is
logged per frame.

| Symptom | Cause and fix |
|---|---|
| Notice "SignalRGB not active", log "No SignalRGB reply" (cable) | Patched firmware not flashed, VIA / Console / Wuque tool holding the interface, or the keyboard on stock firmware (it answers `0xFF`). The plugin retries 5 times, 5 s apart. |
| Notice "SignalRGB paused" / "not in 2.4G mode" (receiver) | The keyboard is on the cable, on Bluetooth, or was unplugged without Fn+V. Unplug the cable and press Fn+V. |
| Log "No reply over 2.4G" | The keyboard is off or asleep (press a key), or not paired with this receiver. The plugin keeps retrying every 10 s. |
| Only part of the board follows over 2.4G | Normal for a moment after a change; the rest arrives within about a second. Fast effects never fully catch up (see above). |
| Plugin replaced but nothing changed | SignalRGB was still running in the tray: exit it from the tray icon and start it again. |

## How the plugins work

Both files are **generated** from one source, [`plugin/src/tiga_plugin.src.js`](../plugin/src/tiga_plugin.src.js),
by `tools/gen_plugins.py`, so they can't drift apart: they differ only in the header (name,
VID/PID, `const Radio`). They speak the SRGBmods QMK protocol 1.0.6 on the raw HID interface
(interface 1, usage page `0xFF60`, usage `0x61`). They started from SRGBmods' plugin template
([SRGBmods/qmk-plugins](https://github.com/SRGBmods/qmk-plugins)).

**Cable plugin:**

- Initialize: queries `0x28 0x21 0x22 0x23 0x27`, one reply each. It streams only if the
  keyboard reports protocol 1.0.6, 86 LEDs and firmware type 1 or 2; otherwise it shows a
  notification and sends no colour data. It retries the handshake every 5 s, 5 times, in case
  VIA held the interface at start. Then it sends `0x25` (SignalRGB mode on).
- Render: 86 LEDs as 10 `0x24` packets (9 LEDs each, the last one 5).
- Shutdown: `0x26`. If SignalRGB keeps rendering afterwards (e.g. after the PC resumes), the
  plugin sends `0x25` again by itself.
- Version 1.1.0 and later send exactly what version 1.0.1 sent (1.2.0 changed only the name,
  publisher and credit header); the test suite compares every write, read and notification
  with a copy of 1.0.1.

**2.4G plugin, what it does differently:**

- Handshake: stale reports flushed, then `0x22`, `0x28`, `0x21`, `0x27`, one at a time, up to
  250 ms per reply, the next packet at least 150 ms after a query and 80 ms after its reply.
  `0x23` is not sent. Same gate as on the cable. Then `25 01`: SignalRGB mode on with the
  firmware's quiet switch, so colour packets get no reply (half the radio traffic).
- Render: at most **one** packet per frame, at least 80 ms after the previous packet (100 ms
  after `0x25`/`0x26`): a 9-LED group not sent for 3 s first, else the next group that
  changed (round robin), else, in a still scene, the group sent longest ago once 1.5 s old.
- `0x25` once per session and `0x26` once at shutdown, never periodically: `0x23`, `0x25`,
  `0x26` and `0x27` are each one flipped bit away from a VIA write command, and the radio path
  has no checksum ([technical/2.4g-link.md](technical/2.4g-link.md)).
- Replies are read every frame. `0xFF` + a copy of a packet means VIA got it: the keyboard is
  not in 2.4G mode. The plugin stops, shows a notice, and retries with one `0x22` every 5 s
  (five times), then every 10 s. An exact echo of a colour packet means the keyboard lost its
  SignalRGB state; the plugin sends one `25 01`.
- A colour packet whose bytes 6–7 happen to form a valid vendor-command checksum is sent with
  one colour bit changed, so the receiver never sees a valid checksummed command.

## LED layout

`tools/gen_tiga_layout.py` builds the LED table from firmware data (the LED matrix in
`g_led_config` and the layer-0 keymap) plus the physical key positions in the official VIA
definition. The canvas uses half-key cells (31 × 11), so no two LEDs share a cell; key names
follow SignalRGB's list. All 86 LEDs are mapped exactly once.

- LED 29 (Backspace) is wired to matrix row 3, column 13, a free column next to Enter, so the
  firmware lists it in row 3 although it sits in the number row.
- Five LEDs belong to switches that exist only in alternative layouts, so with the default
  keycaps they sit under a wider key: 28 (Backspace, split L), 61 (Left Shift, split R),
  77/79/80 (split Space and 7U Space).

## Known limits

- **No SignalRGB over Bluetooth.** In BT mode the keyboard shows its own effect.
- **2.4G is slow** (about one packet per 80–100 ms) and needs **Fn+V after every cable plug or
  unplug**; **no sleep while SignalRGB streams over 2.4G** (see above).
- **Frame rate on the cable is capped at about 50 frames/s** by the firmware's USB task.
- **Brightness is capped by the firmware**: 60 % on USB power, 20 % on battery. Intended: it
  keeps the vendor's current limit.
- **Vendor indicator LEDs** (Caps Lock, mode and so on) are still drawn over SignalRGB's colours.
- **No shutdown colour:** 2 s after the last packet the keyboard shows its own effect.
- The firmware's own LED coordinates for the Shift row are about 0.9 key widths off from the
  physical keys; the plugins use the VIA positions instead.
- **Soft reset while streaming** (inferred, not seen): if the keyboard resets within its first
  2 s of uptime while SignalRGB is on, the LEDs could stay dark for the rest of those 2 s.
