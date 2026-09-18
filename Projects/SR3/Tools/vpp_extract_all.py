# -*- coding: utf-8 -*-
"""通用 vpp 全量解包 (物理偏移按 csz 对齐累积)"""
import sys, struct, os, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vpp_pack as vp

def extract(src_vpp, dst_dir):
    hdr, entries, names = vp.read_vpp(src_vpp)
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir)
    d = open(src_vpp, 'rb').read()
    data_phys = hdr['data_off']
    for e in entries:
        seg = d[data_phys:data_phys + 0x1000] if e['csz'] == 0xFFFFFFFFFFFFFFFF else None
        if e['csz'] == 0xFFFFFFFFFFFFFFFF:
            payload = d[data_phys:data_phys + e['usz']]
            data_phys += (e['usz'] + 0xFFF) // 0x1000 * 0x1000
        else:
            m1, m2, clen, usz = struct.unpack_from('<IIII', d, data_phys)
            assert m1 == vp.MAGIC1 and m2 == vp.MAGIC2, (e['name'], hex(m1), hex(m2))
            payload = vp.lz4_decompress(d[data_phys+16:data_phys+16+clen], clen, usz)
            phys_len = 16 + clen
            data_phys += (phys_len + 0xFFF) // 0x1000 * 0x1000
        if e['csz'] != 0xFFFFFFFFFFFFFFFF and len(payload) != e['usz']:
            print(f"WARN {e['name']}: 解压 {len(payload)} != usz {e['usz']}")
        out = os.path.join(dst_dir, e['name'])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as f:
            f.write(payload)
    print(f"解包 {len(entries)} 文件 -> {dst_dir}")

if __name__ == '__main__':
    extract(sys.argv[1], sys.argv[2])
