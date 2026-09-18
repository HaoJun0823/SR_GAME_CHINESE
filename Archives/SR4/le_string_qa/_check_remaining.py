import re, json, sys
stem = sys.argv[1] if len(sys.argv) > 1 else "customize_us"
LINE_RE = re.compile(r'^\s*"(?P<k>(?:[^"\\]|\\.)*)"\s*:\s*"(?P<v>(?:[^"\\]|\\.)*)"\s*;?\s*$')
cjk = re.compile(r'[一-鿿]')
tp = f".qa/_trans_{stem}.json"
tr = json.load(open(tp, encoding="utf-8")) if __import__("os").path.exists(tp) else {}
rem = []
for line in open(f"zh/{stem}.txt", encoding="utf-8-sig", newline=""):
    m = LINE_RE.match(line.rstrip("\n"))
    if m and not cjk.search(m.group("v")):
        rem.append((m.group("k"), m.group("v")))
print(f"[{stem}] remaining non-CJK: {len(rem)}")
for k, v in rem:
    print("  ", repr(k), "=>", repr(v), "| in_trans=", k in tr)
