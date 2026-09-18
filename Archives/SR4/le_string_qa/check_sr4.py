#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SR4R 翻译 QA 校验
=================
对 resource/le_string 做结构校验:
  - en/ 与 zh/ 逐文件: 数据条目数一致、KEY 集合一致(无缺失/多余)、无 BOM
  - 统计 zh 仍等于英文(未译)的条目数
对 resource/dict/zh 词典做:
  - 行可解析、已译(含中文) vs 未译 计数

用法:  python check_sr4.py            # 校验 le_data + Dict_CHS
        python check_sr4.py le_data    # 仅 le_data
        python check_sr4.py dict       # 仅 Dict_CHS
退出码: 结构错误(KEY 缺失/多余/BOM) 时非 0
"""
import re, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # resource/le_string/
PROJECT = os.path.dirname(ROOT)                                    # SR4R_DLL/

LINE_RE = re.compile(r'^("(?P<k>(?:[^"\\]|\\.)*)":\s*")(?P<v>(?:[^"\\]|\\.)*)("\s*;?\s*)$')

def parse_kv(path):
    """返回 (keys_in_order, key2val) ; 跳过注释/空行。"""
    keys = []
    kv = {}
    with open(path, encoding="utf-8", newline="") as f:
        for ln in f:
            body = ln.rstrip("\r\n")
            st = body.strip()
            if not st or st.startswith("#") or st.startswith("//"):
                continue
            m = LINE_RE.match(body)
            if not m:
                continue
            keys.append(m.group("k"))
            kv[m.group("k")] = m.group("v")
    return keys, kv

def has_bom(path):
    with open(path, "rb") as f:
        return f.read(3) == b"\xef\xbb\xbf"

def check_le_data():
    od = os.path.join(ROOT, "en")
    sd = os.path.join(ROOT, "zh")
    ok = True
    tot = missing = extra = untrans = 0
    for f in sorted(os.listdir(od)):
        if not f.endswith(".txt"):
            continue
        sp = os.path.join(sd, f)
        ok_, sv = parse_kv(os.path.join(od, f))
        ok2, sv2 = parse_kv(sp)
        bom = has_bom(sp)
        miss = set(ok_) - set(ok2)
        ext = set(ok2) - set(ok_)
        # 未译: schinese 值 == original 值(且非纯符号)
        un = sum(1 for k in ok_ if k in sv2 and sv2[k] == sv.get(k))
        tot += len(ok_); untrans += un
        flag = ""
        if miss or ext or bom:
            ok = False; flag = "  <<< ERROR"
        if miss:   flag += f" missing={len(miss)}"
        if ext:    flag += f" extra={len(ext)}"
        if bom:    flag += " BOM"
        print(f"  {f:24s} orig={len(ok_):6d} sch={len(ok2):6d} 未译={un:6d}{flag}")
    print(f"  [le_data 合计] 条目={tot} 未译={untrans} ({100*untrans/tot:.1f}% 仍为英文)")
    return ok

def check_dict():
    d = os.path.join(PROJECT, "dict", "zh")
    print("  -- dict/zh --")
    for f in sorted(os.listdir(d)):
        if not f.endswith(".txt"):
            continue
        _, kv = parse_kv(os.path.join(d, f))
        tr = sum(1 for v in kv.values() if re.search(r'[一-鿿]', v))
        print(f"  {f:24s} 条目={len(kv):6d} 已译={tr:6d} 未译={len(kv)-tr:6d}")

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("=== SR4R 翻译 QA ===")
    if mode in ("all", "le_data"):
        print("[le_data]")
        a = check_le_data()
    if mode in ("all", "dict"):
        check_dict()
    print("=== 完成 ===")
    if mode in ("all", "le_data") and not a:
        sys.exit(1)

if __name__ == "__main__":
    main()
