# -*- coding: utf-8 -*-
"""从官方 misc.vpp_pc.orig 重新解包 cur_misc 基准(净化之前 v7.2 的污染残留)。
物理偏移按 csz 累积(与原厂 pack / 我们的 pack 一致), 顺序 LZ4 解压还原。
"""
import sys, os, struct, shutil
ROOT = r"C:/Users/haojun0823/WorkBuddy/2026-09-04-02-19-04"
sys.path.insert(0, ROOT)
import vpp_pack as vp

ORIG = r"I:/SteamLibrary/steamapps/common/Saints Row The Third Remastered/cache/misc.vpp_pc.orig"
CUR  = os.path.join(ROOT, "cur_misc")
CHUNK = 0x1000

hdr, entries, names = vp.read_vpp(ORIG)
d = open(ORIG, "rb").read()
data_start = hdr["data_off"]
phys = 0
os.makedirs(CUR, exist_ok=True)   # 直接覆盖写即可净化(残留无关文件不影响打包)
n_ok = n_bad = 0
for i, e in enumerate(entries):
    name, usz, csz = e["name"], e["usz"], e["csz"]
    off = data_start + phys
    try:
        if csz == vp.NEG:
            # 未压缩特例(原样存放)
            raw = d[off:off + usz]
        else:
            # 压缩文件可能由多个 <=32MB 的 LZ4 块首尾拼接而成(原厂对巨型文件分块),
            # 每块独立 16B头{magic1,magic2,lz4len,blk_usz}. 逐块解压直到累计 usz.
            raw = bytearray()
            pos = off
            while len(raw) < usz:
                m1, m2, lz4len, blk_usz = struct.unpack_from("<IIII", d, pos)
                assert m1 == vp.MAGIC1 and m2 == vp.MAGIC2, \
                    f"{name} 子块 magic 异常 @0x{pos:x}: {m1:08x} {m2:08x}"
                comp = d[pos + 16:pos + 16 + lz4len]
                blk = vp.lz4_decompress(comp, len(comp), blk_usz)
                assert len(blk) == blk_usz, \
                    f"{name} 子块解压 {len(blk)} != blk_usz {blk_usz}"
                raw += blk
                pos += 16 + lz4len
            raw = bytes(raw)
        assert len(raw) == usz, f"{name} 解压长度 {len(raw)} != usz {usz}"
        open(os.path.join(CUR, name), "wb").write(raw)
        n_ok += 1
    except Exception as ex:
        n_bad += 1
        print(f"[ERROR] {name}: {ex}")
    size = usz if csz == vp.NEG else csz
    is_last = (i == len(entries) - 1)
    phys += size if is_last else ((size + CHUNK - 1) // CHUNK) * CHUNK
print(f"解包完成: 成功 {n_ok}, 失败 {n_bad}, 物理累积末偏移=0x{phys:x} (cdata=0x{hdr['cdata']:x})")
assert n_bad == 0, "存在解包失败文件, 中止"
assert phys == hdr["cdata"], "物理累积与 cdata 不符"
print(f"cur_misc 已净化 -> {CUR}")
