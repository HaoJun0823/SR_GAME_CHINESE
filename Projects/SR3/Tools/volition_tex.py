# -*- coding: utf-8 -*-
"""SR3 Remastered 纹理容器 (cvbm_pc/gvbm_pc) + 字体 vf3 解析工具
cvbm_pc = GEKV 容器 (scanti 公开格式)
gvbm_pc = 去掉 128B 头的裸 DDS 数据流 (DXT1/DXT5)
vf3_pc  = VFNT v4 字体度量表 (记录顺序 = charlist 码位顺序)
"""
import struct, zlib, os, sys

FMT_DXT1 = 400
FMT_DXT3 = 401
FMT_DXT5 = 402


def parse_cvbm(path):
    """解析 GEKV 容器, 返回 (header, [record,...])"""
    c = open(path, 'rb').read()
    magic, ver, sizec, sizeg, ntex, ndup, u3 = struct.unpack_from('<4sIIIIhH', c, 0)
    assert magic == b'GEKV', f'bad magic {magic}'
    off = 24
    recs = []
    for i in range(ntex):
        foff, w, h, tenum = struct.unpack_from('<QHHI', c, off); off += 16
        flags = c[off:off+6]; off += 6
        hasalpha = c[off]; off += 1
        flags2 = c[off:off+12]; off += 12
        nmm, siz = struct.unpack_from('<BI', c, off); off += 5
        unk3 = c[off:off+32]; off += 32
        end = c.index(b'\x00', off)
        name = c[off:end].decode('ascii', 'replace'); off = end + 1
        fmt = tenum & 0xFFFF if tenum > 0xFFFF else tenum
        recs.append(dict(off=foff, w=w, h=h, fmt=fmt, fmt_raw=tenum,
                         mips=nmm, size=siz, alpha=hasalpha, name=name,
                         record_off_in_container=24 + i*72))
    return dict(version=ver, ntex=ntex), recs


