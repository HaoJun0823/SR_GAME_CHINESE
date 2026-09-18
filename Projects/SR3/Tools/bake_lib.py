# -*- coding: utf-8 -*-
"""
SRTT3 字库烘焙核心库 (v7)
- 复刻游侠几何: L=72, cell=72, 4096x4096, 56x56=3136 槽
- 主动规避字符集禁区 A (0x7F-0x9F idx95-127) 与禁区 B (0x145-0x168 idx293-328)
- DXT5 alpha 向量化编码
"""
import struct
import numpy as np
import freetype

FONT_SRC = r"G:/Archives/Fonts/02_SourceHanSans-VF/Variable/TTF/HW/SourceHanSansHWSC-VF.ttf"

# ===== 几何常量 =====
# ✅ 2026-09-04 真因已 IDA 实证 (修正旧"GPU 池 ≈3448 槽"推断):
#    崩溃源于 "font cpu" 内存池 64KB 上限 —— sub_140859F20 按 vf3 文件总字节数
#    从该池线性分配 (sub_140853430), 3 字体合计超 64KB 即得 NULL -> 崩溃。
#    解决: scripts\fontpool_patch.asi 把 .rdata 常量 qword_140E4FEB0 (RVA 0xE4FEB0)
#    从 0x10000 扩到 0x80000 (512KB)。
#    gpu 池 (qword_140E4FEB8, 17MB) 存图集纹理: 4096x4096 DXT5 = 16.78MB < 17MB 可容纳。
# 故槽位数不受 3448 约束; 56x56=3136 是游侠复刻几何, count=3061~3136 均安全。
# 汉字 2807 个 (覆盖游戏 UI 实际用字足矣)。
ATLAS_W = 4096
ATLAS_H = 4096
CELL    = 72          # uv 网格步进 (游侠实测值)
COLS    = ATLAS_W // CELL   # 56
ROWS    = ATLAS_H // CELL   # 56
N_SLOT  = COLS * ROWS       # 3136
L       = 72          # 行高 (vf3 +0x16), 必须 <= CELL
P       = 56          # 字形渲染 em size -> 实际位图约 50px
BASE    = 32          # baseChar

# ✅ vf3 体积预算 (font cpu 池扩容后 = 512KB, 需容纳全部 3 字体):
# font_body_v7 + font_header_pc_v7 + ug-debug ≈ 169KB + 8KB, 余量充足。
# 若未来单 vf3 超限: 减 count 或等比缩小度量数组, 切勿再撞池上限。
VF3_BUDGET_CPU_POOL = 0x80000 - 0x8000   # 512KB 池 - 32KB 安全余量 (3 字体共用)

# ===== 槽位规划 (idx = 槽码 - 0x20) =====
IDX_ASCII_LO, IDX_ASCII_HI = 0, 94        # 槽码 0x20-0x7E
IDX_ZONEA_LO, IDX_ZONEA_HI = 95, 127      # 禁区A 0x7F-0x9F  -> 留空
IDX_SYM_LO,   IDX_SYM_HI   = 128, 292     # 槽码 0xA0-0x144
IDX_ZONEB_LO, IDX_ZONEB_HI = 293, 328     # 禁区B 0x145-0x168 -> 留空
IDX_HAN_LO,   IDX_HAN_HI   = 329, N_SLOT-1  # 槽码 0x169-0x103F

N_ASCII = IDX_ASCII_HI - IDX_ASCII_LO + 1   # 95
N_SYM   = IDX_SYM_HI   - IDX_SYM_LO   + 1   # 165
N_HAN   = IDX_HAN_HI   - IDX_HAN_LO   + 1   # 3767


