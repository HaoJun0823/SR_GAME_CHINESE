#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SR4R 翻译 - 抽取待译条目
========================
从 resource/le_string/zh/<f>_us.txt 抽出 value 仍为英文(=未译) 的条目,
写成 resource/le_string/.qa/_untrans_<f>.json : { "KEY": "English value", ... }
供人工/AI 填写中文后, 由 apply_translations.py 套回。

用法:  python extract_untrans.py <filename_without_ext>
例:    python extract_untrans.py activity_us
"""
import re, os, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # resource/le_string/
SCH = os.path.join(ROOT, "zh")
OUT = os.path.join(ROOT, ".qa")

LINE_RE = re.compile(r'^(?P<pre>"(?:[^"\\]|\\.)*":\s*")(?P<v>(?:[^"\\]|\\.)*)(?P<post>"\s*;?\s*)$')

# 含 CJK 即视为已译
def is_cn(s):
    return bool(re.search(r'[一-鿿]', s))

def main():
    if len(sys.argv) < 2:
        print("用法: python extract_untrans.py <file_stem>  (如 activity_us)")
        sys.exit(1)
    stem = sys.argv[1]
    path = os.path.join(SCH, stem + ".txt")
    if not os.path.exists(path):
        print("找不到:", path); sys.exit(1)
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for ln in f:
            body = ln.rstrip("\r\n")
            st = body.strip()
            if not st or st.startswith("#") or st.startswith("//"):
                continue
            m = LINE_RE.match(body)
            if not m:
                continue
            key = m.group("pre").split('": "', 1)[0].strip('"')  # 粗略取 KEY
            # 更稳妥: 从 pre 里取 KEY
            km = re.match(r'^"((?:[^"\\]|\\.)*)"', m.group("pre"))
            key = km.group(1) if km else ""
            val = m.group("v")
            if not is_cn(val):
                out[key] = val
    op = os.path.join(OUT, "_untrans_" + stem + ".json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"已抽取 {len(out)} 条待译 -> {op}")

if __name__ == "__main__":
    main()
