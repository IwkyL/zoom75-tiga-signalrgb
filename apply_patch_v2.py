"""Make patch v2 from the official Zoom75 TIGA firmware v1.1, without a compiler.

The official firmware belongs to Meletrix and is not in this repository. Download it
yourself (Meletrix's firmware page for the Zoom75 TIGA, version 1.1):
    WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex
    SHA-256 678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b

This script
  1. refuses any input with a different SHA-256 (the patch addresses fit only that build);
  2. checks the original bytes at the four hook sites;
  3. appends the prebuilt patch code from prebuilt/ (compiled from patch_v2/ by
     build_patch_v2.py; both SHA-256 are checked): 480 B of v1 at 0x0801A828 and 528 B of v2
     at 0x0801AA08, ending at 0x0801AC17, inside the official sectors (README section 1);
  4. rewrites the four BL instructions (hooks A-D);
  5. writes <out>/patched_v2.hex in the original's record layout, but only if its SHA-256 is
     1f1f1c469d437d6d8626d9f667e32f0788b4798807b25c71fe8524f4772fe59d, the build that was
     verified offline and on a keyboard. Otherwise nothing is written.

Needs only Python 3 (standard library). Flash the result with Meletrix's
WuqueDFUupgradeProgrammer, normal upgrade ("erase necessary sectors"); read README.md
sections 1 and 3 first.

usage: python apply_patch_v2.py <official v1.1 .hex> [--out DIR]
"""
import argparse
import hashlib
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ORIGINAL_SHA256 = "678ec1441940c56e88626294f91c08628be6f2180ab56b8d1f5398906fb75d4b"
RESULT_SHA256 = "1f1f1c469d437d6d8626d9f667e32f0788b4798807b25c71fe8524f4772fe59d"
BLOBS = [   # (file, load address, SHA-256)
    ("srgb_v1.bin", 0x0801A828, "b9ca4c09157a8c57f7bc27e97c1bb22b998d4b64fa04e3b8fd35dabbbf850f0b"),
    ("srgb_radio.bin", 0x0801AA08, "20d03fff719bc15d5a5fa60cdb6327cc0b24a639733728915ee43b16c1844358"),
]
FLASH_BASE = 0x08000000
IMAGE_END = 0x0801A828          # first byte after the official image
PATCH_LIMIT = 0x0801B000        # end of sector 53
# (hook, site, original bytes, new target): build_patch_v2.py HOOKS; targets from the v2 map.
HOOKS = [
    ("A", 0x08014D42, bytes.fromhex("fdf763fb"), 0x0801A828),  # USB HID task -> srgb_rx (v1)
    ("B", 0x0801017C, bytes.fromhex("00f0a2f8"), 0x0801AB70),  # rgb_matrix_task -> srgb_render2
    ("C", 0x080140E2, bytes.fromhex("fbf79dfa"), 0x0801AA08),  # +DATA: -> srgb_rx_radio
    ("D", 0x0801413E, bytes.fromhex("f3f787ff"), 0x0801AB40),  # reply line -> srgb_radio_send
]


def die(msg):
    sys.exit(f"apply_patch_v2: ERROR: {msg}")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def encode_bl(src, dst):
    """Thumb-2 BL encoding T1 for a call from src to dst (build_patch_v2.py)."""
    off = dst - (src + 4)
    if off & 1 or not -(1 << 24) <= off < (1 << 24):
        die(f"BL out of range: 0x{src:08X} -> 0x{dst:08X}")
    s = (off >> 24) & 1
    i1 = (off >> 23) & 1
    i2 = (off >> 22) & 1
    imm10 = (off >> 12) & 0x3FF
    imm11 = (off >> 1) & 0x7FF
    j1 = (~i1 ^ s) & 1
    j2 = (~i2 ^ s) & 1
    return struct.pack("<HH", 0xF000 | (s << 10) | imm10, 0xD000 | (j1 << 13) | (j2 << 11) | imm11)


