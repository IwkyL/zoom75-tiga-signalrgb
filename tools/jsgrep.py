"""Search a (minified) JS file for a regex and print each hit with surrounding context.

usage: python -I tools/jsgrep.py <file.js> <regex> [context_chars=200] [max_hits=50]
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
pat = re.compile(sys.argv[2])
ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 200
mx = int(sys.argv[4]) if len(sys.argv) > 4 else 50
hits = list(pat.finditer(text))
print(f"{len(hits)} hits")
for m in hits[:mx]:
    s, e = max(0, m.start() - ctx), min(len(text), m.end() + ctx)
    print(f"--- @{m.start()}")
    print(text[s:e])
