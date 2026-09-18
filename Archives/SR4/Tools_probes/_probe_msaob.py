# -*- coding: utf-8 -*-
"""
在 MS Store 可执行段裸映像 (SR4R_dump_text.bin) 上重放 8 条 AOB 特征码。

目的: 验证 CFG_MSSTORE 里的 8 个 hook 地址是否"名副其实"。
关键点 —— 不做循环论证:
  特征码命中了某地址，只能说明"字节序列在那儿"，不能说明"那是目标函数"。
  因此本脚本额外做【语义交叉验证】: 特征码里被 mask 通配掉的字节，正是
  RIP 相对位移 / call rel32 —— 把它们解出来，就能得到"这个函数引用了哪个全局
  / 调用了哪个函数"。这些目标地址来自**另一条独立命中的特征码**，互为印证。

  · DRAW_WIDE[44..47] 是 call rel32 -> 应指向 FONT_LOOKUP (源码注释如此声明)
  · TEXOBJ[10..13] 是 mov r32,[rip+d] -> 应指向 fontCount 全局
  · LANG_CUR[3..6] / LANG_TXT[3..6] 指向语言服务单例 -> 两者必须【同一个】全局

mask 语义与 dllmain.cpp 一致: mask[i]==0 表示通配该字节, mask[i]==1 表示精确比较。
"""
import struct, os

DUMP = r"G:\Projects\SR4R_DLL\.temp\dump_ms\SR4R_dump_text.bin"
TEXT_VA = 0x140001000          # .text 段 VA (见 .map)
GAME_BASE = 0x140000000

MS_FONTCOUNT = 0x146CFCDA8     # 运行期自解
MS_FONTLOOKUP = 0x140E0A530    # 运行期自解 == CFG_MSSTORE.FontLookup

# name, sig(bytes), mask(1=比较/0=通配; None=全比较), 语义说明, (disp在sig内偏移, 指令末在sig内偏移)
SIGS = [
    ("DRAW_WIDE", bytes([
        0x40,0x53,0x56,0x57,0x48,0x81,0xEC,0x10,0x01,0x00,0x00,0x8B,0xBC,0x24,0x60,
        0x01,0x00,0x00,0x49,0x8B,0xD9,0x0F,0x29,0xB4,0x24,0xF0,0x00,0x00,0x00,0x8B,
        0xCF,0x44,0x0F,0x29,0x74,0x24,0x70,0x0F,0x28,0xF2,0x44,0x0F,0x28,0xF1,
        0x00,0x00,0x00,0x00]),
     [1]*44 + [0,0,0,0], "尾部 call rel32 -> 期望指到 FONT_LOOKUP", (44, 49)),

    ("FORMAT", bytes([0x40,0x55,0x56,0x57,0x41,0x57,0x48,0x8D,0xAC,0x24,0x78,0xD0,0xFF,0xFF,0xB8,0x88]),
     None, "帧大小 lea rbp,[rsp-2F88h] (非地址)", None),

    ("FONT_LOOKUP", bytes([0x83,0xF9,0xFF,0x7D,0x3E,0x8D,0x81,0xFF,0xFF,0xFF,0x7F,0x83,0xF8,0xFF,0x7E,0x2E]),
     None, "叶子函数, 无地址字段", None),

    ("TEXOBJ", bytes([0x4C,0x63,0xC1,0x85,0xC9,0x78,0x77,0x44,0x3B,0x05,0,0,0,0,0x7D,0x6E]),
     [1]*10 + [0,0,0,0] + [1,1], "mov r8d,[rip+d] -> 期望指到 fontCount 全局", (10, 16)),

    ("SRV_RESOLVE", bytes([0x40,0x53,0x48,0x83,0xEC,0x20,0x0F,0xB6,0xDA,0x83,0xF9,0xFF,0x74,0x4F,0x0F,0xBA]),
     None, "无地址字段", None),

    ("LANG_CUR", bytes([0x48,0x8B,0x05,0,0,0,0,0x48,0x85,0xC0,0x74,0x0C,0x48,0x8B,0x50,0x08,
                        0x48,0x85,0xD2,0x74,0x03,0x48,0xFF,0xE2,0x33,0xC0,0xC3]),
     [1,1,1,0,0,0,0] + [1]*20, "mov rax,[rip+d] -> 语言服务单例 (vtable[1] thunk)", (3, 7)),

    ("LANG_TXT", bytes([0x48,0x8B,0x05,0,0,0,0,0x48,0x85,0xC0,0x74,0x0B,0x48,0x8B,0x10,0x48]),
     [1,1,1,0,0,0,0] + [1]*9, "mov rax,[rip+d] -> 语言服务单例 (vtable[0] thunk)", (3, 7)),

    ("SUBTITLE", bytes([0x4C,0x8B,0xDC,0x55,0x56,0x41,0x54,0x49,0x8D,0xAB,0xA8,0xFB,0xFF,0xFF,0x48,0x81]),
     None, "帧大小 lea rbp,[r11-458h] (非地址)", None),
]

ORDER = ["DRAW_WIDE","FORMAT","FONT_LOOKUP","TEXOBJ","SRV_RESOLVE","LANG_CUR","LANG_TXT","SUBTITLE"]

CFG_MSSTORE = {
    "DRAW_WIDE":   0x140EF6D40,
    "FORMAT":      0x140E92DA0,
    "FONT_LOOKUP": 0x140E0A530,
    "TEXOBJ":      0x140DEAF00,
    "SRV_RESOLVE": 0x1411DFE30,
    "LANG_CUR":    0x140E92770,
    "LANG_TXT":    0x140E92750,
    "SUBTITLE":    0x1400488A60,
}
STEAM = {"DRAW_WIDE":0x140DC36A0,"FORMAT":0x140CF9A00,"FONT_LOOKUP":0x140BF8550,
         "TEXOBJ":0x140B7AF30,"SRV_RESOLVE":0x140E2C9E0,"LANG_CUR":0x140CF1980,
         "LANG_TXT":0x140CF1960,"SUBTITLE":0x1403D18F0}


