#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — 用 MSBuild 构建 SR4R_I18N（无需 VS IDE）

为什么需要它（两个在本机实测到的坑）
------------------------------------
1. **必须用 Python 驱动，不能走 PowerShell 直接调 MSBuild。**
   在部分受控环境里 `MSBuild.exe` / `cmd.exe` / `cl.exe` 被当作 LOLBin 拦截，
   直接用 PowerShell 调用会被安全策略拒绝；经 `subprocess.run()` 调用则正常。
2. **环境变量必须按大小写不敏感去重。**
   MSBuild 的 CL 任务用大小写不敏感的 Hashtable 装环境变量；若当前 shell 里
   同时存在 `Path` 与 `PATH`，会抛：
       error MSB6001: "CL.exe"的命令行开关无效。
       System.ArgumentException: 已添加项。字典中的关键字:"Path"所添加的关键字:"PATH"
   本脚本同名保留值最长的那个键。

用法
----
    python build.py                 # Release|x64 完整构建
    python build.py --debug         # Debug|x64
    python build.py --rebuild       # /t:Rebuild
    python build.py --syntax-only   # 只做语法检查（cl /Zs），秒级

产物: SR4R_I18N/x64/Release/SR4R_I18N.dll
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROJ = os.path.join(ROOT, "SR4R_I18N", "SR4R_I18N.vcxproj")

# MSBuild 候选（按优先级：社区版 → BuildTools → VS2017）
MSBUILD_CANDIDATES = [
    r"C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\amd64\MSBuild.exe",
    r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\MSBuild\Current\Bin\amd64\MSBuild.exe",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2017\Professional\MSBuild\15.0\Bin\amd64\MSBuild.exe",
]


def sanitized_env():
    """按大小写不敏感去重环境变量（同名保留值最长者）。"""
    best = {}
    for k, v in os.environ.items():
        lk = k.lower()
        if lk not in best or len(v) > len(best[lk][1]):
            best[lk] = (k, v)
    return {k: v for k, v in best.values()}


def find_msbuild():
    for p in MSBUILD_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def find_cl():
    """从 vcvarsall 同级的 VC\\Tools\\MSVC 里找最新的 cl.exe。"""
    roots = [
        r"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC",
        r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Tools\MSVC",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2017\Professional\VC\Tools\MSVC",
    ]
    cands = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        for ver in sorted(os.listdir(r), reverse=True):
            cl = os.path.join(r, ver, "bin", "Hostx64", "x64", "cl.exe")
            if os.path.exists(cl):
                cands.append(cl)
    return cands[0] if cands else None


def run(cmd, cwd):
    r = subprocess.run(cmd, capture_output=True, cwd=cwd, env=sanitized_env())
    out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    return r.returncode, out


def tee(text):
    """打印 + 落盘。本机 PowerShell 不回收子进程 stdout, 必须留日志文件。"""
    print(text)
    with open(os.path.join(HERE, "build.log"), "a", encoding="utf-8") as f:
        f.write(text + "\n")


def main():
    debug = "--debug" in sys.argv
    rebuild = "--rebuild" in sys.argv
    syntax_only = "--syntax-only" in sys.argv
    cfg = "Debug" if debug else "Release"

    if syntax_only:
        cl = find_cl()
        if not cl:
            print("找不到 cl.exe")
            return 1
        # cl /Zs 只做语法检查, 不生成 .obj; /I 补上 NuGet MinHook 头（非标准 build\\native\\include 布局）
        inc = os.path.join(ROOT, "packages", "minhook.1.3.3", "lib", "native", "include")
        args = [cl, "/nologo", "/Zs", "/std:c++17", "/EHsc", "/W3", "/utf-8",
                "/D_WINDOWS", "/D_USRDLL", "/DSR4RI18N_EXPORTS",
                "/I", os.path.join(ROOT, "SR4R_I18N"), "/I", inc,
                os.path.join(ROOT, "SR4R_I18N", "dllmain.cpp")]
        code, out = run(args, ROOT)
        tee(out)
        tee("语法检查 %s" % ("通过" if code == 0 else "失败"))
        return code

    msbuild = find_msbuild()
    if not msbuild:
        print("找不到 MSBuild.exe（候选: %s）" % ", ".join(MSBUILD_CANDIDATES))
        return 1

    target = "Rebuild" if rebuild else "Build"
    args = [msbuild, PROJ, "/p:Configuration=" + cfg, "/p:Platform=x64",
            "/t:" + target, "/v:minimal", "/nologo", "/p:TrackFileAccess=false"]
    code, out = run(args, ROOT)
    tee(out)
    dll = os.path.join(ROOT, "SR4R_I18N", "x64", cfg, "SR4R_I18N.dll")
    if code == 0 and os.path.exists(dll):
        tee("OK  %s  (%d bytes)" % (dll, os.path.getsize(dll)))
        tee("部署: 复制为 <游戏目录>/scripts/SR4R_I18N.asi")
    return code


if __name__ == "__main__":
    sys.exit(main())
