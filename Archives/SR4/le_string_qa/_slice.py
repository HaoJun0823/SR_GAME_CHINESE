#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SR4R 翻译 - 分批切片
========================
从 _untrans_<stem>.json 中切出第 idx 批(每批 size 条)尚未翻译的 KEY,
写入 _todo_<stem>_<idx>.json 供人工翻译; 已存在于 _trans_<stem>.json 的 KEY 自动跳过。

用法:  python _slice.py <stem> <size> <idx> [out_path]
例:    python _slice.py hud_us 70 1
"""
import json, os, sys

QA = os.path.dirname(os.path.abspath(__file__))
stem = sys.argv[1]
size = int(sys.argv[2])
idx  = int(sys.argv[3])
out  = sys.argv[4] if len(sys.argv) > 4 else os.path.join(QA, f"_todo_{stem}_{idx:02d}.json")

unpath = os.path.join(QA, f"_untrans_{stem}.json")
trpath = os.path.join(QA, f"_trans_{stem}.json")
un = json.load(open(unpath, encoding="utf-8"))
trans = json.load(open(trpath, encoding="utf-8")) if os.path.exists(trpath) else {}

keys = list(un.keys())
start = size * (idx - 1)
batch = [k for k in keys[start:start + size] if k not in trans]
todo = {k: un[k] for k in batch}
json.dump(todo, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

total_remain = sum(1 for k in keys if k not in trans)
print(f"batch {idx}: 本批 {len(todo)} 条 (已跳过已译 {size - len(batch) if len(batch) < size else 0})"
      f"; 全局剩余 {total_remain}; -> {out}")
