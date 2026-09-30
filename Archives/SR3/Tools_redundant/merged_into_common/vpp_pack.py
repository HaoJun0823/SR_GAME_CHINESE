# -*- coding: utf-8 -*-
"""
Remastered (Version06-x64) VPP 打包器
====================================
已知格式(经 vpp_verify.py 实证):
- header 0x188B: magic(0x51890ACE) ver(6) name[65] path[256] flags(u32@0x14C)
  + u64@0x150 unk, u64@0x158 count, u64@0x160 packfileSize, u64@0x168 dirSize,
  + u64@0x170 nameSize, u64@0x178 uncompressedDataSize, u64@0x180 compressedDataSize
- chunk = 0x1000; layout: chunk0=header, chunk1..=directory(48B*N), names, data
- directory entry 48B: {u64 nameOffset, u64 unk0, u64 dataOffset(虚拟: usz 对齐累积),
                         u64 usz, u64 csz, u64 unk0}
- 物理数据区: 每文件块 = 16B头{0x0FEEDBEE,0x00BADBEE,u32 lz4len,u32 usz} + LZ4 block
  物理偏移按 csz 的 0x1000 对齐累积, 最后一个文件不 pad
- 未压缩特例: csz = 0xFFFFFFFFFFFFFFFF, 数据原样存放(按 usz 对齐)
"""
import os
import struct

CHUNK = 0x1000
MAGIC1 = 0x0FEEDBEE
MAGIC2 = 0x00BADBEE
NEG = 0xFFFFFFFFFFFFFFFF

try:
    import lz4.block as _lb
    def lz4_compress(data):
        return _lb.compress(data, mode="high_compression", compression=12,
                            store_size=False)
except ImportError:
    print("WARNING: lz4 库不可用, 使用内置慢速 LZ4 压缩器")
    lz4_compress = None

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
                if b != 0xFF:
                    break
        out += src[si:si + lit]; si += lit
        if si >= s_len:
            break
        ml = token & 0xF
        off = src[si] | (src[si + 1] << 8); si += 2
        if ml == 15:
            while True:
                b = src[si]; si += 1
                ml += b
                if b != 0xFF:
                    break
        ml += 4
        for _ in range(ml):
            out.append(out[-off])
    return bytes(out[:dst_size])


