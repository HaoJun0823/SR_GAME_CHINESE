#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对 DumpText.dtxt（DLL 收集的"经 FORMAT 的实际文本"）做覆盖度分类统计。

目的：把客户机描述的"有的全/有的不全/有的缺字/有的空白/有的只剩KEY裸键"
      量化成可复现的数字分类。

分类：
  CJK_TL    译文（含中文）—— 词典命中且已上屏
  KEYLIKE   大写+下划线形态的"裸 KEY"（如 OPTION_YES）—— 引擎查表失败回落
  EN_WORDS  可读英文短串 —— 词典未覆盖（或本就是不需要译的调试串）
  OTHER     其它（数字/符号/占位）
"""
import os
import re
import sys
from collections import Counter

SRC = sys.argv[1] if len(sys.argv) > 1 else \
    r"G:\Projects\SR4R_DLL\.temp\flightlogs\DumpText.v14_dict_2232.dtxt"
OUT = r"G:\Projects\SR4R_DLL\.temp\_dtx_class.txt"

KEYLIKE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")
CJK = re.compile(r"[\u3400-\u9FFF\uF900-\uFAFF]")
ENW = re.compile(r"[A-Za-z]{2,}")

lines = []
with open(SRC, "r", encoding="utf-8", errors="replace") as f:
    for raw in f:
        t = raw.strip()
        if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
            t = t[1:-1]
        if t:
            lines.append(t)

# 去重（保序）—— 同一串跨帧/跨页会重复
seen = set()
uniq = []
for t in lines:
    if t in seen:
        continue
    seen.add(t)
    uniq.append(t)

cats = {"CJK_TL": [], "KEYLIKE": [], "EN_WORDS": [], "OTHER": []}
for t in uniq:
    if CJK.search(t):
        cats["CJK_TL"].append(t)
    elif KEYLIKE.match(t):
        cats["KEYLIKE"].append(t)
    elif ENW.search(t):
        cats["EN_WORDS"].append(t)
    else:
        cats["OTHER"].append(t)

out = []
out.append("=" * 90)
out.append("DumpText 覆盖度分类   src=%s" % SRC)
out.append("=" * 90)
out.append("原始行 %d 条，去重后 %d 条" % (len(lines), len(uniq)))
out.append("")
tot = len(uniq)
for k in ("CJK_TL", "KEYLIKE", "EN_WORDS", "OTHER"):
    n = len(cats[k])
    out.append("  %-9s %4d 条  (%5.1f%%)" % (k, n, 100.0 * n / max(tot, 1)))
out.append("")
for k in ("KEYLIKE", "EN_WORDS", "CJK_TL", "OTHER"):
    out.append("-" * 90)
    out.append("### %s  (%d)" % (k, len(cats[k])))
    out.append("-" * 90)
    for s in cats[k][:80]:
        out.append("    %s" % s)
    if len(cats[k]) > 80:
        out.append("    ... 其余 %d 条省略" % (len(cats[k]) - 80))
    out.append("")

# 前缀词族统计：看 KEYLIKE 是否集中在某几个命名空间
pref = Counter()
for s in cats["KEYLIKE"]:
    pref[s.split("_")[0]] += 1
out.append("=" * 90)
out.append("裸 KEY 前缀族分布")
out.append("=" * 90)
for k, v in pref.most_common():
    out.append("  %-14s %d" % (k, v))

txt = "\n".join(out)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(txt)
print(txt)
