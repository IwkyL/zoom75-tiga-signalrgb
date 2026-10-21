"""Parse an Intel HEX image: segments, size, vector table, start address; write a flat .bin.

usage: python -I tools/hexinfo.py <image.hex> [out.bin]
"""
import struct
import sys

from intelhex import IntelHex

path = sys.argv[1]
ih = IntelHex(path)
segs = ih.segments()
print("segments:")
for a, b in segs:
    print(f"  0x{a:08X} - 0x{b - 1:08X}  ({b - a} bytes)")
lo, hi = ih.minaddr(), ih.maxaddr()
print(f"min 0x{lo:08X} max 0x{hi:08X} span {hi - lo + 1} bytes ({(hi - lo + 1) / 1024:.1f} KiB)")
print("start address record:", ih.start_addr)

data = ih.tobinstr(start=lo, end=hi)  # gaps padded with 0xFF
if len(sys.argv) > 2:
    open(sys.argv[2], "wb").write(data)
    print("wrote", sys.argv[2], len(data))

names = ["initial_SP", "Reset", "NMI", "HardFault", "MemManage", "BusFault", "UsageFault",
         "rsv7", "rsv8", "rsv9", "rsv10", "SVCall", "DebugMon", "rsv13", "PendSV", "SysTick"]
vec = struct.unpack_from("<64I", data, 0)
print("vector table:")
for i, v in enumerate(vec):
    n = names[i] if i < 16 else f"IRQ{i - 16}"
    print(f"  [{i:2}] {n:<11} 0x{v:08X}")
