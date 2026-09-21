import os, glob, subprocess, shutil

BASE = r"I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\unpack"
SCH = os.path.join(BASE, "text", "le_data", "schinese")
OUT = os.path.join(BASE, "text", "le_data", "le_string_chs")
EXE = os.path.join(BASE, "ThomasJepp.SaintsRow.BuildStrings.exe")

os.makedirs(OUT, exist_ok=True)

si = subprocess.STARTUPINFO()
si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
si.wShowWindow = subprocess.SW_HIDE

files = sorted(glob.glob(os.path.join(SCH, "*_us.txt")))
results = []
for txt in files:
    base = os.path.basename(txt)              # activity_us.txt
    dst = os.path.join(OUT, base)
    shutil.copy2(txt, dst)
    try:
        r = subprocess.run(
            [EXE, base], cwd=OUT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            startupinfo=si, timeout=120,
        )
        rc = r.returncode
    except Exception as e:
        rc = -1
    prod = os.path.join(OUT, base[:-4] + ".le_strings")   # activity_us.le_strings
    ok = os.path.exists(prod)
    results.append((base, ok, rc))
    if os.path.exists(dst):
        os.remove(dst)   # 删除临时 txt

print("%-26s %5s %5s" % ("FILE", "OK", "RC"))
for b, ok, rc in results:
    print("%-26s %5s %5s" % (b, "YES" if ok else "NO", str(rc)))
print("=" * 40)
print("总计 %d / 成功 %d / 失败 %d" % (
    len(results), sum(1 for _, ok, _ in results if ok), sum(1 for _, ok, _ in results if not ok)))
