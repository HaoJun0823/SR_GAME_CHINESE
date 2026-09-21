# -*- coding: utf-8 -*-
"""
SR4R_I18N -- MS Store 版主程序 dump 的 IDA 加载脚本
=====================================================

用途
----
把 SR4R_dump_text.bin（MS Store 版 sr_hv.exe 的 .text 段裸 dump）在 IDA 里
装成"地址正确"的数据库：反汇编出来的地址与 dllmain.cpp 里的 VA 表、
与 SR4R_I18N.log 里打印的地址**完全同口径**，可以直接对拍。

背景
----
MS Store (MSIXVC) 的 exe 在授权进程外是密文（熵 8.0、无 MZ/PE），
磁盘上无法静态分析。但进程内是可读的，所以由 DLL 自己在运行时
把 .text 段原样写出（text_dump=2 开关），得到这份 bin。

    .text va=0x140001000  vsize=0x127DB1F  bin_off=0x0  size=0x127DB1F
    文件大小 19389215 == 0x127DB1F  (精确相等, 逐字节原样)

映射关系（唯一需要记住的一条公式）
----------------------------------
    bin_off = VA - 0x140000000 - 0x1000
    VA      = bin_off + 0x140000000 + 0x1000

用法（IDA Pro 9.x / 8.x，二选一）
---------------------------------
【方式 A · File > Open 时指定（推荐, 最省事）】
  1. File > Open  ->  选 SR4R_dump_text.bin
  2. 弹窗 "Binary file" 里：
       Processor type : x86-64 (metapc)
       [x] Loading address  填  0x140001000
       [x] Manual load（可选, 想在加载时微调段属性才勾）
  3. 打开后 File > Script file... 选本脚本，跑完即得正确数据库。

【方式 B · 用 -B 批处理（无头, 适合反复重建）】
  ida64.exe -B -pmetapc SR4R_dump_text.bin
  然后 File > Script file... 选本脚本。

脚本做了什么
------------
1. 校验加载基址是不是 0x140001000（不对就自动 rebase）
2. 把整段标成 64 位代码，跑 autoanalysis
3. 修正 9 个已定位 hook 的地址为**函数头**并加注释/命名
   （这些地址来自 dllmain.cpp 的 AOB + VA 表, 已实测命中）
4. 在 IDA 里落一份"已知锚点"清单到 IDA 输出窗口, 便于 Ctrl+L 跳转

注意
----
- 这是**裸段**, 不是完整 PE：没有 import 表 / 没有 rdata / 没有重定位。
  所以 IDA 看不到 API 名字, `call sub_XXXX` 都是裸地址。
  排查 hook 落点、比对特征码、看局部逻辑**完全够用**；
  想追 API 语义需要用 Steam/GOG 版同源函数做参照（L3 就是这么来的）。
- 段内若有 .pdata / 对齐填充, IDA 可能报"这里不是代码"。属正常。
"""

import idaapi
import idc
import ida_bytes
import ida_auto
import ida_kernwin
import ida_segment
import ida_funcs
import ida_nalt
import ida_name

# ---------------------------------------------------------------- 常量

EXPECT_BASE = 0x140001000          # .text 段的加载基址
EXPECT_SIZE = 0x127DB1F            # .text 段大小
IMAGE_BASE  = 0x140000000          # 游戏 exe 的 ImageBase
TEXT_RVA    = 0x1000               # .text 段 RVA
GAME_TS     = 0x5E58CEF8           # MS Store 版 TimeDateStamp
GAME_EP     = 0x00FC44FC           # MS Store 版 EntryPoint RVA
GAME_SOI    = 0x07E6D000           # MS Store 版 SizeOfImage

# 9 个 hook + mountreg：name -> (VA, 说明)
# VA 全部来自 dllmain.cpp 的 AOB 定位结果 / VA 表，已在四构建上验证。
# 来源: dllmain.cpp 的 CFG_MSSTORE / kBuildFp / MOUNT_REG 的 AOB 定位结果。
ANCHORS = [
    ("hk_draw_wide",   0x140EF6D40, "DrawWide       (CFG_MSSTORE)"),
    ("hk_format",      0x140E92DA0, "Format         (CFG_MSSTORE)"),
    ("hk_font_lookup", 0x140E0A530, "FontLookup     (CFG_MSSTORE)"),
    ("hk_tex_obj",     0x140DEAF00, "TexObj         (CFG_MSSTORE)"),
    ("hk_srv_resolve", 0x1411DFE30, "SrvResolve     (CFG_MSSTORE)"),
    ("hk_lang_cur",    0x140E92770, "LangCur        (CFG_MSSTORE)"),
    ("hk_lang_txt",    0x140E92750, "LangTxt        (CFG_MSSTORE)"),
    ("hk_subtitle",    0x140488A60, "SubtitleDraw   (CFG_MSSTORE)"),
    ("fn_mount_reg",   0x140DF0AB0, "MOUNT_REG 挂载表注册器 (L3 实测命中)"),
]

