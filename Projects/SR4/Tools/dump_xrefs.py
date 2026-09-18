# -*- coding: utf-8 -*-
"""
在 MS Store 段 dump 上做"调用点反查"(xref)，独立复核 8 个 hook 落点。

为什么要它:
  IDA 库只有 Steam/GOG/EPIC 三份，MS Store 的 exe 是 MSIXVC 密文，离线取不到。
  但我们在进程内落了 .text 裸映像 —— 有了它就能用纯算术做 xref:
    · E8 rel32 / E9 rel32            -> 直接调用/跳转
    · 8 字节小端 == 目标 VA          -> 函数指针(vtable 注册等)
  这条链完全独立于"特征码命中"，是第二套证据。

关键疑问 (本脚本要回答的):
  · MS 上 DRAW_WIDE 有多少调用点? Steam 是 6 个 —— 若量级一致, 说明角色相同
  · MS 上 LANG_CUR / LANG_TXT 有没有调用点? Steam 上 LANG_CUR 是 0, 若 MS 也是 0,
    说明这个 hook 落点是个"没人调用的 thunk", 挂了也没用
  · FORMAT 的调用点是否与 Steam 同构
"""
import struct, os

DUMP = r"G:\Projects\SR4R_DLL\.temp\dump_ms\SR4R_dump_text.bin"
TEXT_VA = 0x140001000

MS = {
    "DRAW_WIDE":   0x140EF6D40,
    "FORMAT":      0x140E92DA0,
    "FONT_LOOKUP": 0x140E0A530,
    "TEXOBJ":      0x140DEAF00,
    "SRV_RESOLVE": 0x1411DFE30,
    "LANG_CUR":    0x140E92770,
    "LANG_TXT":    0x140E92750,
    "SUBTITLE":    0x1400488A60,
}
# 这 6 个在 dump 触发时已安装(hook 入口被 5 字节 jmp 覆盖), 不影响"反查调用点"
PATCHED = ["DRAW_WIDE","FONT_LOOKUP","TEXOBJ","SRV_RESOLVE","LANG_CUR","LANG_TXT"]

# Steam 对照 (来自 IDA xrefs_to)
STEAM_XREF = {
    "DRAW_WIDE": 6, "FORMAT": 5, "FONT_LOOKUP": 12, "TEXOBJ": 2,
    "SRV_RESOLVE": 4, "LANG_CUR": 0, "LANG_TXT": 1, "SUBTITLE": 6,
}


def find_riprefs(buf, va):
    """找 ldr/lea reg,[rip+disp32] 之类对 VA 的引用 —— 用 48 8B 05 / 48 8D 05 / 4C 8B 05 等前缀"""
    out = []
    for pre in (b"\x48\x8B\x05", b"\x48\x8D\x05", b"\x4C\x8B\x05", b"\x4C\x8D\x05",
                b"\x48\x8B\x0D", b"\x48\x8D\x0D", b"\x4C\x8B\x0D", b"\x4C\x8D\x0D"):
        start = 0
        while True:
            j = buf.find(pre, start)
            if j < 0:
                break
            disp = struct.unpack_from("<i", buf, j + 3)[0]
            if TEXT_VA + j + 7 + disp == va:
                out.append(TEXT_VA + j)
            start = j + 1
    return out


def find_calls(buf, va):
    """E8/E9 rel32 目标 == va"""
    out = []
    for op in (0xE8, 0xE9):
        start = 0
        while True:
            j = buf.find(bytes([op]), start)
            if j < 0:
                break
            rel = struct.unpack_from("<i", buf, j + 1)[0]
            if TEXT_VA + j + 5 + rel == va:
                out.append((TEXT_VA + j, op))
            start = j + 1
    return out


def find_abspointers(buf, va):
    """8 字节小端 == va (vtable/函数指针表)"""
    needle = struct.pack("<Q", va)
    out, start = [], 0
    while True:
        j = buf.find(needle, start)
        if j < 0:
            break
        out.append(TEXT_VA + j)
        start = j + 1
    return out


def main():
    buf = open(DUMP, "rb").read()
    print("=" * 100)
    print("MS Store .text 调用点反查 (dump 大小 %d)" % len(buf))
    print("=" * 100)
    print("  %-13s %-16s %-8s %-8s %-8s %s" % ("hook", "MS VA", "call", "lea", "ptr", "Steam xref (IDA)"))
    print("  " + "-" * 92)

    detail = {}
    for name in ["DRAW_WIDE", "FORMAT", "FONT_LOOKUP", "TEXOBJ", "SRV_RESOLVE", "LANG_CUR", "LANG_TXT", "SUBTITLE"]:
        va = MS[name]
        calls = find_calls(buf, va)
        leas = find_riprefs(buf, va)
        ptrs = find_abspointers(buf, va)
        detail[name] = (calls, leas, ptrs)
        print("  %-13s 0x%-13X %-8d %-8d %-8d %d" % (name, va, len(calls), len(leas), len(ptrs), STEAM_XREF.get(name, -1)))

    print("")
    print("=" * 100)
    print("调用点明细")
    print("=" * 100)
    for name in ["DRAW_WIDE", "FORMAT", "LANG_CUR", "LANG_TXT", "FONT_LOOKUP", "SRV_RESOLVE"]:
        calls, leas, ptrs = detail[name]
        print("")
        print("### %s" % name)
        if calls:
            print("    call 调用点 (%d):" % len(calls))
            for a, op in calls[:20]:
                print("      0x%X  (%s)" % (a, "call" if op == 0xE8 else "jmp"))
        else:
            print("    call 调用点: 无")
        if leas:
            print("    rip 引用 (%d): %s" % (len(leas), " ".join("0x%X" % x for x in leas[:12])))
        if ptrs:
            print("    绝对指针 (%d): %s" % (len(ptrs), " ".join("0x%X" % x for x in ptrs[:12])))

    print("")
    print("=" * 100)
    print("结论")
    print("=" * 100)
    dc = len(detail["DRAW_WIDE"][0])
    lcc = len(detail["LANG_CUR"][0]); ltc = len(detail["LANG_TXT"][0])
    print("  · DRAW_WIDE   MS call=%d  (Steam IDA=6)  %s" % (
        dc, "量级一致 -> 角色相同, 落点可信" if dc >= 4 else "明显偏少 -> 需复核"))
    print("  · LANG_CUR    MS call=%d  (Steam IDA=0)  %s" % (
        lcc, "两版都是 0 -> 该 thunk 从不被直接调用, hook 它没有作用" if lcc == 0 and STEAM_XREF["LANG_CUR"] == 0 else "有调用点"))
    print("  · LANG_TXT    MS call=%d  (Steam IDA=1)  %s" % (
        ltc, "与 Steam 一致(极低频) -> 参与文本链路但覆盖面极窄" if ltc <= 2 else "调用点较多"))


if __name__ == "__main__":
    main()
