# -*- coding: utf-8 -*-
"""MS Store dump 离线取证：验证 VA 映射 + 定位 Format / Subtitle 真身。"""
import re, sys, io, os

ROOT = r"G:\Projects\SR4R_DLL"
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
STEAM = r"I:\SteamLibrary\steamapps\common\Saints Row IV\sr_hv.exe"
INC = os.path.join(ROOT, r"SR4R_I18N\aob_l2_arrays.inc")
OUT = os.path.join(ROOT, r"Tools\_probe_out.txt")

DUMP_VA = 0x140001000
dump = open(DUMP, "rb").read()

# ---------- L1 特征码（与 dllmain.cpp 同步） ----------
SIGS = {
  "DRAW_WIDE": ("40 53 56 57 48 81 EC 10 01 00 00 8B BC 24 60 01 00 00 49 8B D9 0F 29 B4 24 F0 00 00 00 8B CF 44 0F 29 74 24 70 0F 28 F2 44 0F 28 F1 ?? ?? ?? ??",
                "1 " * 44 + "0 0 0 0", 0x140EF6D40),
  "FORMAT":    ("40 55 56 57 41 57 48 8D AC 24 78 D0 FF FF B8 88", None, None),
  "FONT_LOOKUP": ("83 F9 FF 7D 3E 8D 81 FF FF FF 7F 83 F8 FF 7E 2E", None, 0x140E0A530),
  "TEXOBJ":    ("4C 63 C1 85 C9 78 77 44 3B 05 ?? ?? ?? ?? 7D 6E", None, 0x140DEAF00),
  "SRV_RESOLVE": ("40 53 48 83 EC 20 0F B6 DA 83 F9 FF 74 4F 0F BA", None, 0x1411DFE30),
  "LANG_CUR":  ("48 8B 05 ?? ?? ?? ?? 48 85 C0 74 0C 48 8B 50 08 48 85 D2 74 03 48 FF E2 33 C0 C3", None, 0x140E92770),
  "LANG_TXT":  ("48 8B 05 ?? ?? ?? ?? 48 85 C0 74 0B 48 8B 10 48", None, 0x140E92750),
  "SUBTITLE":  ("4C 8B DC 55 56 41 54 49 8D AB A8 FB FF FF 48 81", None, None),
}

# mask 表：从 dllmain 源码里的手写 mask 推断（默认全 1，'??' 即 0）
def parse_pat(txt):
    toks = txt.split()
    b = bytes(int(t, 16) if t != "??" else 0 for t in toks)
    m = bytes(0 if t == "??" else 1 for t in toks)
    return b, m

def find(b, m):
    rx = re.compile(b"".join(re.escape(bytes([c])) if mm else b"." for c, mm in zip(b, m)), re.DOTALL)
    return [mo.start() for mo in rx.finditer(dump)]

def load_l2():
    src = open(INC, "r", encoding="utf-8").read()
    out = {}
    for name, arr in re.findall(r"static const uint8_t (R2_\w+)\[(\d+)\]", src):
        pass
    # 直接抓数组体
    for mo in re.finditer(r"static const uint8_t (R2_\w+|RM_\w+)\[(\d+)\]\s*=\s*\{([^}]*)\}", src):
        nm, ln, body = mo.group(1), int(mo.group(2)), mo.group(3)
        vals = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", body)]
        out[nm] = (vals, ln)
    return out

L2 = load_l2()

def l2_of(key):
    a = L2["R2_" + key][0]
    m = L2["RM_" + key][0]
    return bytes(a), bytes(m)

