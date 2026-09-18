#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SR4R 翻译 - SR3 翻译记忆继承 (确定性, 无需 AI)
================================================
把三代(SR3R)已译好的 "英文 -> 中文" 直接套用到四代(SR4R):

  1) resource/le_string/zh/<f>_us.txt
     每条目值是英文原文; 若英文原文命中 SR3 TM -> 把值替换为中文。
     保留 `#` 头部注释、KEY 与所有格式标记(如 [format][color:..]/{0}/\\n/\\N/\\" 等)。
  2) Dict_CHS/le_data_supplement.txt
     键是英文原文; 若键命中 SR3 TM -> 把值替换为中文。

注意:
  - 不改变未命中条目(保持英文占位)。
  - 写回时原样保留 SR3 中文(其已遵循 \" 与 \n 约定), 不二次转义。
  - 保留原文件行尾(\n / \r\n)。

用法:
  python apply_inherit.py            # 实际应用并写回
  python apply_inherit.py --dry      # 只统计, 不写回
"""
import re, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # le_data/
PROJECT = os.path.dirname(os.path.dirname(ROOT))                 # SR4R_DLL/
SR3_SUPP = os.path.join(PROJECT, "..", "SR3R_DLL", "Dict_CHS", "le_data_supplement.txt")
SR3_SUPP = os.path.abspath(SR3_SUPP)

LE_DATA = os.path.join(ROOT, "schinese")
SUPP    = os.path.join(PROJECT, "Dict_CHS", "le_data_supplement.txt")

# 匹配 "KEY": "VALUE"  或  "KEY": "VALUE";
# 用命名组 pre/post 取首尾引号(避免命名组占用编号导致 group(1)/group(3) 错位)
LINE_RE = re.compile(r'^(?P<pre>"(?:[^"\\]|\\.)*":\s*")(?P<v>(?:[^"\\]|\\.)*)(?P<post>"\s*;?\s*)$')

def load_sr3_tm(path):
    d = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("//"):
                continue
            m = re.match(r'^"(?P<k>(?:[^"\\]|\\.)*)":\s*"(?P<v>(?:[^"\\]|\\.)*)"\s*;?\s*$', ln)
            if not m:
                continue
            en, ch = m.group("k"), m.group("v")
            if re.search(r'[一-鿿]', ch):
                d[en] = ch
    return d

def split_nl(s):
    if s.endswith("\r\n"):
        return s[:-2], "\r\n"
    if s.endswith("\n"):
        return s[:-1], "\n"
    return s, ""

def process_file(path, tm, dry):
    total = inh = 0
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
            en = m.group("v")
            total += 1
            if en in tm:
                newv = tm[en]
                out.append(m.group("pre") + newv + m.group("post") + nl)
                inh += 1
            else:
                out.append(ln)
    if not dry:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.writelines(out)
    return total, inh

def main():
    dry = "--dry" in sys.argv
    tm = load_sr3_tm(SR3_SUPP)
    print(f"[SR3 TM] 已译条目 = {len(tm)}  (来源 {SR3_SUPP})")
    print(f"[模式] {'DRY-RUN(仅统计)' if dry else 'WRITE(写回)'}")
    print("-" * 60)

    # 1) schinese 23 文件
    sch_total = sch_inh = 0
    for f in sorted(os.listdir(LE_DATA)):
        if not f.endswith(".txt"):
            continue
        p = os.path.join(LE_DATA, f)
        t, h = process_file(p, tm, dry)
        sch_total += t; sch_inh += h
        print(f"  schinese/{f:24s}  条目={t:6d}  继承={h:6d} ({100*h/t:.0f}%)")
    print(f"  [zh 合计] 条目={sch_total}  继承={sch_inh} ({100*sch_inh/sch_total:.1f}%)")

    # 2) le_data_supplement.txt
    if os.path.exists(SUPP):
        t, h = process_file(SUPP, tm, dry)
        print(f"  [supplement ] 条目={t:6d}  继承={h:6d} ({100*h/t:.1f}%)")
    print("-" * 60)
    print("完成。" if not dry else "DRY-RUN 完成, 未写回。")

if __name__ == "__main__":
    main()
