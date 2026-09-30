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

CI 在构建脚本之后**再跑一遍** `Projects/Common/tools/verify/verify_release.py`，不复用构建脚本的内部状态，
逐包断言「能否真的装进游戏跑起来」：

```bat
python Projects/Common/tools/verify/verify_release.py release
python Projects/Common/tools/verify/verify_release.py release --strict    :: 警告也当失败
```

覆盖：目录结构 / ASI 是否 x64 PE 且静态 CRT / ini 与 asi 基名及 font_file 指向 /
`update/` 全量 le_strings 结构自检 / `dict/` 每个文件可解析出条目 /
charlist 字符数区间 / 字体存在 / Loader 体积 / 说明与许可完整性。

---

## 五、GitHub Actions

工作流：`.github/workflows/build-release.yml`

| 触发 | 行为 |
|---|---|
| push 到 `master`（忽略 `**.md`、`Documents/**`、`Archives/**`） | 构建 + 校验 + **打包 zip** + 上传 artifact，随后 `publish` job 在**同一 run** 内自动按「日期-时间」打 tag 并发布 Release（三个独立附件，GitHub 限制用 ASCII 名 `Saint-Row-…`，内容仍为中文名 zip） |
| pull request | 构建 + 校验（门禁；**不发布**） |
| 手动 `workflow_dispatch` | 构建 + 校验，可选 `skip_dll`（不发布） |
| 打 tag（`refs/tags/*`） | **不再单独触发发布**。旧设计依赖 tag 重触发 `package` job，但 `GITHUB_TOKEN` 推送的 tag 不会再次触发 `on:push` 工作流（GitHub 防循环机制），会静默失败，故已废弃该路径 |

### 产物分两个层次（重要）

流水线里有**两种**形态，别混淆：

| 层次 | 位置 | 由谁产出 | 是否上传 |
|---|---|---|---|
| 中间目录树 | `release/sr3r_common/` 等三个目录 | `build_release.py` | **不上传**，只是打包的输入 |
| 发布包 | `release/*.zip` 三个中文名 zip | `Projects/Common/tools/cli/pack_release.py` | **Artifact 内容**；Release 附件因 GitHub 不支持中文名改用 ASCII 名（见发布机制） |

命令：

```bat
:: build 阶段（workflow 里在 windows runner 上跑）
python Projects/Common/tools/cli/pack_release.py release
python Projects/Common/tools/cli/pack_release.py release --date 20260922   :: 指定日期，便于本地复现

:: publish 阶段（workflow 里在 push master 的同一 run 内，于 ubuntu runner 上跑：下载 + 归拢 + 打 tag + 挂 Release）
```

### 发布包命名

构建产物用**中文名** zip（Artifact 内容；解压后内部文件也中文）。但 **GitHub Release
附件名不支持中文**（API 上传会被静默破坏成 `_._._`），故发到 Release 的附件改用 ASCII 名，
二者对照：

| 构建目标 | 构建产物（中文名 zip，Artifact） | Release 附件名（ASCII） |
|---|---|---|
| `sr3r_common` | `《黑道圣徒III》_简体中文_通用_补丁_{BUILD_DATE}.zip` | `Saint-Row-3-Common-CHS-Patch-{DateTime}.zip` |
| `sr4r_common` | `《黑道圣徒IV》_简体中文_通用_补丁_{BUILD_DATE}.zip` | `Saint-Row-4-Common-CHS-Patch-{DateTime}.zip` |
| `sr4r_microsoft` | `《黑道圣徒IV》_简体中文_微软商店_补丁_{BUILD_DATE}.zip` | `Saint-Row-4-Microsoft-CHS-Patch-{DateTime}.zip` |

- **单一事实来源** = `Projects/Common/tools/cli/pack_release.py` 里的 `ZIP_NAMES` 字典（中文名）。
  打包逻辑只此一处；`publish` job 不再自己压 zip，只负责「下载 + 校验 + 打 tag + 建 Release + 以 ASCII 名上传」。
  这样避免同一产物两种形态、两处代码各自漂移。
- `{BUILD_DATE}` = **UTC+8** 的 `YYYYMMDD`。脚本里显式
  `datetime.now(timezone.utc) + timedelta(hours=8)`；
  **不能直接用本地/UTC 的 `date`** —— runner 默认 UTC，晚间触发会与国内日期差一天。
- **UTF-8 文件名标志位**：脚本用 Python 标准库 `zipfile`，
  对非 ASCII 条目名**自动置位 UTF-8 标志（general purpose bit 11）**，
  无需任何额外参数。这比 7-Zip（要专门开关）和 PowerShell `Compress-Archive`
  （**根本不置位** → 解压乱码）都可靠，且跨平台、可本地自测。
