# 构建与发布

本仓库的发布包由 **一条命令** 从源材料装配而成，产物目录形如 `release/{game}_{version}/`，
可直接解压到游戏根目录。

规格原始描述见仓库根 `github_action流程.txt`；实现见 `build_release.py`。

---

## 一、命名体系

| 维度 | 取值 | 说明 |
|---|---|---|
| `game` | `sr3r` / `sr4r` | 《黑道圣徒 III 重制版》 / 《黑道圣徒 IV》 |
| `version` | `common` / `microsoft` | `microsoft` 目前仅 `sr4r` 有（MS Store / MSIXVC 版） |
| `lang` | `chs` | 目前仅简体中文 |

输出目录 **`release/{game}_{version}/`**，三个目标：

```
release/sr3r_common/         SR3 通用（Steam / GOG / EPIC）
release/sr4r_common/         SR4 通用
release/sr4r_microsoft/      SR4 Microsoft Store 版
```

> **为什么目录名不含 `lang`？**
> 规格里 `lang` 是命名组成部分，但目录树只取 `{game}_{version}`。
> `lang` 决定的是「装什么内容」（取 `Resource/` 下哪个语言子目录、产出哪些
> `_zh`/`_us` 后缀），而 `game`/`version` 决定「装给哪个游戏构建」——两者正交。
> 当前 `lang` 恒为 `chs`；将来增加语言时只需 `--lang` 换源目录，
> 不同语言的包本来就分开分发，不必挤进同一个目录。

---

## 二、一条命令跑完

```bat
cd G:\Projects\SR_GAME_CHINESE
python build_release.py
```

退出码：`0` = 全部成功；`1` = 任一步失败（供 CI 判定）。

常用变体：

```bat
python build_release.py --target sr3r_common   :: 只构建一个目标
python build_release.py --skip-dll             :: 跳过 DLL 编译（复用已有产物，快速迭代资源）
python build_release.py --dll-only             :: 只编译 DLL
python build_release.py --out-root D:\rel      :: 换输出根目录
python build_release.py --lang CHS             :: 指定语言目录（默认 CHS）
```

---

## 三、八步流程

`build_release.py` 对每个目标严格执行以下 8 步（编号与 `github_action流程.txt` 一致）：

| # | 步骤 | 输入 | 输出 |
|---|---|---|---|
| 1 | **编译 DLL** | `Projects/{G}/{G}R_I18N/*.vcxproj`（`vc_141`，`Release\|x64`） | `release/{gv}/scripts/{G}R_I18N.asi` |
| 2 | **复制词典** | `Resource/{game}/CHS/dict/*.txt` | `release/{gv}/scripts/dict/` |
| 3 | **构建 le_string** | 模板 `data/{game}/{version}/misc/*.le_strings` + `Resource/{game}/CHS/le_string/*.txt` | `release/{gv}/update/*.le_strings` |
| 4 | **生成 charlist** | `Resource/{game}/CHS/{dict,le_string}/*.txt`（全部中文文本） | `release/{gv}/scripts/charlist.txt` |
| 5 | **复制字体** | `Fonts/*.ttf` | `release/{gv}/scripts/` |
| 6 | **ASI Loader + 说明** | `dist/common/winmm.dll`、`dist/common/必读说明.txt` | `release/{gv}/` |
| 7 | **复制 ini** | `dist/{game}/scripts/*.ini` | `release/{gv}/scripts/` |
| 8 | **合并许可** | `License/*.txt` | `release/{gv}/License.txt` |

### 产物目录树

```
release/sr3r_common/
├── winmm.dll                    ← 步骤 6：ASI Loader（必需）
├── 必读说明.txt                  ← 步骤 6
├── License.txt                  ← 步骤 8（合并 5 份许可）
├── scripts/
│   ├── SR3R_I18N.asi            ← 步骤 1（汉化本体）
│   ├── SR3R_I18N.ini            ← 步骤 7（配置，基名必须与 asi 一致）
│   ├── SourceHanSansHWSC-VF.ttf ← 步骤 5
│   ├── charlist.txt             ← 步骤 4（字符清单）
│   └── dict/                    ← 步骤 2（12 个词典 txt）
└── update/                      ← 步骤 3（42 个 le_strings：21 表 × _zh/_us）
```

