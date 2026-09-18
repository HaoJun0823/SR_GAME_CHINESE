# -*- coding: utf-8 -*-
"""
SRTT3 Remastered 中文版 v7 —— 复刻游侠(ali213) us 分支方案
=========================================================
几何参数完整复刻游侠实测值, 但槽码主动规避两处字符集禁区 (游侠穿禁区B能跑,
我们无必要冒险)。

  图集  : font_body_nobdr 4096x4096 DXT5 mips=1 (16,777,216B)  [官方 jap 同配置先例]
  cell  : 72x72, 56 列 x 56 行 = 3136 槽
  L     : 72 (vf3 +0x16)
  count : 3136, base=32, kern=0
  vf3   : font_body + font_header_pc 均指向 font_body_nobdr.tga (共享图集)

槽位规划 (idx = 槽码 - 0x20):
  idx    0-94   (0x20-0x7E)  ASCII 可打印 95
  idx   95-127  (0x7F-0x9F)  [禁区A] 留空
  idx  128-292  (0xA0-0x144) 符号 + 中文标点 + Latin 补充 165
  idx  293-328  (0x145-0x168) [禁区B] 留空
  idx  329-3135 (0x169-0xC4F) 汉字 2807 (GB2312 一级)

规避的雷区:
  1. DXT5 块步进 16 (v5 空白 bug)          -> bake_lib 向量化编码 + round-trip 已验证
  2. 字符集禁区 A/B (v3/v4 崩溃)           -> 槽码主动跳过
  3. le_strings 全量重排                    -> repack_inplace 原位覆盖
  4. cvbm 自拼字段错位                      -> build_cvbm 保留 template 只改 7 字段
  5. font cpu 池 64KB (真因, IDA 实证)   -> scripts\fontpool_patch.asi 扩 .rdata 常量到 512KB
     (旧推断"gpu 池 ≈3448 槽"已废弃; v1 count=3448 崩是因为 vf3 体积 101KB>64KB)
  6. 打包器不可信                           -> F1 零改动重打包已验证
"""
import sys, os, struct, shutil, subprocess, time, json
import numpy as np

ROOT = r"C:/Users/haojun0823/WorkBuddy/2026-09-04-02-19-04"
GAME = r"I:/SteamLibrary/steamapps/common/Saints Row The Third Remastered"
M    = os.path.join(GAME, "unpack", "misc")
CUR  = os.path.join(ROOT, "cur_misc")
CACHE= os.path.join(GAME, "cache")
ORIG = os.path.join(CACHE, "misc.vpp_pc.orig")

sys.path.insert(0, ROOT)
import bake_lib as bl
import volition_tex as vt
import le_strings_repack as lr

