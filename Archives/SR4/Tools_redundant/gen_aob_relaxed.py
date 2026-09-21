#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_aob_relaxed.py — 生成/校验「跨构建多层特征码」(L1 精确 / L2 放宽 / L3 老构建精确)

背景
----
一次特征码匹配失败会走三层定位, 顺序 L1 -> L2 -> L3 -> 版本 VA 表:

  L1  SIG_*        从函数入口起逐字节精确(相对地址字节 mask 通配), 覆盖新构建族
                   (Steam / GOG / EPIC 三版逐字节同源, 换构建只漂移地址类字段)。
  L2  R2_*/RM_*    L1 落空时的第一次放宽: 保留指令结构, 只通配「编译期布局字段」——
                   lea rbp,[rsp-imm32] 帧大小 / mov eax,imm32 给 __chkstk 的帧大小 /
                   mov rax,[rip+disp32] cookie 位置 / call rel32 / [rsp+disp8] 溢出槽。
  L3  R3_*         **换代**构建的精确特征码。Microsoft Store 版 (sriv.exe,
                   TDS=5E58CEF8, 2020-02, 比三版早三年) 的 Format / Subtitle 换了一代
                   MSVC, 前导形态整个不同 —— 不是字段值漂移, 而是:

                     新版: push rbp; push rsi; push rdi; push r15
                           lea rbp,[rsp-2F88h]; mov eax,3088h; call __chkstk; sub rsp,rax
                     老版: mov [rsp+20h],r9; mov [rsp+18h],r8; mov [rsp+8],rcx   <- 参数
                           push rbp; push rbx; push rsi; push r14                home 区
                           lea rbp,[rsp-2F58h]; mov eax,3058h; call __chkstk; sub rsp,rax
                                                                                 先落地

                   Subtitle 同因: `mov r11,rsp` 换成 `mov rax,rsp`, xmm 号与寄存器组
                   也不同。**任何字节级 mask 都救不了长度不同的指令序列**, 所以必须
                   每个构建族一套精确特征码(L3), 而不是继续放宽。
                   L3 是**纯精确**的: 同一构建内 RIP 相对位移与 rel32 都不随 ASLR 变化,
                   所以无需任何 mask。

                   MountReg 是 2026-09-20 加入的第三条换代 hook —— 它此前是本项目
                   **唯一的手写 AOB 项**(不经过本脚本), 结果 MS Store 上
                   `mountreg: AOB 未命中 (L1 0 / L2 0 hits)` 把 loose le_string 打瘫。
                   教训: **手写项漏 L3 是必然** —— 它绕过了本脚本对四构建的强制断言。
                   现已并入: L1 读 dllmain 的 SIG_/MASK_MOUNT_REG(31 字节),
                   L2 由 operand_wild_set 机械推导(**与旧手写 mask 逐位一致**),
                   L3 由 MS 裸段生成。内部偏移按构建族在运行时切换(见 dllmain.cpp
                   g_mrOffMovzx/Count/Array): 新族 0x11/0x23/0x35, MS 0x19/0x30/0x3F。

