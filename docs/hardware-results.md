# Hardware results

What was tested on a real Zoom75 TIGA (firmware v1.1, patched), with SignalRGB 2.5.80 on
Windows 11, in October 2026. "Script" means `tools/srgb_hidtest.py` checked it automatically;
"seen" means a person watched the keyboard; "log" means SignalRGB's log. The logs themselves
are not in the repository. Details: [technical/2.4g-link.md](technical/2.4g-link.md) and
[technical/power-and-sleep.md](technical/power-and-sleep.md).

## Summary

| Area | Result |
|---|---|
| SignalRGB on the cable | Works, about 50 frames/s, no tearing |
| SignalRGB through the 2.4G receiver | Works, about one packet per 80–100 ms: full-board changes in about 1 s, single keys in about 0.2 s |
| VIA, Meletrix Console, LCD, knob, Bluetooth | Work as before (checked on the keyboard with patch v1; patch v2 passes their packets through unchanged, verified in the emulator) |
| Typing over 2.4G while SignalRGB streams | Normal (patch v2) |
| Fallback to the keyboard's own effect | 2 s after the last packet, at once on `0x26`, unplug, mode switch, PC sleep |
| Sleep on battery (2.4G) | Sleeps about 11–13 min after the last key press when idle; stays awake while SignalRGB streams |

## Patch v1 (USB), 2026-10-20

| Check | Result | How |
|---|---|---|
| Query replies `0x21 0x22 0x23 0x27 0x28` | Byte-exact, one reply each, 2–3 ms | Script, 3 runs |
| Error replies (253 / 254), valid packet with mode off → no reply | As specified | Script |
| Only `0x21`–`0x28` sent, never `0x13` | 5,512 and 5,552 packets | Log of the script |
| Stream while mode off is ignored | Yes | Seen |
| Solid red / green / blue | Whole board | Seen |
| Frame rate | 49.0 and 49.5 frames/s (2.00 ms per packet) | Script |
| Tearing | None | Seen (rainbow) |
| LED walk 0–85 | Every row in the expected order | Seen |
| Fallback 2 s after the last packet | Yes | Seen |
| Resume without a new `0x25` | At once | Seen |
| `0x26` → normal effect | At once | Seen |
| Wuque tool open → interface 1 locked | Windows error 32; interface back once closed | `--list` before and after |
| BT or 2.4G mode with the cable plugged in | USB stays up; streamed colours ignored, normal effect | Script + seen |
| Unplug while streaming | Normal effect | Seen |
| Typing and mode switching during the test | Normal | Seen |
| SignalRGB loads the plugin, handshake, `0x25` | Yes (protocol 1.0.6, 86 LEDs, type VIA) | Log |
| SignalRGB effects on the keyboard | Work | Seen |
| SignalRGB exit sends `0x26` | Yes | Log |
| VIA and the Console afterwards | Work | Checked |
| PC sleep (S3) with SignalRGB running | `0x26` ("system suspending"); LEDs dark for a moment, then the normal effect | Log + seen |
| PC wake | SignalRGB restarts the plugin; its colours return by themselves | Log + seen |

On sleep, SignalRGB 2.5.80 restarts the plugin once more (handshake + `0x25`, no colours)
about 3 s after Shutdown, just before the PC sleeps. The keyboard then holds its last frame
for at most 2 s before the timeout hands the LEDs back; the 2 s timeout exists for this.

## Patch v2 and SignalRGB over 2.4G, 2026-10-20/21

| Check | Result | How |
|---|---|---|
| Wired after flashing v2: read-only, `--led`, SignalRGB | Pass, as with v1 | Script + seen |
| Receiver read-only queries | Byte-exact, 38–94 ms round trip | Script |
| Receiver, packets back to back | Only the first packet of each burst arrives | Script + seen |
| Smallest loss-free spacing | 100 ms with replies (quiet off), 75 ms with quiet on | `--steps pace` |
| Long runs at 100 ms (quiet off) / 80 ms (quiet on) | 3,000/3,000 exact echoes; no reply to 5,167 colour packets (no damage seen); 300/300 probes answered | `--steps soak` |
| Typing while streaming at full load | Normal | Seen |
| Partial updates (the 2.4G plugin's pacing) | A still scene stays, indicators clear, one key walks cleanly, a full-board change takes about 0.8 s | `--steps partial` + seen |
| SignalRGB through the receiver | Handshake, `25 01`; Forced red fills the board; red → blue within about 1 s; a wave follows in steps; typing normal | Log + seen |
| Cable plugged in while SignalRGB runs | The cable plugin takes over, smooth; the 2.4G plugin pauses | Log + seen |
| Unplug, Fn+V | The 2.4G plugin resumes within about 6 s (once the first Fn+V did not take) | Log + seen |
| Exit SignalRGB | `0x26`, own effect at once | Log + seen |
| Stream stops without `0x26` | Own effect back after about 2 s | Script + seen |
| Idle on battery, 2.4G, nothing sent | Asleep (LEDs and LCD off) within about 13 min; 31 probes over 2.5 min unanswered; a key press wakes it, radio back 16–21 s later | `--steps idle` + seen |
| 16 min stream at the plugin's pacing, untouched | Stays awake: colours alternating to the end, 12/12 probes answered | `--steps idle` + seen |
| Two plugin bugs found here, fixed the same day | Queries 80 ms apart: the second arrived damaged (now 150 ms); SignalRGB's `device.read` result is not a plain Array (now copied by index) | Log |

## Plugins 1.2.0 (new names and publisher), 2026-10-27

1.2.0 changed only the device names, the publisher and a comment header at the top of each
file. The data sent to the keyboard is the same as in 1.1.0 (the offline suite compares every
packet), so the results above still apply. This check confirms SignalRGB loads the renamed
files.

| Check | Result | How |
|---|---|---|
| SignalRGB loads both renamed plugin files | Yes | Log |
| Cable: listed as "Meletrix Zoom75 TIGA", plugin 1.2.0, handshake (protocol 1.0.6, 86 LEDs, type VIA), `0x25` | Yes | Log |
| 2.4G: listed as "Meletrix Zoom75 TIGA 2.4G"; with the cable plugged in it waits and the cable plugin streams | As expected | Log |
| SignalRGB effects on the keyboard | Work | Seen |