# ============ 英->中 对照表 (按 menu_us 实际原文精确匹配) ============
EN2ZH = {
    # --- 主菜单核心 (v6 已验证) ---
    "SINGLE PLAYER": "单人游戏",
    "CO-OP": "合作模式",
    "CAMPAIGN": "主线战役",
    "CO-OP CAMPAIGN": "合作战役",
    "CHECK MESSAGES": "查看信息",
    "COMMUNITY": "社区",
    "DOWNLOADABLE CONTENT": "下载内容",
    "OPTIONS": "选项",
    # --- 通用 UI ---
    "QUIT": "退出",
    "CONTINUE": "继续",
    "NEW GAME": "新游戏",
    "LOAD GAME": "载入游戏",
    "SAVE GAME": "保存游戏",
    "SETTINGS": "设置",
    "AUDIO": "音频",
    "VIDEO": "视频",
    "CONTROLS": "控制",
    "GAMEPLAY": "游戏性",
    "DISPLAY": "显示",
    "CREDITS": "制作人员",
    "EXIT": "退出",
    "BACK": "返回",
    "ACCEPT": "确定",
    "CANCEL": "取消",
    "YES": "是",
    "NO": "否",
    "ON": "开",
    "OFF": "关",
    "ENABLED": "已启用",
    "DISABLED": "已禁用",
    "NONE": "无",
    "STATIC": "静态",
    "DEFAULT": "默认",
    "APPLY": "应用",
    "RESET": "重置",
    "CLOSE": "关闭",
    "OK": "确定",
    "DELETE": "删除",
    "CREATE": "创建",
    "EDIT": "编辑",
    "SELECT": "选择",
    "START": "开始",
    "RESUME": "继续",
    "RESTART": "重新开始",
    "PAUSE": "暂停",
    "MENU": "菜单",
    "MAIN MENU": "主菜单",
    "PAUSE MENU": "暂停菜单",
    "HELP": "帮助",
    "ABOUT": "关于",
    "LANGUAGE": "语言",
    "SUBTITLES": "字幕",
    "BRIGHTNESS": "亮度",
    "VOLUME": "音量",
    "MUSIC": "音乐",
    "SOUND": "音效",
    "VOICE": "语音",
    "RESOLUTION": "分辨率",
    "QUALITY": "画质",
    "GRAPHICS": "图形",
    "ADVANCED": "高级",
    "KEYBOARD": "键盘",
    "MOUSE": "鼠标",
    "GAMEPAD": "手柄",
    "SENSITIVITY": "灵敏度",
    "INVERT": "反转",
    "VIBRATION": "振动",
    # --- 游戏内容 ---
    "OUTFITS": "服装",
    "CHARACTER GALLERY": "角色画廊",
    "DISTRICT CONTROL": "区域控制",
    "TAKEOVER HOOD": "接管地盘",
    "TAKEOVER ASSIST": "接管协助",
    "PREVIEW PACKS": "预览礼包",
    "CREATE NEW ID": "创建新档案",
    "INVALID NAME": "无效名称",
    "NO PROFILE DETECTED": "未检测到档案",
    "OPEN HDR SETTINGS": "打开 HDR 设置",
    "SCREEN SPACE REFLECTIONS": "屏幕空间反射",
    "ACTIVITIES": "活动",
    "MISSIONS": "任务",
    "STRONGHOLD": "据点",
    "MAP": "地图",
    "INVENTORY": "物品栏",
    "WEAPONS": "武器",
    "VEHICLES": "载具",
    "UPGRADES": "升级",
    "STATS": "统计",
    "OBJECTIVES": "目标",
    "COLLECTIBLES": "收集品",
    "CHALLENGES": "挑战",
    "ACHIEVEMENTS": "成就",
    "LEADERBOARDS": "排行榜",
    "MULTIPLAYER": "多人游戏",
    "COOPERATIVE": "合作",
    "COMPETITIVE": "对抗",
    "FRIENDS": "好友",
    "INVITE": "邀请",
    "JOIN": "加入",
    "HOST": "主持",
    "LOBBY": "大厅",
    "READY": "准备",
    "LOADING": "载入中",
    "SAVING": "保存中",
    "PLEASE WAIT": "请稍候",
    "CONFIRM": "确认",
    "WARNING": "警告",
    "ERROR": "错误",
    "SUCCESS": "成功",
    "FAILED": "失败",
    "CONNECTED": "已连接",
    "DISCONNECTED": "已断开",
    "SIGNED IN": "已登录",
    "SIGNED OUT": "已登出",
}


