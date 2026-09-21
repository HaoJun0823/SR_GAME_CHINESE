#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_release.py — {game}_{version}_{lang} 一键发布构建编排
================================================================================
按 `github_action流程.txt` 定义的 8 步流程，把仓库里的源材料装配成可直接分发的
`release/{game}_{version}/` 目录树。

命名体系
    game    : sr3r | sr4r
    version : common | microsoft          (microsoft 目前仅 sr4r 有)
    lang    : chs                          (目前仅 CHS)

    → 输出目录 release/{game}_{version}/  例如 release/sr3r_common/

关于 lang（与规格 `{game}_{version}_{lang}` 的关系）
    规格里 lang 是命名组成部分，但目录树只取 `{game}_{version}`。
    因为 lang 决定「装什么内容」（Resource 下取哪个语言子目录、产出哪些
    `_zh`/`_us` 后缀），而 game/version 决定「装给哪个游戏构建」——两者正交。
    当前 lang 恒为 CHS；将来增加语言时只需 `--lang` 换源目录即可，
    目录树不必随之膨胀（不同语言的包本来就要分开分发）。

流程（对应 github_action流程.txt 的 8 条）
    1. 编译 x64 DLL（vc_141 工具集）→ release/{gv}/scripts/{Name}.asi
    2. 复制 Resource/{game}/{LANG}/dict/*.txt → release/{gv}/scripts/dict/
    3. 用 le_string py 脚本构建 Resource/{game}/{LANG}/le_string/*.txt
       （模板取 data/{game}/{version}/misc/*.le_string）→ release/{gv}/update/
    4. 生成 charlist.txt（基于 Resource/{game}/{LANG}/ 全部 txt）→ release/{gv}/scripts/
    5. 复制 Fonts/ 下的字体 → release/{gv}/scripts/
    6. 复制 dist/common/winmm.dll 与 必读说明.txt → release/{gv}/
    7. 复制 dist/{game}/scripts/*.ini → release/{gv}/scripts/
    8. 合并 License/ 下全部 txt → release/{gv}/License.txt

用法
    python build_release.py                      # 全部：sr3r_common / sr4r_common / sr4r_microsoft
    python build_release.py --target sr3r_common # 只构建一个
    python build_release.py --skip-dll           # 跳过 DLL 编译（复用已有产物，快速迭代资源）
    python build_release.py --dll-only           # 只编译 DLL
    python build_release.py --out-root D:\\rel    # 换输出根目录

退出码: 0=全部成功; 1=任一步失败（供 CI 判定）。
"""
import os
import sys
import glob
import shutil
import argparse
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'Tools'))
sys.path.insert(0, os.path.join(ROOT, 'Projects', 'SR4', 'Tools'))
sys.path.insert(0, os.path.join(ROOT, 'Projects', 'SR3', 'Tools'))

import build_charlist
import build_release_le_strings as le_build

DEFAULT_LANG = 'CHS'

# ── 构建矩阵 ────────────────────────────────────────────────────────────────
# target             game  version     dll 工程（相对 ROOT）                                         asi 名            ini 名           注释前缀
TARGETS = {
    'sr3r_common': dict(
        game='SR3', version='common',
        proj='Projects/SR3/SR3R_I18N/SR3R_I18N.vcxproj',
        asi='SR3R_I18N.asi', ini='SR3R_I18N.ini', prefix='SR3'),
    'sr4r_common': dict(
        game='SR4', version='common',
        proj='Projects/SR4/SR4R_I18N/SR4R_I18N.vcxproj',
        asi='SR4R_I18N.asi', ini='SR4R_I18N.ini', prefix='SR4'),
    'sr4r_microsoft': dict(
        game='SR4', version='microsoft',
        proj='Projects/SR4/SR4R_I18N/SR4R_I18N.vcxproj',
        asi='SR4R_I18N.asi', ini='SR4R_I18N.ini', prefix='SR4'),
}

# 工具集：**显式指定 v141**（不靠 vcxproj 声明兜底）。
#
# ★★ 2026-09-21 最终结论（CI 实测驱动）：
#   v141 不是偏好，是 **MinHook 1.3.3 的硬约束**：
#     · 依赖包 `packages/minhook.1.3.3/build/native/minhook.targets` 里，工具集
#       只列到 v141（v90/v100/v110/v120/v140/v141）；
#     · 而 `.nupkg` 内**不含** libMinHook.lib —— 该文件靠 targets 按
#       $(PlatformToolset) 前缀匹配后 <Copy> 生成。
#     · v142/v143/v145 全部匹配不上 → MH_ToolSet 为空 → MH_LibSuffix 变
#       成 "x64--md" → 58 条 Copy 条件无一命中 → `lib\MinHook.lib` 不存在
#       → **LNK1104 无法打开文件 "libMinHook.lib"**。
#   所以两个工程（SR3/SR4，含 Win32/x64 四个配置）已统一改成 v141。
#
#   ⚠️ 与 MSB8020 的区别：MSB8020 = runner 缺该工具集（装组件可解）；
#      LNK1104 = 依赖包不支持该工具集（装什么都没用，只能回退 v141）。
#
#   历史上 SR4 工程写 v145 能编过，是因为本机 packages 目录里**残留**了上一次
#   v141 构建 Copy 出来的 libMinHook.lib（SHA256 与 libMinHook-x64-v141-mt.lib
#   完全一致）—— 属于「蹭到」而非「支持」。CI 全新 clone + NuGet 还原后
#   该文件不存在，故 v145 在 CI 必挂。
#
#   这里显式传 /p:PlatformToolset 是为了「即使有人误改 vcxproj 也编不出错东西」，
#   需要临时试验其它工具集时用环境变量覆盖（但注意 MinHook 会链接失败）：
#
#       SR_I18N_TOOLSET=v143   python build_release.py
#
TOOLSET = os.environ.get('SR_I18N_TOOLSET', 'v141').strip()

# 命令行构建必须显式指定 Windows SDK 版本。
#   原因：vcxproj 里写 `<WindowsTargetPlatformVersion>10.0</...>`（=「最新」）时，
#   VS IDE 能自己解析出具体版本，但 MSBuild 命令行 + 旧工具集组合下会直接报
#   MSB8036「找不到 Windows SDK 版本10.0」。SR3 的工程本来就写死了 10.0.19041.0，
#   所以只有 SR4 会踩到。这里统一显式传入，**不改 vcxproj**（IDE 里照常可用）。
#   ── 2026-09-21 放宽：不再写死 10.0.19041.0，而是**探测本机已安装的 SDK**，
#      取 19041 优先，否则退到可用的最高版本。CI runner 上不一定装了 19041，
#      写死会让 SR4 报 MSB8036。可用 SR_I18N_WINSDK 覆盖。
#      （实测 windows-2022 镜像装了 19041，见官方 Windows2022-Readme「Installed
#        Windows SDKs」：10.0.17763.0 / 10.0.19041.0 / 10.0.22621.0 / 10.0.26100.0）
WINSDK_VERSION = os.environ.get('SR_I18N_WINSDK', '').strip() or None


def find_winsdk(prefer='10.0.19041.0'):
    """探测已安装的 Windows SDK 版本（返回 prefer，否则可用的最高版本）。"""
    roots = [r for r in (os.environ.get('ProgramFiles(x86)'),
                         os.environ.get('ProgramFiles')) if r]
    found = set()
    for root in roots:
        base = os.path.join(root, 'Windows Kits', '10', 'Include')
        if os.path.isdir(base):
            for d in os.listdir(base):
                if d[:2].isdigit() and os.path.isfile(
                        os.path.join(base, d, 'um', 'windows.h')):
                    found.add(d)
    if not found:
        return prefer
    if prefer in found:
        return prefer

    def key(v):
        try:
            return tuple(int(x) for x in v.split('.'))
        except ValueError:
            return (0,)
    return sorted(found, key=key)[-1]



def p(*a):
    print(*a, flush=True)


# ══════════════════════════════════════════════════════════════════════════
# 环境 / 通用工具
# ══════════════════════════════════════════════════════════════════════════
def find_msbuild():
    """定位可用的 MSBuild.exe（优先 amd64）。"""
    pats = []
    for root in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')):
        if not root:
            continue
        vs = os.path.join(root, 'Microsoft Visual Studio')
        if not os.path.isdir(vs):
            continue
        pats += glob.glob(os.path.join(vs, '*', '*', 'MSBuild', 'Current', 'Bin', 'amd64', 'MSBuild.exe'))
        pats += glob.glob(os.path.join(vs, '*', '*', 'MSBuild', 'Current', 'Bin', 'MSBuild.exe'))
    pats = sorted(set(pats))
    amd = [x for x in pats if 'amd64' in x]
    return (amd or pats)[0] if pats else None


def clean_env():
    """PATH 去重（大小写不敏感）——Windows 上重名项会让 MSBuild 子进程环境出错。"""
    seen, out = set(), []
    for seg in os.environ.get('PATH', '').split(os.pathsep):
        k = seg.strip().lower()
        if not seg or k in seen:
            continue
        seen.add(k)
        out.append(seg)
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join(out)
    return env


def copy_files(src_dir, dst_dir, exts=None):
    """复制目录下匹配的文件（不递归）。返回复制数量。"""
    if not os.path.isdir(src_dir):
        return 0
    os.makedirs(dst_dir, exist_ok=True)
    n = 0
    for f in sorted(os.listdir(src_dir)):
        sp = os.path.join(src_dir, f)
        if not os.path.isfile(sp):
            continue
        if exts and not f.lower().endswith(tuple(exts)):
            continue
        shutil.copy2(sp, os.path.join(dst_dir, f))
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════════════
# 步骤 1: 编译 DLL
# ══════════════════════════════════════════════════════════════════════════
def find_nuget():
    """定位 NuGet.exe（VS 自带优先，其次 PATH）。找不到返回 None。"""
    pats = []
    for root in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')):
        if not root:
            continue
        base = os.path.join(root, 'Microsoft Visual Studio')
        if not os.path.isdir(base):
            continue
        for ed in ('Enterprise', 'Professional', 'Community', 'BuildTools'):
            pats.append(os.path.join(base, '2022', ed, 'Common7', 'IDE',
                                     'CommonExtensions', 'Microsoft', 'NuGet', 'NuGet.exe'))
        pats += glob.glob(os.path.join(base, '**', 'NuGet.exe'), recursive=True)
    for c in pats:
        if os.path.isfile(c):
            return c
    return shutil.which('nuget')


def ensure_nuget(proj):
    """校验 minhook 包已还原；缺失时尝试用 NuGet.exe 自动还原。

    vcxproj 里 Import 了 ..\\packages\\minhook.1.3.3\\build\\native\\minhook.targets，
    而 Projects/**/packages/ 在 .gitignore 里 —— 全新 clone 后必须先还原，
    否则 EnsureNuGetPackageBuildImports 会硬报错。
    """
    pkg_dir = os.path.normpath(os.path.join(os.path.dirname(proj), '..', 'packages'))
    targets = os.path.join(pkg_dir, 'minhook.1.3.3', 'build', 'native', 'minhook.targets')
    if os.path.isfile(targets):
        return

    # ── 尝试自动还原 ──────────────────────────────────────────────────────
    cfg = os.path.join(os.path.dirname(proj), 'packages.config')
    nuget = find_nuget() if os.path.isfile(cfg) else None
    if nuget:
        p(f'  还原 NuGet 包（{os.path.relpath(cfg, ROOT)}）…')
        r = subprocess.run([nuget, 'restore', cfg,
                            '-PackagesDirectory', pkg_dir, '-NonInteractive'],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=ROOT, timeout=600)
        if r.returncode == 0 and os.path.isfile(targets):
            p(f'  已还原 minhook.1.3.3 -> {os.path.relpath(pkg_dir, ROOT)}')
            return
        note = '\n'.join((r.stdout or '').splitlines()[-6:])
        p(f'  自动还原未成功（rc={r.returncode}）：{note}')

    raise RuntimeError(
        f'缺少 NuGet 包 minhook.1.3.3：{targets} 不存在。\n'
        f'       请在 VS 中打开 {os.path.relpath(proj, ROOT)} 触发自动还原，'
        f'或手工放置 packages/minhook.1.3.3/。')


def compile_dll(cfg, msbuild):
    """编译 x64 Release DLL，返回产物 .dll 绝对路径。

    工具集：显式传 /p:PlatformToolset（默认 v141，见 TOOLSET 的注释 —— 这是
    MinHook 1.3.3 的硬约束，不是偏好）。vcxproj 里四个配置也都已改成 v141。
    SDK：显式传入探测到的版本（见 find_winsdk），避免 MSB8036。
    """
    proj = os.path.join(ROOT, cfg['proj'])
    if not os.path.isfile(proj):
        raise RuntimeError(f'找不到工程文件 {proj}')
    ensure_nuget(proj)

    sdk = WINSDK_VERSION or find_winsdk()
    cmd = [msbuild, proj,
           '/p:Configuration=Release', '/p:Platform=x64',
           f'/p:WindowsTargetPlatformVersion={sdk}',
           '/t:Rebuild', '/v:minimal', '/nologo']
    if TOOLSET:
        cmd.insert(4, f'/p:PlatformToolset={TOOLSET}')
    p(f'  msbuild {os.path.relpath(proj, ROOT)}  '
      f'(Release|x64, PlatformToolset={TOOLSET or "vcxproj 默认"}, SDK={sdk})')
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace',
                       env=clean_env(), cwd=ROOT, timeout=1800)
    if r.returncode != 0:
        tail = '\n'.join((r.stdout or '').splitlines()[-40:])
        err = '\n'.join((r.stderr or '').splitlines()[-20:])
        raise RuntimeError(f'MSBuild 失败 (rc={r.returncode}):\n{tail}\n--- stderr ---\n{err}')

    stem = os.path.splitext(os.path.basename(cfg['proj']))[0]
    out_dll = os.path.join(os.path.dirname(proj), 'x64', 'Release', f'{stem}.dll')
    if not os.path.isfile(out_dll):
        cands = set(glob.glob(os.path.join(os.path.dirname(proj), '**', 'Release', '*.dll'),
                              recursive=True))
        if not cands:
            raise RuntimeError(f'编译成功但找不到产物 dll（期望 {out_dll}）')
        out_dll = max(cands, key=os.path.getmtime)

    # 归一化为绝对路径并立即固化校验：
    #   dll_cache 里的路径会被后续 target 复用（sr4r_common → sr4r_microsoft 共用同一
    #   vcxproj），一旦缓存的是相对路径或大小写/分隔符不一致的写法，copy2 就会抛
    #   裸的 [WinError 2]。在这里一次性规范到 abspath 并断言文件存在。
    out_dll = os.path.abspath(out_dll)
    if not os.path.isfile(out_dll):
        raise RuntimeError(f'编译产物路径失效：{out_dll}')
    return out_dll


def find_existing_dll(cfg):
    """--skip-dll 模式下复用工程现有的 dll 产物。"""
    proj_dir = os.path.dirname(os.path.join(ROOT, cfg['proj']))
    stem = os.path.splitext(os.path.basename(cfg['proj']))[0]
    cands = set(glob.glob(os.path.join(proj_dir, 'x64', 'Release', f'{stem}.dll')))
    cands |= set(glob.glob(os.path.join(proj_dir, '**', 'Release', '*.dll'), recursive=True))
    if not cands:
        raise RuntimeError(
            f'--skip-dll 但找不到已编译的 dll（请先不带 --skip-dll 跑一次）：{proj_dir}')
    return os.path.abspath(max(cands, key=os.path.getmtime))


# ══════════════════════════════════════════════════════════════════════════
# 步骤 1~8
# ══════════════════════════════════════════════════════════════════════════
def step1_asi(cfg, out_root, dll_src):
    """1. DLL → release/{gv}/scripts/{Name}.asi"""
    dst = os.path.join(out_root, cfg['rel'], 'scripts', cfg['asi'])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # 显式校验，避免 copy2 只抛裸的 [WinError 2] 让人无从下手
    if not os.path.isfile(dll_src):
        raise RuntimeError(
            f'DLL 源文件不存在：{dll_src}\n'
            f'       （缓存/回退路径已失效，请重跑或使用 --skip-dll 前先完整编译一次）')
    shutil.copy2(dll_src, dst)
    p(f'  scripts\\{cfg["asi"]}  ({os.path.getsize(dst):,} B)')
    return 1


def step2_dict(cfg, out_root, lang):
    """2. Resource/{game}/{LANG}/dict/*.txt → release/{gv}/scripts/dict/"""
    src = os.path.join(ROOT, 'Resource', cfg['game'], lang, 'dict')
    dst = os.path.join(out_root, cfg['rel'], 'scripts', 'dict')
    n = copy_files(src, dst, exts=['.txt'])
    if n == 0:
        raise RuntimeError(f'词典目录为空或不存在：{src}')
    p(f'  scripts\\dict\\  {n} 个 txt')
    return n


def step3_le_strings(cfg, out_root, lang):
    """3. le_string 构建 → release/{gv}/update/"""
    up = os.path.join(out_root, cfg['rel'], 'update')
    done, fails = le_build.build_game(cfg['game'], cfg['version'],
                                     suffixes=('zh', 'us'), out_dir=up, quiet=True)
    if fails:
        raise RuntimeError(f'le_string 构建失败 {len(fails)} 项：{fails[:3]}')
    n = len([f for f in os.listdir(up) if f.endswith('.le_strings')]) if os.path.isdir(up) else 0
    if n == 0:
        raise RuntimeError(f'update/ 未产出任何 .le_strings：{up}')
    p(f'  update\\  {n} 个 .le_strings（{done} 张表 × _zh/_us）')
    return n


def step4_charlist(cfg, out_root, lang):
    """4. charlist.txt → release/{gv}/scripts/charlist.txt"""
    dst = os.path.join(out_root, cfg['rel'], 'scripts', 'charlist.txt')
    tag = f'{cfg["prefix"]}R_I18N charset list v1 (build_release.py)'
    n, _stats = build_charlist.build(cfg['game'], lang, dst, tag)
    p(f'  scripts\\charlist.txt  {n} 个非 ASCII 字符')
    return n


def step5_fonts(cfg, out_root):
    """5. Fonts/ → release/{gv}/scripts/"""
    dst = os.path.join(out_root, cfg['rel'], 'scripts')
    n = copy_files(os.path.join(ROOT, 'Fonts'), dst, exts=['.ttf', '.otf'])
    if n == 0:
        raise RuntimeError(f'Fonts/ 下没有字体文件：{os.path.join(ROOT, "Fonts")}')
    fonts = [f for f in sorted(os.listdir(dst)) if f.lower().endswith(('.ttf', '.otf'))]
    p(f'  scripts\\  {n} 个字体：{", ".join(fonts)}')
    return n


def step6_loader(cfg, out_root):
    """6. dist/common/winmm.dll + 必读说明.txt → release/{gv}/"""
    common = os.path.join(ROOT, 'dist', 'common')
    dst = os.path.join(out_root, cfg['rel'])
    os.makedirs(dst, exist_ok=True)
    got = []
    for f in ('winmm.dll', '必读说明.txt'):
        sp = os.path.join(common, f)
        if not os.path.exists(sp):
            raise RuntimeError(f'缺少 {sp}（ASI Loader / 说明文件）')
        sz = os.path.getsize(sp)
        if f.endswith('.dll') and sz < 100 * 1024:
            raise RuntimeError(f'{sp} 体积仅 {sz} B，不像真实 ASI Loader')
        shutil.copy2(sp, os.path.join(dst, f))
        got.append(f'{f}({sz:,}B)')
    p('  ' + ', '.join(got))
    return len(got)


def step7_ini(cfg, out_root):
    """7. dist/{game}/scripts/*.ini → release/{gv}/scripts/"""
    src = os.path.join(ROOT, 'dist', cfg['game'], 'scripts')
    dst = os.path.join(out_root, cfg['rel'], 'scripts')
    n = copy_files(src, dst, exts=['.ini'])
    if n == 0:
        raise RuntimeError(f'缺少 ini：{src}')
    inis = [f for f in sorted(os.listdir(dst)) if f.lower().endswith('.ini')]
    # DLL 按「自身模块名.ini」查找配置，故 ini 必须与 asi 同基名
    if cfg['ini'] not in inis:
        raise RuntimeError(f'ini 名不匹配：期望 {cfg["ini"]}（与 {cfg["asi"]} 同基名），实际 {inis}')
    p(f'  scripts\\{cfg["ini"]}')
    return n


LICENSE_ORDER = ['ASILoader.txt', 'MinHook.txt', 'stb.txt', 'source-han-serif.txt', 'GPLv3.txt']


def step8_license(cfg, out_root):
    """8. 合并 License/ 下全部 txt → release/{gv}/License.txt"""
    src = os.path.join(ROOT, 'License')
    files = sorted(f for f in os.listdir(src) if f.lower().endswith('.txt')) \
        if os.path.isdir(src) else []
    if not files:
        raise RuntimeError(f'License/ 下没有 txt：{src}')
    known = [f for f in LICENSE_ORDER if f in files]
    rest = [f for f in files if f not in LICENSE_ORDER]

    bar = '=' * 78
    parts = []
    for f in known + rest:
        t = open(os.path.join(src, f), 'r', encoding='utf-8-sig', errors='replace').read().strip()
        parts.append(f'{bar}\n== {f}\n{bar}\n\n{t}\n')
    text = '\n\n'.join(parts) + '\n'

    dst = os.path.join(out_root, cfg['rel'], 'License.txt')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    p(f'  License.txt  合并 {len(files)} 份：{", ".join(known + rest)}')
    return len(files)


STEPS = [
    ('编译 DLL -> scripts\\{asi}', step1_asi),
    ('复制词典 -> scripts\\dict\\', step2_dict),
    ('构建 le_string -> update\\', step3_le_strings),
    ('生成 charlist.txt -> scripts\\', step4_charlist),
    ('复制字体 -> scripts\\', step5_fonts),
    ('复制 ASI Loader + 说明 -> 根', step6_loader),
    ('复制 ini -> scripts\\', step7_ini),
    ('合并 License -> License.txt', step8_license),
]


# ══════════════════════════════════════════════════════════════════════════
def build_target(name, out_root, lang, skip_dll, dll_only, msbuild, dll_cache):
    """执行单个 target 的全部步骤。返回 (ok, fails)。"""
    cfg = dict(TARGETS[name])
    cfg['rel'] = name          # 目录名 == target 名 == {game}_{version}

    p('')
    p('#' * 74)
    p(f'# {name}   game={cfg["game"]}  version={cfg["version"]}  lang={lang}')
    p('#' * 74)

    fails = []
    try:
        # ── 步骤 1：DLL ────────────────────────────────────────────────────
        if skip_dll:
            dll_src = dll_cache.get(cfg['proj']) or find_existing_dll(cfg)
            p(f'[跳过] 编译 DLL（复用 {os.path.relpath(dll_src, ROOT)}）')
        else:
            if cfg['proj'] not in dll_cache:
                p('[1/8] ' + STEPS[0][0].format(asi=cfg['asi']))
                dll_cache[cfg['proj']] = compile_dll(cfg, msbuild)
            else:
                p(f'[1/8] 复用本次已编译的 {os.path.basename(dll_cache[cfg["proj"]])}')
            dll_src = dll_cache[cfg['proj']]

        if dll_only:
            step1_asi(cfg, out_root, dll_src)
            p('  --dll-only：仅编译 DLL，跳过资源步骤。')
            return True, []

        # 步骤 1 的落位（放在资源步骤前，保证 scripts/ 目录先建好）
        step1_asi(cfg, out_root, dll_src)

        # ── 步骤 2~8 ──────────────────────────────────────────────────────
        rest = STEPS[1:]
        for i, (desc, fn) in enumerate(rest, start=2):
            p(f'[{i}/8] {desc.format(asi=cfg["asi"])}')
            if fn in (step2_dict, step3_le_strings, step4_charlist):
                fn(cfg, out_root, lang)
            else:
                fn(cfg, out_root)
    except Exception as e:
        fails.append(str(e))
        p(f'  !! 失败：{e}')
    return not fails, fails


def summarize(out_root, names):
    p('')
    p('=' * 74)
    p('构建产物')
    p('=' * 74)
    grand_files = 0
    grand_bytes = 0
    for n in names:
        d = os.path.join(out_root, n)
        if not os.path.isdir(d):
            p(f'{n}: (缺失)')
            continue
        files, total = [], 0
        for dp, _dns, fns in os.walk(d):
            for f in fns:
                fp = os.path.join(dp, f)
                total += os.path.getsize(fp)
                files.append(os.path.relpath(fp, d).replace('\\', '/'))
        grand_files += len(files)
        grand_bytes += total
        p(f'{n}/    {len(files)} 文件, {total/1048576:.1f} MB')
        tops = {}
        for rel in files:
            head = rel.split('/')[0] if '/' in rel else rel
            tops[head] = tops.get(head, 0) + 1
        for k in sorted(tops):
            p(f'    {k:18s} {tops[k]}')
    p('-' * 74)
    p(f'合计 {len(names)} 个包, {grand_files} 文件, {grand_bytes/1048576:.1f} MB')


# ══════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description='{game}_{version}_{lang} 发布构建')
    ap.add_argument('--target', action='append', choices=sorted(TARGETS),
                    help='限定构建目标（可重复；默认全部）')
    ap.add_argument('--lang', default=DEFAULT_LANG, help=f'语言目录（默认 {DEFAULT_LANG}）')
    ap.add_argument('--out-root', default=os.path.join(ROOT, 'release'),
                    help='输出根目录（默认 <repo>/release）')
    ap.add_argument('--skip-dll', action='store_true', help='跳过 DLL 编译（复用已有产物）')
    ap.add_argument('--dll-only', action='store_true', help='只编译 DLL，跳过资源步骤')
    args = ap.parse_args()

    if args.skip_dll and args.dll_only:
        p('!! --skip-dll 与 --dll-only 不能同时使用')
        return 1

    names = args.target or sorted(TARGETS)
    out_root = os.path.abspath(args.out_root)

    p('SR 汉化发布构建  {game}_{version}_{lang}')
    p(f'  仓库根: {ROOT}')
    p(f'  输出:   {out_root}')
    p(f'  目标:   {", ".join(names)}   lang={args.lang}')

    # 语言目录预检（早失败，避免跑到一半才发现）
    for n in names:
        d = os.path.join(ROOT, 'Resource', TARGETS[n]['game'], args.lang)
        if not os.path.isdir(d):
            p(f'!! {n}: 语言目录不存在 {d}')
            return 1

    msbuild = None
    if not args.skip_dll:
        msbuild = find_msbuild()
        if not msbuild:
            p('!! 找不到 MSBuild.exe。请安装 Visual Studio（含 C++ 生成工具），'
              '或使用 --skip-dll 复用已有产物。')
            return 1
        p(f'  MSBuild: {msbuild}')

    dll_cache = {}
    built, fails_all = [], []
    for n in names:
        try:
            ok, fl = build_target(n, out_root, args.lang, args.skip_dll,
                                  args.dll_only, msbuild, dll_cache)
        except Exception as e:
            ok, fl = False, [str(e)]
            p(f'!! {n} 失败：{e}')
        if ok:
            built.append(n)
        else:
            fails_all.extend((n, x) for x in fl)

    if built:
        summarize(out_root, built)

    p('')
    if fails_all:
        p('构建失败 —— 退出码 1')
        for n, e in fails_all:
            p(f'  [{n}] {(e or "").splitlines()[0]}')
        return 1
    if args.dll_only:
        p('DLL 编译完成（--dll-only）。')
    else:
        p('全部完成（8 步流程全部通过）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
