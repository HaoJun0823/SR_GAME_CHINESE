# -*- coding: utf-8 -*-
"""以块头为准(校验 16B magic)重解游侠 misc.vpp_pc -> al213_misc/, 并与官方 unpack/misc 全量对拍"""
import sys, struct, hashlib, os
sys.path.insert(0, r"C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04")
from vpp_pack import read_vpp, lz4_decompress, CHUNK, MAGIC1, MAGIC2, NEG

ALI = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\汉化\游侠\cache\misc.vpp_pc"
OUT = r"C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04\al213_misc"
OFF = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\misc"

def main():
    hdr, entries, names = read_vpp(ALI)
    d = open(ALI, "rb").read()
    phys = hdr["data_off"]
    os.makedirs(OUT, exist_ok=True)
    bad = []
    ok = 0
    for i, ent in enumerate(entries):
        name = ent["name"]
        csz, usz = ent["csz"], ent["usz"]
        raw = d[phys:phys + min(csz, 0x10000000)]
        is_last = (i == len(entries) - 1)
        if csz == NEG:
            data = raw[:usz]
            phys += usz if is_last else ((usz + CHUNK - 1) // CHUNK) * CHUNK
        else:
            m1, m2, l4len, u4 = struct.unpack_from("<4I", raw, 0)
            if m1 != MAGIC1 or m2 != MAGIC2:
                bad.append((i, name, hex(m1), hex(m2), csz, usz, phys))
                # 尽力回退: 按 usz 推进对齐, 继续(避免级联崩)
                data = None
                phys += csz if is_last else ((csz + CHUNK - 1) // CHUNK) * CHUNK
            else:
                data = lz4_decompress(raw[16:16 + l4len], l4len, u4)
                phys += csz if is_last else ((csz + CHUNK - 1) // CHUNK) * CHUNK
        if data is not None:
            open(os.path.join(OUT, name), "wb").write(data)
            ok += 1
    print("解出 %d/%d, 坏块 %d" % (ok, len(entries), len(bad)))
    for b in bad[:20]:
        print("BAD:", b)

    # 与官方 unpack/misc 对拍
    o_names = set(os.listdir(OFF))
    a_names = set(os.listdir(OUT))
    print("官方 %d 文件 | 游侠解出 %d 文件" % (len(o_names), len(a_names)))
    print("only-in-官方(游侠删除):")
    for n in sorted(o_names - a_names):
        print("  -", n)
    print("only-in-游侠(新增):")
    for n in sorted(a_names - o_names):
        print("  +", n)
    changed, same = [], []
    for n in sorted(o_names & a_names):
        h1 = hashlib.md5(open(os.path.join(OFF, n), "rb").read()).hexdigest()
        h2 = hashlib.md5(open(os.path.join(OUT, n), "rb").read()).hexdigest()
        (changed if h1 != h2 else same).append(n)
    print("\n== changed (%d) ==" % len(changed))
    for n in changed:
        s1 = os.path.getsize(os.path.join(OFF, n)); s2 = os.path.getsize(os.path.join(OUT, n))
        print("  %-40s %8d -> %8d" % (n, s1, s2))
    print("\nunchanged: %d" % len(same))

if __name__ == "__main__":
    main()