---

## 四、构建时做的校验

任一步失败立即中止该目标，并计入退出码。关键校验点：

- **步骤 1**：NuGet（MinHook）已还原（缺失时脚本会自动调 `NuGet.exe restore`，
  见下）；MSBuild 返回码为 0；产物 dll 存在且路径经 `abspath` 固化。
- **步骤 3**：每个 le_strings 产出后**独立重新解析**（不复用 repack 自检），核对
  魔数 `0xA84C7F73` / bucket 条目总数 == `header.stringCount` / offset 表长度 /
  回读条目数。这是 8 字节步长事故的同源防线，详见 `Documents/le_strings_repack_bugfix.md`。
- **步骤 4**：写出后**回读比对**，字符序列必须与统计结果逐字一致。
- **步骤 6**：`winmm.dll` 体积 < 100 KB 时判定为非法 Loader。
- **步骤 7**：ini 基名必须与 asi 同基名（DLL 按「自身模块名.ini」查找配置）。

### 两个易踩的坑（已在脚本里处理）

**1. `WindowsTargetPlatformVersion` + v141**

SR4 的 vcxproj 写 `<WindowsTargetPlatformVersion>10.0</...>`（=「最新」）。VS IDE 能自己
解析，但 **MSBuild 命令行 + v141 组合下会报 MSB8036「找不到 Windows SDK 版本10.0」**。
脚本统一显式传 `/p:WindowsTargetPlatformVersion=10.0.19041.0`（与 v141 同期、兼容最好），
**不改 vcxproj**——IDE 里照常可用。SR3 本来写死 `10.0.19041.0` 故不受影响。

**2. `Projects/**/packages/` 被 gitignore，但没有它编不了**

`.gitignore` 第 32 行忽略 `Projects/**/packages/`，而 vcxproj `Import` 了
`..\packages\minhook.1.3.3\build\native\minhook.targets`。全新 clone 后必须还原，
否则 `EnsureNuGetPackageBuildImports` 硬报错。

