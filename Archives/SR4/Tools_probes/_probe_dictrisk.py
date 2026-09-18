# -*- coding: utf-8 -*-
"""
扫描生成的 le_string_keys.txt，找出「会让 Format 崩溃」的危险条目。

危险判据:
  R1 值里含 %  且 不是 %%
     -> 引擎 g_origFormat(dst, trans, cap, args, argc) 会用原 argc 去取参数,
        译文新增的 % 说明符会读到不存在的参数 => 野指针崩溃
  R2 值里含真实的换行 / 回车 (不是字面 \n)
     -> 词典按行解析 => bad line, 条目被截断 (本次 117 条 bad line 的成因)
  R3 值里含未配对的双引号
  R4 键里含 % / 换行 / 引号
  R5 值里含 {n} 花括号占位符 (引擎 Format 可能不认, 至少语义错)
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = [
    os.path.join(ROOT, "resource", "dict", "zh", "le_string_keys.txt"),
    os.path.join(ROOT, "resource", "dict", "en", "le_string_keys.txt"),
]

LINE_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*:\s*"(.*)"\s*;?\s*$')


def analyze(path):
    print("=" * 76)
    print("FILE:", path)
    if not os.path.exists(path):
        print("  (missing)")
        return
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    lines = raw.split("\n")
    print("  physical lines:", len(lines))

    r2 = r3 = r4 = r5 = 0
    r1_list = []
    r5_list = []
    bad = []
    ok = 0

    for i, line in enumerate(lines, 1):
        s = line.rstrip("\r")
        if not s.strip():
            continue
        if s.lstrip().startswith("//"):
            continue
        m = LINE_RE.match(s)
        if not m:
            bad.append((i, s[:90]))
            continue
        key, val = m.group(1), m.group(2)
        ok += 1

        # R4 键
        if "%" in key or "\n" in key or "\r" in key or '"' in key:
            r4 += 1
            print("  [R4] line %d key risky: %r" % (i, key[:60]))

        # R1 值里的 %
        pcts = re.findall(r"%(.)", val)
        real = [c for c in pcts if c != "%"]
        if real:
            r1_list.append((i, key, val, real))

        # R5 {n}
        if re.search(r"\{\d+\}", val):
            r5 += 1
            if len(r5_list) < 15:
                r5_list.append((i, key, val))

    print("  parsed entries:", ok)
    print("  UNPARSEABLE (bad line) :", len(bad))
    print("  [R1] value contains %% specifier :", len(r1_list))
    print("  [R4] key contains %% / quote / newline :", r4)
    print("  [R5] value contains {n} :", r5)

    if bad:
        print("  --- first 12 unparseable lines ---")
        for i, s in bad[:12]:
            print("    line %-5d %s" % (i, s))

    if r1_list:
        print("  --- ALL R1 entries (value has %%) ---")
        for i, k, v, spec in r1_list[:60]:
            print("    line %-5d key=%-42s specs=%s" % (i, k[:42], spec))
            print("           val=%s" % v[:110])

    if r5_list:
        print("  --- sample R5 entries (value has {n}) ---")
        for i, k, v in r5_list:
            print("    line %-5d key=%-42s val=%s" % (i, k[:42], v[:110]))


for t in TARGETS:
    analyze(t)
print("=" * 76)
