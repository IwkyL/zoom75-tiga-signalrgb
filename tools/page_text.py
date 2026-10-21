"""Strip an HTML page to readable text (links kept inline) and print the part around a keyword.

usage: python -I tools/page_text.py <page.html> <keyword> [before] [after]
"""
import html
import re
import sys

path, kw = sys.argv[1], sys.argv[2]
before = int(sys.argv[3]) if len(sys.argv) > 3 else 300
after = int(sys.argv[4]) if len(sys.argv) > 4 else 3000

t = open(path, encoding="utf-8", errors="replace").read()
t = re.sub(r"<script.*?</script>", "", t, flags=re.S)
t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
t = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>', r" [LINK \1] ", t)
t = re.sub(r"<[^>]+>", "\n", t)
t = html.unescape(t)
t = re.sub(r"[ \t]+", " ", t)
t = re.sub(r"\n\s*\n+", "\n", t)
idx = [m.start() for m in re.finditer(re.escape(kw), t, flags=re.I)]
print("hits:", idx)
if idx:
    sys.stdout.reconfigure(encoding="utf-8")
    print(t[max(0, idx[0] - before): idx[-1] + after])
