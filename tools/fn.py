"""Print the decompiled C (and optionally the listing) of functions from a Ghidra export dir.

usage: python -I tools/fn.py <export_dir> <addr> [addr ...] [--asm]
"""
import re
import sys

args = [a for a in sys.argv[2:] if not a.startswith("--")]
want_asm = "--asm" in sys.argv
d = sys.argv[1]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
decomp = open(f"{d}/decomp.c", encoding="utf-8").read()
blocks = re.split(r"(?m)^// ===== ", decomp)
by_addr = {}
for b in blocks[1:]:
    by_addr[int(b[:10], 16)] = "// ===== " + b
lines = open(f"{d}/listing.asm", encoding="utf-8").read().splitlines() if want_asm else []

for a in args:
    a = int(a, 0)
    print(by_addr.get(a, f"// no function at 0x{a:08X}").rstrip())
    if want_asm:
        on = False
        for ln in lines:
            if ln.startswith(";===== FUNCTION "):
                on = int(ln.split()[2], 16) == a
                continue
            if on:
                print(ln)
    print()
