# -*- coding: utf-8 -*-
"""验证 vpp 物理布局(csz 对齐) + LZ4 解压闭环"""
import struct, os

VPP = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\cache\misc.vpp_pc"
LOCAL = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\misc"
d = open(VPP, "rb").read()

count, psize, dsize, nsize = struct.unpack_from("<QQQQ", d, 0x158)[:4]
dir_off = 0x1000
names_off = dir_off + ((dsize + 0xFFF) // 0x1000) * 0x1000
data_start = names_off + ((nsize + 0xFFF) // 0x1000) * 0x1000

ents = []
for i in range(count):
    e = dir_off + i * 48
    name_off, unk1, data_off, usz, csz, unk2 = struct.unpack_from("<QQQQQQ", d, e)
    end = d.index(b"\x00", names_off + name_off)
    name = d[names_off + name_off:end].decode("ascii", "replace")
    ents.append((name, data_off, usz, csz, unk1, unk2))

def lz4_decompress(src, s_len, dst_size):
    out = bytearray()
    si = 0
    while si < s_len:
        token = src[si]; si += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[si]; si += 1
                lit += b
                if b != 0xFF: break
        out += src[si:si + lit]; si += lit
        if si >= s_len: break
        ml = token & 0xF
        off = src[si] | (src[si + 1] << 8); si += 2
        if ml == 15:
            while True:
                b = src[si]; si += 1
                ml += b
                if b != 0xFF: break
        ml += 4
        for _ in range(ml):
            out.append(out[-off])
    return bytes(out[:dst_size])

# 按 csz 物理对齐推进, 验证目标文件 + 全量解压统计
want = {"font_debug.vf3_pc", "font_body.vf3_pc", "font_zh.vf3_pc", "charlist_zh.dat",
        "font_zh.cvbm_pc", "font_zh.gvbm_pc", "game_lib.lua", "font_body.cvbm_pc",
        "font_body.gvbm_pc", "font_zh_nobdr.gvbm_pc"}
phys = 0
ok_all, bad_all = 0, 0
for i, (name, data_off, usz, csz, unk1, unk2) in enumerate(ents):
    is_last = (i == len(ents) - 1)
    compressed = (csz != 0xFFFFFFFFFFFFFFFF)
    if name in want or i < 2:
        if compressed:
            blk = data_start + phys
            hdr = d[blk:blk + 16]
            m1, m2, lz4len, h_usz = struct.unpack("<4I", hdr)
            raw = d[blk + 16:blk + 16 + lz4len]
            dec = lz4_decompress(raw, len(raw), usz)
            local = os.path.join(LOCAL, name)
            if os.path.exists(local):
                lb = open(local, "rb").read()
                print("  %-24s phys=%8d hdr=(%08X %08X lz4=%d usz=%d) dec=%d local=%d match=%s"
                      % (name, phys, m1, m2, lz4len, h_usz, len(dec), len(lb), dec == lb))
            else:
                print("  %-24s phys=%8d (本地缺失) dec=%d" % (name, phys, len(dec)))
        else:
            blk = data_start + phys
            seg = d[blk:blk + usz]
            print("  %-24s 未压缩条目 phys=%d usz=%d 前16B=%s" % (name, phys, usz, seg[:16].hex(" ")))
    # 推进
    size = csz if compressed else usz
    phys += size if is_last else ((size + 0xFFF) // 0x1000) * 0x1000

print("最终物理偏移=%d (应=cdata)" % phys)
cdata = struct.unpack_from("<Q", d, 0x180)[0]
print("cdata=%d match=%s" % (cdata, phys == cdata))
