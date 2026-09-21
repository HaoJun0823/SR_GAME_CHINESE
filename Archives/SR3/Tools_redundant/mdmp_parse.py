#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minidump 快速解析器：提取异常、崩溃线程、模块、寄存器、栈。"""
import struct, sys, os

# ---------- stream types ----------
STREAM_TYPES = {
    0: "UnusedStream", 1: "ReservedStream0", 2: "ReservedStream1",
    3: "ThreadListStream", 4: "ModuleListStream", 5: "MemoryListStream",
    6: "ExceptionStream", 7: "SystemInfoStream", 8: "ThreadExListStream",
    9: "Memory64ListStream", 10: "CommentStreamA", 11: "CommentStreamW",
    12: "HandleDataStream", 13: "FunctionTableStream", 14: "UnloadedModuleListStream",
    15: "MiscInfoStream", 16: "MemoryInfoListStream", 17: "ThreadInfoListStream",
    18: "HandleOperationListStream", 19: "TokenStream", 20: "JavaScriptDataStream",
    21: "SystemMemoryInfoStream", 22: "ProcessVmCountersStream", 23: "IptTraceStream",
    24: "ThreadNamesStream",
}

def read_cstr(data, rva):
    # MINIDUMP_STRING: Length(4 bytes, in bytes) + UTF-16LE buffer
    try:
        ln = struct.unpack_from("<I", data, rva)[0]
        if 0 < ln < 4096:
            return data[rva + 4: rva + 4 + ln].decode("utf-16-le", "replace")
    except Exception:
        pass
    end = data.find(b"\x00", rva)
    if end == -1:
        return data[rva:].decode("utf-8", "replace")
    return data[rva:end].decode("utf-8", "replace")

def parse(data):
    assert data[:4] == b"MDMP", "not a minidump"
    nstreams, dir_rva = struct.unpack_from("<II", data, 8)
    print(f"== Minidump: {nstreams} streams, dir@0x{dir_rva:x}")

    streams = {}
    for i in range(nstreams):
        stype, dsize, rva = struct.unpack_from("<III", data, dir_rva + i * 12)
        streams[stype] = (rva, dsize)
        print(f"  stream {stype} ({STREAM_TYPES.get(stype, '?'):24s}) size={dsize} @0x{rva:x}")

    # ---- modules ----
    mods = []  # (base, size, name)
    if 4 in streams:
        rva, dsize = streams[4]
        cnt = struct.unpack_from("<I", data, rva)[0]
        off = rva + 4
        print(f"\n== Modules: {cnt}")
        for _ in range(cnt):
            base, size, cksum, ts, name_rva = struct.unpack_from("<QIIII", data, off)
            name = read_cstr(data, name_rva)
            mods.append((base, size, name))
            off += 108
            print(f"  {base:016x} +0x{size:08x}  {name}")

    # ---- exception stream ----
    if 6 in streams:
        rva, dsize = streams[6]
        tid, = struct.unpack_from("<I", data, rva)
        # MINIDUMP_EXCEPTION at rva+8
        eo = rva + 8
        code, flags, erec, eaddr, nparam, _al = struct.unpack_from("<IIQQII", data, eo)
        print(f"\n== Exception stream (thread {tid})")
        print(f"  ExceptionCode = 0x{code:08X}  flags=0x{flags:x}")
        print(f"  ExceptionAddress = 0x{eaddr:016x}")
        print(f"  NumberParameters = {nparam}")
        if nparam > 0:
            params = struct.unpack_from("<" + "Q" * nparam, data, eo + 32)
            for i, p in enumerate(params):
                print(f"    param[{i}] = 0x{p:016x}")
        ctx_loc = struct.unpack_from("<II", data, eo + 152)  # DataSize, Rva
        ctx_rva = ctx_loc[1]
        print(f"  ThreadContext @0x{ctx_rva:x} (size {ctx_loc[0]})")
        if ctx_rva and ctx_rva + 0x1D0 <= len(data):
            # AMD64 CONTEXT register offsets (aligned to real layout)
            rax = struct.unpack_from("<Q", data, ctx_rva + 0x78)[0]
            rcx = struct.unpack_from("<Q", data, ctx_rva + 0x80)[0]
            rdx = struct.unpack_from("<Q", data, ctx_rva + 0x88)[0]
            rbx = struct.unpack_from("<Q", data, ctx_rva + 0x90)[0]
            rsp = struct.unpack_from("<Q", data, ctx_rva + 0x98)[0]
            rbp = struct.unpack_from("<Q", data, ctx_rva + 0xA0)[0]
            rsi = struct.unpack_from("<Q", data, ctx_rva + 0xA8)[0]
            rdi = struct.unpack_from("<Q", data, ctx_rva + 0xB0)[0]
            r8  = struct.unpack_from("<Q", data, ctx_rva + 0xB8)[0]
            r9  = struct.unpack_from("<Q", data, ctx_rva + 0xC0)[0]
            r10 = struct.unpack_from("<Q", data, ctx_rva + 0xC8)[0]
            r11 = struct.unpack_from("<Q", data, ctx_rva + 0xD0)[0]
            r12 = struct.unpack_from("<Q", data, ctx_rva + 0xD8)[0]
            r13 = struct.unpack_from("<Q", data, ctx_rva + 0xE0)[0]
            r14 = struct.unpack_from("<Q", data, ctx_rva + 0xE8)[0]
            r15 = struct.unpack_from("<Q", data, ctx_rva + 0xF0)[0]
            rip = struct.unpack_from("<Q", data, ctx_rva + 0xF8)[0]
            eflags = struct.unpack_from("<I", data, ctx_rva + 0x44)[0]
            print(f"  rax={rax:016x} rbx={rbx:016x} rcx={rcx:016x} rdx={rdx:016x}")
            print(f"  rsi={rsi:016x} rdi={rdi:016x} rbp={rbp:016x} rsp={rsp:016x}")
            print(f"  r8 ={r8:016x} r9 ={r9:016x} r10={r10:016x} r11={r11:016x}")
            print(f"  r12={r12:016x} r13={r13:016x} r14={r14:016x} r15={r15:016x}")
            print(f"  rip={rip:016x} eflags={eflags:08x}")
            # resolve faulting address to module
            for b, s, n in mods:
                if b <= eaddr < b + s:
                    print(f"  -> exception addr in {os.path.basename(n)} +0x{eaddr-b:x}")
                    break
            for b, s, n in mods:
                if b <= rip < b + s:
                    print(f"  -> RIP in {os.path.basename(n)} +0x{rip-b:x}")
                    break
            return dict(tid=tid, code=code, eaddr=eaddr, rip=rip, rsp=rsp, rbp=rbp,
                        regs=dict(rax=rax, rbx=rbx, rcx=rcx, rdx=rdx, rsi=rsi, rdi=rdi,
                                  r8=r8, r9=r9, r10=r10, r11=r11, r12=r12, r13=r13,
                                  r14=r14, r15=r15), mods=mods, streams=streams)
    return None

if __name__ == "__main__":
    path = sys.argv[1]
    with open(path, "rb") as f:
        data = f.read()
    parse(data)
