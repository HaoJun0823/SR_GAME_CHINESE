# SR_GAME_CHINESE

《黑道圣徒》（Saints Row）系列 PC 版**外挂式运行时汉化**项目仓库。

本仓库同时承载 **SR3 Remastered** 与 **SR4 Re-Elected** 两个游戏的汉化运行时 DLL、翻译资源、
构建工具与逆向资料。所有汉化均通过 **DLL 代理注入（ASI Loader）** 在内存中完成，
**不修改任何游戏原始资源文件**，可随时卸载还原。

> ⚠️ **本文为 2026-09 起的维护状态说明，有几处已与仓库现状不符（2026-10-02 盘点确认）**：
> · 旧工具路径 `Tools/*.py`、`Projects/<游戏>/Tools/` 已整体迁至 `Projects/Common/tools/`，
>   残留的旧路径引用请按本文「工具」一节的对照表换算，**直接照旧路径敲命令会失败**；
> · `Projects/SR3/SR3R_I18N/` 下**没有** `aob_l2_arrays.inc` —— SR3 的 AOB pattern 内联在
>   `dllmain.cpp` 里（运行时扫描 + 唯一命中断言），SR4 才外置成 `.inc` 并做 L2/L3 分层；
>   **`Tools/verify_aob.py`（离线校验 SR3 特征唯一命中）从未落地，勿引用**；
> · `dist/` 是**构建输入源**而非产物输出目录（详见下文「目录总览」）。
> 历史事故记录类文档（`Documents/le_strings_repack_bugfix.md` 等）的正文按原样保留，
> 其中提到的旧路径**不要改写**——那是当时的真实现场，改写会失真。

---

## 目录总览

| 目录 | 用途 | 是否入库 |
| --- | --- | --- |
| `Projects/` | 汉化 DLL 源码工程（VS 解决方案 + 代码） | ✅ 入库 |
| `Resource/` | 翻译资源：文本表（`le_string`）与词典（`dict`），按语言分子目录 | ✅ 入库 |
| `Documents/` | **已存在资产的说明文档**（面向阅读，非研究记录） | ✅ 入库 |
| `Archives/` | 归档区：历史版本、临时文件、一次性产物（默认不维护） | ✅ 入库 |
| `Fonts/` | 汉化用中文字体 | ✅ 入库 |
| `License/` | 本项目许可证 + 第三方依赖许可证 | ✅ 入库 |
| `dist/` | 构建**输入**源：ASI Loader（`common/winmm.dll`）+ `必读说明.txt` + 各游戏 `scripts/*.ini`。**构建产物输出到 `release/`，不是这里** | ✅ 入库 |

> `Archives/` 中的内容仅作留档，**不参与构建、不保证可用**。不要把新产物往里塞。

---

## 一、Projects — 汉化 DLL 源码

两个游戏各占一个子目录，互不依赖，可独立编译。

### `Projects/SR3` — SR3 Remastered 汉化

```
Projects/SR3/
├─ SR3R_DLL.slnx              # VS 解决方案
├─ SR3R_I18N/                 # 主工程（Win32/x64 DLL）
│  ├─ dllmain.cpp             # 入口 + 全部 hook 逻辑（核心文件）
│  ├─ charset_data.h          # 字符集表（生成物）
│  ├─ stb_truetype.h          # 运行时字形栅格化（第三方，Public Domain/MIT）
│  ├─ framework.h / pch.h / pch.cpp
│  ├─ packages.config         # MinHook 1.3.3（NuGet）
│  └─ README.md               # 本工程说明
   （无独立 aob_l2_arrays.inc：SR3 的 AOB pattern 直接**内联在 dllmain.cpp** 里，
    运行时 `AobFind()` 扫描 + 唯一命中断言；只有 SR4 才外置成 .inc 并做 L2/L3 分层）
```

> 旧 `Projects/SR3/Tools/`（含 `le_string_dev/` 翻译流水线）已整体迁出并归档，
> 见文末「工具」一节。

### `Projects/SR4` — SR4 Re-Elected 汉化

```
Projects/SR4/
├─ SR4R_DLL.slnx
├─ SR4R_I18N/                 # 与 SR3 同构，唯一差异：本节多了 aob_l2_arrays.inc
│  ├─ dllmain.cpp             # 入口 + hook 逻辑（v1.7）
│  ├─ aob_l2_arrays.inc       # L2/L3 层 AOB 特征数组（由 gen_aob_relaxed 生成，见「工具」一节）
│  ├─ charset_data.h
│  ├─ stb_truetype.h
│  └─ packages.config
```

> 旧 `Projects/SR4/Tools/` 已整体迁出并归档，见文末「工具」一节。

### 工作原理（两作一致）