def parse_hex(text):
    """Flat image from FLASH_BASE; refuses gaps, bad checksums and other record types."""
    image, seg = {}, 0
    for n, line in enumerate(text.splitlines(), 1):
        if not line.startswith(":"):
            die(f"bad hex line {n}")
        raw = bytes.fromhex(line[1:])
        if sum(raw) & 0xFF or raw[0] != len(raw) - 5:
            die(f"bad record in hex line {n}")
        kind, addr16, data = raw[3], raw[1] << 8 | raw[2], raw[4:-1]
        if kind == 0:
            for k, b in enumerate(data):
                image[seg + addr16 + k] = b
        elif kind == 4:
            seg = (data[0] << 8 | data[1]) << 16
        elif kind not in (1, 5):
            die(f"unexpected record type {kind} in line {n}")
    if min(image) != FLASH_BASE or max(image) + 1 != IMAGE_END or len(image) != IMAGE_END - FLASH_BASE:
        die("the image does not cover exactly 0x08000000-0x0801A827")
    return bytearray(image[a] for a in range(FLASH_BASE, IMAGE_END))


def hex_lines_patched(orig_text, image, new_end):
    """build_patch_v2.py: re-emit the original hex, changing only data records whose bytes
    differ, and extend the image with 16-byte records up to new_end."""
    def record(addr16, data):
        body = bytes([len(data), addr16 >> 8, addr16 & 0xFF, 0]) + data
        return ":" + (body + bytes([(-sum(body)) & 0xFF])).hex().upper()

    parsed, seg = [], 0
    for line in orig_text.splitlines():
        raw = bytes.fromhex(line[1:])
        if raw[3] == 4:
            seg = ((raw[4] << 8) | raw[5]) << 16
        parsed.append((line, raw, seg))
    last_data = max(i for i, (_, raw, _) in enumerate(parsed) if raw[3] == 0)
    out = []
    for idx, (line, raw, seg) in enumerate(parsed):
        if raw[3] != 0:
            out.append(line)
            continue
        n, addr16 = raw[0], (raw[1] << 8) | raw[2]
        a = seg + addr16
        if idx == last_data:
            n = max(n, min(16 - a % 16, new_end - a))
        new = bytes(image[a - FLASH_BASE:a - FLASH_BASE + n])
        out.append(record(addr16, new) if new != raw[4:-1] else line)
        if idx == last_data:
            cur = a + n
            while cur < new_end:
                if cur >> 16 != seg >> 16:
                    die("patch would need a new extended-address record")
                take = min(16, new_end - cur)
                out.append(record(cur & 0xFFFF, bytes(image[cur - FLASH_BASE:cur - FLASH_BASE + take])))
                cur += take
    return "\r\n".join(out) + "\r\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("hex", type=Path, help="the official firmware v1.1 .hex")
    ap.add_argument("--out", type=Path, default=Path("."), help="output folder (default: current folder)")
    args = ap.parse_args()

    raw = args.hex.read_bytes()
    if sha256(raw) != ORIGINAL_SHA256:
        die(f"{args.hex.name} has SHA-256 {sha256(raw)}; this patch fits only the official v1.1 build "
            f"{ORIGINAL_SHA256}")
    text = raw.decode("ascii")
    image = parse_hex(text)
    for name, site, orig, _ in HOOKS:
        if image[site - FLASH_BASE:site - FLASH_BASE + 4] != orig:
            die(f"hook {name} at 0x{site:08X} does not hold the expected original bytes")
    for name, load, digest in BLOBS:
        blob = (ROOT / "prebuilt" / name).read_bytes()
        if sha256(blob) != digest:
            die(f"prebuilt/{name} has SHA-256 {sha256(blob)}, expected {digest}")
        if load != FLASH_BASE + len(image):
            die(f"prebuilt/{name} must load at 0x{FLASH_BASE + len(image):08X}")
        image += blob
    new_end = FLASH_BASE + len(image)
    if new_end > PATCH_LIMIT:
        die("patched image would end past 0x0801AFFF")
    for _, site, _, target in HOOKS:
        image[site - FLASH_BASE:site - FLASH_BASE + 4] = encode_bl(site, target)

    result = hex_lines_patched(text, image, new_end).encode("ascii")
    if sha256(result) != RESULT_SHA256:
        die(f"result SHA-256 {sha256(result)} is not the verified build {RESULT_SHA256}; nothing written")
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / "patched_v2.hex"
    if out.exists() and out.read_bytes() != result:
        die(f"{out} exists and differs; move it away first")
    out.write_bytes(result)
    print(f"wrote {out}\n  SHA-256 {RESULT_SHA256} (the verified patch v2 build)\n"
          f"  image 0x08000000-0x{new_end - 1:08X}, {len(result)} bytes")


if __name__ == "__main__":
    main()
