# -*- coding: utf-8 -*-
"""验证构建产物里确实包含 L3 特征码字节 + 复核 .inc 与 dllmain 的绑定。"""
import os, re
ROOT = r"G:\Projects\SR4R_DLL"
DLL = os.path.join(ROOT, r"SR4R_I18N\x64\Release\SR4R_I18N.dll")
INC = os.path.join(ROOT, r"SR4R_I18N\aob_l2_arrays.inc")
DUMP = os.path.join(ROOT, r".temp\dump_ms\SR4R_dump_text.bin")
OUT = os.path.join(ROOT, r"Tools\_verify_out.txt")
DUMP_VA = 0x140001000

def arr(src, name):
    m = re.search(r"\b" + name + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", src, re.S)
    return bytes(int(t, 0) & 0xFF for t in (x.strip() for x in m.group(1).replace("\n", " ").split(",")) if t)

inc = open(INC, encoding="utf-8").read()
dll = open(DLL, "rb").read()
dmp = open(DUMP, "rb").read()

w = []
w.append("构建产物: %s (%d 字节)" % (DLL, len(dll)))
w.append("")
for nm, va in (("R3_FORMAT", 0x140E92DA0), ("R3_SUBTITLE", 0x140488A60)):
    sig = arr(inc, nm)
    in_dll = dll.find(sig)
    in_dump = dmp.find(sig)
    w.append("%-12s %d 字节" % (nm, len(sig)))
    w.append("   字节: " + " ".join("%02X" % b for b in sig))
    w.append("   出现在 DLL 产物里 : %s" % (hex(in_dll) if in_dll >= 0 else "!! 未找到"))
    w.append("   出现在 MS dump 里  : %s  (期望 %s)" %
             (hex(DUMP_VA + in_dump) if in_dump >= 0 else "!! 未找到", hex(va)))
    w.append("")
    # dump 内的唯一性(与生成器同口径)
    cnt = 0; p = dmp.find(sig)
    while p >= 0:
        cnt += 1; p = dmp.find(sig, p + 1)
    w.append("   draft 内唯一命中数 = %d (必须为 1)" % cnt)
    w.append("")

# L2/L3 数组名与 dllmain 引用一致性
main = open(os.path.join(ROOT, r"SR4R_I18N\dllmain.cpp"), encoding="utf-8").read()
w.append("dllmain 引用检查:")
for ref in re.findall(r"AOB_ENTRY\([^;]*;", main):
    w.append("   " + " ".join(ref.split()))
w.append("")
w.append("inc 里定义的数组: " + ", ".join(sorted(set(re.findall(r"static const uint8_t (\w+)\[", inc)))))
open(OUT, "w", encoding="utf-8").write("\n".join(w) + "\n")
print("ok")
