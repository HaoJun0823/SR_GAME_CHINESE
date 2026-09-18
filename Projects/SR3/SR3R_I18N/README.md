# SR3R_I18N — Saints Row The Third Remastered External Localization DLL

外挂式运行时汉化 DLL：不动任何游戏资源文件，在内存中替换文本并注入中文字形渲染。
External, runtime-only localization DLL: no game resource files are touched; text is swapped in memory and CJK glyph rendering is injected on the fly.

> **语言 / Language**: 中文在前，English follows. Two languages say the same things; read either one.
> **License**: GPL-3.0（与本项目仓库一致 / same as this repository）
> **Author**: HaoJun0823 — https://www.haojun0823.xyz | https://github.com/HaoJun0823/SR3R_I18N

---

## 目录 / Contents

- [它做什么 / What it does](#它做什么--what-it-does)
- [安装与文件说明 / Install & files](#安装与文件说明--install--files)
- [词典格式 / Dictionary format](#词典格式--dictionary-format)
- [配置 / Configuration](#配置--configuration)
- [技术架构 / Technical architecture](#技术架构--technical-architecture)
- [可靠性结论与陷阱 / Verified facts & pitfalls](#可靠性结论与陷阱--verified-facts--pitfalls)
- [性能与显存 / Performance & VRAM](#性能与显存--performance--vram)
- [调试与日志 / Debugging & logs](#调试与日志--debugging--logs)
- [移植到其他游戏 / Porting to other games](#移植到其他游戏--porting-to-other-games)
- [限制 / Limitations](#限制--limitations)

---

## 它做什么 / What it does

中文（简述）：
- 运行时替换 UI/字幕等宽字符文本为中文（词典替换，不改资源包）。
- 引擎字体不含 CJK 字形：DLL 伪造"扩展字体对象"，把 0x20–0xFFFF 全 BMP 码位映射到自建图集（stb_truetype 光栅化思源黑体）。
- 官方英文图集原样保留读回拼接，未翻译内容（如 Credits）照常渲染。
- `misc.vpp_pc` 等所有资源保持官方原版；卸载 DLL 即完全恢复原版行为。

English (brief):
- Replaces wide-char UI/subtitle text with Chinese at runtime (dictionary swap, resource archives untouched).
- The engine fonts have no CJK glyphs: the DLL fakes an "extended font object" mapping every BMP codepoint (0x20–0xFFFF) into a self-built atlas rasterized via stb_truetype (Source Han Sans).
- Official English atlases are read back and blitted verbatim, so untranslated content (e.g. Credits) renders exactly as before.
- All game resources (incl. `misc.vpp_pc`) remain pristine; removing the DLL restores stock behavior completely.

---

## 安装与文件说明 / Install & files

全部放在游戏根目录的 `scripts\` 下 / Everything lives under `scripts\` in the game root:

| 文件 / File | 说明 / Purpose |
|---|---|
| `SR3R_I18N.asi` | 主 DLL（ASI Loader 加载，根目录 `binkw64.dll` 是加载器，勿删 / the loader, do not delete） |
| `SR3R_I18N.ini` | 配置（UTF-8）/ configuration (UTF-8) |
| `dict\*.txt` | 词典文件夹，le_strings 格式 / dictionary folder, le_strings format |
| `charlist.txt` | 字符清单（可选；补齐词典未覆盖的官方用字，如“齿”）/ charset list (optional; adds glyphs missing from the dictionary, e.g. 齿) |
| `SourceHanSansHWSC-VF.ttf` | 中文字体（可在 ini 换名）/ CJK font (rename via ini) |
| `SR3R_I18N.log` | 运行日志 / runtime log |
| `DumpText.dtxt` | 未命中文本自动收集（v7.5.2 起默认关，可在 ini 开启）/ auto-collected untranslated strings (off by default since v7.5.2, opt-in via ini) |

要求 / Requirements: x64 游戏版本；需已安装 ASI Loader（本仓库附带的方式）或等价注入器。
x64 game build; requires ASI Loader (bundled approach) or an equivalent injector.

---

## 词典格式 / Dictionary format

与游戏原生 `unpack\text\le_data\<lang>\*.txt` 相同的 le_strings 行格式 / same line format as the game's native le_strings files:

```
"KEY": "VALUE"
```

规则 / Rules:
- 每行一条；键值均带双引号，冒号两侧空格随意 / one entry per line; both sides quoted, spaces around `:` optional
- 转义只有三种 / exactly three escapes: `\\` `\"` `\n`（其它按字面 / others literal）
- KEY = 英文原文（引擎在渲染层看到的字符串）；VALUE = 中文译文 / KEY = the English string the engine renders; VALUE = the Chinese translation
- `HASH_xxxxxxxx` 键是引擎字符串槽位名，其值**不是**运行时查表键，DLL 跳过它们 / `HASH_xxxxxxxx` keys are engine string-slot names whose values are NOT runtime lookup keys; the DLL skips them
- 多文件同键：后加载的覆盖先加载的（文件名字典序）/ same key across files: later file wins (alphabetical order)
- 修改 txt 后重启游戏即生效 / edits apply after a game restart

---

## 配置 / Configuration

`SR3R_I18N.ini`（UTF-8，可带 BOM；键名不分大小写 / key names case-insensitive）:

```ini
[settings]
dict_dir = dict                              ; 词典文件夹（相对 asi）/ dict folder (relative to the asi)
font_file = SourceHanSansHWSC-VF.ttf         ; 中文字体文件名（相对 asi）/ CJK TTF file (relative to the asi)
dump_enabled = 0                             ; v7.5.2 默认关: 收集未命中文本（内核汉化接管后无意义）/ collect untranslated text (off by default since v7.5.2)
lang_early = 1                               ; v7.4 语言服务层整句替换 / language-service early swap
subtitle_early = 1                           ; v7.5 字幕绘制入口整句替换（折行前）/ subtitle entry swap (before word-wrap)
charlist_file = charlist.txt                 ; v7.5.1 字符清单（补齐词典外用字，可选）/ charset list (optional, fills dict-missing glyphs)
early_diag = 0                               ; v7.5.2 默认关: 命中/miss 探针诊断（开发期调试用）/ probe diagnostics (off by default, dev only)
```

词典加载失败或文件夹为空时，DLL 进入 idle 模式：只打日志，不装任何 hook。
If the dictionary folder is missing or empty, the DLL goes idle: it logs and installs no hooks.

---

## 技术架构 / Technical architecture

```
文本层 / Text layer
  Hook A  DrawWide     R9=text ptr   -> dictionary swap
  Hook B  Format       RDX=fmt ptr   -> dictionary swap
  Hook J  Subtitle     RCX=text ptr  -> dictionary swap BEFORE engine word-wrap
  Hook F/G LangCur/LangTxt            -> early full-sentence swap (language-service return layer)
字形层 / Glyph layer (only when translated text contains CJK)
  Hook C  FontLookup   -> return fake font object per fontId
  Hook D  TexObj       -> magic texId -> fake texture object (w/h)
  Hook E  SrvResolve   -> magic texId -> self-built atlas SRV
```

> **v7.6 起目标地址不再硬编码**（括号内为 Steam 版 IDA 名 `sub_1408B5FF0` 等，仅供对照）。
> 运行时改由 **AOB 特征扫描**定位：函数入口在 `.text` 段内按字节特征唯一匹配；引擎全局
> （字体表/字体数/D3D device/context）由锚点函数内的 rip-relative 指令现场解码。
> 因此同一份 `SR3R_I18N.asi` 同时适用于 **Steam 版 `SRTTR.exe`** 与 **Epic 版 `SRTTR_EPIC.exe`**。
> 离线自检：`python Tools/verify_aob.py`（在两个 EXE 上验证 12 条特征唯一命中 + 全局解算）。
> Since v7.6 targets are **not hardcoded** (names in parentheses are Steam-build IDA labels, for reference only).
> Function entries are found by unique AOB byte-pattern scan in `.text`; engine globals are decoded from
> rip-relative operands inside anchor functions. One binary therefore serves both **Steam `SRTTR.exe`**
> and **Epic `SRTTR_EPIC.exe`**. Offline self-check: `python Tools/verify_aob.py`.

要点 / Key points:

1. **词典**（/ Dictionary）
   - 开链 CRC32 哈希（65536 桶 ×2 字节宽字符 CRC），全部字符串驻留 128MB arena，加载后只读、无锁查询。
   - Open-chaining CRC32 hash (65,536 buckets), all strings in a 128 MB arena; read-only after load, lock-free lookups.
   - 查表在微秒级；**不要**怀疑它慢（见性能节 / see Performance section）。

2. **伪字体对象**（/ Fake font object）
   - 引擎字体布局：208B 头 + metrics(16B/字形) + xtab/ytab(4B/字形) + kern 表。
   - Engine font layout: 208-byte header + metrics (16 B/glyph) + xtab/ytab (4 B/glyph) + kern table.
   - 伪造对象把 `count` 扩为 0xFFE0（覆盖 0x20–0xFFFF）；官方槽位区（约 336 字形）的 metrics/xtab/ytab/kern **原样照抄**，保证英文渲染逐像素不变。
   - The fake extends `count` to 0xFFE0 (0x20–0xFFFF); the official slot region (~336 glyphs) is copied byte-for-byte, so English rendering is pixel-identical.
   - 中文槽位 metrics：advance=缩放后步进、quad 宽=cellW（可收窄）、kernStart=-1（无 kern），xtab/ytab 指向自建图集区；quad/UV 高度锁官方 cellH（对象级，中英共享，不可改）。
   - CJK slot metrics: advance = scaled advance, quad width = cellW (shrinkable), kernStart=-1 (no kerning), xtab/ytab point into the new atlas region; quad/UV height is locked to the official cellH (object-level, shared with English — untouchable).

3. **两阶段后台构建**（/ Two-phase background build）
   - 字符集 = 词典收集 ∪ charlist.txt（官方简体 le_data 用字，v7.5.1）→ 字形覆盖不再受词典用字限制。
   - Charset = dictionary-collected ∪ charlist.txt (official simplified le_data charset, v7.5.1) → glyph coverage no longer bounded by dictionary text.
   - 阶段 1（后台线程）：stb_truetype 光栅化全部字符 → 灰度 cell 缓存；容量不足时自适应收窄 cellW（字形等比缩小、底部坐官方基线）。
   - Phase 1 (background thread): stb_truetype rasterizes every character into a grayscale cell cache; when capacity falls short, cellW auto-shrinks (glyphs scale uniformly, baseline pinned to the official one).
   - 阶段 2（后台线程）：读回官方图集（staging + 格式感知解码）→ 下方拼接中文区 → 创建 D3D11 纹理/SRV → 组装伪对象。
   - Phase 2 (background thread): read back the official atlas (staging + format-aware decode) → append the CJK region below → create D3D11 texture/SRV → assemble the fake object.
   - 构建期间引擎用官方字体渲染中文，被引擎自身边界检查安全拦截 → **短暂空白后自动恢复**，不崩溃、不花屏。
   - While building, the engine renders CJK with the official font; its own bounds check turns misses into blanks — a brief blank that self-heals, no crash.

4. **MAGIC texId 安全区**（/ MAGIC texId safe zone）
   - `0x60000000 + fontId`：bit24=0 避开引擎动态纹理分支，数值远超纹理注册数，且 SRV 缓存表对界外 id 自动跳过读写 → 无越界副作用。
   - `0x60000000 + fontId`: bit24=0 keeps it out of the engine's dynamic-texture branch, the value far exceeds the texture registry, and the SRV cache auto-skips out-of-range ids — no side effects.

5. **防错位**（/ Update-resilience）
   - 安装每个 hook 前比对目标函数入口 16 字节特征；游戏更新后特征不匹配即放弃安装（idle），绝不盲 patch。
   - Each hook verifies a 16-byte prologue signature before installing; on mismatch (game update) it aborts to idle rather than blindly patching.

---

## 可靠性结论与陷阱 / Verified facts & pitfalls

以下均为本工程实测/反汇编实证的结论，按"踩坑代价"排序 / All verified by testing or disassembly in this project, ordered by how expensive each was to learn:

1. **kern 空指针崩溃（v6.6）** / **kern-table null crash**
   - 容量截断丢弃的字形若落入零填充槽位区，其 `metrics+12 (kernStart)` 是 0 而不是 -1；引擎渲染到该字时无条件扫 kern 表 → 解引用 NULL 崩溃。
   - A truncated glyph landing in the zero-filled region has `kernStart == 0`, not -1; the engine unconditionally walks the kern table on draw → null deref crash.
   - **铁律：任何未填槽位，kernStart 必须显式写 -1。** / **Rule: every unfilled slot must get kernStart = -1 explicitly.**

2. **填充顺序覆盖（v6.7）** / **fill-order clobbering**
   - "空白兜底 → 中文真实值"两段填充若倒序执行，兜底值会把刚写好的 UV/kernStart 全部覆盖 → 中文全灭。
   - If the blank-fallback pass runs after the CJK pass, fallback values overwrite real UVs/kernStart → all CJK invisible.
   - **铁律：先兜底，后真实。** / **Rule: fallback first, real values second.**

3. **官方图集是 BC 压缩**（BC3/DXT5）/ **Official atlases are BC-compressed (BC3/DXT5)**
   - staging `RowPitch` 是**块行字节数**（如 2048 宽 BC3 = 8192），不是像素行 ×4。
   - The staging `RowPitch` is the **block-row size in bytes** (2048-wide BC3 → 8192), not pixels×4.
   - 必须按 DXGI 格式选 CPU 解码器逐块解码（本 DLL 内置 BC1/2/3/4）；图集统一转 BGRA8 再拼接。
   - Decode block-by-block with a CPU decoder selected by DXGI format (BC1/2/3/4 implemented here); normalize to BGRA8 before compositing.

4. **图集加宽后读回宽高要用官方源宽** / **Use the source width when reading back a widened atlas**
   - 伪图集 8192 宽、官方源 4096 宽：读回循环必须按源宽 4096 拷贝、写入新 pitch，否则官方区被拉花。
   - Fake atlas 8192 wide, official source 4096: the readback loop must copy by the *source* width into the new pitch, or the official region smears.

5. **触发点选择** / **Hook-point choice**
   - 菜单文本和 DrawWide 走不同渲染器，但都必经 `FontLookup` → 在那里触发升级才能全覆盖；游戏共 3 个字体（正文/大标题/调试），零售版只有前两个需要升级。
   - Menu text and DrawWide use different renderers but both funnel through `FontLookup` — trigger upgrades there for full coverage. The game has 3 fonts (body / headers / debug); only the first two need upgrading in retail.

6. **触发条件用"文本字符集扫描"而不是"词典 miss"** / **Trigger on text charset scan, not dictionary misses**
   - 只要当前绘制文本含词典字符集内的字符就触发该字体升级；与替换是否命中无关。
   - Trigger a font upgrade whenever the *drawn text* contains any character from the dictionary charset — regardless of whether the swap hit.

7. **引擎边界检查可依赖（本作）** / **Engine bounds checks are reliable (this title)**
   - 槽码查询函数对 `slot < 0 || slot >= count` 返回 -1 并用 missAdvance 兜底；未就绪时把官方对象交给引擎渲染中文是安全的（空白回退）。
   - The slot query returns -1 with missAdvance fallback for out-of-range slots; handing the official font to the engine during build is safe (blank fallback).

8. **dump 收集别逐条刷盘** / **Never fflush per dumped string**
   - 切界面时未命中文本集中涌入，逐条 `fflush` 让渲染线程等磁盘 → 卡顿。改为 1MB 全缓冲 + 后台线程 30s 批量落盘 + 退出 fclose。
   - Scene transitions flood new misses; per-entry `fflush` stalls the render thread on disk I/O. Use 1 MB full buffering + a 30 s background flush + fclose at exit.

9. **字体构建必须整体后台化** / **Font build must be fully off-thread**
   - 读回+拼接+上传大纹理在渲染线程做 = 进主界面卡 1 秒；全部移入后台线程（含 D3D 阶段，CAS 3→5 防并发，临时失败 100ms 间隔重试 30s）。
   - Readback + composite + upload on the render thread = ~1 s main-menu stall. Move everything (incl. the D3D stage) to a background thread with CAS 3→5 concurrency guard and a 30 s retry loop at 100 ms intervals.

10. **MinHook + /GL 的坑** / **MinHook + /GL gotcha**
    - MinHook 静态库以 /GL 编译，链接时强制重启 LTCG；无功能影响，但别在链接器里既关 LTCG 又链 MinHook。
    - The MinHook static lib is built with /GL, forcing an LTCG restart at link time; harmless, just don't fight it by disabling LTCG while linking MinHook.

11. **编译环境硬约束** / **Build-environment constraints**
    - VS2017 v141 工具集、/MT、C++17；x64。
    - VS2017 v141 toolset, /MT, C++17; x64.

12. **语音字幕链不走 Format，且尾部带时长控制码**（v7.5）/ **Voice subtitles bypass Format and carry a trailing duration code**
    - 语音字幕文本经字符串表直出，不经 formatter，也不经过语言服务 —— 在其绘制入口（折行发生之前）替换才是唯一可靠落点；该函数返回值就是显示时长（秒）。
    - Voice-subtitle text goes straight from the string table to the draw entry; it never passes the formatter or the language service. Replacing at the draw entry — before word-wrap — is the only reliable interception point; the function's return value *is* the display duration in seconds.
    - 文本尾部可能有字面 `\n<毫秒>` 控制码（时长由引擎 `atoi/1000` 解析）：剥离后查词典、命中后拼回原尾码，时长语义才不变。
    - The text may end with a literal `\n<milliseconds>` duration code (parsed by the engine as `atoi/1000`): strip it before the dictionary lookup, append the original tail back after the swap — otherwise subtitle timing changes.
    - 佐证：游侠汉化同样 hook 此函数入口（SIG3），词典 17411 条 KEY 全是英文整句 —— 折行残段问题在入口整串替换架构下天然不存在。
    - Corroboration: the ali213 patch hooks this same entry (SIG3) with 17,411 full-sentence keys — wrap-fragment misses simply cannot exist under entry-point whole-string replacement.

13. **图集 cell 高度锁死，只能收窄宽度**（v7.5.1）/ **Atlas cell height is locked; only width can shrink**
    - 反汇编实证：quad/UV 高度取自 font 对象 +22（对象级，中英文共享）；UV 宽度却是每槽位独立（metrics+4）。动高度会把英文一起压扁，动宽度只影响中文。
    - Disassembly-verified: quad/UV height comes from font+22 (object-level, shared with English); UV width is per-slot (metrics+4). Changing height squashes English too; changing width only affects CJK.
    - 容量不足时方案：cellW 自适应收窄（每轮 -8 直到 列×行 ≥ 字符数），字形按 cellW/cellH 等比缩小、底部对齐官方基线 —— 不变形、英文零影响。
    - Capacity fix: auto-shrink cellW (-8 per round until cols×rows ≥ glyph count); glyphs scale uniformly by cellW/cellH with the baseline pinned — no distortion, zero English impact.
    - 前提：思源黑体 CJK 字形 advance=100% em（全宽），横向压扁必然变形，所以必须等比缩放而非只压宽度。
    - Prerequisite: Source Han Sans CJK glyphs advance 100% em (full-width), so horizontal-only compression would distort — uniform scaling is mandatory.
    - charlist.txt 第 0 行是零宽字符水印（0x200B-0x200D），解析时必须跳过，否则会混入数千个“幽灵字符”。
    - charlist.txt line 0 is a zero-width watermark row (0x200B-0x200D); the parser must skip it or thousands of phantom characters slip in.

---

## 性能与显存 / Performance & VRAM

| 项目 / Item | 数值 / Value |
|---|---|
| 查表延迟 / Lookup latency | 微秒级（CRC 哈希）/ microseconds (CRC hash) |
| font0 图集 / font0 atlas | 2048×13712 BGRA8 ≈ 107 MB VRAM |
| font1 图集 / font1 atlas | 8192×16348 BGRA8 ≈ 508 MB VRAM（官方宽 4096 装不下全字符集，按需加宽 / widened from 4096 because the official width cannot fit the full charset） |
| 首次构建 / First build | 每字体一次，后台线程，font0 ≈ 1 s / font1 ≈ 4 s；期间中文短暂空白 / once per font, background; brief blank while building |
| 常驻开销 / Steady-state | 每 draw 一次哈希查表 + 一次字符集扫描；无可测量帧耗 / one hash lookup + one charset scan per draw; no measurable frame cost |

显存预算参考：8 GB 卡实测无压力 / VRAM budget verified comfortable on an 8 GB card.

---

## 调试与日志 / Debugging & logs

`SR3R_I18N.log` 关键行 / Key log lines:

```
cfg: ... loaded (dict_dir=dict font_file=... dump=0)
dict: 96 files, ... keys, 0 HASH_ skipped, ... cjk, ... bad, arena .../131072 KB
charlist: ...merged 624 new chars (total 2996, ...)
font0: upgrade requested (first CJK text)
font0: rasterized 2287 cells (...)
font0: src atlas 2048x1024 fmt=BC3(76) ... RowPitch=8192
font0: LIVE atlas=2048x13712 cells=2287 kept=2287
v7.5 active: dict=... keys (... files), hooks A=1 B=1 C=1 D=1 E=1 F=1 G=1 H=1 I=1 J=1, idling
stats: draw hit=... miss=... | format hit=... miss=... | wrap=... | sub hit=... miss=... | dumped=... | fonts=...
```

> v7.5.2 起探针统计行（early/setText/setTag/refresh/wrapEv）已退役不再输出，仅保留主统计行。
> Since v7.5.2 the probe stat lines (early/setText/setTag/refresh/wrapEv) are retired; only the main stats line remains.

排查速查 / Quick diagnosis:
- 中文全空白 → 查 `font0: LIVE` 是否出现；没有则看它上一行报错 / all CJK blank → check for `font0: LIVE`; read the error line above it if missing
- 崩溃在渲染 → 先怀疑 kernStart 兜底（陷阱 1）/ crash in rendering → suspect kernStart fallback first (pitfall 1)
- `signature mismatch, ABORT` → 游戏更新，需重新定位特征码 / game updated; re-derive signatures
- `DumpText.dtxt` 默认不再收集（v7.5.2）；需要时在 ini 设 `dump_enabled=1`，每 30 秒批量落盘 / miss collection is off by default since v7.5.2; set `dump_enabled=1` to re-enable (30 s flush batches)

---

## 移植到其他游戏 / Porting to other games

可直接复用的思路（按性价比排序）/ Directly reusable ideas (best value first):

1. 词典层：le_strings txt + CRC 哈希 + arena（与游戏引擎无关）/ dictionary layer: le_strings txt + CRC hash + arena (engine-agnostic)
2. 两阶段字体构建 + 后台线程 + 官方图集读回拼接 / two-phase font build off-thread + official-atlas readback composite
3. MAGIC texId 式"安全区"选法：选一个引擎永不注册、且所有缓存表对它越界安全的 id 段 / the MAGIC-id "safe zone" trick: pick an id range the engine never registers and whose caches bounds-check safely
4. 伪对象槽位填充三铁律：kernStart=-1、先兜底后真实、防御检查别挡合法请求 / the three fake-object fill rules: kernStart=-1, fallback-then-real, never let defensive checks reject legal requests

必须按目标游戏重做的部分 / Must be redone per game:
- 5 个 hook 目标函数与特征码、字体对象布局、纹理对象布局、图集格式 / the five hook targets + signatures, font/tex object layouts, atlas format

---

## 限制 / Limitations

- 仅覆盖宽字符文本链路（UI/字幕/菜单）；引擎内部窄字符路径（如某些调试输出）不处理 / wide-char paths only; narrow internal paths untouched
- 字符集超容量时 font1 自动收窄 cellW（v7.5.1），中文字形缩小到约 78% 宽度；更高容量需继续加宽图集（显存线性增长）/ when the charset exceeds capacity, font1 auto-shrinks cellW (v7.5.1), rendering CJK at ~78% width; more capacity requires a wider atlas (VRAM grows linearly)
- 游戏更新会使特征码失效（设计为安全 idle，不会崩）/ game updates invalidate signatures (by design it idles safely, never crashes)
- 未命中文本收集需要 `dump_enabled=1` 且重启后生效 / miss collection requires `dump_enabled=1`, applied on restart

> AI生成
