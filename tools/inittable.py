"""Decode the vendor auto-init ("export") table: records of {name_str*, desc_str*, fn*}.

usage: python -I tools/inittable.py <image.bin> <start> <end> [base=0x08000000]
"""
import struct
import sys

data = open(sys.argv[1], "rb").read()
start, end = int(sys.argv[2], 0), int(sys.argv[3], 0)
base = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x08000000


def cstr(addr):
    off = addr - base
    if not 0 <= off < len(data):
        return None
    e = data.find(b"\0", off)
    s = data[off:e]
    if not s or any(c < 32 or c > 126 for c in s):
        return None
    return s.decode()


a = start
while a + 12 <= end:
    n, d, f = struct.unpack_from("<III", data, a - base)
    ns, ds = cstr(n), cstr(d)
    if ns is not None and ds is not None:
        print(f"0x{a:08X}  fn=0x{f:08X}  {ns!r:32} {ds!r}")
        a += 12
    else:
        a += 4
