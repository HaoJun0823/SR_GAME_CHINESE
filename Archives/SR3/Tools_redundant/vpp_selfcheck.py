# -*- coding: utf-8 -*-
"""自解析验证重打包的 vpp: 解出全部文件与源目录逐字节比对"""
import os
import struct
import sys
import vpp_pack as VP

vpp = sys.argv[1]
srcdir = sys.argv[2]

d = open(vpp, "rb").read()
hdr, entries, names = VP.read_vpp(vpp)
count = hdr["count"]
data_start = hdr["data_off"]

def unpack_one(phys, usz, csz):
    if csz == VP.NEG:
        return d[data_start + phys:data_start + phys + usz]
    blk = data_start + phys
    m1, m2, lz4len, h_usz = struct.unpack_from("<4I", d, blk)
    assert m1 == VP.MAGIC1 and m2 == VP.MAGIC2
    assert h_usz == usz
    raw = d[blk + 16:blk + 16 + lz4len]
    return VP.lz4_decompress(raw, lz4len, usz)

ok = bad = 0
phys = 0
for i, ent in enumerate(entries):
    is_last = (i == count - 1)
    dec = unpack_one(phys, ent["usz"], ent["csz"])
    lp = os.path.join(srcdir, ent["name"])
    if os.path.exists(lp):
        lb = open(lp, "rb").read()
        if dec == lb:
            ok += 1
        else:
            bad += 1
            print("  MISMATCH: %s (%d vs %d)" % (ent["name"], len(dec), len(lb)))
    size = ent["csz"] if ent["csz"] != VP.NEG else ent["usz"]
    phys += size if is_last else ((size + 0xFFF) // 0x1000) * 0x1000

print("比对: %d OK, %d MISMATCH (共 %d)" % (ok, bad, count))
print("物理偏移尾=%d cdata=%d" % (phys, hdr["cdata"]))
# header 字段合理性
print("packfileSize=%d 实际=%d" % (hdr.get("raw_size", 0), len(d)))
