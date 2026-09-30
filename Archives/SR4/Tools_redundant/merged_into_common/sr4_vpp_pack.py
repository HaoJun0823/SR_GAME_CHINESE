#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sr4_vpp_pack.py - Saints Row IV (Re-Elected / EOS 2024+) vpp_pc 打包器
=====================================================================
SR4 vpp 格式 (version 10):
- header 0x28B: magic u32(0x51890ACE) ver u32(10) crc u32? 0x0C
  + flags u32 @0x10? + count u32 @0x14 + dirSize u32 @0x18 + nameSize u32 @0x1C
- directory: count 条 x 24B { nameOff u32, 0 u32, dataOff u32, usz u32, csz u32, flags u32 }
- names: 紧跟目录, 每个名字 \\0 结尾
- data: 紧跟名字区 (无 0x1000 对齐!), 每个文件 = zlib 流(不含 adler32 尾部校验)
  压缩 = zlib.decompressobj().decompress(blob) (eof=False 正常, 流无 trailer)
- csz 累计 = dataOff 链; 块间无 padding
- 未压缩特例: csz == usz 且 数据非 0x78 开头 -> 直接存储

用法:
  python sr4_vpp_pack.py <template_vpp> <src_dir> <output_vpp>
  template_vpp 用于提取文件名顺序与 header 字段; src_dir 须含全部同名文件
"""
import os, sys, struct, zlib

MAGIC = 0x51890ACE
VERSION = 10


def read_template(path):
    """读取模板 vpp, 返回 (count, names, names_bytes, flags, crc, unk0C, dir_entries)"""
    d = open(path, 'rb').read()
    magic, ver = struct.unpack_from('<II', d, 0)
    if magic != MAGIC:
        raise ValueError(f'{path}: magic 0x{magic:08X} != 0x{MAGIC:08X}')
    if ver != VERSION:
        raise ValueError(f'{path}: version {ver} != {VERSION}')
    crc = struct.unpack_from('<I', d, 0x08)[0]
    unk0C = struct.unpack_from('<I', d, 0x0C)[0]
    flags = struct.unpack_from('<I', d, 0x10)[0]
    count = struct.unpack_from('<I', d, 0x14)[0]
    dir_size = struct.unpack_from('<I', d, 0x18)[0]   # = count * 24
    name_size = struct.unpack_from('<I', d, 0x1C)[0]

    dir_off = 0x28
    names_off = dir_off + count * 24

    # 提取文件名顺序 + 每条目录的 nameOff 和 flags
    names = []
    dir_entries = []  # [{name_off, flags}, ...]
    for i in range(count):
        no, b, doff, usz, csz, fl = struct.unpack_from('<6I', d, dir_off + i * 24)
        end = d.index(b'\x00', names_off + no)
        name = d[names_off + no:end].decode('ascii', 'replace')
        names.append(name)
        dir_entries.append(dict(name_off=no, flags=fl))

    # names 区原始字节
    names_bytes = d[names_off: names_off + name_size]

    return count, names, names_bytes, flags, crc, unk0C, dir_entries


def compress_data(data):
    """
    压缩数据为标准 zlib 流 (有 2 字节 0x78 头, 去掉 adler32 尾部 4 字节).
    匹配 SR4 vpp 格式: decompress_blob() 通过 blob[:2] in (0x7801/0x785e/0x789c/0x78da) 识别 zlib.
    如果压缩无收益则返回原数据 (未压缩存储).
    返回 (is_compressed, payload, csz)
    """
    usz = len(data)
    # 标准 zlib 压缩 (含 2 字节头 + adler32 尾部 4 字节)
    comp = zlib.compress(data, zlib.Z_BEST_COMPRESSION)
    # 去掉 adler32 尾部 4 字节, 匹配 SR4 vpp 格式
    comp = comp[:-4]
    if len(comp) < usz:
        return True, comp, len(comp)
    else:
        return False, data, usz


def pack_vpp(template_path, src_dir, out_path, verbose=True):
    """打包: 用 src_dir 中的文件按 template 的顺序与 names 重打包."""
    count, names, names_bytes, flags, crc, unk0C, dir_entries = read_template(template_path)

    # 准备每个文件的数据
    entries = []
    total_usz = 0
    total_csz = 0

    for idx, name in enumerate(names):
        p = os.path.join(src_dir, name)
        if not os.path.exists(p):
            raise FileNotFoundError(f'缺失文件: {p}')
        data = open(p, 'rb').read()
        is_comp, payload, csz = compress_data(data)
        usz = len(data)
        entries.append(dict(
            name=name, payload=payload, usz=usz, csz=csz, is_comp=is_comp,
            name_off=dir_entries[idx]['name_off'],
            entry_flags=dir_entries[idx]['flags'],
        ))
        total_usz += usz
        total_csz += csz

    # 计算布局
    # header: 0x28B
    # directory: count * 24B
    # names: name_size bytes (names_bytes 原样)
    # data: 紧跟, 无对齐

    dir_off = 0x28
    names_off = dir_off + count * 24
    name_size = len(names_bytes)
    data_base = names_off + name_size

    # 计算 dataOff (累计偏移, 无 padding)
    data_off_acc = 0
    for ent in entries:
        ent['data_off'] = data_off_acc
        data_off_acc += ent['csz']

    # 构建输出
    out = bytearray()

    # --- header (0x28B) ---
    header = bytearray(0x28)
    struct.pack_into('<II', header, 0x00, MAGIC, VERSION)
    struct.pack_into('<I', header, 0x08, crc)
    struct.pack_into('<I', header, 0x0C, unk0C)
    struct.pack_into('<I', header, 0x10, flags)
    struct.pack_into('<I', header, 0x14, count)
    struct.pack_into('<I', header, 0x18, count * 24)   # dirSize
    struct.pack_into('<I', header, 0x1C, name_size)    # nameSize
    out += header

    # --- directory (count * 24B) ---
    # 使用模板的 name_off 和 flags, 避免子串匹配问题
    dir_buf = bytearray(count * 24)
    for i, ent in enumerate(entries):
        struct.pack_into('<6I', dir_buf, i * 24,
                         ent['name_off'], 0, ent['data_off'],
                         ent['usz'], ent['csz'], ent['entry_flags'])
    out += dir_buf

    # --- names ---
    out += names_bytes

    # --- data ---
    for ent in entries:
        out += ent['payload']

    # 写出
    with open(out_path, 'wb') as f:
        f.write(bytes(out))

    if verbose:
        comp_ratio = total_csz / max(total_usz, 1)
        print(f'打包完成: {count} 文件, '
              f'未压缩 {total_usz:,} -> 压缩 {total_csz:,} '
              f'(压缩比 {comp_ratio:.2f}), '
              f'输出 {len(out):,} 字节')
    return out_path


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    pack_vpp(sys.argv[1], sys.argv[2], sys.argv[3])