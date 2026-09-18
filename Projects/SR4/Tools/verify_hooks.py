# -*- coding: utf-8 -*-
"""
verify_hooks.py — 跨构建 hook 落点验证器

【为什么需要它】
  8 条 AOB 特征码里，MS Store 那 6 个"installed (via aob)"的地址并非离线独立验证得到，
  而是**从游戏日志里抄回来的**（见 dllmain.cpp CFG_MSSTORE 注释）。
  这是个循环: 特征码命中了某处 -> 记下该处 -> 下次日志仍显示同一处 -> 看起来"稳定"。

  本脚本提供**第二条独立证据链**: 特征码里被 mask 通配掉的字节，恰恰是
  RIP 相对位移 / call rel32 —— 把它们解出来，就得到"这个函数引用了哪个全局 /
  调用了哪个函数"。这些目标必须与**另一条独立命中的特征码**给出的地址吻合。
  不吻合 => 至少有一个落点错了。这不是循环论证。

【交叉验证清单】
  · DRAW_WIDE 尾部 call rel32          -> 必须 == FONT_LOOKUP 落点
  · TEXOBJ  mov r8d,[rip+d]            -> 必须 == fontCount 全局
  · FONT_LOOKUP cmp eax,[rip+d]        -> 必须 == fontCount 全局（同一张字体表）
  · LANG_CUR / LANG_TXT 单例            -> 必须引用**同一个**全局（一对 vtable thunk）
  · FORMAT / SUBTITLE 的 __security_cookie -> 必须引用**同一个**全局（编译器级常量）

【用法】
  python Tools/verify_hooks.py                # MS 段dump + Steam 对照
"""
import struct, os, sys

PROJ = r"G:\Projects\SR4R_DLL"
MS_DUMP = os.path.join(PROJ, r".temp\dump_ms\SR4R_dump_text.bin")
MS_BASE = 0x140001000          # 裸映像 file[0] 对应 VA（见 .map）
GAME_BASE = 0x140000000

EXES = {
    "Steam": os.path.join(PROJ, r"I_SteamPlaceholder"),   # 占位，见 main 里替换
}

# ------------------------------------------------------------------ 特征码定义
# (name, L1 sig, L1 mask, L2 sig, L2 mask, L3 sig, L3 mask)
# mask 语义同 dllmain.cpp: 1=精确比较, 0=通配
NAMES = ["DRAW_WIDE","FORMAT","FONT_LOOKUP","TEXOBJ","SRV_RESOLVE","LANG_CUR","LANG_TXT","SUBTITLE"]

def B(*a):
    return bytes(a)

L1 = {
"DRAW_WIDE": (B(0x40,0x53,0x56,0x57,0x48,0x81,0xEC,0x10,0x01,0x00,0x00,0x8B,0xBC,0x24,0x60,
                0x01,0x00,0x00,0x49,0x8B,0xD9,0x0F,0x29,0xB4,0x24,0xF0,0x00,0x00,0x00,0x8B,
                0xCF,0x44,0x0F,0x29,0x74,0x24,0x70,0x0F,0x28,0xF2,0x44,0x0F,0x28,0xF1,
                0x00,0x00,0x00,0x00),
              [1]*44+[0,0,0,0]),
"FORMAT": (B(0x40,0x55,0x56,0x57,0x41,0x57,0x48,0x8D,0xAC,0x24,0x78,0xD0,0xFF,0xFF,0xB8,0x88), None),
"FONT_LOOKUP": (B(0x83,0xF9,0xFF,0x7D,0x3E,0x8D,0x81,0xFF,0xFF,0xFF,0x7F,0x83,0xF8,0xFF,0x7E,0x2E), None),
"TEXOBJ": (B(0x4C,0x63,0xC1,0x85,0xC9,0x78,0x77,0x44,0x3B,0x05,0,0,0,0,0x7D,0x6E),
           [1]*10+[0,0,0,0]+[1,1]),
"SRV_RESOLVE": (B(0x40,0x53,0x48,0x83,0xEC,0x20,0x0F,0xB6,0xDA,0x83,0xF9,0xFF,0x74,0x4F,0x0F,0xBA), None),
"LANG_CUR": (B(0x48,0x8B,0x05,0,0,0,0,0x48,0x85,0xC0,0x74,0x0C,0x48,0x8B,0x50,0x08,
               0x48,0x85,0xD2,0x74,0x03,0x48,0xFF,0xE2,0x33,0xC0,0xC3),
             [1,1,1,0,0,0,0]+[1]*20),
"LANG_TXT": (B(0x48,0x8B,0x05,0,0,0,0,0x48,0x85,0xC0,0x74,0x0B,0x48,0x8B,0x10,0x48),
             [1,1,1,0,0,0,0]+[1]*9),
"SUBTITLE": (B(0x4C,0x8B,0xDC,0x55,0x56,0x41,0x54,0x49,0x8D,0xAB,0xA8,0xFB,0xFF,0xFF,0x48,0x81), None),
}

