"""Decode the vendor CRC-framed HID command table (layout inferred from FUN_0800AC7C in TIGA v1.1).

group (12 bytes): [0]=cmd byte (frame byte 0), [1]=flag, [3]=flag, [5]=entry count, [8..11]=entry table
entry (12 bytes): [0]=sub-command (frame byte 1, 0 = any), [1]=reply length, [4..7]=reply data ptr, [8..11]=handler

usage: python -I tools/vendortable.py <image.bin> <table_addr> [groups=8] [base=0x08000000]
"""
import struct
import sys

data = open(sys.argv[1], "rb").read()
tab = int(sys.argv[2], 0)
groups = int(sys.argv[3], 0) if len(sys.argv) > 3 else 8
base = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x08000000


def u32(a):
    return struct.unpack_from("<I", data, a - base)[0]


def cstr(a):
    if not base <= a < base + len(data):
        return ""
    o = a - base
    e = data.find(b"\0", o, o + 40)
    s = data[o:e]
    return s.decode() if s and all(32 <= c < 127 for c in s) else ""


for g in range(groups):
    ga = tab + 12 * g
    gb = data[ga - base: ga - base + 12]
    cmd, f1, f3, cnt = gb[0], gb[1], gb[3], gb[5]
    et = u32(ga + 8)
    print(f"group {g}: cmd=0x{cmd:02X} flag1={f1} flag3={f3} entries={cnt} table=0x{et:08X}")
    for i in range(cnt):
        ea = et + 12 * i
        eb = data[ea - base: ea - base + 12]
        dp, fn = u32(ea + 4), u32(ea + 8)
        s = cstr(dp)
        print(f"   sub=0x{eb[0]:02X} len={eb[1]:3} data=0x{dp:08X}{' ' + repr(s) if s else ''} handler=0x{fn:08X}")