```
游戏进程启动
   └─ 加载 binkw64.dll（本体是代理 DLL）
        ├─ 转发所有原始导出 → 真正的 binkw64_原版.dll
        └─ 加载 ASI Loader → 载入 SRxR_I18N.asi/dll
             ├─ AOB 特征扫描定位引擎函数（L1/L2/L3 分层，抗版本偏移）
             ├─ MinHook 挂接文本渲染与字符串取用路径
             ├─ 运行时解析全局变量地址（不硬编码绝对地址）
             └─ 命中词典时替换为中文；未命中回落原文
```

- **字符渲染**：引擎原生字体不含 CJK 字形，故在运行时用 `stb_truetype` 从 `Fonts/` 的
  TTF 现场生成字形图集并注入引擎字体缓存。
- **AOB 分层**：L1 = 完整特征（最严格）；L2 = 通配放宽后的特征数组；L3 = 关键指令片段。
  游戏更新导致字节偏移变化时，优先在 L2/L3 命中。

### 编译

需要 **Visual Studio（含 C++ 桌面开发工作负载）**。

**推荐用统一入口 `build_release.py`**（8 步全流程，含 DLL 编译与资源装配）：

```bat
:: 全流程：编译 DLL → 铺 dict/le_string/charlist/font → 产出 release/{game}_{version}/
python Projects\Common\tools\automation\build_release.py

:: 只编译 DLL（其余步骤跳过）
python Projects\Common\tools\automation\build_release.py --dll-only

:: 复用已有 DLL，只重建资源（改翻译/字典时的快速迭代）
python Projects\Common\tools\automation\build_release.py --skip-dll
```

它内部用 MSBuild 编译对应 vcxproj（Release|x64），并自动完成：
`ensure_nuget()` 还原 MinHook、`find_winsdk()` 探测 Windows SDK 版本（避免 MSB8036）。
产物 DLL 由脚本自动收进 `release/{game}_{version}/scripts/{G}R_I18N.asi`，
再按游戏目录结构部署到游戏目录即可（`dist/` 是输入源，不是产物目录）。

> ⚠️ 旧的 `Projects\SR4\Tools\build.py` 与 `--syntax-only`（`cl /Zs` 语法检查）**已不存在**，
> 当前工具链没有暴露独立的语法检查入口，需要时直接在 VS 中打开 vcxproj。

> 各作工具脚本的分工见文末「**工具**」一节；旧的 `Projects/<游戏>/Tools/README.md`
> 已迁至 `Archives/Common/Tools_redundant/readme_legacy/`。

---

## 二、Resource — 翻译资源

结构为 `Resource/<游戏>/<语言>/<类型>/`，三轴：游戏 × 语言 × 资源类型。

```
Resource/
├─ SR2/{CHS,CHT,ENG}/         # 预留占位（未来支持）
├─ SR3/
│  ├─ CHS/le_string/          # 简体中文 · 文本表
│  ├─ CHS/dict/               # 简体中文 · 词典（术语、语音、硬编码串）
│  ├─ CHT/{le_string,dict}/   # 预留占位
│  └─ ENG/{le_string,dict}/   # 英文原文（提取自游戏，供对照）
└─ SR4/  （结构同 SR3）
```

### 两类资源的区别

| 类型 | 形态 | 作用 |
| --- | --- | --- |
| `le_string` | vpp_pc 归档中的 `le_strings` 文本表，格式 `"KEY": "VALUE"` | 游戏内绝大多数 UI/字幕/任务文本，**随归档打包替换** |
| `dict` | 纯文本词典 | 供 DLL 运行时查表：主词典、语音表（`voice_*.txt`）、EXE 硬编码串（`exe_hardcoded.txt`）、补充表（`le_data_supplement.txt`）、角色名表（`charlist.txt`）等 |

`dict` 是**运行时生效**的部分（DLL 内置查表），`le_string` 是**随包替换**的部分。两者互补，
覆盖不同的文本来源。

### 现有资源规模

| 路径 | 文件数 | 说明 |
| --- | --- | --- |
| `Resource/SR3/CHS/le_string` | 21 | 主文本表分片 |
| `Resource/SR3/CHS/dict` | 14 | 含 `charlist.txt`、`exe_hardcoded.txt`、`voice_001..010.txt`、`SR3R_I18N.ini.example` |
| `Resource/SR3/ENG/le_string` | 21 | 英文原文对照 |
| `Resource/SR4/CHS/le_string` | 23 | 含 `dlc1..7`、`new_sr35_us.txt`、`optional_quests_us.txt`、`platform_ggp_us.txt`、`platform_nx64_us.txt` |
| `Resource/SR4/CHS/dict` | 20 | 含 `le_string_keys.txt`、`exe_hardcoded.txt`、`voice_001..017.txt` |
| `Resource/SR4/ENG/le_string` | 23 | 英文原文对照 |
| `CHT/*`、`SR2/*` | 0 | 占位，待填充 |