def build_charset(extra_needed=None):
    """生成字符集: ASCII(95) + 符号/中文标点(165) + 汉字(GB2312 一级全 3755)。
    extra_needed: 译文实际需要的汉字集合 —— 优先前插, 确保被拼音截断时仍保留常用字。
    """
    # --- ASCII 可打印 0x20-0x7E ---
    ascii_chars = [chr(c) for c in range(0x20, 0x7F)]

    # --- GB2312 一级汉字 (0xB0A1-0xD7F9) 全部 3755 字 ---
    # 注意: GB2312 一级是按拼音排序的, 绝不可截断取前 N 个 ——
    # 那样会整段丢掉 w/x/y/z 拼音的常用字(中/新/无/用/战/选...)。
    han = []
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                han.append(bytes([hi, lo]).decode('gb2312'))
            except Exception:
                pass
    # 去重保序
    seen, han_u = set(), []
    for c in han:
        if c not in seen:
            seen.add(c)
            han_u.append(c)
    # 优先纳入实际需要的汉字: 前插到最前, 即便原拼音序靠后被截断也不丢。
    if extra_needed:
        extra = [c for c in extra_needed
                 if 0x4E00 <= ord(c) <= 0x9FFF and c not in ascii_chars]
        ext_set = set(extra)
        han_u = extra + [c for c in han_u if c not in ext_set]
    # 汉字全量纳入 (3136 槽 - 95 ASCII - 165 符号 = 2876 可用, 实际 GB2312 一级 3755
    # 仍需截断到 N_HAN; cpu 池已由 fontpool_patch.asi 扩容, 瓶颈不再在池而在槽位几何)
    han_u = han_u[:N_HAN]
    assert len(han_u) == N_HAN, \
        f"GB2312 一级不足以截取 {N_HAN} 字 (实际 {len(han_u)})"

    # --- 中文标点 + 常用符号 (补齐 165) ---
    sym = list("　、。〈〉《》「」『』【】〔〕！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？"
               "＠［＼］＾＿｀｛｜｝～￠￡￥‰§℃°±×÷∞←↑→↓◆●■▲▼★☆※"
               "！？，．：；…—‘’“”（）《》【】〔〕～"
               "①②③④⑤⑥⑦⑧⑨⑩"
               "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞß"
               "àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
               "ĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚě"
               "ŒœŠšŸŽžƒ")
    seen_s, sym_u = set(), []
    for c in sym:
        if c not in seen_s and len(sym_u) < N_SYM:
            seen_s.add(c)
            sym_u.append(c)
    # 不足用 GB2312 二级字补齐
    if len(sym_u) < N_SYM:
        extra = []
        for hi in range(0xD8, 0xF8):
            for lo in range(0xA1, 0xFF):
                try:
                    extra.append(bytes([hi, lo]).decode('gb2312'))
                except Exception:
                    pass
        for c in extra:
            if len(sym_u) >= N_SYM:
                break
            if c not in seen_s and c not in seen:
                seen_s.add(c)
                sym_u.append(c)
    return ascii_chars, sym_u, han_u


def render_glyphs(chars, px):
    """FreeType 渲染, 返回 {ch: (h,w) uint8 alpha}"""
    face = freetype.Face(FONT_SRC)
    face.set_pixel_sizes(0, px)
    out = {}
    for ch in chars:
        try:
            face.load_char(ch, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL)
        except Exception:
            out[ch] = np.zeros((0, 0), np.uint8)
            continue
        bmp = face.glyph.bitmap
        w, h = bmp.width, bmp.rows
        raw = bmp.buffer
        if not isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw)
        if raw and w and h:
            out[ch] = np.frombuffer(raw, dtype=np.uint8).reshape(h, w).copy()
        else:
            out[ch] = np.zeros((0, 0), np.uint8)
    return out


def encode_dxt5_alpha_vector(alpha):
    """
    alpha: (H, W) uint8, H/W 均为 4 的倍数
    返回 DXT5 alpha 平面字节流 (每块 8B, 块行优先)
    """
    H, W = alpha.shape
    assert H % 4 == 0 and W % 4 == 0
    bh, bw = H // 4, W // 4
    # (bh, bw, 4, 4) -> (N, 16)
    blocks = alpha.reshape(bh, 4, bw, 4).transpose(0, 2, 1, 3).reshape(-1, 16)
    N = blocks.shape[0]

    a0 = blocks.max(axis=1).astype(np.int32)
    a1 = blocks.min(axis=1).astype(np.int32)

    # a0 > a1 分支: 8 级插值; a0 == a1: 全用索引0
    gt = a0 > a1
    # 6 段插值权重
    num = np.stack([
        6 * a0 + 1 * a1,
        5 * a0 + 2 * a1,
        4 * a0 + 3 * a1,
        3 * a0 + 4 * a1,
        2 * a0 + 5 * a1,
        1 * a0 + 6 * a1,
    ], axis=1) // 7                                   # (N,6)
    ramp = np.concatenate([
        a0[:, None], a1[:, None], num.astype(np.int32)
    ], axis=1).astype(np.int32)                        # (N,8)

    bl = blocks.astype(np.int32)[:, :, None]           # (N,16,1)
    diff = np.abs(bl - ramp[:, None, :])               # (N,16,8)
    idx = diff.argmin(axis=2).astype(np.uint64)        # (N,16)

    bits = np.zeros(N, dtype=np.uint64)
    for i in range(16):
        bits |= idx[:, i] << np.uint64(3 * i)

    # 拆 6 字节 little-endian
    b = np.empty((N, 8), dtype=np.uint8)
    b[:, 0] = np.where(gt, a0, a0).astype(np.uint8)
    b[:, 1] = np.where(gt, a1, a0).astype(np.uint8)
    for k in range(6):
        b[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(0xFF)).astype(np.uint8)
    return b.tobytes()


def make_color_plane(bw, bh):
    """DXT5 color 平面: 全白 (引擎只取 alpha); 每块 8B"""
    # 标准全白块: c0=0xFF,0xFF c1=0xFF,0xFF, 索引全 0
    blk = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00])
    return blk * (bw * bh)


def interleave(alpha_bytes, color_bytes, n_blocks):
    """交错 alpha(8B) + color(8B) -> DXT5 流 (每块 16B)"""
    a = np.frombuffer(alpha_bytes, dtype=np.uint8).reshape(n_blocks, 8)
    c = np.frombuffer(color_bytes, dtype=np.uint8).reshape(n_blocks, 8)
    out = np.empty((n_blocks, 16), dtype=np.uint8)
    out[:, 0:8] = a
    out[:, 8:16] = c
    return out.tobytes()