def scan(buf, sig, mask, cap=400):
    """朴素扫描: 1=比较 0=通配。先把最少的固定字节做粗筛。"""
    n = len(sig)
    pos_all = [i for i in range(n) if (mask is None or mask[i] == 1)]
    if not pos_all:
        return []
    # 选一个固定字节做粗筛锚（取中段，减少误筛开销）
    anchor = pos_all[len(pos_all) // 2]
    ab = sig[anchor]
    hits, start = [], 0
    while len(hits) < cap:
        j = buf.find(bytes([ab]), start)
        if j < 0:
            break
        st = j - anchor
        if st >= 0 and st + n <= len(buf):
            ok = True
            for i in pos_all:
                if buf[st + i] != sig[i]:
                    ok = False
                    break
            if ok:
                hits.append(st)
        start = j + 1
    return hits


def main():
    if not os.path.isfile(DUMP):
        print("dump 不存在:", DUMP)
        return
    buf = open(DUMP, "rb").read()
    print("=" * 100)
    print("MS Store .text 裸映像重放: %s" % DUMP)
    print("  file size = %d (0x%X) | .text va = 0x%X | file[0] <-> VA 0x%X" % (len(buf), len(buf), TEXT_VA, TEXT_VA))
    print("=" * 100)

    sema = {}   # name -> list of (va, target)

    for name, sig, mask, note, field in SIGS:
        hits = scan(buf, sig, mask)
        print("")
        print("### %-13s  len=%2d   %s" % (name, len(sig), note))
        if not hits:
            print("    命中 0 处   <== 该特征码在 MS 构建里不存在")
            sema[name] = []
            continue
        print("    命中 %d 处:" % len(hits))
        tl = []
        for h in hits:
            va = TEXT_VA + h
            tag = ""
            if va == CFG_MSSTORE[name]:
                tag = "   <== CFG_MSSTORE 声明值"
            elif abs(va - CFG_MSSTORE[name]) < 0x200:
                tag = "   <== 接近 CFG 值 (差 %+d)" % (va - CFG_MSSTORE[name])
            extra = ""
            if field:
                off, iend = field
                disp = struct.unpack_from("<i", buf, h + off)[0]
                tgt = TEXT_VA + h + iend + disp
                tl.append((va, tgt))
                extra = "   -> rip tgt VA 0x%X" % tgt
            print("      va=0x%X%s%s" % (va, extra, tag))
        sema[name] = tl

    print("")
    print("=" * 100)
    print("语义交叉验证 (独立于'特征码命中'的第二条证据链)")
    print("=" * 100)

    dw = sema.get("DRAW_WIDE", [])
    dwt = [t for (v, t) in dw if v == CFG_MSSTORE["DRAW_WIDE"]]
    if dwt:
        print("  [1] DRAW_WIDE 落点 0x%X 尾部 call 目标 = 0x%X" % (CFG_MSSTORE["DRAW_WIDE"], dwt[0]))
        print("      期望 = FONT_LOOKUP  0x%X" % MS_FONTLOOKUP)
        print("      => %s" % ("一致 (两个独立命中的特征码互相印证)" if dwt[0] == MS_FONTLOOKUP
                               else "不一致 (至少一个落点是错的)"))
    else:
        print("  [1] DRAW_WIDE: CFG 值未在扫描命中列表里 -> 无法交叉验证")

    tx = sema.get("TEXOBJ", [])
    txt = [t for (v, t) in tx if v == CFG_MSSTORE["TEXOBJ"]]
    if txt:
        print("  [2] TEXOBJ 落点 0x%X 引用的全局 = 0x%X" % (CFG_MSSTORE["TEXOBJ"], txt[0]))
        print("      期望 = fontCount  0x%X (运行期自解)" % MS_FONTCOUNT)
        print("      => %s" % ("一致" if txt[0] == MS_FONTCOUNT else "不一致"))
    else:
        print("  [2] TEXOBJ: CFG 值未命中 -> 无法交叉验证")

    lc = [t for (v, t) in sema.get("LANG_CUR", []) if v == CFG_MSSTORE["LANG_CUR"]]
    lt = [t for (v, t) in sema.get("LANG_TXT", []) if v == CFG_MSSTORE["LANG_TXT"]]
    if lc and lt:
        print("  [3] LANG_CUR 单例 = 0x%X" % lc[0])
        print("      LANG_TXT 单例 = 0x%X" % lt[0])
        print("      => %s" % ("同一全局 (确为一对 vtable thunk)" if lc[0] == lt[0]
                               else "不同全局 (两者不属于同一对象)"))
    else:
        print("  [3] LANG_CUR/LANG_TXT: 至少一个 CFG 值未命中 -> 无法交叉验证")

    print("")
    print("=" * 100)
    print("8 个落点相对 FORMAT 的偏移: MS Store  vs  Steam")
    print("=" * 100)
    print("  %-13s %-16s %-16s %-16s" % ("hook", "MS VA", "MS - FORMAT", "Steam - FORMAT"))
    for name in ORDER:
        ms = CFG_MSSTORE[name]
        st = STEAM[name]
        print("  %-13s 0x%-14X 0x%-14X 0x%-14X" % (
            name, ms,
            (ms - CFG_MSSTORE["FORMAT"]) & 0xFFFFFFFFFFFFFFFF,
            (st - STEAM["FORMAT"]) & 0xFFFFFFFFFFFFFFFF))


if __name__ == "__main__":
    main()
