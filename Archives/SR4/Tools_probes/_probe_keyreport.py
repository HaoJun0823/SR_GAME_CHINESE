# -*- coding: utf-8 -*-
"""从 v1.4 日志提取所有上屏的裸 KEY, 并在项目 resource/ 全量搜索其译文。

输出 (UTF-8, 无 BOM):
  1) 日志中出现的裸 KEY 清单(去重)
  2) 每个 KEY 在 le_string/{zh,en} 与 dict/ 中的命中情况
  3) 结论表: 有译文 / 仅英文 / 完全缺失
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

LOG = r"G:\Downloads\SR4R_I18N (2).log"
PROJ = r"G:\Projects\SR4R_DLL"
OUT = r"G:\Projects\SR4R_DLL\.temp\_keyreport.txt"

KEYLIKE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def extract_keys():
    keys = []
    seen = set()
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            for m in re.finditer(r'(?:pend|pre|old|new)="([^"]*)"', line):
                v = m.group(1)
                if KEYLIKE.match(v) and "_" in v and v not in seen:
                    seen.add(v)
                    keys.append(v)
    return keys


def load_tables():
    """返回 {键: (来源, 值)} 合并视图。"""
    view = {}
    for sub in ("en", "zh"):
        d = os.path.join(PROJ, "resource", "le_string", sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".txt"):
                continue
            p = os.path.join(d, fn)
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.match(r'^"([^"]+)"\s*:\s*"(.*)"\s*;?\s*$', line.rstrip("\n"))
                    if m:
                        view.setdefault(m.group(1), {})[sub] = (fn, m.group(2))
    return view


def has_cjk(s):
    return any(u"\u4e00" <= c <= u"\u9fff" for c in s)


def main():
    keys = extract_keys()
    view = load_tables()

    lines = []
    lines.append("=== 日志(v1.4)中上屏的裸 KEY: %d 个 ===" % len(keys))
    lines.append("")

    zh_ok, en_only, missing = [], [], []
    for k in keys:
        e = view.get(k, {})
        zh = e.get("zh")
        en = e.get("en")
        if zh and has_cjk(zh[1]):
            zh_ok.append((k, zh[0], zh[1]))
            tag = "ZH OK "
        elif en:
            en_only.append((k, en[0], en[1]))
            tag = "EN ONLY"
        else:
            missing.append(k)
            tag = "MISSING"
        lines.append("[%s] %-42s zh=%s en=%s" % (
            tag, k,
            ("%s:%r" % zh) if zh else "-",
            ("%s:%r" % en) if en else "-"))

    lines.append("")
    lines.append("=== 汇总 ===")
    lines.append("有中文译文 : %d" % len(zh_ok))
    lines.append("仅英文     : %d" % len(en_only))
    lines.append("完全缺失   : %d" % len(missing))
    lines.append("")
    lines.append("--- 仅英文(需翻译/继承) ---")
    for k, fn, v in en_only:
        lines.append("  %-42s %s : %r" % (k, fn, v))
    lines.append("")
    lines.append("--- 完全缺失(项目内无任何条目) ---")
    for k in missing:
        lines.append("  " + k)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("written:", OUT, "keys:", len(keys))


if __name__ == "__main__":
    main()
