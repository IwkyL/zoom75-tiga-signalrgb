"""Decode a QMK-style g_led_config { matrix_co[ROWS][COLS]; point[N]{x,y}; flags[N]; } from flash.

usage: python -I tools/ledconfig.py <image.bin> <addr> <rows> <cols> <n_leds> [base=0x08000000] [--json out.json]
"""
import json
import sys

argv = sys.argv[1:]
json_out = None
if "--json" in argv:
    i = argv.index("--json")
    json_out = argv[i + 1]
    del argv[i:i + 2]
args = argv
data = open(args[0], "rb").read()
addr, rows, cols, n = (int(x, 0) for x in args[1:5])
base = int(args[5], 0) if len(args) > 5 else 0x08000000
o = addr - base

mco = [list(data[o + r * cols: o + (r + 1) * cols]) for r in range(rows)]
po = o + rows * cols
pts = [(data[po + 2 * i], data[po + 2 * i + 1]) for i in range(n)]
fo = po + 2 * n
flags = list(data[fo: fo + n])

print("matrix_co (LED index per [row][col], -- = NO_LED):")
for r in range(rows):
    print(f"  r{r}: " + " ".join("--" if v == 0xFF else f"{v:2}" for v in mco[r]))
mapped = sorted(v for row in mco for v in row if v != 0xFF)
print(f"mapped LEDs: {len(mapped)}, min {mapped[0]}, max {mapped[-1]}, duplicates: {len(mapped) - len(set(mapped))}")
print(f"unmapped LED indices < {n}: {sorted(set(range(n)) - set(mapped))}")
print("points / flags:")
for i in range(n):
    print(f"  led {i:2}: x={pts[i][0]:3} y={pts[i][1]:3} flags=0x{flags[i]:02X}")
end = fo + n
print(f"struct ends at 0x{base + end:08X}; next bytes: {data[end:end + 8].hex(' ')}")
if json_out:
    json.dump({"matrix_co": mco, "point": pts, "flags": flags}, open(json_out, "w"), indent=1)
