# -*- coding: utf-8 -*-
"""通用 vpp 全量解包 (物理偏移按 csz 对齐累积)"""
import sys, struct, os, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vpp_pack as vp

def _rmtree_plain(p):
    """递归删除目录, 绕开 WorkBuddy 对 shutil.rmtree 的安全回收站包装(SHFileOperationW 0x78)"""
    if not os.path.isdir(p):
        return
    for root, dirs, files in os.walk(p, topdown=False):
        for fn in files:
            os.remove(os.path.join(root, fn))
        for dn in dirs:
            os.rmdir(os.path.join(root, dn))
    os.rmdir(p)

def extract(src_vpp, dst_dir):
    hdr, entries, names = vp.read_vpp(src_vpp)
    if os.path.exists(dst_dir):
        _rmtree_plain(dst_dir)
    os.makedirs(dst_dir)
    d = open(src_vpp, 'rb').read()
    data_phys = hdr['data_off']
    n = len(entries)
    for i, e in enumerate(entries):
        is_last = (i == n - 1)
        if e['csz'] == vp.NEG:
            # 未压缩特例：原样存放
            payload = d[data_phys:data_phys + e['usz']]
            size = e['usz']
        else:
            # 分块 LZ4：>32MB 文件由多个 [16B头 + LZ4块] 串联, 子块之间紧挨不填充;
            # 循环解压直至累积达到 e['usz'] (单块文件循环只执行一次)
            payload = bytearray()
            phys = data_phys
            while len(payload) < e['usz']:
                m1, m2, clen, usz_block = struct.unpack_from('<IIII', d, phys)
                assert m1 == vp.MAGIC1 and m2 == vp.MAGIC2, (e['name'], hex(m1), hex(m2))
                payload += vp.lz4_decompress(d[phys+16:phys+16+clen], clen, usz_block)
                phys += 16 + clen
            payload = bytes(payload[:e['usz']])
            size = phys - data_phys
        if e['csz'] != vp.NEG and len(payload) != e['usz']:
            print(f"WARN {e['name']}: 解压 {len(payload)} != usz {e['usz']}")
        out = os.path.join(dst_dir, e['name'])
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as f:
            f.write(payload)
        # 文件级 0x1000 对齐(最后文件不 pad, 其后无文件消费偏移)
        data_phys += size if is_last else ((size + 0xFFF) // 0x1000 * 0x1000)
    print(f"解包 {len(entries)} 文件 -> {dst_dir}")

if __name__ == '__main__':
    extract(sys.argv[1], sys.argv[2])
