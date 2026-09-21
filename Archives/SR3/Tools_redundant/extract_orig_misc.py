# -*- coding: utf-8 -*-
"""从 misc.vpp_pc.orig 全量抽取 -> unpack/misc (恢复官方基准)

⚠️⚠️ 已知严重 BUG (2026-09-04 05:36 崩溃根因):
官方 orig vpp 的物理块顺序 ≠ 目录顺序, 且 >32MB 的文件被官方分段存储
(如 always_loaded_veh.gpeg_pc=133MB / interface-backend.gpeg_pc=48MB),
本脚本按目录顺序推进物理偏移会把大文件解成 32MB 截断版,
用截断源打包部署会直接导致游戏崩溃 (UI 纹理损坏)。
=== 请勿运行此脚本恢复基准 ===
可靠官方基准 = 工作区 cur_misc/ (375 文件, 04:12 校验正确)。
"""
import os, struct, sys
sys.path.insert(0, r"C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04")
from vpp_pack import read_vpp, lz4_decompress, CHUNK, NEG

SRC = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\cache\misc.vpp_pc.orig"
DST = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\misc"

def main():
    hdr, entries, names = read_vpp(SRC)
    d = open(SRC, "rb").read()
    phys = hdr["data_off"]
    os.makedirs(DST, exist_ok=True)
    n = 0
    for i, ent in enumerate(entries):
        name = ent["name"]; csz, usz = ent["csz"], ent["usz"]
        raw = d[phys:phys + min(csz, 0x10000000)]
        is_last = (i == len(entries) - 1)
        if csz == NEG:
            data = raw[:usz]
            phys += usz if is_last else ((usz + CHUNK - 1) // CHUNK) * CHUNK
        else:
            m1, m2, l4len, u4 = struct.unpack_from("<4I", raw, 0)
            data = lz4_decompress(raw[16:16 + l4len], l4len, u4)
            phys += csz if is_last else ((csz + CHUNK - 1) // CHUNK) * CHUNK
        with open(os.path.join(DST, name), "wb") as f:
            f.write(data)
        n += 1
    print("extracted", n, "files ->", DST)

if __name__ == "__main__":
    main()
