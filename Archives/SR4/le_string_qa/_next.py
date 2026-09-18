#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SR4R 翻译 - 按全局剩余顺序切片(避免窗口错位)
读取 _untrans_<stem>.json 与 _trans_<stem>.json, 计算剩余(未译)KEY 列表(文件顺序),
取 [offset : offset+size] 写入 _todo_<stem>_<NN>.json。已译自动跳过。

用法:  python _next.py <stem> <offset> <size> <out>
"""
import json, os, sys, re
QA = os.path.dirname(os.path.abspath(__file__))
stem = sys.argv[1]; off = int(sys.argv[2]); sz = int(sys.argv[3]); out = sys.argv[4]
un = json.load(open(os.path.join(QA, f"_untrans_{stem}.json"), encoding="utf-8"))
tp = os.path.join(QA, f"_trans_{stem}.json")
tr = json.load(open(tp, encoding="utf-8")) if os.path.exists(tp) else {}
PLACE = re.compile(r'^\{[^}]*\}$')  # 纯 {..} 占位符(图片/代码) 不译, 从切片排除
rem = [k for k in un if k not in tr and not PLACE.match(un[k].strip())]
chunk = rem[off:off + sz]
json.dump({k: un[k] for k in chunk}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"offset={off} 本段={len(chunk)} 全局剩余={len(rem)} -> {out}")
