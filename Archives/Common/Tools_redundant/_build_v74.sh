#!/usr/bin/env bash
# v7.4 一次性编译脚本: cl.exe 命令行构建 Release|x64 (v141 /MT /std:c++17)
# 等价于 VS Release|x64 配置; 产物写到 x64/Release/SR3R_I18N.dll
set -e
export MSYS2_ARG_CONV_EXCL='*'

PROJ="G:/Projects/SR3R_DLL/SR3R_I18N"
BT="$PROJ/.build_tmp"
mkdir -p "$BT"

CL="/c/Program Files (x86)/Microsoft Visual Studio/2017/Professional/VC/Tools/MSVC/14.16.27023/bin/Hostx64/x64/cl.exe"
MSVC="C:\\Program Files (x86)\\Microsoft Visual Studio\\2017\\Professional\\VC\\Tools\\MSVC\\14.16.27023"
SDK="C:\\Program Files (x86)\\Windows Kits\\10"
MH="C:\\Users\\haojun0823\\.nuget\\packages\\minhook\\1.3.3\\lib\\native"

echo "[1/2] compiling dllmain.cpp (PCH off, /MT, C++17)..."
"$CL" /nologo /LD /EHsc /Y- /O2 /utf-8 /MT /std:c++17 /Z7 \
  /D NDEBUG /D SR3RI18N_EXPORTS /D _WINDOWS /D _USRDLL /D _CRT_SECURE_NO_WARNINGS \
  /I"$PROJ" \
  "/I$MSVC\\include" \
  "/I$SDK\\Include\\10.0.19041.0\\ucrt" \
  "/I$SDK\\Include\\10.0.19041.0\\um" \
  "/I$SDK\\Include\\10.0.19041.0\\shared" \
  "/I$MH\\include" \
  /Fo"$BT\\" /Fd"$BT\\" \
  "$PROJ/dllmain.cpp" \
  /link /OUT:"$PROJ/x64/Release/SR3R_I18N.dll" /SUBSYSTEM:WINDOWS \
  "/LIBPATH:$MSVC\\lib\\x64" \
  "/LIBPATH:$SDK\\Lib\\10.0.19041.0\\ucrt\\x64" \
  "/LIBPATH:$SDK\\Lib\\10.0.19041.0\\um\\x64" \
  "/LIBPATH:$MH\\lib" \
  libMinHook-x64-v141-mt.lib kernel32.lib user32.lib advapi32.lib ole32.lib shell32.lib

echo "[2/2] done:"
ls -la "$PROJ/x64/Release/SR3R_I18N.dll"
