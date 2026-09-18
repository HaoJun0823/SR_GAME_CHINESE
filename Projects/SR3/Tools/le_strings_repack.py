# -*- coding: utf-8 -*-
"""
SRTT le_strings 通用 repack 器 (保桶版)
=======================================
保留原文件的 bucket 分配 (引擎按 bucket 查找, 不按 hash%nb 重新分桶);
桶内 entries 按 hash 排序重排 (稳定) + 4 字节对齐, 改写 pair 中给出的 hash 文本。
其余原 hash 沿用其原文本。
"""
import struct


def read_with_bucket(path):
    """返回 (fid, ver, nb, nstr, [(bucket_idx, hash, text_bytes)])
       text_bytes = buf[s_off+4 : 终止 0x0000 +2]  (含末尾 0x0000)
    """
    buf = open(path, "rb").read()
    fid, ver, nb, nstr = struct.unpack_from("<IHHI", buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f"{path}: 非 le_strings (ID={fid:#x})")
    out = []
    for i in range(nb):
        base = 12 + i * 16
        count, _, off, _ = struct.unpack_from("<IIII", buf, base)
        for j in range(count):
            so = off + j * 4
            s_off = struct.unpack_from("<I", buf, so)[0]
            if s_off == 0:
                out.append((i, 0, b""))     # 占位空槽 (s_off=0)
                continue
            if s_off + 4 > len(buf):
                out.append((i, 0, b""))
                continue
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from("<H", buf, e)[0] != 0:
                e += 2
            text_bytes = buf[s_off + 4: e + 2]
            h = struct.unpack_from("<I", buf, s_off)[0]
            out.append((i, h, text_bytes))
    return fid, ver, nb, nstr, out


def repack(in_path, pairs, out_path):
    """
    pairs: dict {hash: text_u16_bytes_LE(末尾 0x0000)}
           None 或缺失 = 沿用原文件文本
    """
    pairs = dict(pairs)
    fid, ver, nb, nstr, entries = read_with_bucket(in_path)
    # 应用覆盖 (保留空槽)
    out_entries = []
    for b, h, t in entries:
        if h == 0:
            out_entries.append((b, 0, b""))
        elif h in pairs and pairs[h] is not None:
            out_entries.append((b, h, pairs[h]))
            pairs.pop(h, None)
        else:
            out_entries.append((b, h, t))
    # 每桶内按 hash 升序 (空槽 0 排最前)
    buckets = [[] for _ in range(nb)]
    for b, h, t in out_entries:
        buckets[b].append((h, t))
    for b in range(nb):
        buckets[b].sort(key=lambda x: x[0])
    # 写文件
    head_size = 12 + 16 * nb + 4 * nstr
    string_start = (head_size + 3) & ~3
    out = bytearray(string_start)
    struct.pack_into("<IHHI", out, 0, fid, ver, nb, nstr)
    cur = string_start
    bucket_offsets = [[] for _ in range(nb)]
    for b in range(nb):
        for h, t in buckets[b]:
            if h == 0:
                bucket_offsets[b].append(0)        # 空槽: offset=0
                continue
            entry = struct.pack("<I", h) + t
            while cur & 3:
                cur += 1
                out.append(0)
            bucket_offsets[b].append(cur)
            out += entry
            cur = len(out)
    # 桶 header (count + offset 表起点)
    for b in range(nb):
        bucket_header = 12 + b * 16
        off_table_start = 12 + nb * 16 + 4 * sum(len(x) for x in buckets[:b])
        struct.pack_into("<IIII", out, bucket_header, len(buckets[b]), 0, off_table_start, 0)
    # offset 表
    pos = 12 + 16 * nb
    for b in range(nb):
        for off in bucket_offsets[b]:
            struct.pack_into("<I", out, pos, off)
            pos += 4
    open(out_path, "wb").write(bytes(out))
    return nstr


def repack_inplace(in_path, pairs, out_path):
    """
    原位覆盖版: 保持官方文件布局完全不变, 只把 pairs{hash: 新文本(含0x0000)}
    对应条目的文本**在原 string 槽位原位改写** (不足的剩余字节清零)。
    要求每条新文本 <= 该条目原文本槽长(含终止符), 否则报错。
    适用: 引擎可能依赖 string 区线性布局, 全量 repack 重排有风险 (v3 崩溃疑因)。
    """
    buf = bytearray(open(in_path, "rb").read())
    fid, ver, nb, nstr = struct.unpack_from("<IHHI", buf, 0)
    if fid != 0xA84C7F73:
        raise ValueError(f"{in_path}: 非 le_strings (ID={fid:#x})")
    pairs = dict(pairs)
    replaced = {}
    for i in range(nb):
        count, _, off, _ = struct.unpack_from("<IIII", buf, 12 + i * 16)
        for j in range(count):
            s_off = struct.unpack_from("<I", buf, off + j * 4)[0]
            if s_off == 0:
                continue
            h = struct.unpack_from("<I", buf, s_off)[0]
            if h not in pairs:
                continue
            # 原文本区: [s_off+4, e) 到 0x0000 终止 (含 e, e+1 两字节 0x00)
            e = s_off + 4
            while e + 1 < len(buf) and struct.unpack_from("<H", buf, e)[0] != 0:
                e += 2
            slot_len = (e + 2) - (s_off + 4)      # 原文本字节数(含终止 0x0000)
            new_t = pairs.pop(h)
            assert len(new_t) <= slot_len, \
                f"hash {h:#x}: 新文本 {len(new_t)}B > 原槽 {slot_len}B 无法原位覆盖"
            # 写 {hash} 不变, 覆盖文本并清零剩余
            buf[s_off + 4: s_off + 4 + len(new_t)] = new_t
            buf[s_off + 4 + len(new_t): s_off + 4 + slot_len] = b"\x00" * (
                slot_len - len(new_t))
            replaced[h] = (s_off, slot_len, len(new_t))
    if pairs:
        miss = ", ".join(f"{h:#x}" for h in pairs)
        raise ValueError(f"以下 hash 在原文件中不存在: {miss}")
    open(out_path, "wb").write(bytes(buf))
    return replaced


def demo():
    in_p = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\misc\menu_us.le_strings"
    slots = b"\x70\x01\x71\x01\x72\x01\x73\x01\x74\x01\x75\x01\x00\x00"
    n = repack(in_p, {0x7f75a300: slots}, in_p)
    print(f"repack OK nstr={n}")


if __name__ == "__main__":
    demo()