L2 = {
"DRAW_WIDE": (B(0x40,0x53,0x56,0x57,0x48,0x81,0xEC,0,0,0,0,0x8B,0xBC,0x24,0,0,0,0,0x49,0x8B,0xD9,
                0x0F,0x29,0xB4,0x24,0,0,0,0,0x8B,0xCF,0x44,0x0F,0x29,0x74,0x24,0,0x0F,0x28,0xF2,
                0x44,0x0F,0x28,0xF1,0xE8,0,0,0),
              [1,1,1,1,1,1,1,0,0,0,0,1,1,1,0,0,0,0,1,1,1,1,1,1,1,0,0,0,0,1,1,1,
               1,1,1,1,0,1,1,1,1,1,1,1,1,0,0,0]),
"FORMAT": (B(0x40,0x55,0x56,0x57,0x41,0x57,0x48,0x8D,0xAC,0x24,0,0,0,0,0xB8,0,0,0,0,0xE8,0,0,0,0,
             0x48,0x2B,0xE0,0x48,0x8B,0x05,0,0,0,0,0x48,0x33,0xC4,0x48,0x89,0x85,0,0,0,0,0x4C,0x89,0x4C,0x24),
           [1,1,1,1,1,1,1,1,1,1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1,1,1,1,1,1,0,0,0,0,
            1,1,1,1,1,1,0,0,0,0,1,1,1,1]),
"FONT_LOOKUP": (B(0x83,0xF9,0xFF,0x7D,0x3E,0x8D,0x81,0xFF,0xFF,0xFF,0x7F,0x83,0xF8,0xFF,0x7E,0x2E,
                  0x3B,0x05,0,0,0,0,0x7D,0x26,0x48,0x63,0xC1,0x48,0xB9,0xF4,0xFF,0xFF,0xFF,0x07,
                  0x00,0x00,0x00,0x48,0xC1,0xE0,0x04,0x48,0x03,0xC8,0x48,0x8B,0x05,0),
                # 通配: [18..21] = cmp eax,[rip+d] 的 disp32；[47] = 尾部 mov rax,[rip+d] 的 disp32 首字节
                [1]*18+[0,0,0,0]+[1]*25+[0]),
"TEXOBJ": (B(0x4C,0x63,0xC1,0x85,0xC9,0x78,0x77,0x44,0x3B,0x05,0,0,0,0,0x7D,0x6E,
             0x4C,0x8B,0x0D,0,0,0,0,0x4B,0x8D,0x0C,0x40,0x49,0x8B,0x44,0xC9,0x08,
             0x48,0x85,0xC0,0x74,0x44,0x0F,0xB7,0x50,0x14,0x66,0x83,0xFA,0x01,0x76,0x3A,0x41),
           # 通配: [10..13] = mov r8d,[rip+d] (fontCount)；[19..22] = mov r0,[rip+d] (字体表)
           [1]*10+[0,0,0,0]+[1]*5+[0,0,0,0]+[1]*25),
"SRV_RESOLVE": (B(0x40,0x53,0x48,0x83,0xEC,0,0x0F,0xB6,0xDA,0x83,0xF9,0xFF,0x74,0x4F,0x0F,0xBA,
                  0xE1,0x18,0x73,0x19,0xE8,0,0,0,0,0x48,0x85,0xC0,0x74,0x3F,0x48,0x8B,
                  0x10,0x48,0x8B,0xC8,0x48,0x83,0xC4,0,0x5B,0x48,0xFF,0x62,0x20,0xB2,0x01,0xE8),
                [1,1,1,1,1,0,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,0,0,0,1,1,1,1,1,1,1,
                 1,1,1,1,1,1,1,0,1,1,1,1,1,1,1,1]),
"LANG_CUR": (B(0x48,0x8B,0x05,0,0,0,0,0x48,0x85,0xC0,0x74,0x0C,0x48,0x8B,0x50,0x08,
               0x48,0x85,0xD2,0x74,0x03,0x48,0xFF,0xE2,0x33,0xC0,0xC3),
             [1,1,1,0,0,0,0]+[1]*20),
"LANG_TXT": (B(0x48,0x8B,0x05,0,0,0,0,0x48,0x85,0xC0,0x74,0x0B,0x48,0x8B,0x10,0x48,
               0x85,0xD2,0x74,0x03,0x48,0xFF,0xE2,0x33,0xC0,0xC3,0xCC),
             [1,1,1,0,0,0,0]+[1]*20),
"SUBTITLE": (B(0x4C,0x8B,0xDC,0x55,0x56,0x41,0x54,0x49,0x8D,0xAB,0,0,0,0,0x48,0x81,
                0xEC,0,0,0,0,0x41,0x0F,0x29,0x73,0,0x45,0x0F,0x29,0x43,0,0x48,
                0x8B,0x05,0,0,0,0,0x48,0x33,0xC4,0x48,0x89,0x85,0,0,0,0),
              [1]*10+[0,0,0,0]+[1,1,1,0,0,0,0,1,1,1,1,0,1,1,1,1,0,1,1,1,0,0,0,0,
               1,1,1,1,1,1,0,0,0,0]),
}

