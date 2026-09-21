# -*- coding: utf-8 -*-
"""
SRTT 字库小样验证 - 阶段1: FreeType 渲染汉字 + 图集烘字形 + vf3 追加表项
==========================================================================
路线(已破译): 文本 u16 = 槽码; ASCII 明文; 追加槽码 = 0x170+t (368+t),
             字体 vf3 表 idx = 槽码-32, 官方 336 项(idx 0..335, ch 32..367) 原样,
             追加项 idx = 336+t (ch=368+t)。字形烘进 font_body_nobdr 图集空区。
小样: 6 汉字 "测试中文显示" -> 槽 368..373 -> vf3 idx 336..341
     cell: 字形 40px, cell 44px, 图集 stride 48px, 起点 (12,744) 空区
产物:
  fonts_sample/zh_cells.png   (目视字形)
  patch 后 font_body_nobdr.gvbm_pc
  新 font_body.vf3_pc (追加 6 项)
"""
import sys, os, struct
import numpy as np
import freetype

M = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\misc"
OUT = r"C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04\fonts_sample"
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, r"C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04")
import volition_tex as vt

GLYPHS = "测试中文显示"
FONT_SRC = r"C:\Windows\Fonts\msyh.ttc"
GLYPH_H = 40          # 字形像素高
CELL = 44             # cell 尺寸 (vf3 asc/w 值)
STRIDE = 48           # 图集内每字步长 (含 pad)
X0, Y0 = 12, 744      # 图集写入起点 (y>=740 官方空区)


def render_glyphs(text, pixel_h):
    """FreeType 渲染 -> {ch: (alpha_ndarray h,w, 偏移x,y)}"""
    face = freetype.Face(FONT_SRC)
    face.set_pixel_sizes(0, pixel_h)
    out = {}
    for ch in text:
        face.load_char(ch, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL)
        bmp = face.glyph.bitmap
        w, h = bmp.width, bmp.rows
        raw = bmp.buffer
        if not isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw)          # 新版 freetype-py 返回 list
        buf = np.frombuffer(raw, dtype=np.uint8).reshape(h, w) if raw else np.zeros((h, w), np.uint8)
        out[ch] = (buf, face.glyph.bitmap_left, face.glyph.bitmap_top)
    return out


def pack_cell(alpha, ah, aw, left, top):
    """字形位图居中放入 CELL×CELL 的 alpha, 返回 (u8 CELL,CELL) 含 pad"""
    cell = np.zeros((CELL, CELL), np.uint8)
    # 字形应在 cell 内水平居中; 垂直按字形 bbox 顶部 top 对齐行基线近似: 简单垂直居中
    x = (CELL - aw) // 2 - min(0, left)
    y = (CELL - ah) // 2
    # 裁剪边界
    sx = max(0, -x); sy = max(0, -y)
    dx, dy = max(0, x), max(0, y)
    ex = min(aw, CELL - dx); ey = min(ah, CELL - dy)
    if ex > sx and ey > sy:
        cell[dy:dy + ey - sy, dx:dx + ex - sx] = alpha[sy:ey, sx:ex]
    return cell


def encode_dxt5_block(rgba16):
    """16 RGBA 像素 -> 16B DXT5。RGB 全白固定(c0=0xFFFF); alpha 规范 6 级编码。"""
    out = bytearray(16)
    a = [p[3] for p in rgba16]
    # a0=255, a1=0 (a0>a1): 6级表 idx0=255 idx1=0 idx2=204 idx3=153 idx4=102 idx5=51 idx6=0 idx7=255
    order = [255, 0, 204, 153, 102, 51, 0, 255]
    out[0] = 255; out[1] = 0
    bits = 0
    for i in range(16):
        av = a[i]
        best, bi = 999, 0
        for k, lv in enumerate(order):
            d = abs(av - lv)
            if d < best:
                best, bi = d, k
        bits |= bi << (3 * i)
    out[2] = bits & 0xFF
    out[3] = (bits >> 8) & 0xFF
    out[4] = (bits >> 16) & 0xFF
    out[5] = (bits >> 24) & 0xFF
    # RGB 全白: c0=0xFFFF (colidx 全 0 -> 全取 c0 白色)
    out[6] = 0xFF; out[7] = 0xFF
    out[8] = 0xFF; out[9] = 0xFF
    # colidx 32bit 全 0
    out[10] = 0; out[11] = 0; out[12] = 0; out[13] = 0
    return bytes(out)