英文原文保留在 `ENG/` 下是为了**对照与再提取**：新增条目时以英文为基准，译文对齐同一 KEY。

---

## 三、Documents — 文档

存放**已有资产的说明**，不是研究/实验记录。

| 文件 | 内容 |
| --- | --- |
| `SR3/3DM-hook重写技术规格.md` | 早期 3DM 版 hook 重写技术规格 |
| `SR3/字幕换行英文残留问题解决全记录.md` | 字幕换行处英文残留的排查与解决 |
| `SR3/SRTT3_CN_HANDBOOK.md` | 汉化手册 |
| `SR3/SRTT3_CN_ACHIEVEMENTS.md` | 成就文本汉化记录 |
| `SR4/SR4R_I18N_TECH_SPEC.md` | SR4 汉化工程完整技术规格 |
| `SR4/glossary_voice.md` | 语音术语表 |
| `build_and_release.md` | 构建与发版流程（触发表、产物表、发布包命令） |
| `le_strings_repack_bugfix.md` | **历史事故复盘**：offset 表 8 字节步长写错导致启动崩溃（勿改路径） |
| `loose_first_crash_analysis.md` | loose 文件加载优先导致的崩溃分析 |
| `自动化构建流程.txt` | le_string 汉化自动构建流程（含工具路径对照表，2026-10-02 已更新） |
| `github_action流程.txt` | **草稿级** 8 步流程原始规格：语义与 `build_release.py` 一致，但用反斜杠 + 小写路径（`resource\{game}\{lang}\`），目录名大小写与仓库实际不符（实为 `Resource/<game>/CHS/`）。**要看规范版本请看 `build_and_release.md`** |

> 上表已剔除 `SR3/SR3R_I18N_README.md` —— 它是 `Projects/SR3/SR3R_I18N/README.md` 的
> 旧副本（2026-10-02 前 md5 完全一致，后随原文档更新而分叉），已删除避免两份走偏。

---

## 四、Fonts — 字体

| 文件 | 说明 |
| --- | --- |
| `SourceHanSansHWSC-VF.ttf` | 思源黑体（简体，可变字重），供运行时字形图集生成使用 |

DLL 在运行时从该字体动态生成所需字形的位图并注入引擎，因此**只需携带字体文件**，
无需预先烘焙整张图集。

---

## 五、License — 许可证

本项目以 **GPL v3** 发布。第三方依赖许可证一并收录：

| 文件 | 覆盖对象 |
| --- | --- |
| `GPLv3.txt` | 本项目 |
| `MinHook.txt` | MinHook（BSD-2-Clause） |
| `ASILoader.txt` | Ultimate ASI Loader（MIT） |
| `README.md` | 许可证索引说明 |

`stb_truetype` 为 Public Domain / MIT，声明已内嵌于两份 `stb_truetype.h` 头部。

> 游戏本体及其中所有素材、文本、商标的版权归 Deep Silver / Volition / THQ Nordic 所有。
> 本仓库仅包含汉化所需的自制代码与翻译文本，不包含任何游戏原始资源。

---

## 六、Archives — 归档

```
Archives/
├─ SR3/       # SR3 历史归档（历史版本、一次性脚本产物等）
└─ SR4/
   ├─ temp/          # 迁移前 .temp/ 内容
   └─ le_string_qa/  # le_string 文本表 QA 过程产物
