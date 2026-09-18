#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""minidump 栈扫描: 定位崩溃线程 Rsp 附近内存, 找模块范围内的 qword (潜在返回地址),
打印调用链候选 + 栈顶上下文。"""
import struct, sys, os

def read_cstr(data, rva):
    try:
        ln = struct.unpack_from("<I", data, rva)[0]
        if 0 < ln < 4096:
            return data[rva+4: rva+4+ln].decode("utf-16-le", "replace")
    except Exception:
        pass
    end = data.find(b"\x00", rva)
    return data[rva:end].decode("utf-8", "replace") if end != -1 else data[rva:].decode("utf-8", "replace")

def parse(data):
    nstreams, dir_rva = struct.unpack_from("<II", data, 8)
    streams = {}
    for i in range(nstreams):
        stype, dsize, rva = struct.unpack_from("<III", data, dir_rva + i*12)
        streams[stype] = (rva, dsize)

    mods = []
    if 4 in streams:
        rva, dsize = streams[4]
        cnt = struct.unpack_from("<I", data, rva)[0]
        off = rva + 4
        for _ in range(cnt):
            base, size, cksum, ts, name_rva = struct.unpack_from("<QIIII", data, off)
            mods.append((base, size, read_cstr(data, name_rva)))
            off += 108

    # 崩溃线程 + 上下文
    crash = {}
    if 6 in streams:
        rva, dsize = streams[6]
        tid = struct.unpack_from("<I", data, rva)[0]
        eo = rva + 8
        code, flags, erec, eaddr, nparam, _al = struct.unpack_from("<IIQQII", data, eo)
        ctx_loc = struct.unpack_from("<II", data, eo + 152)
        ctx_rva = ctx_loc[1]
        crash = dict(tid=tid, code=code, eaddr=eaddr, ctx_rva=ctx_rva)

    # 找含 rsp 的内存区 (MemoryList 5 / Memory64List 9)
    regions = []  # (start, data_off, size)
    if 9 in streams:  # 64 位列表
        rva, dsize = streams[9]
        n, base_rva = struct.unpack_from("<QQ", data, rva)
        off = rva + 16
        for i in range(n):
            start, size = struct.unpack_from("<QQ", data, off + 16*i)
            regions.append((start, base_rva, size))
            base_rva += size
    elif 5 in streams:
        rva, dsize = streams[5]
        n = struct.unpack_from("<I", data, rva)[0]
        off = rva + 4
        for i in range(n):
            start, dsize2, doff = struct.unpack_from("<QII", data, off + 16*i)
            regions.append((start, doff, dsize2))

    # 崩溃线程上下文寄存器
    regs = {}
    crva = crash.get("ctx_rva")
    if crva:
        for nm, off in [("rax",0x78),("rcx",0x80),("rdx",0x88),("rbx",0x90),("rsp",0x98),
                        ("rbp",0xA0),("rsi",0xA8),("rdi",0xB0),("r8",0xB8),("r9",0xC0),
                        ("r10",0xC8),("r11",0xD0),("r12",0xD8),("r13",0xE0),("r14",0xE8),
                        ("r15",0xF0),("rip",0xF8)]:
            regs[nm] = struct.unpack_from("<Q", data, crva + off)[0]
        rsp = regs["rsp"]
        print(f"crash tid={crash['tid']} code={crash['code']:#x} rip={regs['rip']-0x140000000:#x} (exe+{regs['rip']-0x140000000:x}) rsp={rsp:#x}")
        # 找覆盖 rsp 的区域
        stack = None
        for start, doff, size in regions:
            if start <= rsp < start + size:
                stack = data[doff + (rsp - start): doff + size]
                s_start = rsp
                print(f"stack region: {start:#x}..{start+size:#x} (size {size:#x}) rsp off {rsp-start:#x}, avail {len(stack)} bytes")
                break
        if stack is None:
            print("!! rsp 不在任何内存区")
            # 打印全部内存区前 20
            for start, doff, size in regions[:20]:
                print(f"  mem {start:#x} size {size:#x}")
            return
        # 扫描 qword 返回地址候选
        hits = []
        for i in range(0, len(stack) - 8, 8):
            q = struct.unpack_from("<Q", stack, i)[0]
            for b, s, n in mods:
                if b <= q < b + s:
                    hits.append((i, q, q - b, os.path.basename(n)))
                    break
        print(f"\n== 栈返回地址候选 (模块内偏移) ==")
        shown = 0
        for off, q, moff, mn in hits:
            mark = ""
            print(f"  [rsp+0x{off:04x}] {q:016x}  {mn}+0x{moff:x}")
            shown += 1
            if shown > 60:
                print("  ...")
                break
    else:
        print("no exception ctx")

if __name__ == "__main__":
    with open(sys.argv[1], "rb") as f:
        parse(f.read())
