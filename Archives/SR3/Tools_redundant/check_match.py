# -*- coding: utf-8 -*-
import os, sys, struct
sys.path.insert(0, ".")
import build_v7 as b7
import le_strings_repack as lr
src = os.path.join(b7.M, "menu_us.le_strings")
if not os.path.exists(src):
    src = os.path.join(b7.CUR, "menu_us.le_strings")
fid, ver, nb, nstr, entries = lr.read_with_bucket(src)
present = set()
for bb, hh, tt in entries:
    if hh == 0 or len(tt) <= 2: continue
    n16 = (len(tt)-2)//2
    vals = struct.unpack(f"<{n16}H", tt[:n16*2])
    txt = "".join(chr(v) for v in vals if v)
    present.add(txt)
print(f"menu_us 含 {len(present)} 个可读串")
print("=== EN2ZH 中精确命中的 (会是中文): ===")
hit = [k for k in b7.EN2ZH if k in present]
for k in hit: print(f"   {k!r} -> {b7.EN2ZH[k]!r}")
print(f"命中 {len(hit)} / {len(b7.EN2ZH)}")
print("=== EN2ZH 中未命中 (仍显示英文): ===")
miss = [k for k in b7.EN2ZH if k not in present]
print("  " + ", ".join(miss))