# 给 mountreg 反查出的两个全局（挂载表三要素）也标一下，
# 值来自 dllmain 的 MOUNTREG_OFF_* 解址公式：
#   计数 = fn+0x30+7+disp32   |   数组+4 = fn+0x3F+7+disp32
MOUNTREG_FN = 0x140DF0AB0
MR_OFF_MOVZX_ESI_MS = 0x19
MR_OFF_COUNT_INSN_MS = 0x30
MR_OFF_ARRAY_INSN_MS = 0x3F


# ---------------------------------------------------------------- 工具

def msg(s):
    print("[ida_load] %s" % s)


def va_to_off(va):
    """VA -> 这份 dump 里的文件偏移"""
    return va - IMAGE_BASE - TEXT_RVA


def off_to_va(off):
    """文件偏移 -> VA"""
    return off + IMAGE_BASE + TEXT_RVA


def get_text_seg():
    """取到我们关心的那个段（第一个 64 位代码段）"""
    for i in range(ida_segment.get_segm_qty()):
        s = ida_segment.getnseg(i)
        if s is None:
            continue
        if s.start == EXPECT_BASE:
            return s
    # 没找到精确的，就退而求其次取第一个
    return ida_segment.getnseg(0)


# ---------------------------------------------------------------- 主流程

def read_disp32(insn_va):
    """读一条 rip 相对指令的 disp32（disp32 在操作码之后第 3 字节，即 insn+3）"""
    d = ida_bytes.get_dword(insn_va + 3)
    if d >= 0x80000000:
        d -= 0x100000000
    return d


def rip_target(insn_va, insn_len):
    """rip 相对寻址目标 = 下一条指令地址 + disp32"""
    return insn_va + insn_len + read_disp32(insn_va)


def resolve_mountreg_globals():
    """反解 mountreg 内的挂载表计数/数组全局地址, 并在 IDA 里标注"""
    fn = MOUNTREG_FN
    msg("")
    msg("--- 反解 mountreg (fn=%#x) 的挂载表全局 ---" % fn)

    # 计数: mov r10d, cs:[rip+disp32]  (10 字节: 44 8B 15 + disp32)
    cnt_insn = fn + MR_OFF_COUNT_INSN_MS
    b = ida_bytes.get_bytes(cnt_insn, 3)
    if b == b"\x44\x8B\x15":
        cnt_va = rip_target(cnt_insn, 7)
        msg("  计数 @ %#x   (insn %#x: mov r10d, cs:[rip+..])" % (cnt_va, cnt_insn))
        ida_name.set_name(cnt_va, "g_mountCount", ida_name.SN_NOCHECK | ida_name.SN_FORCE)
        idc.set_cmt(cnt_insn, "SR4R: 挂载表计数 (dword) -> %#x" % cnt_va, 0)
        ida_bytes.create_data(cnt_va, ida_bytes.FF_DWORD, 4, idaapi.BADADDR)
    else:
        msg("  !! 计数指令字节不符: %s (期望 44 8B 15)" % b.hex(" "))

    # 数组: lea rax, [rip+disp32]  (7 字节: 48 8D 05 + disp32)，得到「数组+4」
    arr_insn = fn + MR_OFF_ARRAY_INSN_MS
    b = ida_bytes.get_bytes(arr_insn, 3)
    if b == b"\x48\x8D\x05":
        arrp4_va = rip_target(arr_insn, 7)
        arr_va = arrp4_va - 4
        msg("  数组+4 @ %#x -> 数组 @ %#x   (insn %#x: lea rax,[rip+..])"
            % (arrp4_va, arr_va, arr_insn))
        ida_name.set_name(arr_va, "g_mountArray", ida_name.SN_NOCHECK | ida_name.SN_FORCE)
        idc.set_cmt(arr_insn, "SR4R: 挂载表数组(12B/项, 上限16) -> %#x" % arr_va, 0)
        # 表项 12 字节, 上限 16 项 => 192 字节
        ida_bytes.create_data(arr_va, ida_bytes.FF_BYTE, 192, idaapi.BADADDR)
    else:
        msg("  !! 数组指令字节不符: %s (期望 48 8D 05)" % b.hex(" "))

    # movzx esi, r8b 校验（确认这确实是那个函数）
    mz = fn + MR_OFF_MOVZX_ESI_MS
    b = ida_bytes.get_bytes(mz, 4)
    msg("  movzx esi,r8b @ %#x = %s (期望 41 0F B6 F0)" % (mz, b.hex(" ")))

    msg("")
    msg("  [注] 上面两个全局地址落在 .text 之外（0x146CDxxxx）—— 这份 dump")
    msg("       只含 .text 段，所以看不到它们的初值。要看内容需另做一次")
    msg("       数据段 dump，或用 IDA 的 Dump memory 在附加调试时取。")