```

**只进不出。** 归档内容不参与构建，清理时请先确认无引用。

---

## 七、工作流

1. **改翻译** → 编辑 `Resource/<游戏>/<语言>/dict/*.txt` 或 `le_string/*.txt`
2. **重打包**（若改的是 `le_string`）→ `Projects/Common/tools/cli/le_string_pack.py pack-dir`，
   或直接跑 `build_release.py --skip-dll` 走完整装配
3. **编译** → `build_release.py --dll-only`（内部调 msbuild 编译 vcxproj，无需手工拼命令）
4. **部署** → 把产物 + 字体 + `dist/` 中的注入器放到游戏目录
5. **验证** → 启动游戏，检查目标文本是否替换、有无崩溃

---

## 八、维护约定

- 归档区只进不出；不确定是否还有用的，先放 `Archives/`。
- 构建产物（`x64/`、`Release/`、`*.dll`、`*.pdb`、`packages/`、`.temp/`）一律不入库，见 `.gitignore`。
- 新增语言（如 `CHT`）时，复制同结构的 `CHS/` 目录再逐条翻译，保持 KEY 与文件集合一致。

### 关于换行符（重要）

**本仓库不做换行符归一化**（见 `.gitattributes`）。

原因：本仓库源码来自两个既有仓库，其中大量文件（`.h`/`.cpp`/`.py`/`.json`/`.vcxproj`）
的原始内容就是 CRLF。强制 `eol=lf` 会改写这些文件的字节，使新仓库与旧仓库产生
50+ 处内容差异——而迁移应当是无损的。

约定如下：

| 范围 | 处理 |
| --- | --- |
| `Archives/**` | 按二进制处理，git 不碰换行符、不做 diff 显示 |
| 二进制格式（`.ttf`/`.dll`/`.bin` 等） | 声明为 binary |
| 其余 | 保持文件原样，不做任何转换 |

> 若将来要统一换行符，应当是一次**独立的、显式的重构提交**，
> 而不是混在仓库迁移或功能改动里。

---

## 九、工具（2026-10-02 整理后的现状）

所有工具脚本集中在 **`Projects/Common/tools/`**，按四层组织：

| 层 | 目录 | 角色 |
| --- | --- | --- |
| helper | `tools/helper/` | `le_string_codec.py` —— `*.le_strings` 容器**唯一权威字节级实现**（SR3/SR4 共用）；`le_resource.py` —— `Resource/` 文本统一读取层；`snapshot.py` —— 校对基线快照 |
| cli | `tools/cli/` | 命令行工具：`le_string_extract` / `le_string_pack` / `vpp_extract` / `vpp_pack` / `build_charlist` / `pack_release` / `apply_fixes`（校对回写） |
| automation | `tools/automation/` | `build_release.py`（8 步发布编排）/ `build_release_le_strings.py` |
| verify | `tools/verify/` | `verify_release.py`（产物完整性校验）；`lint_mechanical` / `lint_consistency` / `lint_sense` / `lint_exempt` / `sense_lexicon`（汉化校对三层质检） |

### 汉化校对工具链（2026-10-02 新增）

方案见 `Documents/校对修复方案.md`，术语表见 `Documents/glossary.csv`。

```bash
# 汉化校对工具链（详见 Documents/校对修复方案.md）
python Projects/Common/tools/helper/snapshot.py export        # 建基线（改前必做）
python Projects/Common/tools/verify/lint_mechanical.py        # L1 机械质检
python Projects/Common/tools/verify/lint_consistency.py       # L2 一致性冲突
python Projects/Common/tools/verify/lint_sense.py --jsonl review.jsonl   # L3 义项错配
python Projects/Common/tools/cli/apply_fixes.py --plan fixes.jsonl        # 预演（默认）
python Projects/Common/tools/cli/apply_fixes.py --plan fixes.jsonl --apply # 真改
python Projects/Common/tools/helper/snapshot.py verify        # 回归比对
```

**铁律**：`lint_*` 全部只读；`apply_fixes.py` 默认 dry-run，且**必须先在副本上验证 + diff**；
每次改动都会记入 `reports/fix_log.csv`。

### 旧路径 → 现路径对照（照旧路径敲命令必定失败）

| 文档/记忆中的旧写法 | 现在应该用的 |
| --- | --- |
| `Tools/pack_release.py` | `Projects/Common/tools/cli/pack_release.py` |
| `Tools/verify_release.py` | `Projects/Common/tools/verify/verify_release.py` |
| `Tools/sr4_vpp.py` / `sr4_vpp_pack.py` | `Projects/Common/tools/cli/vpp_extract.py` + `vpp_pack.py` |
| `Tools/le_strings_repack.py`（SR3） | `Projects/Common/tools/cli/le_string_pack.py`（内核 `helper/le_string_codec.py`） |
| `Tools/vpp_extract_all.py` / `vpp_pack.py` | `Projects/Common/tools/cli/vpp_{extract,pack}.py` |
| `Projects/<游戏>/Tools/*.py` | 一律走 `Projects/Common/tools/` 那一套 |
| `Projects/<游戏>/Tools/README.md` | `Archives/Common/Tools_redundant/readme_legacy/` |

**已彻底删除、且没有对应物**（勿再引用）：
`Tools/verify_aob.py`、`Tools/gen_aob2.py`、`Tools/sim_aobscan.py`、
`Tools/build_check.py`、`Tools/build.py`、`Tools/aob_relaxed_report.txt` —— 全网无残留。

**仅存归档区、不在活动工具链**（需要时去 `Archives/` 翻）：
`Tools/gen_aob_relaxed.py` 与 `Tools/verify_runtime_globals.py` 在
`Archives/SR4/Tools_redundant/`（SR4 的 AOB 特征生成与运行时全局量校验）。

**保留原样、不要改路径**：`Documents/le_strings_repack_bugfix.md` 里的
`Projects/SR3/Tools/le_strings_repack.py` —— 那是 8 字节步长事故的事故复盘，
写的是当时的真实现场，改写会失真。

> 工具整理历史与踩坑记录见 `.workbuddy/memory/MEMORY.md`。

