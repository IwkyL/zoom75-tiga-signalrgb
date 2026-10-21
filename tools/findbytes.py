"""Find byte patterns in a flat image and hexdump around each hit.

usage: python -I tools/findbytes.py <image.bin> <hexpattern> [dump_len=48] [base=0x08000000]
       hexpattern like "12 01 00 02" ('??' matches any byte)
"""
import re
import sys

data = open(sys.argv[1], "rb").read()
toks = sys.argv[2].split()
dump = int(sys.argv[3]) if len(sys.argv) > 3 else 48
base = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x08000000
rx = re.compile(b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)])) for t in toks), re.S)


def hexdump(off, n):
    for i in range(off, min(off + n, len(data)), 16):
        row = data[i:min(i + 16, off + n)]
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in row)
        print(f"  0x{base + i:08X}  {row.hex(' '):<48}  {asc}")


hits = [m.start() for m in rx.finditer(data)]
print(f"{len(hits)} hit(s) for {sys.argv[2]}")
for h in hits[:40]:
    print(f"-- 0x{base + h:08X}")
    hexdump(h, dump)