def main():
    seg = get_text_seg()
    if seg is None:
        msg("!! 取不到任何段, 加载可能失败了")
        return

    msg("当前段: %s  start=%#x end=%#x" %
        (ida_segment.get_segm_name(seg), seg.start, seg.end))

    # ---- 1. 基址校验 / 自动 rebase
    if seg.start != EXPECT_BASE:
        delta = EXPECT_BASE - seg.start
        msg("段起始 %#x != 期望 %#x -> 需要 rebase (delta=%+#x)" %
            (seg.start, EXPECT_BASE, delta))
        seg.start += delta
        seg.end += delta
        if not ida_segment.set_segm_start(seg, seg.start, seg.end):
            msg("!! rebase 失败。请重新以 Loading address=0x140001000 打开")
            return
        msg("rebase 完成 -> %#x" % seg.start)
    else:
        msg("基址正确 (%#x), 无需 rebase" % EXPECT_BASE)

    # ---- 2. 标记为 64 位代码
    if not (seg.perm & ida_segment.SEGPERM_EXEC):
        msg("段没有 EXEC 权限, 补上")
        seg.perm |= ida_segment.SEGPERM_EXEC
    if seg.bitness != 2:
        msg("段 bitness=%d, 改为 2 (64-bit)" % seg.bitness)
        seg.bitness = 2

    msg("把整段标成代码并开始自动分析（大段, 会慢一点）...")
    ida_bytes.del_items(seg.start, ida_bytes.DELIT_SIMPLE,
                        seg.end - seg.start)
    ida_auto.auto_wait()
    msg("自动分析完成")

    # ---- 3. 修正 hook 锚点
    hit, miss_hit = 0, 0
    for name, va, desc in ANCHORS:
        if va == 0x140000000:
            msg("跳过 %-16s (VA 未填) %s" % (name, desc))
            miss_hit += 1
            continue
        off = va_to_off(va)
        if off < 0 or off >= EXPECT_SIZE:
            msg("!! %-16s VA=%#x 越界" % (name, va))
            miss_hit += 1
            continue

        # 造函数头（hook 目标基本都是函数入口）
        if not ida_funcs.add_func(va):
            # 可能已经有函数了，或该地址不合法
            if not ida_funcs.get_func(va):
                msg("~~ %-16s %#x 造函数头失败（可能不是代码）" % (name, va))

        ida_name.set_name(va, name, ida_name.SN_NOCHECK | ida_name.SN_FORCE)
        idc.set_cmt(va, "SR4R_I18N anchor: %s -- %s" % (name, desc), 0)
        hit += 1
        msg("锚点 %-16s @ %#x  (off %#x)  %s" % (name, va, off, desc))

    msg("锚点落位: 成功 %d / 未填 %d" % (hit, miss_hit))

    # ---- 3.5 反解 mountreg 内部的挂载表全局地址
    # mountreg 内部两处 rip 相对寻址（MS 世代偏移 0x30 / 0x3F）：
    #   计数  mov r10d, cs:[rip+disp32]   -> disp32 位于 insn+3
    #   数组  lea rax, [rip+disp32]       -> disp32 位于 insn+3（得到的是「数组+4」）
    resolve_mountreg_globals()

    # ---- 4. 记一条 ImageBase 到 IDA 数据库描述里, 便于识别
    ida_nalt.set_root_filename("SR4R_ms_hv.exe.text")
    msg("目标文件已标记为 SR4R_ms_hv.exe.text")

    msg("")
    msg("=== 完成 ===")
    msg("游戏 exe 元信息(MS Store): TS=%#x EP=%#x SOI=%#x" %
        (GAME_TS, GAME_EP, GAME_SOI))
    msg("VA 换算: VA = off + %#x   |   off = VA - %#x" %
        (IMAGE_BASE + TEXT_RVA, IMAGE_BASE + TEXT_RVA))
    msg("现在可以 Ctrl+L 跳到 fn_mount_reg (0x140DF0AB0) 开始看挂载表注册器了")


main()