本脚本
------
  1. 读 Steam / GOG / EPIC 三个 exe 的 PE, 以及 MS Store 版由 DLL 落盘的裸 .text
     (SR4R_dump/SR4R_dump_text.bin + .map, 见 dllmain.cpp DumpExecutableSections);
  2. **还原 MinHook 跳板**: dump 是 hook 安装之后落盘的, 已定位的 6 条 hook 入口前
     5 字节被改写成 `E9 rel32`。用该 hook 的 L1 特征码回填这 5 字节 —— 回填时顺带
     逐字节校验其后字节与 L1 相符, 这同时证明了「dump 的 VA 映射正确」;
  3. L2 通配集合由反汇编机械推导(operand_wild_set), 三版并集, 不允许手工圈;
  4. 为前导换代的 hook 从 MS .text 生成 L3 精确特征码(48B 起, 不足则加长);
  5. 全量校验: 每个 hook 在**四个构建**上都必须能唯一命中其对应层;
  6. 产出 SR4R_I18N/aob_l2_arrays.inc (dllmain.cpp 直接 #include, 杜绝复制漂移),
     并反向校验 .inc 与本次规格一致(DRIFT 检测)。

用法
----
    <venv>\\Scripts\\python.exe Tools\\gen_aob_relaxed.py [游戏目录] [--dis]
    # MS dump 路径默认 .temp/dump_ms/, 可用 --ms-dump <bin> 指定

输出: Tools/aob_relaxed_report.txt + Tools/gen_aob_relaxed.out + aob_l2_arrays.inc
"""

import io
import os
import re
import struct
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 本脚本曾被归档到 Archives/SR4/Tools_redundant/ 等目录, 此时 ROOT = dirname(HERE)
#   会指向错误层级, 导致 DLLMAIN / INC_PATH / DEFAULT_MS_DUMP 全部失效。
#   → 改为「向上探测」: 找到同时含 Projects/SR4/SR4R_I18N 的仓库根目录。
#   （也支持 --repo <dir> 显式指定, 见 main()。）
def _find_root(start):
    d = start
    for _ in range(6):
        if os.path.isdir(os.path.join(d, "Projects", "SR4", "SR4R_I18N")):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.dirname(start)          # 兜底: 旧行为


ROOT = _find_root(HERE)
_SR4 = os.path.join(ROOT, "Projects", "SR4", "SR4R_I18N")
DLLMAIN = os.path.join(_SR4, "dllmain.cpp")
INC_PATH = os.path.join(_SR4, "aob_l2_arrays.inc")
DEFAULT_GAME = r"I:\SteamLibrary\steamapps\common\Saints Row IV"
DEFAULT_MS_DUMP = os.path.join(ROOT, "Archives", "SR4", "temp", "dump_ms",
                               "SR4R_dump_text.bin")
BASE = 0x140000000

# L2 自动推导用: 视为「栈/帧指针」的基址寄存器 (r11 在 Subtitle 里被赋成 rsp 当帧指针用)
FRAME_BASE = {"rsp", "rbp", "r11", "esp", "ebp", "sp", "bp"}
# L2 自动推导用: 立即数即「代码地址」的指令
CALLJMP_MNEM = {"call", "jmp", "ljmp", "lcall"}

# (hook 名, SIG 数组名, MASK 数组名或 None, {新构建族: VA})
HOOKS = [
    ("DrawWide",   "SIG_DRAW_WIDE",     "MASK_DRAW_WIDE", {"Steam": 0x140DC36A0, "GOG": 0x140D4E520, "EPIC": 0x140DBE570}),
    ("Format",     "SIG_FORMAT",        None,             {"Steam": 0x140CF9A00, "GOG": 0x140C88810, "EPIC": 0x140CF48D0}),
    ("FontLookup", "SIG_FONT_LOOKUP",   None,             {"Steam": 0x140BF8550, "GOG": 0x140BBBBB0, "EPIC": 0x140BF3D00}),
    ("TexObj",     "SIG_TEXOBJ",        "MASK_TEXOBJ",    {"Steam": 0x140B7AF30, "GOG": 0x140B99ED0, "EPIC": 0x140B766D0}),
    ("SrvResolve", "SIG_SRV_RESOLVE",   None,             {"Steam": 0x140E2C9E0, "GOG": 0x140DC6E00, "EPIC": 0x140E278B0}),
    ("LangCur",    "SIG_LANG_CUR",      "MASK_LANG_CUR",  {"Steam": 0x140CF1980, "GOG": 0x140C88120, "EPIC": 0x140CEC850}),
    ("LangTxt",    "SIG_LANG_TXT",      "MASK_LANG_TXT",  {"Steam": 0x140CF1960, "GOG": 0x140C88100, "EPIC": 0x140CEC830}),
    ("Subtitle",   "SIG_SUBTITLE_DRAW", None,             {"Steam": 0x1403D18F0, "GOG": 0x1404B1E00, "EPIC": 0x1403D0850}),
    # MOUNT_REG (挂载表注册器, v1.9 loose le_string 用) —— 注意:
    #   * L1 只有 31 字节(L2 取 48), 与其余 hook 的 L1/L2 同长惯例不同;
    #   * L1 自带 MASK(SIG 收在 call 操作码 E8 处, 故 rel32 不在 L1 内);
    #   * 它的 L2 曾经是**手写**项(不经过本脚本), 2026-09-20 实测本脚本的
    #     operand_wild_set 推导结果与手写 mask **逐位完全一致** —— 故正式并入,
    #     手写项退役, 杜绝「手写项漏 L3」这类事故复发。
    ("MountReg",   "SIG_MOUNT_REG",     "MASK_MOUNT_REG", {"Steam": 0x140B7E7A0, "GOG": 0x140BA6570, "EPIC": 0x140B79F40}),
]

EXES = {"Steam": "sr_hv.exe", "GOG": "sr_hv_gog.exe", "EPIC": "sr_hv_epic.exe"}
NEWBUILDS = ("Steam", "GOG", "EPIC")
MSBUILD = "MSStore"

# ---- MS Store 版 (sriv.exe, TDS=5E58CEF8) 的 8 个入口 VA ----
#   6 条由 v1.2 日志的 `hook ... installed (via aob)` 直接给出(命中地址即入口);
#   Format / Subtitle 两条由 2026-09-17 从裸段 dump 逆向定位, 证据:
#     Format   0x140E92DA0: 常量锚点 mov r8d,0FFDFh + movabs r11,3FF000100000200h
#                          (两处各自按 Steam 内偏移 +66h/+74h/+10Eh 反推入口一致),
#                          其后 0x140E92E46 = cmp ax,25h 解析 '%' —— UTF-16 格式化函数;
#     Subtitle 0x140488A60: 语义指纹 mov eax,55555556h(÷3) / mov eax,38E38E39h(÷9)
#                          共现且间距 22h 与 Steam 完全相同;
#                          结构 = mov rax,rsp / push / sub rsp,298h / movaps xmm6→xmm7
#                          / movaps xmm7,xmm2(float 参数) / movaps xmm8,xmm1(double 参数)。
#   两者入口前都有 CC 对齐填充, 边界由填充独立复核。
MS_VA = {
    "DrawWide":   0x140EF6D40,
    "Format":     0x140E92DA0,
    "FontLookup": 0x140E0A530,
    "TexObj":     0x140DEAF00,
    "SrvResolve": 0x1411DFE30,
    "LangCur":    0x140E92770,
    "LangTxt":    0x140E92750,
    "Subtitle":   0x140488A60,
    # MountReg: 2026-09-20 由语义锚点(mov r10d,cs: + lea rax,[rip+] 配对, 中间夹
    #   add rax,0Ch / cmp r10d,10h) 在 MS 裸段中定位, 全段唯一。入口前是 CC 填充。
    #   其序言 push rdi / sub rsp,30h / mov [rsp+20h],-2(SEH) / mov [rsp+40h],rbx
    #   / move [rsp+48h],rsi —— 老 MSVC 形态, 与新构建族完全不同。
    #   内部偏移(MS): movzx +19h / mov r10d +30h / lea rax +3Fh(新族是 +11h/+23h/+35h)。
    #   解的全局: 计数 0x146CDBD2C / 数组+4 0x146CDCE64。
    "MountReg":   0x140DF0AB0,
}
# 前导「换代」的 hook: MS 上 L1/L2 必然落空, 必须靠 L3。
#   这也是 L3 存在的唯一理由 —— 其余各条 MS 上 L1 照常命中(日志已验证)。
#   2026-09-20: MountReg 加入 —— 手写 AOB 项最容易漏 L3, 这次就是被这个坑砸中
#   (MS 报 `mountreg: AOB 未命中 (L1 0 / L2 0 hits)`), 现改为脚本托管。
L3_HOOKS = {"Format", "Subtitle", "MountReg"}
L3_START = 48          # L3 起始长度; 不唯一则每次 +16 加长
L3_MAX = 128

# L2 规格: hook -> (窗口长度, [(起始下标, 长度), ...] 手工参考区间)
#   窗口长度是**权威**的; 区间列表现在只作人类复核参考 —— 实际通配集合由
#   operand_wild_set() 从反汇编推导 (见上)。两边的差集会在报告里标注:
#     [自动补齐手工漏圈: …]  手工圈漏掉的布局字段 (真 bug, Format 曾经如此)
#     [手工多圈(含操作码): …] 手工连操作码/SIB 一起通配了 (降低特异性)
#   注意 LangCur / LangTxt 只有 27 字节, 窗口取到函数尾 C3 为止(再往后是下一个函数)。
L2_SPEC = {
    #                 lea rbp,[rsp-110h]  [rsp+160h]  [rsp+0F0h]  [rsp+70h]  call rel32
    "DrawWide":   (48, [(7, 4), (14, 4), (25, 4), (35, 3), (44, 4)]),
    #                 lea rbp,[rsp-2F88h]  mov eax,chkstk  call __chkstk  cookie disp32
    "Format":     (48, [(10, 4), (15, 4), (20, 4), (30, 4)]),
    #                 cmp ecx,[rip+disp32]                 mov rax,[rip+disp32](窗口尾)
    "FontLookup": (48, [(18, 4), (47, 1)]),
    #                 cmp [rip+disp32]      mov r8,[rip+disp32]
    "TexObj":     (48, [(10, 4), (19, 4)]),
    #                 call rel32
    "SrvResolve": (48, [(20, 4)]),
    #                 mov rax,[rip+disp32]
    "LangCur":    (27, [(3, 4)]),
    #                 mov rax,[rip+disp32]
    "LangTxt":    (27, [(3, 4)]),
    #                 lea rbp,[r11-458h]  sub rsp,540h  [r11-48h]  [r11-68h]  cookie  [rbp-420h]
    "Subtitle":   (48, [(10, 4), (17, 4), (25, 1), (30, 1), (34, 4), (44, 4)]),
    # MountReg: [rsp+8] [rsp+10] 帧大小30h  lea rcx(全局表)  call(注册器)  mov r10d(计数)  lea rax(数组+4)
    #   实测本脚本推导结果与旧手写 mask 逐位一致(见 HOOKS 注释)。
    "MountReg":   (48, [(4, 1), (9, 1), (14, 1), (24, 4), (31, 4), (38, 4)]),
}


# ---------------- 从 dllmain.cpp 解析 C 数组 ----------------

def parse_c_array(src, name):
    m = re.search(r"\b" + re.escape(name) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", src, re.S)
    if not m:
        raise SystemExit("dllmain.cpp 里找不到数组 " + name)
    vals = []
    for tok in m.group(1).replace("\n", " ").split(","):
        tok = tok.strip()
        if not tok:
            continue
        vals.append(int(tok, 0) & 0xFF)
    return vals


# ---------------- 目标读取: PE 与裸段 ----------------

class Pe:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        e = struct.unpack_from("<I", self.d, 0x3C)[0]
        assert self.d[e:e + 4] == b"PE\0\0", "not a PE"
        coff = e + 4
        machine, nsec, tds, _, _, optsz, _ = struct.unpack_from("<HHIIIHH", self.d, coff)
        opt = coff + 20
        self.tds = tds
        self.ep = struct.unpack_from("<I", self.d, opt + 16)[0]
        self.sizeimage = struct.unpack_from("<I", self.d, opt + 56)[0]
        secoff = opt + optsz
        self.secs = []
        for i in range(nsec):
            o = secoff + i * 40
            name = self.d[o:o + 8].rstrip(b"\0").decode("latin1")
            vsz, va, rsz, ra = struct.unpack_from("<IIII", self.d, o + 8)
            ch = struct.unpack_from("<I", self.d, o + 36)[0]
            self.secs.append((name, va, vsz, ra, rsz, ch))

    def set_buffer(self, b):
        self.d = b

    def rva(self, va):
        return va - BASE

    def off_of(self, va):
        rva = self.rva(va)
        for name, sva, vsz, ra, rsz, ch in self.secs:
            if sva <= rva < sva + max(vsz, rsz):
                return ra + (rva - sva)
        return None

    def at_va(self, va, n):
        o = self.off_of(va)
        return None if o is None else self.d[o:o + n]

    def exec_sections(self):
        return [(n, va, vsz, ra, rsz) for (n, va, vsz, ra, rsz, ch) in self.secs
                if (ch & 0x20000000) or (ch & 0x00000020)]


class RawText:
    """DumpExecutableSections 落盘的裸段(.text)。接口与 Pe 对齐。"""

    def __init__(self, bin_path, map_path=None):
        # bytearray: repair_minhook 要回填被 MinHook 改写的入口字节
        self.d = bytearray(open(bin_path, "rb").read())
        if map_path is None:
            map_path = os.path.splitext(bin_path)[0] + ".map"
        txt = open(map_path, "r", encoding="utf-8", errors="replace").read()

        def g(pat, default=0):
            m = re.search(pat, txt)
            return int(m.group(1), 16) if m else default
        self.base = g(r"va_base=0x([0-9A-Fa-f]+)", BASE)
        self.tds = g(r"timestamp=([0-9A-Fa-f]+)")
        self.ep = g(r"ep=([0-9A-Fa-f]+)")
        self.sizeimage = g(r"sizeofimage=([0-9A-Fa-f]+)")
        self.secs = []
        for m in re.finditer(
                r"^(\S+)\s+va=0x([0-9A-Fa-f]+)\s+vsize=0x([0-9A-Fa-f]+)\s+"
                r"bin_off=0x([0-9A-Fa-f]+)\s+size=0x([0-9A-Fa-f]+)", txt, re.M):
            name = m.group(1)
            va = int(m.group(2), 16) - self.base       # 转成 RVA（与 Pe.at_va 同口径）
            vsz = int(m.group(3), 16)
            off = int(m.group(4), 16)
            size = int(m.group(5), 16)
            self.secs.append((name, va, vsz, off, size, 0x60000020))

    def set_buffer(self, b):
        self.d = b

    def rva(self, va):
        return va - BASE

    def off_of(self, va):
        rva = self.rva(va)
        for name, sva, vsz, ra, rsz, ch in self.secs:
            if sva <= rva < sva + max(vsz, rsz):
                return ra + (rva - sva)
        return None

    def at_va(self, va, n):
        o = self.off_of(va)
        return None if o is None else self.d[o:o + n]

    def exec_sections(self):
        return [(n, va, vsz, ra, rsz) for (n, va, vsz, ra, rsz, ch) in self.secs]


def masked_regex(sig, msk):
    """(sig, mask) -> bytes 正则; mask=0 的字节换成 '.' (任意字节)。

    纯 Python 逐字节扫 20MB .text 慢到不可用(4 版 × 8 hook × 多次试验要几十分钟),
    交给 re 引擎在 C 层跑。
    """
    parts = []
    for i, b in enumerate(sig):
        parts.append(b"." if (msk and not msk[i]) else re.escape(bytes([b])))
    return re.compile(b"".join(parts), re.DOTALL)


def aob_hits(pe, sig, msk):
    """复刻 dllmain.cpp 的 AobScan(): 可执行段内扫描, 返回全部命中 VA。"""
    rx = masked_regex(sig, msk)
    hits = []
    for (name, va, vsz, ra, rsz) in pe.exec_sections():
        size = vsz or rsz
        buf = pe.d[ra:ra + size]
        if len(buf) < len(sig):
            continue
        for m in rx.finditer(buf):
            hits.append(BASE + va + m.start())
    return hits


def disasm_hook(pe, va, n):
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    buf = pe.at_va(va, n)
    out = []
    for insn in md.disasm(buf, va):
        if insn.address - va >= n:
            break
        enc = insn.encoding
        out.append((insn.address - va, insn.size, insn.mnemonic, insn.op_str,
                    enc.disp_offset if enc.disp_size else -1,
                    enc.disp_size,
                    enc.imm_offset if enc.imm_size else -1,
                    enc.imm_size))
    return out, buf


def operand_wild_set(pe, va, ln):
    """L2 通配的**权威依据**：由反汇编推导出「编译期布局字段」占用的窗口字节下标。

    规则（原则 = 只通配随构建/帧布局漂移的字段, 其余一律保留）:

      通配:
        - disp  : 基址是栈/帧指针 (rsp/rbp/r11) —— 栈帧里的溢出槽/帧相对寻址
                  或基址是 rip —— 全局/静态变量地址
        - imm   : `sub/add rsp, imm`      (栈帧大小)
                  `mov reg, imm` 且下一条是 call (__chkstk 的帧大小)
                  `call/jmp rel`           (代码地址)
      保留:
        - 操作码 / ModRM / SIB / 前缀 / 寄存器编码
        - 语义立即数 (`cmp ecx,-1` / `bt ecx,0x18` / `shl rax,4` / `cmp dx,1`)
        - 结构体字段偏移 (`[rax+0x14]` / `[rdx+0x20]` / `[r9+rcx*8+8]`)
        - 条件短跳转 rel8 (`je`/`jge`/`jbe` …) —— 函数内布局, 与帧大小无关

    必要性: 手工圈区间极易漏。Format 窗口内 `mov [rbp+2F50h],rax` 的第二个栈帧
    相对 disp32 就曾被漏掉, 而它恰好随帧大小漂移。
    """
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_OP_MEM, CS_OP_REG

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    buf = pe.at_va(va, ln + 16)     # 多读 16B, 保证窗口内最后一条指令能完整解码
    if buf is None:
        raise SystemExit("VA 越界")
    insns = list(md.disasm(buf, va))

    ws = set()

    def mark(start, size):
        ws.update(range(start, min(start + size, ln)))

    for idx, insn in enumerate(insns):
        off = insn.address - va
        if off >= ln:
            break
        enc = insn.encoding

        ops = insn.operands
        mem = next((o for o in ops if o.type == CS_OP_MEM), None)

        # ---- 位移: 仅栈/帧基址 或 RIP 相对 ----
        if enc.disp_size and mem is not None:
            base = insn.reg_name(mem.mem.base) if mem.mem.base else ""
            if base in FRAME_BASE or base == "rip":
                mark(off + enc.disp_offset, enc.disp_size)

        # ---- 立即数 ----
        if enc.imm_size:
            take = False
            if insn.mnemonic in CALLJMP_MNEM:
                take = True                                    # 代码地址
            elif (insn.mnemonic in ("sub", "add") and ops
                  and ops[0].type == CS_OP_REG
                  and insn.reg_name(ops[0].reg) in FRAME_BASE):
                take = True                                    # 栈帧分配/释放
            else:
                # __chkstk 惯例: `mov eax, <帧大小>` 紧跟 `call __chkstk`。
                # 限定 imm 宽度必须是 4 字节 —— 否则 `mov dl,1; call ...` 这类
                # 「语义布尔实参」会被误通配。
                nxt = insns[idx + 1] if idx + 1 < len(insns) else None
                if (insn.mnemonic in ("mov", "movabs") and enc.imm_size == 4
                        and nxt is not None and nxt.mnemonic == "call"):
                    take = True
            if take:
                mark(off + enc.imm_offset, enc.imm_size)

    return ws


# ---------------- MS dump 干净化 ----------------

def repair_minhook(ms, sigs, w):
    """把 dump 里被 MinHook 改写的入口跳板还原成原始字节。

    dump 是 **hook 安装之后** 落盘的: 成功定位的每条 hook, 入口前 5 字节已被
    写成 `E9 rel32`(MinHook 的 detour 跳板)。前 5 字节之外的原始字节仍在原处,
    所以用该 hook 的 L1 特征码回填即可复原。

    回填时的校验是**免费的强证据**: 若"其后字节与 L1 逐字节相符"成立, 就同时
    证明了 (1) dump 的 VA 映射与 dllmain 的 GAME_BASE 口径一致;
              (2) 该 hook 的 L1 在这个构建上确实成立(与游戏日志互相印证)。

    返回修复的条数。未命中 MS_VA 的 hook(Format/Subtitle, 未安装)不做处理。
    """
    fixed = 0
    for (hook, sig_name, msk_name, _) in sigs:
        va = MS_VA.get(hook)
        if va is None:
            continue
        l1 = parse_c_array(_SRC, sig_name)
        l1m = parse_c_array(_SRC, msk_name) if msk_name else None
        o = ms.off_of(va)
        if o is None:
            w("  !! %s @0x%X 越界" % (hook, va))
            continue
        cur = ms.d[o:o + len(l1)]
        if cur[:1] != b"\xE9":
            w("  %-11s @0x%X 入口未被改写(未安装), 跳过" % (hook, va))
            continue
        ok = True
        for k in range(5, len(l1)):
            if (not l1m or l1m[k]) and cur[k] != l1[k]:
                ok = False
                break
        if not ok:
            w("  !! %-11s @0x%X 跳板之后字节与 L1 不符 —— dump 映射或 L1 有问题"
              % (hook, va))
            continue
        for k in range(5):
            ms.d[o + k] = l1[k]
        fixed += 1
        w("  %-11s @0x%X 还原前 5 字节 %s (其后字节与 L1 相符 ✓)"
          % (hook, va, " ".join("%02X" % b for b in l1[:5])))
    return fixed


_SRC = ""


# ---------------- main ----------------

def build_arrays(pes, ms, show_dis, w):
    arrays = []
    results = []
    targets = list(pes.items()) + [(MSBUILD, ms)]

    for hook, sig_name, msk_name, vamap in HOOKS:
        l1 = parse_c_array(_SRC, sig_name)
        l1m = parse_c_array(_SRC, msk_name) if msk_name else None

        ln, ranges = L2_SPEC[hook]
        vams = dict(vamap)
        vams[MSBUILD] = MS_VA[hook]
        ent = {k: pe.at_va(vams[k], ln) for k, pe in targets}
        if any(v is None for v in ent.values()):
            raise SystemExit("VA 越界 " + hook)
        base = ent["Steam"]

        # L2 通配集合 = 反汇编自动推导的「imm/disp/rel32 操作数字节」(权威)。
        #   [!] 只在**新构建族**三版上推导并取并集 —— MS 版前导换代, 结构不同,
        #       把它并进来会用错误的字节位置污染 mask(这是 Format 曾经的坑的镜像)。
        auto_by_build = {k: operand_wild_set(pes[k], vamap[k], ln) for k in NEWBUILDS}
        autos = set().union(*auto_by_build.values())
        inconsistent = len({frozenset(v) for v in auto_by_build.values()}) > 1

        # 手工规格 L2_SPEC 仅作人类复核参考, 用来暴露「漏圈 / 多圈」
        hand = set()
        for st, cnt in ranges:
            for i in range(st, st + cnt):
                if i < ln:
                    hand.add(i)
        missed = sorted(autos - hand)     # 自动发现、手工漏掉的布局字段 (曾经的 bug 来源)
        over = sorted(hand - autos)       # 手工连操作码一起通配了 (降低特异性)

        msk = [0 if i in autos else 1 for i in range(ln)]
        sig = [0 if msk[i] == 0 else base[i] for i in range(ln)]

        # ---- 命中统计: L1 / L2 在四个构建上 ----
        l1h = {k: len(aob_hits(pe, l1, l1m)) for k, pe in targets}
        l2h = {k: len(aob_hits(pe, sig, msk)) for k, pe in targets}

        # ---- L3: 换代构建的精确特征码(纯精确, 无 mask) ----
        l3h = {}
        l3_sig = None
        l3_note = ""
        if hook in L3_HOOKS:
            mo = ms.off_of(MS_VA[hook])
            n = L3_START
            while n <= L3_MAX:
                cand = bytes(ms.d[mo:mo + n])
                h = {k: len(aob_hits(pe, cand, None)) for k, pe in targets}
                if h[MSBUILD] == 1 and all(v <= 1 for k, v in h.items() if k != MSBUILD):
                    l3_sig = cand
                    l3h = h
                    break
                n += 16
            if l3_sig is None:
                raise SystemExit("L3 生成失败(不唯一) " + hook)
            others = [k for k in NEWBUILDS if l3h[k]]
            l3_note = "  L3=%dB" % len(l3_sig)
            if others:
                l3_note += " (在新构建族上也有 %s 命中, 优先级低于 L1/L2, 无碍)" % ",".join(others)

        # ---- 反向校验 .inc 与规格一致 ----
        drift = ""
        if os.path.exists(INC_PATH):
            inc_src = open(INC_PATH, "r", encoding="utf-8").read()
            try:
                if parse_c_array(inc_src, "R2_" + hook.upper()) != sig or \
                   parse_c_array(inc_src, "RM_" + hook.upper()) != msk:
                    drift = "  !! DRIFT: R2_/RM_%s 与规格不一致" % hook.upper()
                elif hook in L3_HOOKS:
                    if parse_c_array(inc_src, "R3_" + hook.upper()) != list(l3_sig):
                        drift = "  !! DRIFT: R3_%s 与规格不一致" % hook.upper()
            except SystemExit:
                drift = "  !! aob_l2_arrays.inc 缺少数组"
        else:
            drift = "  (NEW: aob_l2_arrays.inc 首次生成)"
        note = ""
        if missed:
            note += "  [自动补齐手工漏圈: %s]" % ",".join(str(i) for i in missed)
        if over:
            note += "  [手工多圈(含操作码): %s]" % ",".join(str(i) for i in over)
        if inconsistent:
            note += "  !! 三版反汇编边界不一致, 已取并集"
        results.append((hook, ln, msk, l1h, l2h, l3h, drift + note + l3_note))

        arrays.append("// L2 %s: %d 字节 / %d 通配 (L1 落空时二次尝试)" %
                      (hook, ln, msk.count(0)))
        arrays.append("static const uint8_t R2_%s[%d] = {" % (hook.upper(), ln))
        for r in range(0, ln, 16):
            arrays.append("    " + ",".join("0x%02X" % sig[i] for i in range(r, min(r + 16, ln))) + ",")
        arrays[-1] = arrays[-1][:-1] + " };"
        arrays.append("static const uint8_t RM_%s[%d] = {" % (hook.upper(), ln))
        for r in range(0, ln, 16):
            arrays.append("    " + ",".join(str(msk[i]) for i in range(r, min(r + 16, ln))) + ",")
        arrays[-1] = arrays[-1][:-1] + " };"
        arrays.append("")

        if l3_sig is not None:
            n3 = len(l3_sig)
            arrays.append("// L3 %s: %d 字节 纯精确 (换代构建: 前导形态不同, mask 救不了)"
                          % (hook, n3))
            arrays.append("static const uint8_t R3_%s[%d] = {" % (hook.upper(), n3))
            for r in range(0, n3, 16):
                arrays.append("    " + ",".join("0x%02X" % b for b in l3_sig[r:r + 16]) + ",")
            arrays[-1] = arrays[-1][:-1] + " };"
            arrays.append("")

        if show_dis:
            for k in NEWBUILDS + (MSBUILD,):
                w("---- %s disasm @ %s 0x%X ----" % (hook, k, vams[k]))
                ins, buf = disasm_hook(ms if k == MSBUILD else pes[k], vams[k], ln)
                for (off, size, mn, ops, do, ds, io_, isz) in ins:
                    w("  %02X: %-24s %-38s disp=%s/%d imm=%s/%d" %
                      (off, " ".join("%02X" % b for b in buf[off:off + size]),
                       mn + " " + ops, do, ds, io_, isz))
                w("")
    return arrays, results


def main():
    global _SRC
    game = DEFAULT_GAME
    ms_dump = DEFAULT_MS_DUMP
    show_dis = "--dis" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        game = args[0]
    if "--ms-dump" in sys.argv:
        ms_dump = sys.argv[sys.argv.index("--ms-dump") + 1]
    _SRC = open(DLLMAIN, "r", encoding="utf-8").read()

    pes = {}
    for k, fn in EXES.items():
        p = os.path.join(game, fn)
        if os.path.exists(p):
            pes[k] = Pe(p)
        else:
            print("!! 缺少 " + p)
    if len(pes) < 3:
        raise SystemExit("参考构建不全")

    ms = None
    if os.path.exists(ms_dump):
        ms = RawText(ms_dump)
    else:
        print("!! 缺少 MS dump: " + ms_dump)

    buf = io.StringIO()

    def w(s):
        buf.write(s + "\n")

    w("=" * 92)
    w("跨构建多层特征码生成报告   (%s)" % os.path.basename(__file__))
    w("=" * 92)
    for k, pe in pes.items():
        w("%-8s TDS=0x%08X EP=0x%08X SizeOfImage=0x%08X" % (k, pe.tds, pe.ep, pe.sizeimage))
    if ms:
        w("%-8s TDS=0x%08X EP=0x%08X SizeOfImage=0x%08X   裸段 %d 字节 (%s)"
          % (MSBUILD, ms.tds, ms.ep, ms.sizeimage, len(ms.d), os.path.basename(ms_dump)))
        w("")
        w("---- 还原 MinHook 跳板（dump 在 hook 安装之后落盘, 入口前 5B 被写成 E9 rel32）----")
        n = repair_minhook(ms, HOOKS, w)
        w("  共还原 %d 条；此后 MS .text 即为干净映像" % n)
    w("")

    arrays, results = build_arrays(pes, ms, show_dis, w)

    ok = True
    w("=" * 100)
    w("%-11s %-15s %-15s %-13s %-5s %s"
      % ("hook", "L1 hits(S/G/E/MS)", "L2 hits(S/G/E/MS)", "L3 hits(MS)", "wild", "结论"))
    w("-" * 100)
    for hook, ln, msk, l1h, l2h, l3h, drift in results:
        g4 = lambda d: "%d/%d/%d/%d" % (d["Steam"], d["GOG"], d["EPIC"], d[MSBUILD])
        l3txt = ("%d" % l3h.get(MSBUILD, -1)) if hook in L3_HOOKS else "-"
        # 判据:
        #   新构建族  : L1 恰好 1 命中, 且 L2 也恰好 1 命中(L2 是族内消歧用的备份)
        #   MS 构建   : 换代 hook(Format/Subtitle/MountReg) L1 必须落空且 L3 恰好 1 命中;
        #               其余 L1 必须恰好 1 命中
        ok_new = all(l1h[k] == 1 and l2h[k] == 1 for k in NEWBUILDS)
        if hook in L3_HOOKS:
            ok_ms = (l1h[MSBUILD] == 0 and l3h.get(MSBUILD, 0) == 1)
        else:
            ok_ms = (l1h[MSBUILD] == 1)
        good = ok_new and ok_ms and "DRIFT" not in drift
        ok = ok and good
        w("%-11s %-15s %-15s %-13s %-5d %s"
          % (hook, g4(l1h), g4(l2h), l3txt, msk.count(0),
             ("OK" if good else "!! FAIL") + drift))
    w("=" * 100)
    w("")
    w("判据: 新构建族(Steam/GOG/EPIC) 要求 L1 与 L2 各恰好 1 命中;")
    w("      MS Store 要求 —— 换代 hook(Format/Subtitle/MountReg) L1 落空 + L3 恰好 1 命中,")
    w("      其余 L1 恰好 1 命中。")

    txt = buf.getvalue()
    with open(os.path.join(HERE, "aob_relaxed_report.txt"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(txt)

    head = (
        "// =====================================================================\n"
        "//  aob_l2_arrays.inc — 跨构建多层特征码（本文件由工具生成, 请勿手改）\n"
        "//  ---------------------------------------------------------------------\n"
        "//  生成: python Tools/gen_aob_relaxed.py\n"
        "//  校验: 四构建 (sr_hv.exe / sr_hv_gog.exe / sr_hv_epic.exe / MS Store 裸段)\n"
        "//        每个 hook 都必须能唯一命中其对应层, 否则生成器拒绝出表。\n"
        "//\n"
        "//  L2  R2_*/RM_*  —— L1 落空时的第一次放宽。通配规则由反汇编机械推导:\n"
        "//        基址 rsp/rbp/r11 的 disp(栈帧溢出槽) / 基址 rip 的 disp(全局地址)\n"
        "//        / `sub|add rsp, imm`(帧大小) / `mov reg, imm32` + 紧跟 call\n"
        "//        (__chkstk 帧大小) / `call|jmp rel`(代码地址)。\n"
        "//        保留: 操作码/ModRM/SIB/寄存器编码、语义立即数(cmp/bt/shl)、\n"
        "//              结构体字段偏移、函数内条件短跳转。\n"
        "//  教训: 手工圈通配区间会漏。Format 窗口内第二个栈帧相对 disp32\n"
        "//        `mov [rbp+2F50h],rax` 曾被漏掉, 而它恰随帧大小漂移。\n"
        "//\n"
        "//  L3  R3_*  —— **换代构建**的精确特征码, 纯精确(无 mask)。\n"
        "//        Microsoft Store 版 (sriv.exe, TDS=5E58CEF8, 2020-02) 的\n"
        "//        Format / Subtitle 换了一代 MSVC, 前导形态整个不同:\n"
        "//          新: push rbp/si/di/r15; lea rbp,[rsp-2F88h]; mov eax,3088h;\n"
        "//              call __chkstk; sub rsp,rax\n"
        "//          老: mov [rsp+20h],r9; mov [rsp+18h],r8; mov [rsp+8],rcx  ← 参数\n"
        "//              push rbp/rbx/rsi/r14; lea rbp,[rsp-2F58h]            home 区\n"
        "//              mov eax,3058h; call __chkstk; sub rsp,rax             先落地\n"
        "//        指令长度都不同, 任何字节 mask 都救不了 —— 只能构建族各一套。\n"
        "//        同一构建内 RIP 相对位移与 rel32 不随 ASLR 变化, 故无需 mask。\n"
        "//  详见 Tools/aob_relaxed_report.txt。\n"
        "// =====================================================================\n\n")
    with open(INC_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "\n".join(arrays) + "\n")

    if not ok:
        sys.stderr.write("校验失败, 见 %s\n" % os.path.join(HERE, "aob_relaxed_report.txt"))
        return 1
    sys.stdout.write("OK -> aob_relaxed_report.txt + aob_l2_arrays.inc\n")
    return 0


if __name__ == "__main__":
    # 本机 PowerShell 不回收 stdout, 且 cp936 控制台编不了 ✗ 等字符 -> 全量落盘
    b = io.StringIO()
    old = sys.stdout
    sys.stdout = b
    try:
        rc = main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
        b.write("SystemExit: %s\n" % e)
    except BaseException:
        rc = 1
        b.write(traceback.format_exc())
    finally:
        sys.stdout = old
    with open(os.path.join(HERE, "gen_aob_relaxed.out"), "w", encoding="utf-8") as f:
        f.write(b.getvalue())
    sys.exit(rc)
