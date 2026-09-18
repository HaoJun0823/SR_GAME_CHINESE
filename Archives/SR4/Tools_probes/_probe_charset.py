# -*- coding: utf-8 -*-
"""扫描 le_string_keys.txt 的字符清单，找出异常码位（可能在字形光栅化阶段触发崩溃）"""
import io
import os
import re
import collections
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "resource", "dict", "zh", "le_string_keys.txt")

with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()

cps = collections.Counter(raw)
# 只统计条目值/键范围里的非 ASCII
print("total distinct chars in file:", len(cps))

RANGES = [
    ("ASCII",            0x00, 0x7F),
    ("Latin-1/Ext",      0x80, 0x2AF),
    ("General Punct",    0x2000, 0x206F),
    ("Currency/Symbols", 0x20A0, 0x27BF),
    ("CJK Punct",        0x3000, 0x303F),
    ("Hiragana/Katakana",0x3040, 0x30FF),
    ("Hangul",           0xAC00, 0xD7AF),
    ("CJK Ext-A",        0x3400, 0x4DBF),
    ("CJK Unified",      0x4E00, 0x9FFF),
    ("Yi",               0xA000, 0xA4CF),
    ("Hangul Jamo",      0x1100, 0x11FF),
    ("Compat Ideographs",0xF900, 0xFAFF),
    ("Fullwidth",        0xFF00, 0xFFEF),
    ("PUA",              0xE000, 0xF8FF),
    ("Surrogates",       0xD800, 0xDFFF),
    ("Specials",         0xFFF0, 0xFFFF),
]

hits = collections.defaultdict(list)
for ch in cps:
    cp = ord(ch)
    for name, lo, hi in RANGES:
        if lo <= cp <= hi:
            hits[name].append(ch)
            break

print()
print("--- 码位分布 ---")
for name, lo, hi in RANGES:
    lst = hits.get(name)
    if lst:
        print("  %-20s %5d 个: %s" % (name, len(lst), "".join(sorted(lst))[:70]))

SUSPECT = []
for ch in sorted(cps):
    cp = ord(ch)
    if 0xD800 <= cp <= 0xDFFF:
        SUSPECT.append((cp, ch, "SURROGATE"))
    elif cp > 0xFFFD:
        SUSPECT.append((cp, ch, ">U+FFFD"))
    elif 0xE000 <= cp <= 0xF8FF:
        SUSPECT.append((cp, ch, "PUA"))
    elif cp == 0xFFFD:
        SUSPECT.append((cp, ch, "REPLACEMENT"))
    elif 0x200B <= cp <= 0x200F or 0xFEFF == cp:
        SUSPECT.append((cp, ch, "ZERO-WIDTH/BOM"))
    elif 0x0000 <= cp < 0x20 and cp not in (0x09, 0x0A, 0x0D):
        SUSPECT.append((cp, ch, "CTRL"))
    elif cp in (0x016D, 0x00AD):
        SUSPECT.append((cp, ch, "KNOWN-GARBAGE"))

print()
print("--- 可疑码位 ---")
if not SUSPECT:
    print("  (无)")
for cp, ch, why in SUSPECT:
    print("  U+%04X  %-12s  %s" % (cp, why, unicodedata.name(ch, "?")))

print()
print("--- 值/键中出现 行分隔符 或 制表 ---")
for pat, label in [(r"\t", "TAB"), (r"[\u2028\u2029]", "LINE/PARA SEP"), (r"[\u00A0\u3000]", "NBSP/IDEO SPACE")]:
    n = len(re.findall(pat, raw))
    print("  %-16s %d" % (label, n))
