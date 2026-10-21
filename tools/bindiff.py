"""Summarize where two flat images differ, per 2 KB flash page.

usage: python -I tools/bindiff.py <a.bin> <b.bin> [base=0x08000000] [page=0x800]
"""
import sys

a = open(sys.argv[1], "rb").read()
b = open(sys.argv[2], "rb").read()
base = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x08000000
page = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x800
n = max(len(a), len(b))
a = a.ljust(n, b"\xff")
b = b.ljust(n, b"\xff")
first = next((i for i in range(n) if a[i] != b[i]), None)
print(f"sizes {len(a)} / {len(b)}; first difference at 0x{base + first:08X}" if first is not None else "identical")
for p in range(0, n, page):
    d = sum(1 for i in range(p, min(p + page, n)) if a[i] != b[i])
    print(f"  page 0x{base + p:08X}: {d:5} differing bytes")