L3 = {
"FORMAT": (B(0x4C,0x89,0x4C,0x24,0x20,0x4C,0x89,0x44,0x24,0x18,0x48,0x89,0x4C,0x24,0x08,0x55,
             0x53,0x56,0x41,0x56,0x48,0x8D,0xAC,0x24,0xA8,0xD0,0xFF,0xFF,0xB8,0x58,0x30,0x00,
             0x00,0xE8,0xBA,0x13,0x13,0x00,0x48,0x2B,0xE0,0x4D,0x8B,0xF0,0x48,0x8B,0xDA,0x48), None),
"SUBTITLE": (B(0x48,0x8B,0xC4,0x57,0x41,0x57,0x48,0x81,0xEC,0x98,0x02,0x00,0x00,0x0F,0x29,0x78,
               0xB8,0x44,0x0F,0x29,0x40,0xA8,0x48,0x8B,0x05,0xCB,0xEA,0x3A,0x01,0x48,0x33,0xC4,
               0x48,0x89,0x84,0x24,0x40,0x02,0x00,0x00,0x45,0x8B,0xF9,0x0F,0x28,0xFA,0x44,0x0F), None),
}

# 各 hook 的"可解地址字段": (层, disp32在sig内偏移, 该指令在sig内的结束偏移, 解释)
FIELDS = {
"DRAW_WIDE":   ("L2", 45, 49, "call rel32"),
"FORMAT":      ("L2", 20, 24, "call rel32 (__chkstk)"),
"FONT_LOOKUP": ("L2", 18, 22, "cmp eax,[rip+d]"),
"TEXOBJ":      ("L2", 10, 16, "mov r8d,[rip+d]"),
"SRV_RESOLVE": ("L2", 21, 25, "call rel32"),
"LANG_CUR":    ("L2",  3,  7, "mov rax,[rip+d] (singleton)"),
"LANG_TXT":    ("L2",  3,  7, "mov rax,[rip+d] (singleton)"),
"SUBTITLE":    ("L2", 34, 39, "mov rax,[rip+d] (__security_cookie)"),
}
# FORMAT 的 cookie 在 L2 里另有一处 (27..34)
FIELDS_EXTRA = {
"FORMAT": [("L2", 30, 34, "mov rax,[rip+d] (__security_cookie)")],
# L3 里 FORMAT 的 __chkstk / SUBTITLE 的 cookie
"FORMAT_L3":   [("L3", 34, 38, "call rel32 (__chkstk)")],
"SUBTITLE_L3": [("L3", 25, 29, "mov rax,[rip+d] (__security_cookie)")],
}


