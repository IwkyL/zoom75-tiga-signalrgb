"""Report runs of undefined bytes ('??' lines) in a Ghidra listing export.

usage: python -I tools/gaps.py <listing.asm> [min_len=16]
"""
import re
import sys

min_len = int(sys.argv[2]) if len(sys.argv) > 2 else 16
rx = re.compile(r"^0x([0-9A-F]{8})\s+([0-9a-f]+)\s+\?\? ")
runs, cur = [], None
for ln in open(sys.argv[1], encoding="utf-8"):
    m = rx.match(ln)
    if m:
        a = int(m.group(1), 16)
        b = bytes.fromhex(m.group(2))
        if cur and cur[0] + len(cur[1]) == a:
            cur[1].extend(b)
        else:
            if cur:
                runs.append(cur)
            cur = [a, bytearray(b)]
    elif ln.startswith("0x"):
        if cur:
            runs.append(cur)
            cur = None
if cur:
    runs.append(cur)

big = [r for r in runs if len(r[1]) >= min_len]
total = sum(len(r[1]) for r in runs)
ff = sum(r[1].count(0xFF) for r in runs)
print(f"undefined runs: {len(runs)}, bytes: {total} (0xFF bytes among them: {ff}); runs >= {min_len}: {len(big)}")
for a, b in big:
    print(f"0x{a:08X} len {len(b):5}  {bytes(b[:16]).hex(' ')}")
