"""Print a character range of a (minified) JS file, optionally re-wrapped at ';' and '{' for reading.

usage: python -I tools/jsslice.py <file.js> <start> <length> [wrap=1]
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
s, n = int(sys.argv[2]), int(sys.argv[3])
chunk = text[s:s + n]
if len(sys.argv) <= 4 or sys.argv[4] != "0":
    chunk = re.sub(r";", ";\n", chunk)
    chunk = re.sub(r"\{", "{\n", chunk)
print(chunk)