# ------------------------------------------------------------------ 镜像抽象
class Image:
    def __init__(self, label):
        self.label = label

    def va_of(self, off):
        raise NotImplementedError


class TextImage(Image):
    """单一可执行段 (裸 dump / PE .text) 的字节缓冲"""
    def __init__(self, label, buf, text_va):
        self.label = label
        self.buf = buf
        self.va0 = text_va

    def scan(self, sig, mask, cap=2000):
        n = len(sig)
        # 断言: sig/mask 必须等长 —— 转写特征码时最易犯的错就是 mask 少写几项,
        #   Python 会静静地少比较若干字节(甚至 IndexError), 得出错误的"0 命中"
        if mask is not None and len(mask) != n:
            raise ValueError("sig/mask 长度不一致: len(sig)=%d len(mask)=%d" % (n, len(mask)))
        pos_all = [i for i in range(n) if (mask is None or mask[i] == 1)]
        if not pos_all:
            return []
        anchor = pos_all[len(pos_all) // 2]
        ab = sig[anchor]
        out, start = [], 0
        buf = self.buf
        while len(out) < cap:
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
                    out.append(self.va0 + st)
            start = j + 1
        return out

    def read(self, va, n):
        off = va - self.va0
        if off < 0 or off + n > len(self.buf):
            return b""
        return self.buf[off:off + n]


def load_pe_text(path, label):
    """解析 PE, 取可执行段 (合并成一个缓冲, 因为 AOB 只在 .text 内)"""
    with open(path, "rb") as f:
        data = f.read()
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[e_lfanew:e_lfanew + 4] == b"PE\0\0", "not PE"
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    image_base = struct.unpack_from("<Q", data, opt + 24)[0] if magic == 0x20B else struct.unpack_from("<I", data, opt + 28)[0]
    secs = opt + opt_size
    for i in range(nsec):
        s = secs + i * 40
        name = data[s:s + 8].rstrip(b"\0").decode("latin1")
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, s + 8)
        chars = struct.unpack_from("<I", data, s + 36)[0]
        if chars & 0x20000000 and name == ".text":      # IMAGE_SCN_CNT_CODE
            return TextImage(label, data[raw:raw + rsize], image_base + va)
    raise RuntimeError("no .text in " + path)


def load_ms_dump(path):
    return TextImage("MS Store (dump)", open(path, "rb").read(), MS_BASE)


# ------------------------------------------------------------------ 报告
def scan_all(img, tag):
    print("")
    print("=" * 104)
    print("【%s】%s" % (tag, img.label))
    print("=" * 104)
    res = {}
    for name in NAMES:
        hits = {}
        for lvl, table in (("L1", L1), ("L2", L2), ("L3", L3)):
            if name in table:
                sig, mask = table[name]
                h = img.scan(sig, mask)
                if h:
                    hits[lvl] = h
        line = "  %-13s " % name
        if not hits:
            line += "三层全部落空"
        else:
            parts = []
            for lvl in ("L1", "L2", "L3"):
                if lvl in hits:
                    parts.append("%s x%d @%s" % (lvl, len(hits[lvl]),
                                                 " ".join("0x%X" % a for a in hits[lvl][:6]) + (" ..." if len(hits[lvl]) > 6 else "")))
            line += " | ".join(parts)
        print(line)
        res[name] = hits
    return res