- `packages.config` 位于 **`Projects\<game>\<Proj>\packages.config`**（不是 `Projects\<game>\`）。
- 还原目标是其**上一级** `Projects\<game>\packages\`（与 vcxproj 里 `..\packages\` 一致）。
- 本地：`build_release.py` 的 `ensure_nuget()` 发现包缺失时会自动找 `NuGet.exe` 还原。
- CI：工作流用独立步骤显式还原并断言 `minhook.targets` 至少 2 个。
- 包内含 `libMinHook-x64-v141-mt.lib`，正好对应 v141 + 静态 CRT `/MT`。

### 独立复核

CI 在构建脚本之后**再跑一遍** `Tools/verify_release.py`，不复用构建脚本的内部状态，
逐包断言「能否真的装进游戏跑起来」：

```bat
python Tools/verify_release.py release
python Tools/verify_release.py release --strict    :: 警告也当失败
```

覆盖：目录结构 / ASI 是否 x64 PE 且静态 CRT / ini 与 asi 基名及 font_file 指向 /
`update/` 全量 le_strings 结构自检 / `dict/` 每个文件可解析出条目 /
charlist 字符数区间 / 字体存在 / Loader 体积 / 说明与许可完整性。

---

## 五、GitHub Actions

工作流：`.github/workflows/build-release.yml`

| 触发 | 行为 |
|---|---|
| push 到 `main`/`master`（忽略 `**.md`、`Documents/**`、`Archives/**`） | 构建 + 校验 + 上传 artifact |
| pull request | 同上（验证不破坏构建） |
| 手动 `workflow_dispatch` | 同上，可选 `skip_dll` |
| 打 tag（`refs/tags/*`） | 额外把所有包压成 zip 并创建 Release |

### Release 压缩包命名（仅 tag 触发）

包名统一为 **中文**，格式 `《游戏名》_语言_适用版本_补丁_{BUILD_DATE}.zip`：

| 构建目标 | 压缩包名 |
|---|---|
| `sr3r_common` | `《黑道圣徒III》_简体中文_通用_补丁_{BUILD_DATE}.zip` |
| `sr4r_common` | `《黑道圣徒IV》_简体中文_通用_补丁_{BUILD_DATE}.zip` |
| `sr4r_microsoft` | `《黑道圣徒IV》_简体中文_微软商店_补丁_{BUILD_DATE}.zip` |

- `{BUILD_DATE}` = **UTC+8** 的 `YYYYMMDD`。工作流里显式用
  `TZ='Asia/Shanghai' date +%Y%m%d`，**不能直接用 runner 默认的 `date`**
  —— ubuntu runner 是 UTC，晚间触发会与国内日期差一天。
- 映射写在 `package` job 的 `case "$name" in ... esac` 里；
  未列出的目录回退为 `<目录名>_{BUILD_DATE}.zip`（不报错，便于将来加 target）。
- `zip` 加 `-UN=UTF8` 显式写入 UTF-8 文件名标志位，否则 Windows 自带解压器
  可能按本地代码页解读中文名导致乱码。
- `action-gh-release` 的 `files: release/*.zip` 只收 zip；
  非目录项（如混入的 `README.txt`）在打包循环里由 `[ -d "$name" ] || continue` 跳过。
- 注：Actions artifact 名 `sr-{run_number}-{sha}` 是**流水线内部标识**，
  与下载到的 zip 文件名无关。

**runner 前置要求**（`windows-2022` 镜像已满足）：

- Visual Studio 2022 + **v141 工具集**（`Microsoft.VisualStudio.Component.VC.v141.x86.x64`）
  —— 流程第 1 条明确要求 `vc_141`（MSVC 14.16）。工作流有一步会显式校验该组件存在。
- Python 3.12（`actions/setup-python@v5`）。
- NuGet 由工作流的「还原 NuGet 包 (MinHook)」步骤处理：优先用镜像自带 `NuGet.exe`，
  找不到则下载官方命令行版，然后对两个 `packages.config` 各还原一次。
- 工作流里 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` 必须保留：
  所有 `.py` 输出中文构建日志，Windows 控制台默认编码会让它抛 `UnicodeEncodeError`。

---

## 六、本地手动构建（不使用本脚本时）

```bat
:: 0. 还原 NuGet（Projects/**/packages/ 被 gitignore，全新 clone 必须先做）
nuget restore Projects\SR3\SR3R_I18N\packages.config -PackagesDirectory Projects\SR3\packages
nuget restore Projects\SR4\SR4R_I18N\packages.config -PackagesDirectory Projects\SR4\packages

:: 1. 编译 DLL（等价于 VS 的 Release|x64；SDK 必须显式给，否则 SR4 会 MSB8036）
msbuild Projects\SR3\SR3R_I18N\SR3R_I18N.vcxproj ^
        /p:Configuration=Release /p:Platform=x64 /p:PlatformToolset=v141 ^
        /p:WindowsTargetPlatformVersion=10.0.19041.0 /t:Rebuild

:: 2~8 单独跑各环节
python build_release_le_strings.py --game SR3 --version common --out-dir <目录>
python Tools\build_charlist.py --game SR3 --out <目录>\charlist.txt
```

各脚本无参数运行时只做自检，便于 CI / 快速验证：

```bat
python Projects\SR3\Tools\le_strings_repack.py   :: 打印 [self_test] OK
python Projects\SR4\Tools\sr4le_repack.py        :: 同上
```

---

## 七、相关文件

| 路径 | 说明 |
|---|---|
| `github_action流程.txt` | 流程原始规格（本文档实现的对象） |
| `build_release.py` | **本文档主角**，8 步编排 |
| `build_release_le_strings.py` | 步骤 3 的实现（le_string 封装） |
| `Tools/build_charlist.py` | 步骤 4 的实现（字符清单生成） |
| `Tools/verify_release.py` | 产物独立复核（CI 第二步） |
| `.github/workflows/build-release.yml` | CI 工作流 |
| `Projects/SR3/Tools/`、`Projects/SR4/Tools/` | le_string 底层 repack / 解包工具 |
| `Documents/le_strings_repack_bugfix.md` | 8 字节步长事故分析（构建校验的由来） |
| `Documents/loose_first_crash_analysis.md` | loose 加载优先级事故分析 |
