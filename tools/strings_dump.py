"""Dump printable ASCII and UTF-16LE strings with their flash addresses.

usage: python -I tools/strings_dump.py <image.bin> [base=0x08000000] [minlen=4]
"""
import re
import sys

path = sys.argv[1]
base = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x08000000
minlen = int(sys.argv[3]) if len(sys.argv) > 3 else 4
data = open(path, "rb").read()

for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, data):
    print(f"A 0x{base + m.start():08X} {m.group().decode()!r}")
for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % minlen, data):
    print(f"U 0x{base + m.start():08X} {m.group().decode('utf-16le')!r}")