def semantic(img, res, tag, expect_fontcount=None, cfg=None):
    print("")
    print("  --- 语义交叉验证 (%s) ---" % tag)

    def target(name, lvlsig, off, iend):
        hits = res.get(name, {})
        if lvlsig not in hits or len(hits[lvlsig]) != 1:
            return None
        va = hits[lvlsig][0]
        b = img.read(va, max(iend, off) + 4)
        disp = struct.unpack_from("<i", b, off)[0]
        return va, va + iend + disp

    dw = target("DRAW_WIDE", "L2", 45, 49)
    fl = res.get("FONT_LOOKUP", {}).get("L2", [])
    if dw:
        ok = fl and dw[1] in fl
        print("  [1] DRAW_WIDE 尾部 call      -> 0x%X   FONT_LOOKUP 落点=%s   %s" % (
            dw[1], ("0x%X" % fl[0]) if fl else "未命中",
            "一致 ✅" if ok else "❌ 不一致"))

    tx = target("TEXOBJ", "L2", 10, 16)
    if tx:
        t = "fontCount 期望 0x%X  " % expect_fontcount if expect_fontcount else ""
        print("  [2] TEXOBJ mov r8d,[rip+d]  -> 0x%X   %s%s" % (
            tx[1], t, ("一致 ✅" if tx[1] == expect_fontcount else "❌") if expect_fontcount else ""))

    flr = target("FONT_LOOKUP", "L2", 18, 22)
    if flr:
        print("  [3] FONT_LOOKUP cmp [rip+d]  -> 0x%X   %s%s" % (
            flr[1], ("应与 fontCount 同表: 0x%X  " % expect_fontcount) if expect_fontcount else "",
            ("一致 ✅" if flr[1] == expect_fontcount else "❌") if expect_fontcount else ""))

    lc = target("LANG_CUR", "L2", 3, 7)
    lt = target("LANG_TXT", "L2", 3, 7)
    if lc and lt:
        print("  [4] LANG_CUR singleton      -> 0x%X" % lc[1])
        print("      LANG_TXT singleton      -> 0x%X   %s" % (
            lt[1], "同一全局 ✅ (确为一对 vtable thunk)" if lc[1] == lt[1] else "❌ 不同全局"))

    # FORMAT / SUBTITLE 的 __security_cookie
    fm = None
    if res.get("FORMAT", {}).get("L3"):
        va = res["FORMAT"]["L3"][0]
        b = img.read(va, 60)
        fm = ("L3", va, None)
    f_cookie = None
    if "FORMAT" in res and "L2" in res["FORMAT"]:
        t = target("FORMAT", "L2", 30, 34)
        f_cookie = t[1] if t else None
    sb = None
    if res.get("SUBTITLE", {}).get("L3"):
        hits = res["SUBTITLE"]["L3"]
        if len(hits) == 1:
            va = hits[0]
            b = img.read(va, 34)
            disp = struct.unpack_from("<i", b, 25)[0]
            sb = va + 29 + disp
    if f_cookie and sb:
        print("  [5] FORMAT   __security_cookie -> 0x%X" % f_cookie)
        print("      SUBTITLE __security_cookie -> 0x%X   %s" % (
            sb, "同一全局 ✅" if f_cookie == sb else "❌ 不同全局 (两者不是同一编译单元的 cookie)"))
    elif sb:
        print("  [5] SUBTITLE __security_cookie -> 0x%X  (FORMAT 侧无 L2 命中, 无法对照)" % sb)