- 条目路径分隔符统一为 `/`（`sanitize_arcname()`），Windows 的 `\`
  会让部分解压器出错。
- **白名单制，无兜底**：`ZIP_NAMES` 之外的目录只打印一行提示、不打包。
  注意 `release/` 下还有 `SR3` / `SR4` 两个中间目录（旧布局残留），
  它们会被正确排除。
- **失败即整体失败**：脚本先做全量存在性检查，再开始压缩。
  缺任一期盼目录时 `rc=1` 且**不产出任何 zip**；否则会出现
  「先压出前两个再报错」的半套产物，调用方若只看「有没有 zip」就会误发不完整包。
- **Artifact 拆成三个、命名清晰**（不再封进单个 `sr-release-zips` 大包）：
  `sr3r-common` / `sr4r-common` / `sr4r-microsoft`，各自 `path: stage/<name>`，
  内含 1 个中文名 zip（**扁平、无 `release/` 前缀**），`if-no-files-found: error`，保留 30 天。
  - 拆分的**根因**：旧版用 `path: release/*.zip` 上传，upload-artifact 会保留
    `release/` 前缀，下载后文件落在 `dist/release/*.zip` 而非 `dist/*.zip`，
    导致 `publish` job 的硬断言匹配到 0 个而放弃发 Release（「发不到 Release 页」的元凶）。
    现在每个 artifact 是单文件目录上传，下载到 `dist/` 后三个 zip 直接落在根目录
    （`publish` 还有一步 `find dist -mindepth 2 -name '*.zip' -exec mv -t dist/` 兜底，
    即使下载仍落到子目录也能归拢到根目录）。
- `publish` job（`needs: build`，**只在 push master 时跑**）发版流程：
  1. 三个 artifact **分别下载到 `dist/<artifact-name>/` 子目录**（每个含 1 个中文名 zip），各自校验恰好 1 个；
  2. 按 Asia/Shanghai 算 `tag = YYYY-MM-DD-HH-MM-SS`（无 `v`），`git tag` + push 建 tag；
  3. **生成 Release 说明 `release_notes.md`**（三段，缺一不可）：
     - 🧾 **本次构建相关的提交记录**：以「上一次 Release」为基准取
       `git log <base>..HEAD`，每条渲染成
       `- [<7位hash>](https://github.com/<slug>/commit/<完整hash>) <subject>`；
     - 📦 **三个 ASCII 附件 ↔ 中文原名对照表**（附件名不能用中文，靠这张表对应）；
     - 📖 **`dist/common/必读说明.txt` 全文**：`<details>+<pre>` 折叠嵌入，
       与包内那份**同源同版**（`build_release.py` 第 6 步由该文件复制进 zip 再打包），
       玩家不下载也能看到安装 / 卸载 / 常见问题 / 免责声明。
  4. `gh api POST /releases` 建 Release（`body=@release_notes.md`），取返回的 `upload_url`；
  5. 用 `curl` 向 `upload_url`（uploads.github.com）逐个上传，附件名用 ASCII（`Saint-Row-{Game}-{Platform}-CHS-Patch-{DateTime}.zip`）。
- ★ **Release 说明的「基准 commit」怎么取（踩坑）**：`actions/checkout` 默认
  **只拉分支指针、不拉 tag**（哪怕配了 `fetch-depth: 0`），所以 runner 本地没有
  上次 release 的 tag 对象，`git log <tag>..HEAD` 会直接报
  `fatal: ambiguous argument '<tag>..HEAD': unknown revision`。
  正确做法是取 **release 的 commit.sha** 当范围起点（该 commit 必然在 master 历史里），
  并加一道 `git cat-file -e "${sha}^{commit}"` 存在性校验，取不到就回退首个提交。
  另外 `releases/latest` 的 `.commit.sha` 对「由 tag 建出」的 release **实测返回空**，
  故再退一路查 `git/refs/tags/<tag>` 的 `.object.sha`（本仓库建的是轻量 tag，
  该 sha 直接就是 commit sha；annotated 则要多剥一层 `.object.object.sha`）。
- ★ **commit 链接必须另带 `https://github.com` 前缀**：`slug`（形如 `HaoJun0823/SR_GAME_CHINESE`）
  只能喂给 `gh api repos/$slug/...`；拼 markdown 链接要另起
  `repo_url="https://github.com/$slug"`，否则正文里的 hash 只是一段**点不开的普通文字**。
- ★ **六层 GitHub 坑（2026-09-30 逐层实测，均已修复）**：
  1. **`GITHUB_TOKEN` 防循环**：最早 `auto-tag` 用 token push tag 指望重触发 `package` job，
     但 token 推送的 tag 不会再次触发 `on: push`，`package` 永远 skipped。现改为**同 run 内 `publish` 直接发版**。
  2. **必须用 `upload_url` 上传**：GitHub 已废弃 `api.github.com/.../releases/{id}/assets` 直传端点（404），
     必须用建 release 返回的 `upload_url`（uploads.github.com）。旧 `gh release create` / `softprops` 都踩此坑。
  3. **GitHub 不支持中文名附件**：即便用对端点，中文名经 API 上传会被静默破坏成 `_._._`，
     故附件名一律 ASCII；解压后内部文件仍为中文名，不影响使用。
  4. **`publish` job 必须 checkout**：`gh` 需要 git 上下文，否则
     `fatal: not a git repository`（run 36719311009 实测）。
  5. **curl 不能 `-G` 配 `--data-binary`**：`-G` 会把 zip 字节也拼进 URL 查询串，
     报 `curl: (3) URL rejected: Malformed input to a URL function`；
     正确写法是 `name=` 直接写进查询串、zip 走请求体（`"${BASE}?name=${ascii}"`）。
  6. **本地调试时可能没有 `jq`**：GitHub `ubuntu-latest` 镜像自带 jq，
     但本地 Windows（Portable Git Bash）没有 —— 同一段脚本在本地会**静默取到空值**
     并走进 fallback 分支，排查时勿误判为脚本逻辑 bug。
  7. **`gh api` 的 `-f` 不展开 `@file`**：`-f "body=@release_notes.md"` 会把字符串
     `@release_notes.md` **原样当正文** —— 附件再齐、Release 再成功，玩家看到的说明也是空的。
     **只有 `-F` 才读文件内容**（run 36739064504 实测，此前所有 Release 正文其实都是空的）。
  8. **自检比对用前缀匹配，别用精确相等**（run 36740536134 实测）：正文标题带动态 tag 后缀
     （`## 《黑道圣徒》简体中文汉化补丁 · \`2026-10-01-00-09-01\``），若自检写
     `[ "$body_head" != "## 《黑道圣徒》简体中文汉化补丁" ]` 会**自己把自己判失败**并中断发布。
     正解 `[[ "$body_head" == "## 《黑道圣徒》简体中文汉化补丁"* ]]`，并**在建 release 之前先做本地预检**，
     避免远端留下空 Release + 孤儿 tag。
  9. **shell 自检的段落标记不要带 emoji**：Windows Git Bash 下 emoji 作命令行参数传给 `grep`
     会被代码页转换破坏（本地 `grep` 与 Python 传真真 UTF-8 都返回 0），导致**本地模拟无法通过**，
     而 CI 上不复现。改用 ASCII 标记（如 `Saint-Row-4-Microsoft-CHS-Patch`）+ 中文（`必读说明`）。
  10. **CI 中间产物要进 `.gitignore`**：`release_notes.md` / `commits.md` 由 publish job 产出、
     只供本 run 喂 `gh api`，不入库；否则每次跑完 CI 仓库根目录都冒未跟踪文件，易被误 `git add`。

### action 版本（必须 pin 到 Node 24）

GitHub 已弃用 Node 20 运行时，工作流里所有 action 都升到 **node24** 版本：

| Action | 版本 | 运行时 |
|---|---|---|
| `actions/checkout` | `v7.0.1` | node24 |
| `actions/setup-python` | `v7.0.0` | node24 |
| `actions/upload-artifact` | `v7.0.1` | node24 |
| `actions/download-artifact` | `v8.0.1` | node24 |
| `softprops/action-gh-release` | `v3.0.3` | node24 |

> ★ **升版前务必核实运行时，不要靠猜。** Release 页面**不写**运行时，
> 唯一权威来源是该 tag 下的 `action.yml` 里的 `runs.using`：
> ```bat
> gh api repos/actions/checkout/contents/action.yml?ref=v7.0.1
> ```
> 返回的 `content` 是 base64，解码后找 `using:` 字段。
> 经验值：`checkout` v4 / `setup-python` v5 / `upload-artifact` v4~v5 /
> `download-artifact` v4~v6 都是 node20；跨大版本才换运行时。

**runner 前置要求**（`windows-2022` 镜像已满足）：

- Visual Studio 2022 + **v141 工具集**（`Microsoft.VisualStudio.Component.VC.v141.x86.x64`）
  —— 流程第 1 条明确要求 `vc_141`（MSVC 14.16）。工作流有一步会显式校验该组件存在。
- Python 3.12（`actions/setup-python@v7.0.0`）。
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

:: 9. 打成中文名 zip（发布用，可选）
python Tools\pack_release.py release
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
| `Projects/Common/tools/cli/build_charlist.py` | 步骤 4 的实现（字符清单生成） |
| `Projects/Common/tools/verify/verify_release.py` | 产物独立复核（CI 第二步） |
| `Projects/Common/tools/cli/pack_release.py` | **打成中文名 zip**（Artifact 内容；Release 附件因 GitHub 不支持中文名改用 ASCII 名） |
| `.github/workflows/build-release.yml` | CI 工作流 |
| `Projects/SR3/Tools/`、`Projects/SR4/Tools/` | le_string 底层 repack / 解包工具 |
| `Documents/le_strings_repack_bugfix.md` | 8 字节步长事故分析（构建校验的由来） |
| `Documents/loose_first_crash_analysis.md` | loose 加载优先级事故分析 |
