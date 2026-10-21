"""Build the SignalRGB LED table (index, name, canvas position) for the Zoom75 TIGA
from firmware data plus the official VIA definition, so nothing is typed by hand.

Sources (all local, all checked):
  - g_led_config.matrix_co   analysis/g_led_config.json  (decoded from flash 0x08018B1A
                             by tools/ledconfig.py): which LED index belongs to which
                             matrix position [row][col].
  - default keymap layer 0   analysis/tiga_v11.bin @ 0x08017A06, uint16 [8][17]: the
                             keycode of each matrix position, used for the LED name.
  - VIA definition           downloads/via_tiga/Zoom75_Tiga_rgb-via-v3.json: the physical
                             key positions (KLE) and the layout options.
  - g_led_config.point       only cross-checked against the VIA positions and reported.

The canvas grid is in half key units (one key = 2 cells), so 1u keys never share a cell.

usage: python -I tools/gen_tiga_layout.py [--json out.json] [--quiet]
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDCFG = ROOT / "analysis" / "g_led_config.json"
FWBIN = ROOT / "analysis" / "tiga_v11.bin"
VIAJSON = ROOT / "downloads" / "via_tiga" / "Zoom75_Tiga_rgb-via-v3.json"

FW_BASE = 0x08000000
KEYMAP_ADDR = 0x08017A06          # [layer][8][17] uint16, see FEASIBILITY.md section 5
KEYMAP_ROWS, KEYMAP_COLS = 8, 17
ORIG_HEX = ROOT / "original" / "WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex"
ORIG_SHA256 = "678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b"
NO_LED = 0xFF
N_LEDS = 86

# QMK basic keycodes -> SignalRGB "Supported Key Names"
# (docs.signalrgb.com/developer/plugins/key-names/).
KC_NAMES = {
    0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab", 0x28: "Enter", 0x2C: "Space",
    0x39: "Caps Lock", 0x35: "`", 0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]",
    0x31: "\\", 0x33: ";", 0x34: "'", 0x36: ",", 0x37: ".", 0x38: "/",
    0x4C: "Del", 0x49: "Insert", 0x4A: "Home", 0x4D: "End", 0x4B: "Page Up",
    0x4E: "Page Down", 0x4F: "Right Arrow", 0x50: "Left Arrow", 0x51: "Down Arrow",
    0x52: "Up Arrow", 0xE0: "Left Ctrl", 0xE1: "Left Shift", 0xE2: "Left Alt",
    0xE3: "Left Win", 0xE4: "Right Ctrl", 0xE5: "Right Shift", 0xE6: "Right Alt",
    0xE7: "Right Win", 0x5221: "Fn",            # 0x5221 = MO(1), the Fn key
}
KC_NAMES.update({4 + i: chr(ord("A") + i) for i in range(26)})
KC_NAMES.update({0x1E + i: str(i + 1) for i in range(9)})
KC_NAMES[0x27] = "0"
KC_NAMES.update({0x3A + i: f"F{i + 1}" for i in range(12)})
KC_NAMES.update({0x68 + i: f"F{i + 13}" for i in range(12)})

# LEDs whose switch exists only in a non-default VIA layout option. They sit under
# a wider default keycap (2U Backspace, 2.25U Left Shift, 6.25U Space), so they get
# a descriptive name instead of a duplicate key name.
ALT_NAMES = {
    (1, 13): "Backspace (split L)",
    (4, 1): "Left Shift (split R)",
    (5, 3): "Space (split L)",
    (5, 6): "Space (split M / 7U)",
    (5, 7): "Space (split R)",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_fwbin():
    """tiga_v11.bin must be the flat image of the original hex."""
    from intelhex import IntelHex  # local import: only needed for this check

    if sha256(ORIG_HEX) != ORIG_SHA256:
        sys.exit("original hex SHA-256 mismatch")
    ih = IntelHex(str(ORIG_HEX))
    flat = ih.tobinstr(start=FW_BASE, end=ih.maxaddr())
    if FWBIN.read_bytes() != flat:
        sys.exit(f"{FWBIN} is not the flat image of the original hex")
    return flat


def parse_kle(rows):
    """Minimal KLE parser: returns keys with x, y, w, h (key units) and labels."""
    keys = []
    y = 0.0
    for row in rows:
        x = 0.0
        w = h = 1.0
        for item in row:
            if isinstance(item, dict):
                x += item.get("x", 0)
                y += item.get("y", 0)
                w = item.get("w", w)
                h = item.get("h", h)
                continue
            labels = item.split("\n")
            keys.append({"x": x, "y": y, "w": w, "h": h, "labels": labels})
            x += w
            w = h = 1.0
        y += 1.0
    return keys


def via_positions(via):
    """Matrix position -> list of (cx, cy, group, option), aligned the way VIA draws
    layout options: every option's bounding box is moved onto option 0's."""
    keys = []
    for k in parse_kle(via["layouts"]["keymap"]):
        lab = k["labels"]
        if not lab[0] or "," not in lab[0]:
            continue                           # encoder or decal, no matrix position
        r, c = (int(v) for v in lab[0].split(","))
        grp = opt = None
        if len(lab) > 3 and lab[3]:
            grp, opt = (int(v) for v in lab[3].split(","))
        keys.append(dict(k, r=r, c=c, grp=grp, opt=opt))

    boxes = {}
    for k in keys:
        if k["grp"] is None:
            continue
        b = boxes.setdefault((k["grp"], k["opt"]), [1e9, 1e9])
        b[0] = min(b[0], k["x"])
        b[1] = min(b[1], k["y"])
    for k in keys:
        if k["grp"] is not None and k["opt"] != 0:
            ref = boxes[(k["grp"], 0)]
            own = boxes[(k["grp"], k["opt"])]
            k["x"] += ref[0] - own[0]
            k["y"] += ref[1] - own[1]

    default = [k for k in keys if k["grp"] is None or k["opt"] == 0]
    x0 = min(k["x"] for k in default)
    y0 = min(k["y"] for k in default)
    out = {}
    for k in keys:
        cx = k["x"] - x0 + k["w"] / 2
        cy = k["y"] - y0 + k["h"] / 2
        if k["h"] > 1:                          # ISO enter: use its top row
            cy = k["y"] - y0 + 0.5
        out.setdefault((k["r"], k["c"]), []).append((cx, cy, k["grp"], k["opt"]))
    return out


def choose(cands):
    """Prefer a key that is always present, then option 0, then the lowest option."""
    return sorted(cands, key=lambda t: (t[2] is not None, t[3] or 0, t[2] or 0))[0]


def main():
    argv = sys.argv[1:]
    json_out = None
    if "--json" in argv:
        json_out = argv[argv.index("--json") + 1]
    quiet = "--quiet" in argv

    flat = check_fwbin()
    led = json.loads(LEDCFG.read_text())
    mco, pts = led["matrix_co"], led["point"]
    via = json.loads(VIAJSON.read_text(encoding="utf-8"))
    if (via["vendorId"], via["productId"]) != ("0x1EA7", "0xCEDD"):
        sys.exit("VIA definition is not for 1EA7:CEDD")
    pos = via_positions(via)

    o = KEYMAP_ADDR - FW_BASE
    keymap = [struct.unpack_from(f"<{KEYMAP_COLS}H", flat, o + r * KEYMAP_COLS * 2)
              for r in range(KEYMAP_ROWS)]

    where = {}
    for r, row in enumerate(mco):
        for c, v in enumerate(row):
            if v != NO_LED:
                if v in where:
                    sys.exit(f"LED {v} mapped twice")
                where[v] = (r, c)
    if sorted(where) != list(range(N_LEDS)):
        sys.exit("matrix_co does not map every LED 0..85 exactly once")

    table = []
    problems = []
    for i in range(N_LEDS):
        r, c = where[i]
        kc = keymap[r][c]
        if (r, c) not in pos:
            sys.exit(f"LED {i} at matrix {r},{c} has no key in the VIA layout")
        cx, cy, grp, opt = choose(pos[(r, c)])
        alt_only = all(g is not None and o_ != 0 for _, _, g, o_ in pos[(r, c)])
        if alt_only:
            name = ALT_NAMES.get((r, c))
            if name is None:
                sys.exit(f"LED {i} ({r},{c}) is alternate-layout only and has no name")
        else:
            name = KC_NAMES.get(kc)
            if name is None:
                sys.exit(f"LED {i} ({r},{c}) keycode 0x{kc:04X} has no SignalRGB name")
        gx = int(2 * cx) - 1
        gy = int(2 * cy) - 1
        fx, fy = pts[i]
        table.append(dict(led=i, row=r, col=c, keycode=f"0x{kc:04X}", name=name,
                          alt_only=alt_only, cx=cx, cy=cy, grid=[gx, gy],
                          fw_point=[fx, fy]))

    names = [t["name"] for t in table]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        problems.append(f"duplicate names: {dup}")
    cells = [tuple(t["grid"]) for t in table]
    if len(set(cells)) != len(cells):
        problems.append("two LEDs share a canvas cell")
    size = [max(x for x, _ in cells) + 1, max(y for _, y in cells) + 1]

    if not quiet:
        print(f"{'led':>3} {'mtx':>5} {'keycode':>7}  {'name':<22} {'via cx,cy':>11} "
              f"{'grid':>7} {'fw x,y':>8} {'fw/14':>5} {'dx':>5}")
        for t in table:
            fwu = t["fw_point"][0] / 14.0 + 0.5      # firmware x converted to key units
            print(f"{t['led']:3} {t['row']:2},{t['col']:<2} {t['keycode']:>7}  "
                  f"{t['name']:<22} {t['cx']:5.2f},{t['cy']:4.2f} "
                  f"{t['grid'][0]:3},{t['grid'][1]:<3} {t['fw_point'][0]:3},{t['fw_point'][1]:<4} "
                  f"{fwu:5.2f} {fwu - t['cx']:+5.2f}")
        print(f"canvas size {size}; alternate-only LEDs: "
              f"{[t['led'] for t in table if t['alt_only']]}")
        m29 = where[29]
        print(f"LED 29 is matrix {m29[0]},{m29[1]} (keycode {table[29]['keycode']}); "
              f"matrix_co row 1 holds {[v for v in mco[1] if v != NO_LED]}")
    for p in problems:
        print("PROBLEM:", p)
    if json_out:
        Path(json_out).write_text(json.dumps(
            {"size": size, "names": names, "positions": [t["grid"] for t in table],
             "leds": list(range(N_LEDS)), "table": table}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
