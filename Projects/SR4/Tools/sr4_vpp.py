#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sr4_vpp.py — Saints Row IV (Re-Elected / EOS 2024+) vpp_pc 解析器
====================================================================
SR4 主程序 sr_hv.exe (x64, SR35 引擎) 的 vpp_pc 格式 (version 10):
- header 0x28B: magic u32(0x51890ACE) ver u32(10) crc u32? 0x0C
  + flags u32 @0x10? + count u32 @0x14 + dirSize u32 @0x18 + nameSize u32 @0x1C
- directory: count 条 × 24B { nameOff u32, 0 u32, dataOff u32, usz u32, csz u32, flags u32 }
- names: 紧跟目录, 每个名字 \0 结尾
- data: 紧跟名字区 (无 0x1000 对齐!), 每个文件 = zlib 流(不含 adler32 尾部校验)
  解压 = zlib.decompressobj().decompress(blob)  (eof=False 正常, 流无 trailer)
- csz 累计 = dataOff 链; 块间无 padding
未压缩特例: csz == usz 且 数据非 0x78 开头 -> 直接存储
"""
import os, sys, struct, zlib, shutil

MAGIC = 0x51890ACE
verbose = False


def read_vpp(path):
    d = open(path, 'rb').read()
    magic, ver = struct.unpack_from('<II', d, 0)
    if magic != MAGIC:
        raise ValueError(f'{path}: magic 0x{magic:08X} != 0x{MAGIC:08X}')
    count = struct.unpack_from('<I', d, 0x14)[0]
    dir_off = 0x28
    names_off = dir_off + count * 24
    nsize = struct.unpack_from('<I', d, 0x1C)[0]
    data_base = names_off + nsize
    entries = []
    for i in range(count):
        no, b, doff, usz, csz, flags = struct.unpack_from('<6I', d, dir_off + i * 24)
        end = d.index(b'\x00', names_off + no)
        name = d[names_off + no:end].decode('ascii', 'replace')
        entries.append(dict(name=name, doff=doff, usz=usz, csz=csz, flags=flags))
    return d, entries, data_base


def decompress_blob(blob, usz, name, csz):
    if blob[:2] in (b'\x78\x01', b'\x78\x5e', b'\x78\x9c', b'\x78\xda'):
        dec = zlib.decompressobj()
        try:
            out = dec.decompress(blob)
        except zlib.error:
            out = None
        if out is not None and len(out) == usz:
            return out
        try:
            out = zlib.decompress(blob)
            if len(out) == usz:
                return out
        except zlib.error:
            pass
        raise ValueError(f'{name}: zlib 解压失败 (usz={usz} csz={csz})')
    if len(blob) == usz:
        return blob
    raise ValueError(f'{name}: 未知块类型 head={blob[:4].hex()}')


def extract(src_vpp, dst_dir, limit=None, only=None):
    if not os.path.exists(src_vpp):
        print('not found:', src_vpp)
        return
    d = open(src_vpp, 'rb').read()
    count = struct.unpack_from('<I', d, 0x14)[0]
    dir_off = 0x28
    names_off = dir_off + count * 24
    nsize = struct.unpack_from('<I', d, 0x1C)[0]
    data_base = names_off + nsize
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir)
    n_ok = n_fail = 0
    for i in range(count):
        no, b, doff, usz, csz, flags = struct.unpack_from('<6I', d, dir_off + i * 24)
        end = d.index(b'\x00', names_off + no)
        name = d[names_off + no:end].decode('ascii', 'replace')
        if only and name not in only:
            continue
        blob = d[data_base + doff: data_base + doff + csz]
        try:
            payload = decompress_blob(blob, usz, name, csz)
            if len(payload) != usz:
                print(f"WARN {name}: 解压 {len(payload)} != usz {usz}")
        except ValueError as ex:
            print(f"FAIL {name}: {ex}")
            n_fail += 1
            continue
        out = os.path.join(dst_dir, name)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as f:
            f.write(payload)
        n_ok += 1
        if verbose:
            print(f"OK  {name} ({len(payload)}B)")
        if limit and n_ok >= limit:
            break
    print(f'解包 {n_ok} 文件, 失败 {n_fail} -> {dst_dir}')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    verbose = '-v' in sys.argv
    only = None
    if '--only' in sys.argv:
        only = set(sys.argv[sys.argv.index('--only') + 1:])
    extract(sys.argv[1], sys.argv[2], only=only)