def read_vpp(path):
    """解析 vpp, 返回 (hdr_fields, entries, names_bytes)"""
    d = open(path, "rb").read()
    magic, ver = struct.unpack_from("<II", d, 0)
    assert magic == 0x51890ACE and ver == 6, "不是 Remastered v6 vpp"
    flags = struct.unpack_from("<I", d, 0x14C)[0]
    count, psize, dsize, nsize, udata, cdata = struct.unpack_from("<QQQQQQ", d, 0x158)
    dir_off = CHUNK
    names_off = dir_off + ((dsize + CHUNK - 1) // CHUNK) * CHUNK
    data_off = names_off + ((nsize + CHUNK - 1) // CHUNK) * CHUNK
    entries = []
    for i in range(count):
        e = dir_off + i * 48
        no, unk1, doff, usz, csz, unk2 = struct.unpack_from("<QQQQQQ", d, e)
        end = d.index(b"\x00", names_off + no)
        name = d[names_off + no:end].decode("ascii", "replace")
        entries.append(dict(name=name, name_off=no, data_off=doff, usz=usz,
                            csz=csz, unk1=unk1, unk2=unk2))
    names = d[names_off:names_off + nsize]
    hdr = dict(flags=flags, count=count, dsize=dsize, nsize=nsize,
               udata=udata, cdata=cdata, dir_off=dir_off,
               names_off=names_off, data_off=data_off, raw_size=len(d))
    return hdr, entries, names


# 单块解压上限: 原厂对 >32MB 文件按 32MB 分块(每子块独立 16B头),
# 说明游戏 LZ4 解压器 block_size = 32MB. 压缩侧必须同样分块, 否则游戏无法解压.
BLOCK_USZ = 0x2000000   # 32MB


def _compress_file(data):
    """返回 (is_compressed, payload_bytes). 压缩按 <=32MB 分块, 每块独立 16B头."""
    usz = len(data)
    if lz4_compress is None:
        return False, data          # 无 lz4 库 -> 直接 STORE
    # 先尝试整块压缩(小文件), 失败或大文件则分块
    full = lz4_compress(data)
    if full is not None and len(full) + 16 < usz and usz <= BLOCK_USZ:
        return True, struct.pack("<4I", MAGIC1, MAGIC2, len(full), usz) + full
    # 分块压缩
    payload = bytearray()
    for start in range(0, usz, BLOCK_USZ):
        chunk = data[start:start + BLOCK_USZ]
        comp = lz4_compress(chunk)
        if comp is None:
            return False, data       # 压缩不可用, 退回 STORE
        payload += struct.pack("<4I", MAGIC1, MAGIC2, len(comp), len(chunk)) + comp
    if len(payload) < usz:
        return True, bytes(payload)
    return False, data               # 压缩无收益 -> STORE


def pack_vpp(template_vpp, src_dir, out_path, verbose=True):
    """用 src_dir 中的同名文件(须齐全)按 template_vpp 的顺序与 names 重打包."""
    hdr, entries, names = read_vpp(template_vpp)
    blobs = []          # (name, is_compressed, csz_eff, usz, payload_bytes(含16B头))
    for ent in entries:
        name = ent["name"]
        p = os.path.join(src_dir, name)
        if not os.path.exists(p):
            raise FileNotFoundError("缺失文件: %s" % p)
        data = open(p, "rb").read()
        usz = len(data)
        comp, payload = _compress_file(data)
        if comp:
            csz = len(payload)
            blobs.append((name, True, csz, usz, payload))
        else:
            # 未压缩特例
            blobs.append((name, False, usz, usz, data))
    # 物理布局: 压缩按 csz, 未压缩按 usz, 均 0x1000 对齐, 最后不 pad
    phys_off = 0
    phys_sizes = []
    for i, (name, comp, csz, usz, payload) in enumerate(blobs):
        is_last = (i == len(blobs) - 1)
        size = csz
        phys_sizes.append(size if is_last else ((size + CHUNK - 1) // CHUNK) * CHUNK)
        phys_off += phys_sizes[-1]
    cdata = phys_off
    # 虚拟 dataOffset: usz 对齐累积(官方语义), 最后不 pad
    virt_off = 0
    for i, (name, comp, csz, usz, payload) in enumerate(blobs):
        entries[i]["data_off"] = virt_off
        entries[i]["usz"] = usz
        entries[i]["csz"] = csz if comp else NEG
        is_last = (i == len(blobs) - 1)
        size = usz
        virt_off += size if is_last else ((size + CHUNK - 1) // CHUNK) * CHUNK
    udata_total = virt_off
    # 组装文件
    n = len(entries)
    dsize = n * 48
    dir_chunks = (dsize + CHUNK - 1) // CHUNK
    name_chunks = (hdr["nsize"] + CHUNK - 1) // CHUNK
    data_off = CHUNK * (1 + dir_chunks + name_chunks)
    packfile_size = data_off + cdata
    with open(out_path, "wb") as f:
        # chunk0: header + pad
        f.write(b"\x00" * CHUNK)
        # 回去填 header(稍后用 seek)
        # directory
        for ent in entries:
            f.write(struct.pack("<QQQQQQ", ent["name_off"], 0, ent["data_off"],
                                ent["usz"], ent["csz"], 0))
        pad = CHUNK * dir_chunks - dsize
        f.write(b"\x00" * pad)
        # names
        f.write(names)
        pad = CHUNK * name_chunks - hdr["nsize"]
        f.write(b"\x00" * pad)
        # data
        for i, (name, comp, csz, usz, payload) in enumerate(blobs):
            f.write(payload)
            pad = phys_sizes[i] - len(payload)
            if pad > 0:
                f.write(b"\x00" * pad)
    # 写 header
    with open(out_path, "r+b") as f:
        f.seek(0)
        hb = bytearray(CHUNK)
        struct.pack_into("<II", hb, 0x00, 0x51890ACE, 6)
        struct.pack_into("<I", hb, 0x14C, hdr["flags"])
        struct.pack_into("<QQQQQQ", hb, 0x150, 0, n, packfile_size, dsize,
                         hdr["nsize"], udata_total)
        struct.pack_into("<Q", hb, 0x180, cdata)
        f.write(bytes(hb))
    if verbose:
        print("打包完成: %d 文件, packfile=%d (%d->%d 压缩比 %.2f)"
              % (n, packfile_size, udata_total, cdata, cdata / max(udata_total, 1)))
    return out_path


if __name__ == "__main__":
    import sys
    template = sys.argv[1]
    src = sys.argv[2]
    out = sys.argv[3]
    pack_vpp(template, src, out)
