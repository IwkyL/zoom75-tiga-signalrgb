"""Rebuild the RAM image of a Keil/armlink scatter-load region compressed with __decompress1.

Transcribed from the firmware's own decompressor (TIGA v1.1 @ 0x08000EE8):
  token t: literal count = t & 7 (0 -> next byte), copies (count - 1) literal bytes;
           n = t >> 4 (0 -> next byte);
           bit 3 set:   back-reference, offset = next byte, copy n + 2 bytes from out[-offset];
           bit 3 clear: n zero bytes.

usage: python -I tools/keil_rw_decompress.py <image.bin> <load_addr> <out_size> <out.bin> [base=0x08000000]
"""
import sys

data = open(sys.argv[1], "rb").read()
load = int(sys.argv[2], 0)
size = int(sys.argv[3], 0)
base = int(sys.argv[5], 0) if len(sys.argv) > 5 else 0x08000000

i = load - base
out = bytearray()
while len(out) < size:
    t = data[i]; i += 1
    lit = t & 7
    if lit == 0:
        lit = data[i]; i += 1
    n = t >> 4
    if n == 0:
        n = data[i]; i += 1
    for _ in range(lit - 1):
        out.append(data[i]); i += 1
    if t & 8:
        off = data[i]; i += 1
        for _ in range(n + 2):
            out.append(out[-off])
    else:
        out.extend(b"\0" * n)

print(f"consumed {i - (load - base)} compressed bytes, produced {len(out)} (wanted {size})")
open(sys.argv[4], "wb").write(bytes(out[:size]))
