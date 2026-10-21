"""Parse a USB configuration descriptor (and its HID class descriptors) stored in a flat image.

usage: python -I tools/usbdesc.py <image.bin> <config_desc_addr> [base=0x08000000]
"""
import struct
import sys

data = open(sys.argv[1], "rb").read()
addr = int(sys.argv[2], 0)
base = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x08000000
off = addr - base
total = struct.unpack_from("<H", data, off + 2)[0]
print(f"config descriptor @0x{addr:08X} wTotalLength={total}")
end = off + total
while off < end:
    ln, typ = data[off], data[off + 1]
    d = data[off:off + ln]
    a = base + off
    if typ == 2:
        print(f"  0x{a:08X} CONFIG  numIf={d[4]} cfgVal={d[5]} iCfg={d[6]} attr=0x{d[7]:02X} maxPower={d[8] * 2}mA")
    elif typ == 4:
        print(f"  0x{a:08X} INTERFACE #{d[2]} alt={d[3]} numEP={d[4]} class=0x{d[5]:02X} sub=0x{d[6]:02X} proto=0x{d[7]:02X} iIf={d[8]}")
    elif typ == 0x21:
        rl = struct.unpack_from("<H", d, 7)[0]
        print(f"  0x{a:08X} HID     bcdHID=0x{struct.unpack_from('<H', d, 2)[0]:04X} reportDescLen={rl}")
    elif typ == 5:
        ep = d[2]
        print(f"  0x{a:08X} ENDPOINT 0x{ep:02X} ({'IN' if ep & 0x80 else 'OUT'}) attr=0x{d[3]:02X} maxPkt={struct.unpack_from('<H', d, 4)[0]} interval={d[6]}")
    else:
        print(f"  0x{a:08X} type 0x{typ:02X} len {ln}: {d.hex(' ')}")
    if ln == 0:
        break
    off += ln