def _c565(v):
    r = (v >> 11) & 0x1F; g = (v >> 5) & 0x3F; b = v & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _dxt_palette(c0u, c1u):
    c0, c1 = _c565(c0u), _c565(c1u)
    pal = [c0, c1]
    if c0u > c1u:
        pal += [tuple((2*a + b) // 3 for a, b in zip(c0, c1)),
                tuple((a + 2*b) // 3 for a, b in zip(c0, c1))]
    else:
        pal += [tuple((a + b) // 2 for a, b in zip(c0, c1)), (0, 0, 0)]
    return pal, c0u > c1u


def _alpha_table(a0, a1):
    t = [a0, a1]
    if a0 > a1:
        for i in range(1, 7):
            t.append(((6 - i) * a0 + i * a1) // 7)
    else:
        for i in range(1, 5):
            t.append(((4 - i) * a0 + i * a1) // 5)
        t += [0, 255]
    return t


def decode_dxt5(data, w, h):
    """DXT5 解为 RGBA bytes (top-down, 无翻转)"""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    out = bytearray(w * h * 4)
    stride = w * 4
    pos = 0
    for by in range(bh):
        for bx in range(bw):
            a0, a1 = data[pos], data[pos + 1]
            abits = int.from_bytes(data[pos+2:pos+8], 'little')
            c0u, c1u = struct.unpack_from('<HH', data, pos + 8)
            cbits = int.from_bytes(data[pos+12:pos+16], 'little')
            pos += 16
            atab = _alpha_table(a0, a1)
            pal, _ = _dxt_palette(c0u, c1u)
            for py in range(4):
                yy = by * 4 + py
                if yy >= h: break
                row = yy * stride
                for px in range(4):
                    xx = bx * 4 + px
                    if xx >= w: break
                    ai = (abits >> (3 * (py * 4 + px))) & 7
                    ci = (cbits >> (2 * (py * 4 + px))) & 3
                    r, g, b = pal[ci]
                    o = row + xx * 4
                    out[o] = r; out[o+1] = g; out[o+2] = b; out[o+3] = atab[ai]
    return bytes(out)


def decode_dxt1(data, w, h):
    bw, bh = (w + 3) // 4, (h + 3) // 4
    out = bytearray(w * h * 4)
    stride = w * 4
    pos = 0
    for by in range(bh):
        for bx in range(bw):
            c0u, c1u = struct.unpack_from('<HH', data, pos)
            cbits = int.from_bytes(data[pos+4:pos+8], 'little')
            pos += 8
            pal, four = _dxt_palette(c0u, c1u)
            for py in range(4):
                yy = by * 4 + py
                if yy >= h: break
                row = yy * stride
                for px in range(4):
                    xx = bx * 4 + px
                    if xx >= w: break
                    ci = (cbits >> (2 * (py * 4 + px))) & 3
                    r, g, b = pal[ci]
                    a = 255
                    if (not four) and ci == 3:
                        a = 0
                    o = row + xx * 4
                    out[o] = r; out[o+1] = g; out[o+2] = b; out[o+3] = a
    return bytes(out)


# ============== 编码器（PoC quality: max/min 颜色, max/min alpha） ==============

def _pack_565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _dist2(c1, c2):
    dr = c1[0] - c2[0]; dg = c1[1] - c2[1]; db = c1[2] - c2[2]
    return dr*dr + dg*dg + db*db


def _fit_color(pixels):
    bright = max(pixels, key=lambda p: p[0]+p[1]+p[2])
    dark = min(pixels, key=lambda p: p[0]+p[1]+p[2])
    c0 = (bright[0], bright[1], bright[2])
    c1 = (dark[0], dark[1], dark[2])
    if (c0[0]+c0[1]+c0[2]) <= (c1[0]+c1[1]+c1[2]):
        c0, c1 = c1, c0
    pal = [c0, c1,
           tuple((2*a + b) // 3 for a, b in zip(c0, c1)),
           tuple((a + 2*b) // 3 for a, b in zip(c0, c1))]
    bits = 0
    for i, p in enumerate(pixels):
        d = [(p[0]-q[0])**2 + (p[1]-q[1])**2 + (p[2]-q[2])**2 for q in pal]
        ci = d.index(min(d))
        bits |= ci << (2 * i)
    return _pack_565(*c0), _pack_565(*c1), bits


def _fit_alpha(pixels):
    alphas = [p[3] for p in pixels]
    a0, a1 = max(alphas), min(alphas)
    if a0 == a1:
        return a0, a1, 0
    if a0 > a1:
        pal = [a0, a1]
        for i in range(1, 7):
            pal.append(((6 - i) * a0 + i * a1) // 7)
    else:
        a0, a1 = a1, a0
        pal = [a0, a1]
        for i in range(1, 5):
            pal.append(((4 - i) * a0 + i * a1) // 5)
        pal += [0, 255]
    bits = 0
    for i, av in enumerate(alphas):
        d = [(av - q)**2 for q in pal]
        ai = d.index(min(d))
        bits |= ai << (3 * i)
    return a0, a1, bits


def encode_dxt5(rgba, w, h):
    bw, bh = (w + 3) // 4, (h + 3) // 4
    out = bytearray(bw * bh * 16)
    pos = 0
    stride = w * 4
    for by in range(bh):
        for bx in range(bw):
            pixels = []
            for py in range(4):
                yy = by * 4 + py
                if yy >= h: continue
                for px in range(4):
                    xx = bx * 4 + px
                    if xx >= w: continue
                    o = yy * stride + xx * 4
                    pixels.append((rgba[o], rgba[o+1], rgba[o+2], rgba[o+3]))
            while len(pixels) < 16:
                pixels.append(pixels[-1])
            c0u, c1u, cbits = _fit_color(pixels)
            a0, a1, abits = _fit_alpha(pixels)
            out[pos] = a0; out[pos+1] = a1
            out[pos+2:pos+8] = abits.to_bytes(6, 'little')
            struct.pack_into('<HH', out, pos+8, c0u, c1u)
            out[pos+12:pos+16] = cbits.to_bytes(4, 'little')
            pos += 16
    return bytes(out)


def build_cvbm(template_path, recs, out_path):
    """保留 template cvbm 的全部字节, 只在已知 record 字段位置精确替换.
    recs[i] 需含: off, w, h, mips, size (其他字段照原模板).
    已知字段位置 (绝对 cvbm 偏移, base=24 + i*72):
        +0x00 u64 file_offset
        +0x08 u16 width
        +0x0A u16 height
        +0x0C u32 tex_fmt (含 platform 标志)
        +0x10..0x15 6B flags (不要碰)
        +0x16 u8  hasalpha
        +0x17..0x22 12B flags2 (不要碰)
        +0x23 u8  mip_count
        +0x24 u32 data_size
        +0x28..0x47 32B unk3 (不要碰)
        +0x48 起 name (null-term)
    """
    src = open(template_path, 'rb').read()
    magic, ver, sizec, sizeg, ntex, ndup, u3 = struct.unpack_from('<4sIIIIhH', src, 0)
    size_g_total = sum(r['size'] for r in recs)
    dst = bytearray(src)
    struct.pack_into('<I', dst, 0x0C, size_g_total)
    for i, r in enumerate(recs):
        base = 24 + i * 72
        if 'record_off_in_container' in r:
            base = r['record_off_in_container']
        struct.pack_into('<Q', dst, base + 0x00, r['off'])
        struct.pack_into('<H', dst, base + 0x08, r['w'])
        struct.pack_into('<H', dst, base + 0x0A, r['h'])
        struct.pack_into('<I', dst, base + 0x0C, r['fmt_raw'])
        dst[base + 0x16] = r.get('alpha', 1)
        dst[base + 0x23] = r['mips']
        struct.pack_into('<I', dst, base + 0x24, r['size'])
    with open(out_path, 'wb') as f:
        f.write(dst)


def render_mip(gbytes, rec, level=0):
    """从 gvbm 数据按 record.off 取第 level 级 mip 并解码 RGBA"""
    w, h = rec['w'] >> level, rec['h'] >> level
    fmt = rec['fmt']
    if fmt == FMT_DXT5 or fmt == FMT_DXT3:
        bpp = 1.0
    else:
        bpp = 0.5
    off = rec['off']
    for lv in range(level):
        lw, lh = rec['w'] >> lv, rec['h'] >> lv
        off += int(max(1, lw) * max(1, lh) * bpp)
    size = int(max(1, w) * max(1, h) * bpp)
    data = gbytes[off:off + size]
    if fmt == FMT_DXT5:
        return decode_dxt5(data, w, h), w, h
    if fmt == FMT_DXT1:
        return decode_dxt1(data, w, h), w, h
    raise ValueError(f'unsupported fmt {fmt}')


def write_png(path, rgba, w, h):
    def chunk(tag, payload):
        c = struct.pack('>I', len(payload)) + tag + payload
        return c + struct.pack('>I', zlib.crc32(tag + payload) & 0xFFFFFFFF)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        raw.extend(rgba[y*stride:(y+1)*stride])
    idat = zlib.compress(bytes(raw), 6)
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', ihdr)
    png += chunk(b'IDAT', idat)
    png += chunk(b'IEND', b'')
    open(path, 'wb').write(png)


def build_mip_chain(encoded_mip0, w, h, mip_count):
    """返回 (完整 gvbm bytes, [mip0, mip1, ...]) — 当前实现仅放 mip0, mip_count>=2 时其他级用 1x1 块占位.
    注意: 正确做法是逐级下采样再编码. 这里只是占位, 用于 PoC 验证 cvbm 字节级一致."""
    # 简化: 只放 mip0, 其余 0
    sizes = []
    cw, ch = w, h
    for _ in range(mip_count):
        sizes.append(max(1, cw) * max(1, ch))  # DXT5: 1 byte/pixel
        cw = max(1, cw // 2); ch = max(1, ch // 2)
    total = sum(sizes)
    g = bytearray(total)
    g[0:len(encoded_mip0)] = encoded_mip0
    return bytes(g), sizes


def parse_vf3(path):
    """解析字体度量表 VFNT v4. 16B 记录: {u32 advance, u32 ?, u32 0, u32 0xFFFF}.
    记录数 = charlist count (按 charlist 顺序索引)."""
    d = open(path, 'rb').read()
    magic = d[:4]
    assert magic == b'TNFV', f'bad vf3 magic {magic}'
    # 头部解析 (猜测, 不严格): ver u32 + count u32 + ...
    ver, count = struct.unpack_from('<II', d, 4)
    recs = []
    for i in range(count):
        a, b, c, e = struct.unpack_from('<4I', d, 12 + i*16)
        recs.append(dict(advance=a, b=b, c=c, flag=e))
    return dict(version=ver, count=count), recs