def decode_dxt5_block(blk):
    """16B DXT5 -> 16 RGBA 像素 (RGB 由 col idx 解出; alpha 6/8级)"""
    a0, a1 = blk[0], blk[1]
    aidx = int.from_bytes(blk[2:8], 'little')
    c0 = blk[8] | (blk[9] << 8); c1 = blk[10] | (blk[11] << 8)
    cidx = int.from_bytes(blk[12:16], 'little')
    # 6级 alpha
    al = [a0, a1, (6*a0+1*a1)//7, (5*a0+2*a1)//7, (4*a0+3*a1)//7, (3*a0+4*a1)//7,
          (2*a0+5*a1)//7, (1*a0+6*a1)//7]
    if a0 > a1:
        al[6] = 0; al[7] = 255
    def col565(v):
        r = (v >> 11) & 0x1F; g = (v >> 5) & 0x3F; b = v & 0x1F
        return (r << 3) | (r >> 2), (g << 2) | (g >> 1), (b << 3) | (b >> 2)
    cs = [col565(c0), col565(c1),
          ((2*col565(c0)[0]+col565(c1)[0])//3 if False else 0,)*3]
    # 简化: c0,c1 外插颜色
    r0, g0, b0 = col565(c0); r1, g1, b1 = col565(c1)
    if c0 > c1:
        ctab = [(r0, g0, b0), (r1, g1, b1),
                ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3),
                ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3)]
    else:
        ctab = [(r0, g0, b0), (r1, g1, b1),
                ((r0+r1)//2, (g0+g1)//2, (b0+b1)//2), (0, 0, 0)]
    px = []
    for i in range(16):
        a = al[(aidx >> (3*i)) & 7]
        ci = (cidx >> (2*i)) & 3
        px.append((ctab[ci][0], ctab[ci][1], ctab[ci][2], a))
    return px


def main():
    print("渲染字形:", GLYPHS)
    glyphs = render_glyphs(GLYPHS, GLYPH_H)
    # 拼一张目视 png
    canvas = np.zeros((CELL, CELL * len(GLYPHS)), np.uint8)
    cells = []
    for t, ch in enumerate(GLYPHS):
        alpha, left, top = glyphs[ch]
        cell = pack_cell(alpha, alpha.shape[0], alpha.shape[1], left, top)
        cells.append(cell)
        canvas[:, t*CELL:(t+1)*CELL] = cell
    rgba_v = np.dstack([np.full_like(canvas, 255), np.full_like(canvas, 255), np.full_like(canvas, 255), canvas])
    vt.write_png(os.path.join(OUT, "zh_cells.png"), rgba_v.tobytes(), CELL * len(GLYPHS), CELL)
    print("目视字形 ->", os.path.join(OUT, "zh_cells.png"))

    # 图集块级 patch: 目标区域 x X0..X0+6*STRIDE, y Y0..Y0+CELL
    gv = os.path.join(M, "font_body_nobdr.gvbm_pc")
    data = bytearray(open(gv, 'rb').read())
    W = 2048
    patched = 0
    for t, ch in enumerate(GLYPHS):
        cx = X0 + t * STRIDE
        cell = cells[t]
        for by in range(0, CELL, 4):
            for bx in range(0, CELL, 4):
                gx, gy = cx + bx, Y0 + by
                blk_off = (gy // 4) * (W // 4) * 16 + (gx // 4) * 16
                blk = data[blk_off:blk_off + 16]
                px = decode_dxt5_block(bytes(blk))
                rgba = []
                for j in range(16):
                    py = gy + j // 4; pxx = gx + j % 4
                    a = int(cell[py - Y0, pxx - cx]) if (0 <= py - Y0 < CELL and 0 <= pxx - cx < CELL) else px[j][3]
                    rgba.append((255, 255, 255, a))
                nb = encode_dxt5_block(rgba)
                data[blk_off:blk_off + 16] = nb
                patched += 1
    open(gv, 'wb').write(bytes(data))
    print("图集 patch 块数:", patched)

    # 验证 patch: 解码看字形
    hdr, recs = vt.parse_cvbm(os.path.join(M, "font_body_nobdr.cvbm_pc"))
    rgba, w, h = vt.render_mip(bytes(data), recs[0], 0)
    arr = np.frombuffer(rgba, dtype=np.uint8).reshape(h, w, 4)
    sub = arr[Y0:Y0 + CELL, X0:X0 + 6 * STRIDE]
    print("patch 区字形像素:", int((sub[:, :, 3] > 40).sum()))
    vt.write_png(os.path.join(OUT, "patched_region.png"), sub.tobytes(), sub.shape[1], sub.shape[0])

    # vf3 追加: 官方 336 -> 342
    vf = os.path.join(M, "font_body.vf3_pc")
    d = bytearray(open(vf, 'rb').read())
    count = struct.unpack_from('<I', d, 8)[0]
    assert count == 336, count
    # 追加区域在文件尾部? 官方文件 度量(0xD0,16*336=5376 -> 0x15D0) + x表(0x15D0,1344 -> 0x1B10) + y表(0x1B10,1344 -> 0x2050)
    # 新布局: 头不动(336), 但追加 6 项 -> 需把 x/y 表扩到 342, 重排:
    # 度量区 0xD0..0x15D0 保留; 追加 6 度量; x表 342*4; y表 342*4
    met_off = 0xD0
    new_count = count + len(GLYPHS)
    x_off = met_off + 16 * new_count
    y_off = x_off + 4 * new_count
    new_size = y_off + 4 * new_count
    nd = bytearray(new_size)
    keep = met_off + 16 * count          # 头 + 官方度量字节数
    nd[:keep] = d[:keep]                 # 等长复制, 不会 resize
    # 复制官方 x/y 表到新位置
    old_x = 0x15D0; old_y = 0x1B10
    for i in range(count):
        struct.pack_into('<I', nd, x_off + 4 * i, struct.unpack_from('<I', d, old_x + 4 * i)[0])
        struct.pack_into('<I', nd, y_off + 4 * i, struct.unpack_from('<I', d, old_y + 4 * i)[0])
    # 追加 6 度量 + x/y
    for t in range(len(GLYPHS)):
        ci = count + t
        # 度量 {cell, cell, 0, 0xFFFF}
        struct.pack_into('<II', nd, met_off + 16 * ci, CELL, CELL)
        struct.pack_into('<I', nd, met_off + 16 * ci + 8, 0)
        struct.pack_into('<h', nd, met_off + 16 * ci + 12, -1)
        struct.pack_into('<I', nd, x_off + 4 * ci, X0 + t * STRIDE)
        struct.pack_into('<I', nd, y_off + 4 * ci, Y0)
    struct.pack_into('<I', nd, 8, new_count)
    open(vf, 'wb').write(bytes(nd))
    print("vf3 新文件大小:", new_size, "count:", new_count)
    # 验证追加项
    for t in range(6):
        i = 336 + t
        a, b = struct.unpack_from('<II', nd, met_off + 16 * i)[:2]
        x = struct.unpack_from('<I', nd, x_off + 4 * i)[0]
        y = struct.unpack_from('<I', nd, y_off + 4 * i)[0]
        print(f"  idx{i} ch={368+t} cell={a} x={x} y={y}")
    print("小样文件已就绪: font_body_nobdr.gvbm_pc / font_body.vf3_pc (unpack/misc 内)")


if __name__ == "__main__":
    main()
