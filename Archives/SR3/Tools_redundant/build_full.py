# -*- coding: utf-8 -*-
"""
SRTT3 字库全量烘焙引擎 - build_full.py
======================================
路线(基于 build_menu_zh 拓展):
  1. 字符集 -> 槽分配 (与 menu_test_slots.json 同体系, 增量扩展)
  2. 字体表 vf3 count 增量扩展 (空洞项 0 安全)
  3. 图集 gvbm 块级 DXT5 拷贝保真扩容 (从 2048x1024 扩到 4096x4096/8192x4096 等)
  4. cvbm record w/h/mips/size 更新 (复用 volition_tex.build_cvbm 的 7 字段替换策略)
  5. FreeType 烘字形 + DXT5 字节级 patch (复用 make_sample.bake_font)
  6. 增量模式: 跳过 vf3 已定义 idx 的字 (避免重复烘焙)

输入: han_set JSON {"slot": {ch: 槽码}, "fonts": ["font_body","font_header_pc",...]}
输出: 直接写盘当前 unpack/misc 资源 (vf3/gvbm/cvbm) + 更新槽表

注意: 文本替换走 le_strings_repack.repack (与字库独立);
merge_translations.py 处理翻译 JSON
"""
import sys, os, struct, shutil
import numpy as np
import freetype
sys.path.insert(0, r"C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04")
import volition_tex as vt

M = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack\misc"
FONT_SRC = r"C:\Windows\Fonts\msyh.ttc"

FONTS = [("font_body", "font_body_nobdr"),
         ("font_header", "font_header_nobdr"),
         ("font_header_pc", "font_header_pc_nobdr"),
         ("font_sk", "font_sk_nobdr")]

DEFAULT_EXPANSION = {
    "font_body":       (4096, 4096),
    "font_header":     (4096, 4096),
    "font_header_pc":  (4096, 4096),
    "font_sk":         (8192, 4096),
}


def expand_atlas(cv_path, gv_path, new_w, new_h):
    """块级 DXT5 拷贝保真扩容. cvbm w/h/mips/size 更新. 返回新 gvbm bytes."""
    gv_old = bytearray(open(gv_path, 'rb').read())
    hdr, recs = vt.parse_cvbm(cv_path)
    r = recs[0]
    w_old, h_old = r['w'], r['h']
    assert new_w % 4 == 0 and new_h % 4 == 0
    assert new_w >= w_old and new_h >= h_old
    if (w_old, h_old) == (new_w, new_h):
        return bytes(gv_old)
    bw_old, bw_new = w_old // 4, new_w // 4
    bh_old = h_old // 4
    gv_new = bytearray(new_w * new_h)   # DXT5 每像素 1B (每 4x4 块 16B)
    for by in range(bh_old):
        src_off = by * bw_old * 16
        dst_off = by * bw_new * 16
        gv_new[dst_off:dst_off + bw_old * 16] = gv_old[src_off:src_off + bw_old * 16]
    new_recs = [dict(r)]
    new_recs[0]['w'] = new_w
    new_recs[0]['h'] = new_h
    new_recs[0]['mips'] = 1
    new_recs[0]['size'] = len(gv_new)
    vt.build_cvbm(cv_path, new_recs, cv_path)
    return bytes(gv_new)


