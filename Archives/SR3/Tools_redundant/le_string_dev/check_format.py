#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""格式保真校验：比对 original/<P>_us.txt 与 schinese/<P>_us.txt
检查项：KEY 一致、格式 token 数量一致(%s/%d/%ls/%i/%f/%%/\\n/\\")、
[format]配对、{占位符}集合一致、空值保真、无 BOM。只读，不改文件。
用法：python check_format.py <prefix>      检查单个（如 activity）
      python check_format.py all            检查全部 21 个
"""
import re, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIG = os.path.join(ROOT, "original")
SCH  = os.path.join(ROOT, "schinese")

LINE_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*$')

def parse_le(fp):
    d = {}
    for line in open(fp, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if not m:
            d.setdefault("__UNPARSED__", []).append(line)
            continue
        d[m.group(1)] = m.group(2)
    return d

def metrics(v):
    return {
        "%s": len(re.findall(r"%s", v)),
        "%d": len(re.findall(r"%d", v)),
        "%ls": len(re.findall(r"%ls", v)),
        "%i": len(re.findall(r"%i", v)),
        "%f": len(re.findall(r"%f", v)),
        "%%": len(re.findall(r"%%", v)),
        "fmt_open": len(re.findall(r"\[format\]", v)),
        "fmt_close": len(re.findall(r"\[/format\]", v)),
        "bs_n": v.count("\\n"),
        # 引号单位：英文源用 \" 转义双引号，中文译文用全角引号 “” 等价替换，二者各计 1 单位。
        # 这样既允许 ASCII 转义引号 -> 全角引号的本地化转换，又仍强制引号数量一致。
        "bs_quote": v.count('\\"') + v.count('\u201c') + v.count('\u201d'),
        "ph": sorted(re.findall(r"\{[^}]*\}", v)),
    }

def check_pair(prefix):
    of = os.path.join(ORIG, f"{prefix}_us.txt")
    sf = os.path.join(SCH, f"{prefix}_us.txt")
    if not os.path.exists(of) or not os.path.exists(sf):
        return f"[SKIP] 缺失文件: {of} / {sf}"
    # BOM check
    with open(sf, "rb") as fh:
        bom = fh.read(3) == b"\xef\xbb\xbf"
    o = parse_le(of)
    s = parse_le(sf)
    problems = []
    if bom:
        problems.append("产物含 BOM")
    if "__UNPARSED__" in o:
        problems.append(f"源有 {len(o['__UNPARSED__'])} 行无法解析")
    if "__UNPARSED__" in s:
        problems.append(f"产物有 {len(s['__UNPARSED__'])} 行无法解析: {s['__UNPARSED__'][:3]}")
    ok, so = set(o) - {"__UNPARSED__"}, set(s) - {"__UNPARSED__"}
    missing = ok - so
    extra = so - ok
    if missing:
        problems.append(f"缺 KEY {len(missing)}: {list(missing)[:5]}")
    if extra:
        problems.append(f"多 KEY {len(extra)}: {list(extra)[:5]}")
    n_check = 0
    for k in ok & so:
        ov, sv = o[k], s[k]
        if ov == "" and sv != "":
            problems.append(f"空值未保真 [{k}] 源空->产物:{sv[:20]}")
            continue
        if ov == "" and sv == "":
            continue
        mo, ms = metrics(ov), metrics(sv)
        diffs = {kk: (mo[kk], ms[kk]) for kk in mo if mo[kk] != ms[kk]}
        if diffs:
            problems.append(f"格式不符 [{k}] {diffs}")
        n_check += 1
    status = "PASS" if not problems else f"FAIL({len(problems)})"
    summ = f"[{status}] {prefix:>14}  检查条目 {n_check}  问题 {len(problems)}"
    if problems:
        summ += "\n   - " + "\n   - ".join(problems[:20])
    return summ

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    if arg == "all":
        files = sorted(f for f in os.listdir(ORIG) if f.endswith("_us.txt"))
        prefixes = [f[: -len("_us.txt")] for f in files]
        allp = []
        for p in prefixes:
            allp.append(check_pair(p))
        print("\n".join(allp))
        fails = [x for x in allp if x.startswith("[FAIL") or x.startswith("[SKIP")]
        print("\n==== 汇总:", "全部 PASS" if not fails else f"{len(fails)} 个文件有问题 ====")
    else:
        print(check_pair(arg))

if __name__ == "__main__":
    main()