def build_atlas(extra_needed=None):
    """生成 4096x4096 DXT5 图集 + 槽位度量表"""
    asc, sym, han = bl.build_charset(extra_needed)
    print(f"[1/6] 字表: ASCII {len(asc)} + 符号 {len(sym)} + 汉字 {len(han)}"
          + (f" (优先字 {len(extra_needed)} 个)" if extra_needed else ""))

    # 槽位 -> 字符
    slot2ch = {}
    for i, ch in enumerate(asc):
        slot2ch[bl.IDX_ASCII_LO + i] = ch
    for i, ch in enumerate(sym):
        slot2ch[bl.IDX_SYM_LO + i] = ch
    for i, ch in enumerate(han):
        slot2ch[bl.IDX_HAN_LO + i] = ch
    print(f"      占用槽位 {len(slot2ch)} / {bl.N_SLOT} "
          f"(禁区A {bl.IDX_ZONEA_HI-bl.IDX_ZONEA_LO+1} + 禁区B {bl.IDX_ZONEB_HI-bl.IDX_ZONEB_LO+1} 留空)")

    t0 = time.time()
    glyphs = bl.render_glyphs(list(slot2ch.values()), bl.P)
    n_empty = sum(1 for v in glyphs.values() if v.size == 0 or v.max() == 0)
    print(f"[2/6] 渲染完成 {len(glyphs)} 字 @ {bl.P}px, {time.time()-t0:.1f}s (空字形 {n_empty})")

    # alpha 全图
    t0 = time.time()
    alpha = np.zeros((bl.ATLAS_H, bl.ATLAS_W), np.uint8)
    metrics = {}   # idx -> (adv, width, x, y)

    for idx, ch in slot2ch.items():
        col = idx % bl.COLS
        row = idx // bl.COLS
        cx = col * bl.CELL
        cy = row * bl.CELL
        g = glyphs[ch]
        gh, gw = g.shape
        if gh == 0 or gw == 0:
            metrics[idx] = (0, 0, 0, 0)
            continue
        # 水平【左对齐】(关键): UV 矩形 = (cx, cy, width, L), 从 cell 起点算起。
        # 若水平居中, 字形整体右移 (CELL-gw)/2, 会超出 UV 矩形右边界 -> 字形被裁切。
        # 垂直【居中】: UV 矩形高固定 = L, 字形高 gh < L, 居中留白最自然。
        ox = cx
        oy = cy + (bl.L - gh) // 2
        # 边界裁剪 (防越界)
        x0, y0 = max(0, ox), max(0, oy)
        x1 = min(bl.ATLAS_W, ox + gw)
        y1 = min(bl.ATLAS_H, oy + gh)
        sx0, sy0 = x0 - ox, y0 - oy
        sx1, sy1 = sx0 + (x1 - x0), sy0 + (y1 - y0)
        if x1 > x0 and y1 > y0:
            alpha[y0:y1, x0:x1] = g[sy0:sy1, sx0:sx1]
        # advance: 汉字给固定步进(含字距), ASCII 按实际宽
        is_han = (bl.IDX_HAN_LO <= idx <= bl.IDX_HAN_HI)
        adv = bl.P + 6 if is_han else gw + 2
        # width = UV 矩形宽 (取字形宽, 最小 1)
        wid = max(1, gw)
        metrics[idx] = (adv, wid, cx, cy)

    print(f"[3/6] 图集排布完成 {time.time()-t0:.1f}s, alpha 非零像素 {int((alpha>8).sum()):,}")

    # DXT5 编码
    t0 = time.time()
    alpha_plane = bl.encode_dxt5_alpha_vector(alpha)
    n_blocks = (bl.ATLAS_W // 4) * (bl.ATLAS_H // 4)
    color_plane = bl.make_color_plane(bl.ATLAS_W // 4, bl.ATLAS_H // 4)
    gvbm = bl.interleave(alpha_plane, color_plane, n_blocks)
    print(f"[4/6] DXT5 编码 {time.time()-t0:.1f}s -> {len(gvbm):,}B (期望 {bl.ATLAS_W*bl.ATLAS_H//16*16:,}B)")
    assert len(gvbm) == 16_777_216, f"图集大小异常 {len(gvbm)}"
    del alpha, alpha_plane
    return gvbm, metrics, slot2ch


def build_vf3(template_path, out_path, metrics, atlas_name, count, L):
    """重建 vf3: 保留官方 header 前 0xD0, 强制 kern=0 (met 恒为 0xD0), 重建三区。
    注意: 头部 kern 字段与 metric 写入位置必须一致!
    v7.1 教训: 旧版按 template 原 kern(font_debug=1668) 算 met=0x27f0 写 metric,
    又把头 kern 强制写 0 -> 引擎按 met=0xd0 读到 kern 表数据, 字形全乱。"""
    src = bytearray(open(template_path, "rb").read())
    met = 0xD0                                  # 统一 kern=0, 不保留原 kern 表
    z1 = (met + 16 * count + 15) & ~15
    z2 = (z1 + 4 * count + 15) & ~15
    end = z2 + 4 * count

    out = bytearray(end)
    out[:met] = src[:met]                       # header 前 0xD0 保留
    struct.pack_into("<I", out, 0x08, count)    # count
    struct.pack_into("<H", out, 0x16, L)        # 行高
    struct.pack_into("<I", out, 0x0C, bl.BASE)  # baseChar
    struct.pack_into("<I", out, 0x20, 0)        # kern = 0 (与 met=0xD0 一致)
    # 内嵌图集名 @0x68 (64B, null 填充)
    nb = atlas_name.encode("ascii")
    assert len(nb) < 64, f"图集名过长 {atlas_name}"
    out[0x68:0x68 + 64] = b"\x00" * 64
    out[0x68:0x68 + len(nb)] = nb

    for idx, (adv, wid, x, y) in metrics.items():
        struct.pack_into("<II", out, met + 16 * idx, adv, wid)
        struct.pack_into("<I", out, met + 16 * idx + 8, 0)      # zero
        struct.pack_into("<h", out, met + 16 * idx + 12, -1)    # kernIdx
        struct.pack_into("<H", out, met + 16 * idx + 14, 0)     # pad
        struct.pack_into("<I", out, z1 + 4 * idx, x)
        struct.pack_into("<I", out, z2 + 4 * idx, y)
    open(out_path, "wb").write(bytes(out))
    print(f"      {os.path.basename(out_path)}: count={count} L={L} "
          f"图集={atlas_name!r} {len(out):,}B (met=0x{met:x} z1=0x{z1:x} z2=0x{z2:x})")


def main():
    print("=" * 72)
    print("SRTT3 v7 构建 —— 复刻游侠 us 分支 + 规避字符集禁区")
    print("=" * 72)

    # 0) 留档 + 恢复基准
    cur_vpp = os.path.join(CACHE, "misc.vpp_pc")
    bak = os.path.join(CACHE, "misc.vpp_pc.v6ok")
    if os.path.exists(cur_vpp) and not os.path.exists(bak):
        shutil.copy2(cur_vpp, bak)
        print(f"[0/6] v6 已留档 -> misc.vpp_pc.v6ok")
    print("[0/6] 恢复 unpack/misc <- cur_misc ...")
    subprocess.run(["robocopy", CUR, M, "/MIR", "/NFL", "/NDL", "/NJH", "/NJS"], check=False)
    n = len(os.listdir(M))
    assert n == 375, f"基准恢复失败, 文件数={n}"
    print(f"      已恢复 {n} 文件")

    # 1-4) 图集 —— 优先纳入译文实际用字, 避免拼音截断丢掉常用字(中/主/预/选...)
    needed_han = set()
    for zh in EN2ZH.values():
        needed_han.update(c for c in zh if 0x4E00 <= ord(c) <= 0x9FFF)
    gvbm, metrics, slot2ch = build_atlas(needed_han)

    # 1.5) 译文用字覆盖检查: 缺失字仅警告(不致命), 缺失字将保持英文/显示方块但游戏可进。
    #      (槽位几何 3136 有限, GB2312 一级 3755 无法全装, 拼音靠后的生僻字会缺;
    #       cpu 池已由 fontpool_patch.asi 扩容到 512KB, 不再是瓶颈)
    need = set()
    for zh in EN2ZH.values():
        need.update(c for c in zh if ord(c) > 0x7F)
    missing = sorted(need - set(slot2ch.values()))
    if missing:
        print(f"[1.5/6] ⚠ 字表缺 {len(missing)} 字(将保持英文/方块): {''.join(missing)}")
    else:
        print(f"[1.5/6] 译文用字 {len(need)} 个全部命中字表")

    # 5) cvbm (保留 template, 只改 w/h/mips/size/off)
    cvbm_tpl = os.path.join(M, "font_body_nobdr.cvbm_pc")
    hdr, recs = vt.parse_cvbm(cvbm_tpl)
    r = dict(recs[0])
    r["w"], r["h"] = bl.ATLAS_W, bl.ATLAS_H
    r["mips"] = 1
    r["off"] = 0
    r["size"] = len(gvbm)
    r["alpha"] = 1
    vt.build_cvbm(cvbm_tpl, [r], cvbm_tpl)
    print(f"[5/6] cvbm 重建: {bl.ATLAS_W}x{bl.ATLAS_H} mips=1 size={len(gvbm):,}")
    open(os.path.join(M, "font_body_nobdr.gvbm_pc"), "wb").write(gvbm)
    print(f"      gvbm 写入 {len(gvbm):,}B")
    del gvbm

    # 6) vf3 重建 —— 复刻游侠精确改动范围: 只改 font_body + font_header_pc 两个字体,
    #    让它们共享 font_body_nobdr 中文图集。font_header / font_debug / font_sk / font_zh
    #    保持官方原样 (游侠即如此, 已验证可进)。
    #    ⚠️ count 上限受两重约束:
    #       a) font cpu 池 (已扩容 512KB): 3 个字体 vf3 字节总和必须 < 0x80000
    #       b) gpu 池 17MB: 4096x4096 DXT5 图集 = 16.78MB 已接近极限, 不可再大
    print("[6/6] vf3 重建 (复刻游侠: font_body + font_header_pc 共享中文图集):")
    for name in ["font_body", "font_header_pc"]:
        build_vf3(os.path.join(M, name + ".vf3_pc"),
                  os.path.join(M, name + ".vf3_pc"),
                  metrics, "font_body_nobdr.tga", bl.N_SLOT, bl.L)

    # 6.4) cpu 池预算断言: 全部字体 vf3 总字节 < 扩容后池容量 (fontpool_patch.asi=512KB)
    total_vf3 = sum(os.path.getsize(os.path.join(M, f))
                    for f in os.listdir(M) if f.endswith(".vf3_pc"))
    assert total_vf3 < bl.VF3_BUDGET_CPU_POOL, \
        f"vf3 总字节 {total_vf3:,} >= cpu 池预算 {bl.VF3_BUDGET_CPU_POOL:,}, 会崩 (需 .asi 再扩容或减 count)"
    print(f"      [预算] vf3 总计 {total_vf3:,}B < 池预算 {bl.VF3_BUDGET_CPU_POOL:,}B ✓")

    # 6.5) 防御性自检: 仅对我们改动的中文字体(font_body/font_header_pc)强制 count/L 同步;
    #       font_debug 等官方字体若引用 font_body_nobdr 属正常设计(仅 ASCII, count 小), 仅提示。
    print("      [自检] 扫描全部 vf3 图集引用:")
    for f in sorted(os.listdir(M)):
        if not f.endswith(".vf3_pc"):
            continue
        vv = open(os.path.join(M, f), "rb").read()
        emb = vv[0x68:0x68 + 64].split(b"\x00")[0].decode("ascii", "replace")
        if emb != "font_body_nobdr.tga":
            continue
        cc = struct.unpack_from("<I", vv, 0x08)[0]
        LL = struct.unpack_from("<H", vv, 0x16)[0]
        is_target = f in ("font_body.vf3_pc", "font_header_pc.vf3_pc")
        if is_target:
            status = "OK" if (cc >= bl.N_SLOT and LL == bl.L) else "FAIL!"
            print(f"        {f:<24} count={cc:<6} L={LL:<4} {status}")
            assert cc >= bl.N_SLOT and LL == bl.L, \
                f"{f} 中文字体 count/L 未同步 (count={cc}, L={LL})"
        else:
            print(f"        {f:<24} count={cc:<6} L={LL:<4} (官方非中文字体, 引用 font_body_nobdr 合法)")

    # 7) 文本替换 (menu_us, 按原文匹配)
    ch2slot = {ch: idx + bl.BASE for idx, ch in slot2ch.items()}
    src = os.path.join(M, "menu_us.le_strings")
    fid, ver, nb, nstr, entries = lr.read_with_bucket(src)

    pairs = {}
    skip_fmt = 0
    miss_char = set()
    for b, h, t in entries:
        if h == 0 or len(t) <= 2:
            continue
        n16 = (len(t) - 2) // 2
        vals = struct.unpack(f"<{n16}H", t[:n16 * 2])
        txt = "".join(chr(v) for v in vals if v)
        zh = EN2ZH.get(txt)
        if not zh:
            continue
        # 跳过含格式标记的 (保守)
        if any(c in txt for c in "{}<>\n\t") or "\\" in txt:
            skip_fmt += 1
            continue
        # 编码: 未在字表 -> 跳过
        bad = [c for c in zh if c not in ch2slot]
        if bad:
            miss_char.update(bad)
            continue
        tb = b"".join(struct.pack("<H", ch2slot[c]) for c in zh) + b"\x00\x00"
        pairs[h] = tb

    print()
    print(f"[文本] 对照表 {len(EN2ZH)} 条, 命中 {len(pairs)} 条, 跳过格式串 {skip_fmt}")
    if miss_char:
        print(f"       缺字(未替换): {''.join(sorted(miss_char))}")
    rep = lr.repack_inplace(src, pairs, src)
    print(f"       menu_us 原位覆盖 {len(rep)} 条")

    # 8) 槽表存档
    with open(os.path.join(ROOT, "v7_slot_table.json"), "w", encoding="utf-8") as f:
        json.dump({
            "base": bl.BASE, "count": bl.N_SLOT, "L": bl.L, "cell": bl.CELL,
            "atlas": f"{bl.ATLAS_W}x{bl.ATLAS_H} mips=1",
            "zone_a_idx": [bl.IDX_ZONEA_LO, bl.IDX_ZONEA_HI],
            "zone_b_idx": [bl.IDX_ZONEB_LO, bl.IDX_ZONEB_HI],
            "n_ascii": len(bl.build_charset()[0]),
            "n_sym": len(bl.build_charset()[1]),
            "n_han": len(bl.build_charset()[2]),
            "font": bl.FONT_SRC,
            "ch2slot": ch2slot,
        }, f, ensure_ascii=False, indent=1)
    print(f"       槽表 -> v7_slot_table.json")

    # 9) 打包部署
    out_tmp = os.path.join(ROOT, "misc_v7.vpp_pc")
    py = r"C:/Users/haojun0823/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
    print()
    print(f"[打包] -> {out_tmp}")
    subprocess.run([py, os.path.join(ROOT, "vpp_pack.py"), ORIG, M, out_tmp], check=True)
    shutil.copy2(out_tmp, cur_vpp)
    print(f"[部署] cache/misc.vpp_pc = v7 ({os.path.getsize(cur_vpp):,}B)")
    print("完成。请启动游戏验证: 不崩 + 主菜单部分中文。")


if __name__ == "__main__":
    main()