def patch_alpha_region(data, W, x0, y0, alpha_map, a_h, a_w):
    for by in range(0, a_h, 4):
        for bx in range(0, a_w, 4):
            gx, gy = x0 + bx, y0 + by
            blk_off = (gy // 4) * (W // 4) * 16 + (gx // 4) * 16
            if blk_off + 16 > len(data):
                continue
            blk = bytes(data[blk_off:blk_off + 16])
            a0, a1 = blk[0], blk[1]
            aidx = int.from_bytes(blk[2:8], 'little')
            al = [a0, a1, (6*a0+1*a1)//7, (5*a0+2*a1)//7, (4*a0+3*a1)//7,
                  (3*a0+4*a1)//7, (2*a0+5*a1)//7, (1*a0+6*a1)//7]
            if a0 > a1:
                al[6] = 0; al[7] = 255
            alphas = [al[(aidx >> (3*i)) & 7] for i in range(16)]
            for j in range(16):
                py = gy + j // 4; px = gx + j % 4
                ly, lx = py - y0, px - x0
                if 0 <= ly < a_h and 0 <= lx < a_w:
                    alphas[j] = int(alpha_map[ly, lx])
            order = [255, 0, 204, 153, 102, 51, 0, 255]
            out = bytearray(16)
            out[0] = 255; out[1] = 0
            bits = 0
            for i in range(16):
                av = alphas[i]
                best, bi = 999, 0
                for k, lv in enumerate(order):
                    d = abs(av - lv)
                    if d < best:
                        best, bi = d, k
                bits |= bi << (3 * i)
            out[2] = bits & 0xFF; out[3] = (bits >> 8) & 0xFF
            out[4] = (bits >> 16) & 0xFF; out[5] = (bits >> 24) & 0xFF
            out[6] = 0xFF; out[7] = 0xFF; out[8] = 0xFF; out[9] = 0xFF
            data[blk_off:blk_off + 16] = bytes(out)


def render_glyphs(text, pixel_h):
    face = freetype.Face(FONT_SRC)
    face.set_pixel_sizes(0, pixel_h)
    out = {}
    for ch in text:
        face.load_char(ch, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL)
        bmp = face.glyph.bitmap
        w, h = bmp.width, bmp.rows
        raw = bmp.buffer
        if not isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw)
        buf = np.frombuffer(raw, dtype=np.uint8).reshape(h, w) if raw else np.zeros((h, w), np.uint8)
        out[ch] = buf
    return out


def bake_font_full(vstem, gstem, han_set, slot_map):
    """对单个字体: 增量烘字. slot_map {ch: 槽码}. 返回 [(idx,ch,x,y), ...]."""
    print(f"\n===== {vstem} (+{gstem}) =====")
    vpath = os.path.join(M, vstem + ".vf3_pc")
    cv = os.path.join(M, gstem + ".cvbm_pc")
    gv = os.path.join(M, gstem + ".gvbm_pc")
    d = bytearray(open(vpath, 'rb').read())
    count_old = struct.unpack_from('<I', d, 8)[0]
    L = struct.unpack_from('<H', d, 22)[0]
    met_off = 0xD0
    z1_old = (met_off + 16 * count_old + 15) & ~15

    # 跳过已烘字 (idx 对应度量 advance!=0)
    todo = []
    for ch in han_set:
        idx = slot_map[ch] - 32
        if idx < count_old:
            adv = struct.unpack_from('<I', d, met_off + 16 * idx)[0]
            if adv != 0:
                continue
        todo.append((ch, idx))
    print(f"  vf3 count_old={count_old} 行高 L={L} 待烘 {len(todo)} 字")

    # 烘字参数
    P = round(L * 0.72)
    w4 = P + 16
    pad_top = round((L - P) * 0.6)
    per_row = lambda W: max(1, (W - 8) // w4)

    # 当前图集 + 找底部空带
    hdr, recs = vt.parse_cvbm(cv)
    r = recs[0]
    W, H = r['w'], r['h']
    gdata = bytearray(open(gv, 'rb').read())
    rgba, _, _ = vt.render_mip(bytes(gdata), r, 0)
    alpha = np.frombuffer(rgba, dtype=np.uint8)[3::4].reshape(H, W)
    del rgba
    col_empty = (alpha <= 8).all(axis=1)
    best_y0, best_h = None, 0
    y = H - 1
    while y >= 0:
        if col_empty[y]:
            y0 = y
            while y0 - 1 >= 0 and col_empty[y0 - 1]:
                y0 -= 1
            hh = y - y0 + 1
            if hh > best_h:
                best_h, best_y0 = hh, y0
            y = y0 - 1
        else:
            y -= 1
    cap = 0
    if best_y0 is not None:
        rows = best_h // L
        cap = rows * per_row(W)
    print(f"  当前图集 {W}x{H} 空带 y0={best_y0} 高={best_h} 容量={cap} 需={len(todo)}")

    # 不足则扩容
    if cap < len(todo):
        new_w, new_h = DEFAULT_EXPANSION.get(vstem, (max(W, 4096), max(H, 4096)))
        while new_h * new_w // (w4 * L) < len(todo) + cap:
            new_h *= 2
            if new_h > 32768:
                break
        print(f"  -> 扩容 {W}x{H} -> {new_w}x{new_h}")
        gdata = bytearray(expand_atlas(cv, gv, new_w, new_h))
        W, H = new_w, new_h
        best_y0 = best_y0 if best_y0 else 0

    # 烘字
    glyphs = render_glyphs("".join([ch for ch, _ in todo]), P)
    baked = []
    for t, (ch, idx) in enumerate(todo):
        col = t % per_row(W)
        row = t // per_row(W)
        y0 = best_y0 + row * L
        if y0 + L > H:
            print(f"  ! 图集垂直空间不足 t={t}"); break
        x0 = 8 + col * w4
        buf = glyphs[ch]
        gh, gw = buf.shape
        left_pad = (w4 - gw) // 2
        cell_map = np.zeros((L, w4), np.uint8)
        sx = max(0, -left_pad); sy = max(0, -pad_top)
        ex = min(gw, w4 - left_pad); ey = min(gh, L - pad_top)
        if ex > sx and ey > sy:
            cell_map[pad_top + sy:pad_top + ey, left_pad + sx:left_pad + ex] = buf[sy:ey, sx:ex]
        patch_alpha_region(gdata, W, x0, y0, cell_map, L, w4)
        baked.append((idx, ch, x0, y0))
    open(gv, 'wb').write(bytes(gdata))

    # vf3 扩展
    new_count = max(count_old, max(i for i, _, _, _ in baked) + 1) if baked else count_old
    met_new = met_off
    z1_new = (met_new + 16 * new_count + 15) & ~15
    z2_new = (z1_new + 4 * new_count + 15) & ~15
    nd = bytearray(z2_new + 4 * new_count)
    nd[:met_off] = d[:met_off]
    nd[met_new:met_new + 16 * count_old] = d[met_off:met_off + 16 * count_old]
    for i in range(count_old):
        struct.pack_into('<I', nd, z1_new + 4 * i, struct.unpack_from('<I', d, z1_old + 4 * i)[0])
        struct.pack_into('<I', nd, z2_new + 4 * i, struct.unpack_from('<I', d, z1_old + 4 * (count_old + i))[0])
    for idx, ch, x0, y0 in baked:
        struct.pack_into('<II', nd, met_new + 16 * idx, w4, w4)
        struct.pack_into('<I', nd, met_new + 16 * idx + 8, 0)
        struct.pack_into('<h', nd, met_new + 16 * idx + 12, -1)
        struct.pack_into('<I', nd, z1_new + 4 * idx, x0)
        struct.pack_into('<I', nd, z2_new + 4 * idx, y0)
    struct.pack_into('<I', nd, 8, new_count)
    open(vpath, 'wb').write(bytes(nd))
    print(f"  vf3 重建: count {count_old}->{new_count} 文件 {len(d)}B->{len(nd)}B")
    return baked


def main():
    """演示: scratch 副本上扩容+烘 30 字 (不部署)"""
    global M
    SCRATCH = r"C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04\scratch"
    os.makedirs(SCRATCH, exist_ok=True)
    test_chars = "啊波菜的第三很好我都觉得可了那你哦请让是他我们想在吗这个做你"
    print(f"测试字符 ({len(test_chars)}): {test_chars}")
    # 复制 body 系文件到 scratch
    for stem in ["font_body_nobdr", "font_body"]:
        ext = "cvbm_pc" if "nobdr" in stem else "vf3_pc"
        shutil.copy(os.path.join(M, stem + "." + ext),
                    os.path.join(SCRATCH, stem + "." + ext))
    shutil.copy(os.path.join(M, "font_body_nobdr.gvbm_pc"),
                os.path.join(SCRATCH, "font_body_nobdr.gvbm_pc"))
    # 槽分配: 从 1756 起 (已有 1732..1755 = 24 字)
    import json
    cur = json.load(open(r"menu_test_slots.json", encoding="utf-8")) if os.path.exists("menu_test_slots.json") else {"slot": {}}
    slot = dict(cur.get("slot", {}))
    max_slot = max(slot.values()) if slot else 1755
    for c in test_chars:
        if c not in slot:
            slot[c] = max_slot + 1
            max_slot += 1
    print(f"新增 {len(set(test_chars) - set(cur.get('slot', {})))} 字, 槽位 {min(slot[c] for c in test_chars if c not in cur.get('slot', {}))}..{max_slot}")
    # 在 scratch 跑烘焙
    M_save = M
    M = SCRATCH
    try:
        baked = bake_font_full("font_body", "font_body_nobdr", set(test_chars), slot)
    finally:
        M = M_save
    print(f"烘入 {len(baked)} 字")
    # 自检: 扩容后尺寸与字形像素
    cv = os.path.join(SCRATCH, "font_body_nobdr.cvbm_pc")
    gv = os.path.join(SCRATCH, "font_body_nobdr.gvbm_pc")
    hdr, recs = vt.parse_cvbm(cv)
    r = recs[0]
    print(f"扩容后 cvbm: {r['w']}x{r['h']} mips={r['mips']} size={r['size']}")
    rgba, w, h = vt.render_mip(open(gv, 'rb').read(), r, 0)
    a = np.frombuffer(rgba, dtype=np.uint8)[3::4].reshape(h, w)
    print(f"图集 {w}x{h} 总字形像素: {int((a > 40).sum())}")
    # 验证: 解码第一个新字区
    d = open(os.path.join(SCRATCH, "font_body.vf3_pc"), 'rb').read()
    cnt = struct.unpack_from('<I', d, 8)[0]
    met = 0xD0; z1 = (met + 16 * cnt + 15) & ~15; z2 = (z1 + 4 * cnt + 15) & ~15
    if baked:
        idx, ch, x, y = baked[0]
        sub = a[y:y + 110, x:x + 100]
        print(f"  示例: {ch} uv=({x},{y}) 字形像素 {int((sub > 40).sum())}")


if __name__ == "__main__":
    main()