# ---------- PE 读取（Steam 参照） ----------
class PE:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        e = self.d.index(b"PE\0\0")
        nsec = int.from_bytes(self.d[e+6:e+8], "little")
        optsz = int.from_bytes(self.d[e+20:e+22], "little")
        self.base = int.from_bytes(self.d[e+24+24:e+24+32], "little")
        sec = e + 24 + optsz
        self.secs = []
        for i in range(nsec):
            o = sec + i * 40
            nm = self.d[o:o+8].rstrip(b"\0").decode("latin1")
            vsize = int.from_bytes(self.d[o+8:o+12], "little")
            va = self.base + int.from_bytes(self.d[o+12:o+16], "little")
            rsize = int.from_bytes(self.d[o+16:o+20], "little")
            roff = int.from_bytes(self.d[o+20:o+24], "little")
            ch = int.from_bytes(self.d[o+36:o+40], "little")
            self.secs.append((nm, va, vsize, roff, rsize, ch))
    def off(self, va):
        for nm, sva, vs, roff, rs, ch in self.secs:
            if ch & 0x20000000 and sva <= va < sva + vs:
                return roff + (va - sva)
        return None
    def at(self, va, n):
        o = self.off(va)
        return self.d[o:o+n] if o is not None else None

MD = None
def dis(buf, va, n=110):
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
    global MD
    if MD is None:
        MD = Cs(CS_ARCH_X86, CS_MODE_64); MD.detail = True
    lines = []
    for i in MD.disasm(buf, va):
        if i.address - va >= n:
            break
        lines.append((i.address - va, i.size, i.mnemonic, i.op_str))
    return lines

def fmt_dis(lines, total):
    out = []
    for off, size, mn, ops in lines:
        out.append("    +%03X  %-30s %s" % (off, mn, ops))
    return out

def main():
    w = []
    w.append("=========== A) 6 条已知命中 hook：dump 内唯一性 + VA 校验 ===========")
    for k, (p, mk, exp) in SIGS.items():
        b, m = parse_pat(p)
        hs = find(b, m)
        vas = [hex(DUMP_VA + h) for h in hs[:6]]
        ok = ""
        if exp is not None:
            ok = "OK" if len(hs) == 1 and DUMP_VA + hs[0] == exp else "!! exp=" + hex(exp)
        w.append("%-12s L1 hits=%-3d %s  %s" % (k, len(hs), ok, vas))
        if k in ("FORMAT", "SUBTITLE"):
            a, mm = l2_of(k)
            hs2 = find(a, mm)
            w.append("%-12s L2 hits=%-3d      wc=%d/%d  %s" % (k, len(hs2), mm.count(0), len(mm), [hex(DUMP_VA + h) for h in hs2[:6]]))

    w.append("")
    w.append("=========== B) 结构探针（找 prologue 各片段的真实分布） ===========")
    probes = {
        "Fmt-head 40 55 56 57 41 57 48 8D AC 24": "40 55 56 57 41 57 48 8D AC 24",
        "Fmt-subrsp-rax 48 2B E0 48 8B 05": "48 2B E0 48 8B 05",
        "Fmt-xorraxrsp 48 33 C4": "48 33 C4",
        "Fmt-call 47 41 57 48 8D AC 24": "41 57 48 8D AC 24",
        "lea rbp,[rsp+d32]  48 8D AC 24": "48 8D AC 24",
        "Sub-head 4C 8B DC 55 56 41 54": "4C 8B DC 55 56 41 54",
        "Sub-lea rbp,[r11] 49 8D AB": "49 8D AB",
        "mov r11,rsp 4C 8B DC": "4C 8B DC",
        "B8+E8 chkstk call B8 ?? ?? ?? ?? E8": "B8 ?? ?? ?? ?? E8",
    }
    for label, p in probes.items():
        b, m = parse_pat(p)
        hs = find(b, m)
        w.append("%-42s hits=%-5d  %s" % (label, len(hs), [hex(DUMP_VA + h) for h in hs[:8]]))

    w.append("")
    w.append("=========== C) Steam 参照：Format / Subtitle 入口 ===========")
    steam = PE(STEAM)
    for label, va, n in (("Steam FORMAT", 0x140CF9A00, 96), ("Steam SUBTITLE", 0x1403D18F0, 96)):
        buf = steam.at(va, n)
        w.append("-- %s @%s" % (label, hex(va)))
        w.append("   hex: " + " ".join("%02X" % c for c in buf))
        w += fmt_dis(dis(buf, va, n), n)

    open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
    print("report ->", OUT)

main()
