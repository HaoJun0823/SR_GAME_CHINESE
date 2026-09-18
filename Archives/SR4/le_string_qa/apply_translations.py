#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SR4R 翻译 - 套用译文 JSON
=========================
读取 resource/le_string/.qa/_trans_<f>.json : { "KEY": "中文", ... }
将对应 zh 文件的 value 替换为中文。仅替换 JSON 中给出的 KEY,
保留注释/未译条目/格式标记/行尾。

用法:  python apply_translations.py <file_stem>
例:    python apply_translations.py activity_us
"""
import re, os, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # resource/le_string/
SCH = os.path.join(ROOT, "zh")
QA = os.path.join(ROOT, ".qa")

LINE_RE = re.compile(r'^(?P<pre>"(?:[^"\\]|\\.)*":\s*")(?P<v>(?:[^"\\]|\\.)*)(?P<post>"\s*;?\s*)$')

def split_nl(s):
    if s.endswith("\r\n"): return s[:-2], "\r\n"
    if s.endswith("\n"):   return s[:-1], "\n"
    return s, ""

def main():
    if len(sys.argv) < 2:
        print("用法: python apply_translations.py <file_stem>"); sys.exit(1)
    stem = sys.argv[1]
    path = os.path.join(SCH, stem + ".txt")
    jpath = os.path.join(QA, "_trans_" + stem + ".json")
    if not os.path.exists(jpath):
        print("找不到译文 JSON:", jpath); sys.exit(1)
    with open(jpath, encoding="utf-8") as f:
        trans = json.load(f)
    # 建 KEY->行号 索引(用 pre 中的 KEY)
    applied = 0; skipped = 0
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for ln in f:
            body, nl = split_nl(ln)
            st = body.strip()
            if not st or st.startswith("#") or st.startswith("//"):
                out.append(ln); continue
            m = LINE_RE.match(body)
            if not m:
                out.append(ln); continue
            km = re.match(r'^"((?:[^"\\]|\\.)*)"', m.group("pre"))
            key = km.group(1) if km else None
            if key in trans:
                newv = trans[key]
                out.append(m.group("pre") + newv + m.group("post") + nl)
                applied += 1
            else:
                out.append(ln); skipped += 1
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(out)
    print(f"套用完成: 已译 {applied} 条, 跳过(未在JSON中) {skipped} 条 -> {path}")

if __name__ == "__main__":
    main()
