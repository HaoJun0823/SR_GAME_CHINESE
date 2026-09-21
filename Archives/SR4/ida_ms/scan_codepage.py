# -*- coding: utf-8 -*-
"""
从 MS 版 .text 裸段 dump 里找「编码转换相关」的调用点。

做法：dump 的 .text 里没有 import 表（裸段），但 call 的目标若是 IAT，
会表现为 `FF 15 <rip+disp32>`，disp32 指向 .rdata 的 IAT 槽 —— 而 IAT 槽
在 .text 里看不到。所以我们改用两条互补的线索：

线索 A —— 代码页常量做立即数出现。
  CP_ACP=0, CP_UTF8=65001(0xFDE9), CP_OEMCP=1, CP_UTF7=65000(0xFDE8)
  典型形态：mov r8d, 0FDE9h  /  mov edx, 0FDE9h  /  mov ecx, 0FDE9h
  这是 MultiByteToWideChar / WideCharToMultiByte 的第 1 或第 4 个参数。

线索 B —— 特征字节序列。
  FF 15 disp32     call cs:[rip+disp32]   (IAT 间接调用)
  B8/BA/B9/41 B8  imm32=0xFDE9           (装载 CP_UTF8)
"""
import struct
import os
import re

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "SR4_ms_hv.text.bin")
IMG, TRVA = 0x140000000, 0x1000

b = open(BIN, "rb").read()
SIZE = len(b)


def off(va):
    return va - IMG - TRVA


def va(o):
    return o + IMG + TRVA


def find_all(pat):
    out, i = [], 0
    while True:
        j = b.find(pat, i)
        if j < 0:
            break
        out.append(j)
        i = j + 1
    return out


lines = []
P = lines.append

P("=" * 74)
P("MS 版 sr_hv.exe .text 段 —— 编码转换相关线索扫描")
P("bin: %s  (%d bytes)" % (os.path.basename(BIN), SIZE))
P("=" * 74)

# ---------------- 线索 A：代码页立即数

P("")
P("[A] 代码页立即数出现位置")
P("-" * 74)

# mov ecx, imm32 ; mov edx, imm32 ; mov r8d, imm32 ; mov r9d, imm32
# 0xFDE9 = CP_UTF8, 0xFDE8 = CP_UTF7, 0x0000 = CP_ACP(通常不这么写)
CP_IMMS = {
    b"\xb9\xe9\xfd\x00\x00": ("mov ecx, CP_UTF8(65001)", 0xFDE9),
    b"\xba\xe9\xfd\x00\x00": ("mov edx, CP_UTF8(65001)", 0xFDE9),
    b"\x41\xb8\xe9\xfd\x00\x00": ("mov r8d, CP_UTF8(65001)", 0xFDE9),
    b"\x41\xb9\xe9\xfd\x00\x00": ("mov r9d, CP_UTF8(65001)", 0xFDE9),
    b"\xb9\xe8\xfd\x00\x00": ("mov ecx, CP_UTF7(65000)", 0xFDE8),
    b"\xba\xe8\xfd\x00\x00": ("mov edx, CP_UTF7(65000)", 0xFDE8),
    b"\x41\xb8\xe8\xfd\x00\x00": ("mov r8d, CP_UTF7(65000)", 0xFDE8),
    b"\x41\xb9\xe8\xfd\x00\x00": ("mov r9d, CP_UTF7(65000)", 0xFDE8),
}

total_utf8 = 0
total_utf7 = 0
for pat, (desc, cp) in CP_IMMS.items():
    hits = find_all(pat)
    if not hits:
        continue
    if cp == 0xFDE9:
        total_utf8 += len(hits)
    else:
        total_utf7 += len(hits)
    P("")
    P("  %s  ->  %d 处" % (desc, len(hits)))
    for h in hits[:24]:
        P("      %#010x  (文件偏移 %#x)" % (va(h), h - 3 + 3 if False else h))
    if len(hits) > 24:
        P("      ... 另有 %d 处" % (len(hits) - 24))

P("")
P("  小计: CP_UTF8 立即数 %d 处 | CP_UTF7 立即数 %d 处" % (total_utf8, total_utf7))

# ---------------- 线索 B：call [rip+disp32] 全部
P("")
P("[B] call cs:[rip+disp32]  (FF 15) 的总数 —— 即 IAT 间接调用点")
P("-" * 74)
calls = find_all(b"\xff\x15")
P("  共 %d 处" % len(calls))
P("  （裸段看不到 IAT 名字，逐个名字需要另做数据段 dump 或对照 Steam 版同源函数）")

# ---------------- 线索 C：把 CP_UTF8 立即数附近的反汇编打出来
P("")
P("[C] 含 CP_UTF8 立即数的指令窗口（前后各 32 字节）")
P("-" * 74)

seen_windows = set()
shown = 0
all_utf8_hits = []
for pat, (desc, cp) in CP_IMMS.items():
    if cp != 0xFDE9:
        continue
    for h in find_all(pat):
        all_utf8_hits.append((h, desc))

all_utf8_hits.sort()
for h, desc in all_utf8_hits[:16]:
    # 窗口对齐到 16
    start = (h - 32) & ~0xF
    end = min(h + 32, SIZE)
    key = (start, end)
    if key in seen_windows:
        continue
    seen_windows.add(key)
    shown += 1
    P("")
    P("  --- %s @ %#x ---" % (desc, va(h)))
    for p in range(start, end, 16):
        chunk = b[p:p + 16]
        mark = "  <<<" if p <= h < p + 16 else ""
        P("      %#010x  %-47s %s" % (va(p), chunk.hex(" "), mark))

P("")
P("=" * 74)
P("说明: CP_UTF8 立即数出现即意味着这里有 MultiByteToWideChar /")
P("      WideCharToMultiByte / CompareStringEx 之类显式指定 UTF-8 的调用。")
P("      如果确认存在，就能顺着它找到引擎「默认编码」的确切位置。")
P("=" * 74)

outp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cpscan.txt")
with open(outp, "w", encoding="utf-8", newline="") as f:
    f.write("\n".join(lines) + "\n")
print("written", outp, len(lines), "lines")