def main():
    steam = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv.exe"
    gog   = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv_gog.exe"
    epic  = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv_epic.exe"

    # CFG 声明值 (对照)
    CFG = {
    "Steam": {"DRAW_WIDE":0x140DC36A0,"FORMAT":0x140CF9A00,"FONT_LOOKUP":0x140BF8550,
              "TEXOBJ":0x140B7AF30,"SRV_RESOLVE":0x140E2C9E0,"LANG_CUR":0x140CF1980,
              "LANG_TXT":0x140CF1960,"SUBTITLE":0x1403D18F0, "fontCount":0x146AE504C},
    "GOG":   {"DRAW_WIDE":0x140D4E520,"FORMAT":0x140C88810,"FONT_LOOKUP":0x140BBBBB0,
              "TEXOBJ":0x140B99ED0,"SRV_RESOLVE":0x140DC6E00,"LANG_CUR":0x140C88120,
              "LANG_TXT":0x140C88100,"SUBTITLE":0x1404B1E00, "fontCount":0x14698A5C8},
    "EPIC":  {"DRAW_WIDE":0x140DBE570,"FORMAT":0x140CF48D0,"FONT_LOOKUP":0x140BF3D00,
              "TEXOBJ":0x140B766D0,"SRV_RESOLVE":0x140E278B0,"LANG_CUR":0x140CEC850,
              "LANG_TXT":0x140CEC830,"SUBTITLE":0x1403D0850, "fontCount":0x146ACD304},
    "MS":    {"DRAW_WIDE":0x140EF6D40,"FORMAT":0x140E92DA0,"FONT_LOOKUP":0x140E0A530,
              "TEXOBJ":0x140DEAF00,"SRV_RESOLVE":0x1411DFE30,"LANG_CUR":0x140E92770,
              "LANG_TXT":0x140E92750,"SUBTITLE":0x140488A60,"fontCount":0x146CFCDA8},
    }

    for label, path in (("Steam", steam), ("GOG", gog), ("EPIC", epic)):
        if not os.path.isfile(path):
            print("跳过 %s (文件不存在)" % label); continue
        try:
            img = load_pe_text(path, label)
            r = scan_all(img, label + " 对照")
            print("  --- 与 CFG_%s 声明值比对 ---" % label.upper())
            for name in NAMES:
                hits = r.get(name, {})
                found = None
                for lvl in ("L1", "L2", "L3"):
                    if lvl in hits and len(hits[lvl]) == 1:
                        found = hits[lvl][0]; break
                exp = CFG[label][name]
                m = "命中" if found == exp else "不匹配"
                print("    %-13s 扫描=%-16s CFG=0x%-12X %s" % (
                    name, ("0x%X" % found) if found else "多层/未命中", exp, m))
            semantic(img, r, label, CFG[label]["fontCount"])
        except Exception as e:
            print("  %s 处理失败: %s" % (label, e))

    if os.path.isfile(MS_DUMP):
        img = load_ms_dump(MS_DUMP)
        r = scan_all(img, "MS Store 段 dump")
        print("  --- 与 CFG_MSSTORE 声明值比对 ---")
        for name in NAMES:
            hits = r.get(name, {})
            found = None; lvl_used = None
            for lvl in ("L1", "L2", "L3"):
                if lvl in hits and len(hits[lvl]) == 1:
                    found = hits[lvl][0]; lvl_used = lvl; break
            exp = CFG["MS"][name]
            print("    %-13s 扫描=%-16s(%s)  CFG=0x%-12X %s" % (
                name, ("0x%X" % found) if found else "多层命中/无", lvl_used or "-", exp,
                "一致" if found == exp else ("*** 不一致 ***" if found else "无法唯一确定")))
        semantic(img, r, "MS Store", CFG["MS"]["fontCount"])
    else:
        print("MS dump 不存在: " + MS_DUMP)


if __name__ == "__main__":
    main()
