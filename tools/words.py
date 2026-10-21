"""Print little-endian 32-bit words from a flat image.

usage: python -I tools/words.py <image.bin> <addr> <count> [base=0x08000000]
"""
import struct
import sys

data = open(sys.argv[1], "rb").read()
addr = int(sys.argv[2], 0)
count = int(sys.argv[3], 0)
base = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x08000000
for i in range(count):
    off = addr - base + 4 * i
    (v,) = struct.unpack_from("<I", data, off)
    print(f"0x{addr + 4 * i:08X}  [{(addr - base) // 4 + i:3}]  0x{v:08X}")
