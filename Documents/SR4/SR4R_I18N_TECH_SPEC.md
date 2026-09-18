# SR4R_I18N — Saints Row IV 外挂汉化 DLL 技术方案

## 1. 项目目标

为 Saints Row IV（Steam EOS 2024+ 版）实现运行时外挂式中文汉化，架构参照已交付的 SR3R_I18N（黑道圣徒3重制版）。

**核心原则**：不动任何游戏资源文件，DLL 卸载即完全恢复原版。

---

## 2. 引擎与版本背景

| 项目 | SR3R（已交付） | SR4（本工程） |
|------|----------------|---------------|
| 游戏名 | Saints Row: The Third Remastered | Saints Row IV |
| 主程序 | SRTTR.exe (x64) | sr_hv.exe (x64) |
| 引擎分支 | SR35_GGP (2020 重制版) | SR35_GGP (2013 原版) |
| imagebase | 0x140000000 | 0x140000000（相同） |
| .text 段大小 | — | 0x013FEE9C (~20MB) |
| 主程序大小 | — | 28.8 MB |
| vpp 格式版本 | version 6 (LZ4, 0x1000 对齐) | **version 10** (zlib 无 adler, 无对齐) |
| le_strings 格式 | 12B 头 + 16B bucket + 8B 条目 | **完全相同** |
| vf3 字体格式 | TNFV v4 | **完全相同** |
| ASI Loader | binkw64.dll 3.6MB 自带 | 原版 binkw64.dll 无 loader（用户已解决注入） |
| CJK 原生支持 | 无 | **有**：jap.vf3_pc + charlist_jp.dat (1893 汉字) |

**关键结论**：同引擎同架构，le_strings/字体格式完全兼容，SR3R 的成熟代码可以大规模移植。

---

## 3. SR4 vpp_pc 格式（已逆向验证）

```
Header (0x28 字节):
  +0x00  u32  magic    = 0x51890ACE
  +0x04  u32  version  = 10
  +0x08  u32  crc?
  +0x0C  u32  ?
  +0x10  u32  flags?
  +0x14  u32  count    (文件数)
  +0x18  u32  dirSize  (目录区字节数 = count * 24)
  +0x1C  u32  nameSize (名字区字节数)

Directory (count × 24 字节):
  +0x00  u32  nameOff    (名字区偏移)
  +0x04  u32  (0)
  +0x08  u32  dataOff    (数据区偏移)
  +0x0C  u32  usz        (解压后大小)
  +0x10  u32  csz        (压缩后大小)
  +0x14  u32  flags

Names: 紧跟目录，每个 \0 结尾
Data:  紧跟名字区（无 0x1000 对齐），每个文件 = zlib 流（无 adler32 尾部）

解压方法: zlib.decompressobj().decompress(blob)  (eof=False 属正常)
未压缩特例: csz == usz 且数据非 0x78 开头 → 直接存储
```

工具：`Tools/sr4_vpp.py`（已验证：misc.vpp_pc 317 文件全部解包成功）

---

## 4. 资源概况

### 4.1 misc.vpp_pc 内容（317 文件）

| 类别 | 数量 | 说明 |
|------|------|------|
| le_strings | 210 | 多语言 cz/de/es/fr/it/jp/nl/pl/ru/us，无 zh |
| 字体 (vf3_pc) | 6 | editor-default/font_body/font_header/font_header_pc/font_sk/jap |
| 字体图集 (gvbm_pc) | 12 | 上述字体 + 对应 _nobdr 版本 |
| charlist (dat) | 12 | 各语言字符映射表 |
| cpeg/gpeg | ~30 | UI 纹理 |
| lua | 2 | game_lib.lua, sr3_city.lua |

### 4.2 le_strings 分类（us 版本）

| 文件 | 条目数 | 说明 |
|------|--------|------|
| menu_us | 900 | 菜单文本 |
| subtitle_us | 603 | 字幕 |
| mission_us | — | 任务文本（最大，196KB） |
| hud_us | — | HUD |
| voice_us | — | 语音文本（1.7MB） |
| customize_us | — | 自定义 |
| new_sr35_us | — | SR35 新增文本 |
| platform_pc_us | — | PC 平台文本 |
| static_us | — | 静态文本 |
| 等 | — | 另有 dlc1-7, multiplayer, diversion 等 |

### 4.3 字体信息

| 字体 | vf3 大小 | gvbm 大小 | 说明 |
|------|----------|-----------|------|
| font_body | 16KB | 256KB | 正文字体 |
| font_header_pc | 19KB | 1.3MB | 标题字体 |
| font_sk | 39KB | 2.7MB | 大标题 |
| jap | 58KB | 2.7MB | 日文字体（含 CJK） |
| editor-default | 6KB | 64KB | 编辑器字体 |
| ug-debug | 6KB | 85KB | 调试字体 |

vf3 头格式：`TNFV` (magic) + version 4 + 变长布局（与 SR3R 完全相同）

### 4.4 charlist 分析

| 文件 | 映射项数 | CJK 汉字数 | 说明 |
|------|----------|-----------|------|
| charlist_jp.dat | 2377 | **1893** | 完整日文汉字集 |
| charlist_sk.dat | 1514 | 0 | 韩文 |
| charlist_us.dat | 284 | 0 | ASCII + 欧洲字符 |

**注意**：虽然 SR4 引擎原生支持 CJK（jap 字库），但用户选择了外挂 DLL 自建图集方案（与 SR3R 一致），不复用 jap 字库。

---

## 5. 方案架构

采用与 SR3R 相同的混合架构：

```
┌─────────────────────────────────────────────┐
│              SR4R_I18N.asi                  │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  文本层 (Text Layer)                │    │
│  │  - 词典加载 (le_strings txt 格式)   │    │
│  │  - CRC32 开链哈希 (65536 桶)        │    │
│  │  - 128MB 字符串 arena               │    │
│  │  - Hook A: DrawWide   (文本绘制)    │    │
│  │  - Hook B: Format     (格式化)      │    │
│  │  - Hook J: Subtitle   (字幕入口)    │    │
│  │  - Hook F/G: LangCur/LangTxt        │    │
│  │           (语言服务整句替换)        │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  字形层 (Glyph Layer)               │    │
│  │  - stb_truetype 光栅化思源黑体      │    │
│  │  - 伪字体对象 (count 扩为 0xFFE0)   │    │
│  │  - 自建图集 D3D11 纹理              │    │
│  │  - 读回官方图集 BC 解码 + 拼接      │    │
│  │  - Hook C: FontLookup (字体查询)    │    │
│  │  - Hook D: TexObj    (纹理查询)     │    │
│  │  - Hook E: SrvResolve (SRV 解析)    │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  安全机制                           │    │
│  │  - 16 字节特征码校验 (防更新错位)   │    │
│  │  - 词典空 → idle 模式               │    │
│  │  - 字体构建失败 → 回退官方          │    │
│  │  - kernStart=-1 铁律                │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

---

## 6. Hook 定位状态（全部完成）

### 6.1 函数调用拓扑桥接法

SR3R 和 SR4 使用不同编译器版本/选项，寄存器分配和指令选择完全不同，字节模式匹配不可靠。
但函数调用拓扑（谁调用谁）完全一致。通过 SR3R→SR4 的函数对应关系链式推导，精确定位目标函数。

### 6.2 全部 7 个 Hook 定位结果

> **⚠ 2026-09-12 修正**：下表 J 行（Subtitle）**已废弃** —— 它定位到的是 `char*`(UTF-8) 版
> 字幕函数，而实际在用的 hook 落点是 `wchar_t` 版（见 §6.6）。
> 本表按此错误定位推导出过一个错误的 GOG 地址，导致 GOG 版字幕 hook 静默失效。
> 其余 A~G 行经字节指纹 + IDA 语义复核**全部正确**。
>
> 另注意：本表特征码长度不足以做全模块 AOB 扫描 —— DrawWide 的 16B 在双版本各有
> **2 个命中**（宽字符版 / char 版孪生函数），LangCur 的 16B 有 **3 个命中**。
> 需要 AOB 化时请使用 §6.6 的定稿特征码。

| Hook | 名称 | SR3R VA | SR4 VA | 入口特征码 | 状态 |
|------|------|---------|--------|-----------|------|
| A | DrawWide | `0x1408B5FF0` | `0x140DC36A0` | `40 53 56 57 48 81 EC 10 01 00 00 8B BC 24 60 01` | ✅ |
| B | Format | `0x140812610` | `0x140CF9A00` | `40 55 56 57 41 57 48 8D AC 24 78 D0 FF FF` | ✅ |
| C | FontLookup | `0x140859B10` | `0x140BF8550` | `83 F9 FF 7D 3E 8D 81 FF FF FF 7F 83 F8 FF 7E 2E` | ✅ |
| D | TexObj | `0x14085DB30` | `0x140B7AF30` | `4C 63 C1 85 C9 78 77 44 3B 05 8E A3 D9 05 7D 6E` | ✅ |
| E | SrvResolve | `0x140915D60` | `0x140E2C9E0` | `40 53 48 83 EC 20 0F B6 DA 83 F9 FF` | ✅ |
| F | LangCur | `0x140812060` | `0x140CF1980` | `48 8B 05 B1 DD 4C 06 48 85 C0 74 0C 48 8B 50 08` | ✅ |
| G | LangTxt | `0x140812040` | `0x140CF1960` | `48 8B 05 D1 DD 4C 06 48 85 C0 74 0B 48 8B 10` | ✅ |
| ~~J~~ | ~~Subtitle~~ | `0x1402D2BC0` | ~~`0x140476D80`~~ | `40 53 55 56 57 41 54 41 55 41 56 41 57 48 81 EC F8 00 00 00 …` | ❌ **废弃，见 §6.6** |

### 6.3 SR4 架构变更（重要适配点）

#### Subtitle (Hook J) 架构变更
- SR4 字幕文本从 UTF-16 改为 **UTF-8**（`char*` vs `wchar_t*`）
- SR4 Subtitle (975B) 比 SR3R (1094B) 小 — 不含 `\n`+毫秒时长解析逻辑和 `0x2711` 计数器
- SR4 Subtitle 不直接调 DrawText，绘制逻辑移到独立的渲染函数
- SR4 不再引用 `byte_1415AD4B0`（字幕激活标志），改用 `dword_1417E3E6C` 状态机

#### SrvResolve (Hook E) 架构变更
- SR3R 从 SRV 全局表（`qword_1415456F0[+0x28]`，64字节步长 `shl rax, 6`）查找 SRV
- SR4 改为直接从 `texObj+0x38` 读取 QWORD 指针，跳过全局表索引
- 返回值偏移：SR3R `[entry+0x30]`/`[entry+0x38]` → SR4 `[ptr+8]`/`[ptr+0x10]`（ptr = entry+0x28）

#### 字体对象布局差异
- SR3R font 对象 texId 在 +184 偏移，SR4 改为 **+568** 偏移

### 6.4 完整函数映射表

| 功能 | SR3R | SR4 |
|------|------|-----|
| FontLookup (C) | `sub_140859B10` | `sub_140BF8550` |
| DrawWide (A) | `sub_1408B5FF0` (1333B) | `sub_140DC36A0` (1333B) |
| TexObj (D) | `sub_14085DB30` | `sub_140B7AF30` |
| TexObj wrapper | `sub_14085D930` (39B) | `sub_140B7AC00` (39B) |
| TexObj query (no anim) | — | `sub_140B7AC30` (0x7c) |
| Streaming tex resolver | `sub_1408FE2F0` | `sub_140D98D30` |
| SrvResolve (E) | `sub_140915D60` (259B) | `sub_140E2C9E0` (101B) |
| Tex flag query | `sub_140D313C0` (195B) | `sub_140DFD410` (75B) |
| Format (B) | `sub_140812610` (0x6d2) | `sub_140CF9A00` (0x78c) |
| LangCur (F) | `sub_140812060` | `sub_140CF1980` |
| LangTxt (G) | `sub_140812040` | `sub_140CF1960` |
| SrvResolve cache | `sub_140916DD0` | `sub_140E0F7D0`/`E0FCD0`/`E0FF30` (3变体) |
| Subtitle (J) `char*`版 | `sub_1402D2BC0` (0x446) | `sub_140476D80` (0x3cf) — **非 hook 落点** |
| Subtitle (J) `wchar_t`版 | — | `sub_1403D18F0` (0x458=1112B) — **实际 hook 落点** |
| 折行 wrapper | `sub_14085A1F0` (83B) | `sub_140BF8D40` (80B) |
| 折行核心 | `sub_140859620` | `sub_140BE8830` |
| 测量回调 | `sub_140859C80` | `sub_140BF8760` (27B) |
| 测量函数 | — | `sub_140BE7D60` |
| 测量 wrapper | — | `sub_140BF8740` (14B) |
| D3D11 创建 | `sub_140921C80` | `sub_140E4F1A0` |

### 6.5 全局变量映射表

| SR3R 全局变量 | SR4 全局变量 | 用途 | 状态 |
|---------------|-------------|------|------|
| `qword_142998650` | `qword_1469152D8` | 纹理表 (24字节步长) | ✅ |
| `dword_142998660` | `dword_1469152CC` | 纹理计数 | ✅ |
| `dword_14295B650` | `dword_146AEA4A0` | 随机种子 | ✅ |
| `qword_143070F02` | `qword_1473F0992` | streaming tex 计数 (u16) | ✅ |
| `xmmword_143070F10` | `xmmword_1473F09A0` | streaming tex 表 (8字节步长) | ✅ |
| `qword_142906D28` | `qword_1471BF738` | 语言服务对象 (vtable[0]=LangTxt, [1]=LangCur, [3]=Symbol) | ✅ |
| `qword_1430784F8` | `qword_147667C70` | D3D11Device* | ✅ |
| `qword_143078500` | `qword_147667C78` | D3D11DeviceContext* | ✅ |
| `qword_1415456F0` | **不再需要** | SRV全局表 — SR4 SrvResolve 改用 `[texObj+0x38]` 直接指针 | ✅ |
| FONTTAB | `qword_146AE7060` | 字体表 | ✅ |
| FONTCOUNT | `dword_146AE504C` | 字体计数 | ✅ |
| `qword_142998658` | ? | SRV cache (8字节步长) | ❌ 待定位 |

### 6.6 AOB 定位规范（2026-09-12 定稿，取代 §6.2 的硬编码 VA 方案）

#### 6.6.1 为什么改成 AOB

§6.2 的特征码只用于「已知 VA 处的比对校验」，不是为全模块扫描设计的：
- **DrawWide** 16B → 双版本各有 **2 命中**:宽字符版（`cmp word ptr [rbx],0`）
  与 char 版孪生函数（`cmp byte ptr [rbx],0`），消歧字节在 `0x21` 之后。
- **LangCur** 16B → **3 命中**（都是 `mov rax,[imm]; test; jz; mov rdx,[rax+8]` 形态）。

直接拿它们做扫描会命中错误的孪生函数。

#### 6.6.2 定稿特征码（`??` = 通配，4 字节相对地址一律通配）

| Hook | 长度 | 通配区间 | Steam | GOG | EPIC | 命中数(S/G/E) |
|------|------|---------|--------|-----|------|--------------|
| A DrawWide | **48** | [44..47] `call rel32` | `0x140DC36A0` | `0x140D4E520` | `0x140DBE570` | 1 / 1 / 1 |
| B Format | 16 | — | `0x140CF9A00` | `0x140C88810` | `0x140CF48D0` | 1 / 1 / 1 |
| C FontLookup | 16 | — | `0x140BF8550` | `0x140BBBBB0` | `0x140BF3D00` | 1 / 1 / 1 |
| D TexObj | 16 | [10..13] RIP disp32 | `0x140B7AF30` | `0x140B99ED0` | `0x140B766D0` | 1 / 1 / 1 |
| E SrvResolve | 16 | — | `0x140E2C9E0` | `0x140DC6E00` | `0x140E278B0` | 1 / 1 / 1 |
| F LangCur | **27** | [3..6] RIP disp32 | `0x140CF1980` | `0x140C88120` | `0x140CEC850` | 1 / 1 / 1 |
| G LangTxt | 16 | [3..6] RIP disp32 | `0x140CF1960` | `0x140C88100` | `0x140CEC830` | 1 / 1 / 1 |
| J Subtitle | 16 | — | `0x1403D18F0` | `0x1404B1E00` | `0x1403D0850` | 1 / 1 / 1 |

三版偏移差不成簇（如 Epic−Steam：多数 −0x5130 但 TexObj −0x4860、Subtitle −0x10A0），
**uniform delta 不可行，AOB 是唯一可靠方案**（EPIC 2024-02-28 构建，比 Steam/GOG 都新）。

#### 6.6.2.1 全局变量三版对照

| 变量 | Steam | GOG | EPIC | EPIC 依据 |
|------|-------|-----|------|----------|
| fontTab | `0x146AE7060` | `0x14698A5D0` | **`0x146ACD308`** | IDA 反编译 EPIC FontLookup |
| fontCount | `0x146AE504C` | `0x14698A5C8` | **`0x146ACD304`** | 同上（注意：EPIC 布局 fontTab−4，GOG 是 fontTab−8，Steam 相距 0x2014 —— 三版结构排布不同，不可互推） |
| D3DDevice | `0x147667C70` | `0x14761DEF8` | **`0x14764DF70`** | EPIC D3D11 创建函数 sub_140E4A070 内 `qword_14764DF70 = ppDevice` |
| D3DContext | `0x147667C78` | `0x14761DF00` | **`0x14764DF78`** | 同函数内 `qword_14764DF78 = ppImmediateContext` |

D3D 槽定位方法（跨版本稳健）：D3D11CreateDevice 的 IAT 槽 → thunk（`jmp [IAT]`）→
唯一调用者 = D3D11 创建函数 → 函数内对 `&ppDevice`/`&ppImmediateContext` 的全局写入即槽地址。

DrawWide 消歧点：`0x21` 处 `44 0F 29 74 24 70`（wchar 版）
vs `44 0F 29 AC 24 80 00 00 00`（char 版）。

#### 6.6.3 Subtitle (J) 定位修正

| 项 | 旧（错误） | 新（正确） |
|---|---|---|
| Steam | `0x140476D80` | **`0x1403D18F0`** |
| GOG | `0x140574250` | **`0x1404B1E00`** |
| size | 0x3CF = 975 | **0x458 = 1112** |
| 签名 | `__int64(uint,int,int64,int64,int)` | **`double(__int64,float,double,int)`** |
| 文本 | `char*` (UTF-8) | **`wchar_t`** |
| 依据 | §6.2 拓扑桥接表 | Steam 实际 hook 入口字节指纹 + IDA 语义 |

DLL 里 `SubtitleDraw_t = double(__fastcall*)(const wchar_t*, float, double, int)`，
只有新版签名能对上。旧定位的特征码校验会失败 → **字幕 hook 静默不装**（不崩溃，难察觉）。

注：§6.3 「SR4 字幕文本从 UTF-16 改为 UTF-8」的结论同样不适用于本 hook 落点。

#### 6.6.4 复现工具

| 脚本 | 作用 |
|------|------|
| `Tools/gen_aob2.py` | 从双版本 PE 自动生成 + 校验 AOB（输出可直接粘贴的 C 数组） |
| `Tools/sim_aobscan.py` | 从 `dllmain.cpp` **源码解析**特征码，模拟 `AobScan()` 的 C 逻辑在两版 EXE 上跑落点验证 |
| `Tools/build_check.py` | 无 VS IDE 时用 MSVC 命令行完整构建 DLL（`--syntax-only` 只做语法检查） |

运行时行为：**L1 精确特征码扫描（要求恰好 1 命中）→ L2 放宽特征码（同样要求唯一）→
L3 换代构建精确特征码（同样要求唯一）→ 版本 VA 表**；任一路径都需通过入口特征码校验才装
hook，且校验必须用「命中该地址的那一套 sig/mask」（`LocResult` 三层一起带出来）。
日志会标注 `via aob` / `via aob-l2` / `via aob-l3` / `via va`。
L2 与可执行段落盘见 §6.8（v1.3），L3 与两阶段安装见 §6.9（v1.4）。

### 6.7 全局变量运行时自解（v1.2，2026-09-17）——面向未知构建 / 商店加密版

#### 6.7.1 为什么需要

版本 VA 表只能覆盖已知构建。**Microsoft Store（MSIXVC）版的主程序在授权进程之外读取只能
拿到密文**，离线根本取不到地址：

| 检测项 | `sriv_microsoft.exe`（MS Store 版主程序，`sriv.exe` 的副本） |
|--------|------------------------------------------------------------|
| 大小 | 26,999,296 B |
| `MZ` / `PE\0\0` | **不存在**（文件头即随机字节） |
| 逐块熵（每 2MB） | **7.9999**（整文件 8.0000） |
| 明文串 | 无 ASCII≥8、无 UTF-16LE≥6、无 `Microsoft`/`sr_hv`/`bink` |
| 16B/512B/4KB/64KB 块重复 | 全部 0（非 ECB、非短键 XOR） |
| `P(d[i]==d[i+k])` | 各 k ≈ 0.0039 = 1/256 |

成因：MS Store 版主程序**按授权进程透明解密** —— 游戏进程内是正常 PE（AOB 照常可用），
任何非授权进程（含从 `WindowsApps` 拷出的副本）读到的都是密文。
**结论：离线取指纹 / 取 AOB / 取全局变量这条路是断的**，正确应对是让 DLL
**不再需要**离线地址。

#### 6.7.2 fontTab / fontCount：从 FontLookup 函数体内反解

AOB 定位到 FontLookup 后，该函数本体在 Steam / GOG / EPIC 三版中**字节布局完全一致**
（仅 RIP disp32 不同）。相对函数入口的偏移：

| off | 指令 | 全局 | Steam | GOG | EPIC |
|-----|------|------|-------|-----|------|
| +0x10 | `3B 05 disp` cmp eax,[rip] | 第 2 张表计数 | 0x146AE7080 | 0x14698A5CC | 0x146ACD328 |
| +0x2C | `48 8B 05 disp` mov rax,[rip] | 第 2 张表基址 | 0x146AE7078 | 0x14698A5E8 | 0x146ACD320 |
| **+0x43** | `3B 0D disp` cmp ecx,[rip] | **fontCount** | 0x146AE504C | 0x14698A5C8 | 0x146ACD304 |
| +0x4F | `48 8B 05 disp` mov rax,[rip] | 备用对象 | 0x146AE7070 | 0x14698A5E0 | 0x146ACD318 |
| **+0x57** | `48 8B 05 disp` mov rax,[rip] | **fontTab** | 0x146AE7060 | 0x14698A5D0 | 0x146ACD308 |

dllmain.cpp 内嵌的掩码模式（`??` = disp32 通配，前缀 `FindMasked` 只扫函数头 0x100 字节）：

```
fontTab   : 48 8B 05 ?? ?? ?? ?? 48 63 C9 48 8B 04 C8 C3     ← 尾部 48 8B 04 C8 C3 是
fontCount : 3B 0D ?? ?? ?? ?? 7D ?? 85 C9 79 ?? 48 8B 05        “返回 fontTab[id]” 定式，用于消歧
```

- `RipTargetVa(instr, instrLen)`：`target = instrVA + instrLen + disp32`（disp32 恒为指令末 4 字节）
- 落点必须落在 `[0x140000000, 0x140000000 + SizeOfImage)`，否则判为模式误匹配
- 两条**同时**解出才 `resolved = true`
- 解出的 VA 再经 `VA<T>()` 换算（口径仍是 GAME_BASE 相对）

#### 6.7.3 D3D 设备 / 上下文：零全局变量的版本无关获取

不再读 `qword_147667C70/C78`，改为**从手上的 D3D COM 对象反查**：

```cpp
offSrv->GetDevice(&dev);          // ID3D11ShaderResourceView : ID3D11DeviceChild
dev->GetImmediateContext(&ctx);   // 与 D3D11CreateDevice 的 ppImmediateContext 是同一对象
```

`FinishFont()` 里本来就要拿官方图集 SRV（`g_origSrvResolve(offTexId,0)`），
顺手反查即可，**零新增 hook、零时序问题**。结果存进程级缓存（`g_devCache/g_ctxCache`，
只取一次不 Release）。版本 VA 表降级为兜底，且仅在指纹命中已知构建时才启用。

#### 6.7.4 自学习槽位表（无 fontTab 时的替代）

fontId 即 `fontTab` 下标（三版反汇编证实 `FontLookup(id)` 就是 `return fontTab[id]`）。
`HookFontLookup` 每次调用都能同时拿到 `(fontId, 官方对象)`，顺手记进 `g_slotMap[256]`；
`ResolveSlot` 先查自学习表、再查版本 fontTab。校验"官方对象仍在槽位"时，
fontTab 不可用则改用 `g_origFontLookup((int)slot) == f->official`（语义等价）。

#### 6.7.5 失败必须降级、不许崩溃

```cpp
if (dyn.resolved)   { g_fontTabPtr = VA(dyn.fontTab);   ... }   // 自解成功
else if (指纹命中)  { g_fontTabPtr = VA(cfg.vaFontTab); ... }   // 已知构建回退
else                { g_fontTabPtr = nullptr;           ... }   // 未知构建: 宁可少一个功能
```

未知构建下 `g_fontTabPtr / g_fontCountPtr / g_devSlot / g_ctxSlot` **一律置空**，
防止旧代码 `(*g_fontTabPtr)[slot]` 解引用别的构建 `.data` 里的垃圾指针 → 随机崩溃。
此时文本替换照常工作，字形层改走自学习槽位表 + SRV 反查设备。

#### 6.7.6 复现 / 验证工具

| 脚本 | 作用 |
|------|------|
| `Tools/verify_runtime_globals.py` | **从 dllmain.cpp 源码正则解析**掩码模式，在 Steam/GOG/EPIC 三版上复刻 `AobScan` + `ResolveFontGlobalsDyn` 逻辑，断言解出的 fontTab/fontCount == `CFG_*` 硬编码值（当前三版全部 OK） |

指纹未命中时日志仍打印 `NEWBUILD file=… TimeDateStamp=… EntryPoint=… SizeOfImage=…`
（这三项**运行时**可正常读取）—— 用户跑一次即可把新构建补进 `kBuildFp`。

### 6.8 L1/L2 双层特征码 + 可执行段落盘（v1.3，2026-09-17）——面向「同源但重编译」的构建

#### 6.8.1 实测现场（MS Store 版首次运行日志，2026-09-17 20:26）

```
exe: !! NEWBUILD file=sriv.exe TimeDateStamp=5E58CEF8 EntryPoint=00FC44FC SizeOfImage=07E6D000
globals: dynamic resolve OK | FontLookup=0x140E0A530 fontTab=0x146CFCDB0 fontCount=0x146CFCDA8
hook DrawWide   installed (via aob)      ← A 命中
aob Format: 0 hits -> AOB locate failed  ← B 落空
hook Format  (via va): signature mismatch, ABORT
hook FontLookup/TexObj/SrvResolve/LangCur/LangTxt installed (via aob)
aob Subtitle: 0 hits -> AOB locate failed ← J 落空
hook Subtitle (via va): signature mismatch, ABORT
SR4R v1.2 active: … hooks A=1 B=0 C=1 D=1 E=1 F=1 G=1 J=0
```

即：**全局变量自解成功（§6.7 生效），6/8 hook 的 L1 唯一命中，只有 B(Format) 与
J(Subtitle) 落空**。`sriv.exe` 的 TDS = `5E58CEF8` = 2020-02-27，比 Steam(2023-04)、
GOG(2023-02)、EPIC(2024-02) 都**早**三年 —— 是「同源不同期重编译」，不是另一个产品。

#### 6.8.2 为什么恰好是这两个函数落空：栈帧 imm 在特征码里

对照三版反汇编，B 与 J 的 L1 特征码**都盖住了编译期布局字段**：

| Hook | L1 里被覆盖的漂移字段 | 三版同值 | 含义 |
|------|---------------------|---------|------|
| B Format | `[10..13]` `lea rbp,[rsp-2F88h]`；`[15..18]` `mov eax,0x3088`（`__chkstk` 帧大小） | 0x2F88 / 0x3088 | 局部缓冲区总量 |
| J Subtitle | `[10..13]` `lea rbp,[r11-458h]` | -0x458 | 局部缓冲区总量 |

同一份源码换编译器版本 / 优化档 / 加一个局部变量，这两个 imm 就变，而**操作码序列不变**。
其余 6 条 hook 的特征码恰好都落在「不含 imm 的前导区」，所以照常命中 —— **失败模式与
表象完全自洽**。

其余同类漂移字段（都在 L2 里通配，见 §6.8.3）：

| 字段形态 | 例子 | 漂移来源 |
|---------|------|---------|
| `mov [rbp+disp32], reg` | B `[40..43]` `mov [rbp+2F50h],rax` | **帧大小 − 常量**，与 `lea rbp` 同源 |
| `mov rax,[rip+disp32]` | `/GS` cookie 位置 | 模块内 .data 布局 |
| `call rel32` | `call __chkstk` | 目标函数地址 |
| `mov [rsp+imm8], reg` | 参数溢出槽 | 帧内槽位 |
| `sub/add rsp, imm` | 帧分配量 | 帧大小 |

#### 6.8.3 L2 放宽特征码

每个 hook 增加第二套特征码 `R2_*` / `RM_*`（`SR4R_I18N/aob_l2_arrays.inc`，由工具生成）：

| Hook | L2 长度 | 通配数 | 通配内容（相对入口） |
|------|--------|-------|--------------------|
| A DrawWide | 48 | 16 | `sub rsp,110h` imm、`[rsp+160h]`、`[rsp+0F0h]`、`[rsp+70h]`、窗口尾 `call rel32` |
| **B Format** | **48** | **20** | `[10..13]` `lea rbp` 帧、`[15..18]` chkstk 帧大小、`[20..23]` `call __chkstk`、`[30..33]` /GS cookie、**`[40..43]` `mov [rbp+2F50h],rax`** |
| C FontLookup | 48 | 5 | `cmp eax,[rip+disp32]`、窗口尾 `mov rax,[rip+disp32]` |
| D TexObj | 48 | 8 | `cmp r8d,[rip+…]`、`mov r9,[rip+…]` |
| E SrvResolve | 48 | 6 | `sub rsp,20h` imm8、`call rel32`、`add rsp,20h` imm8 |
| F LangCur | 27 | 4 | `mov rax,[rip+disp32]` |
| G LangTxt | 27 | 4 | `mov rax,[rip+disp32]` |
| **J Subtitle** | **48** | **18** | `lea rbp,[r11-458h]`、`sub rsp,540h`、两处 xmm 保存槽、/GS cookie、`mov [rbp+3E0h],rax` |

**通配集合由反汇编机械推导**（`operand_wild_set()`），不再人工圈定。规则：

| 通配（随构建漂移） | 保留（源级语义，跨构建稳定） |
|---|---|
| 基址为 `rsp`/`rbp`/`r11` 的 `disp` —— 栈帧溢出槽 / 帧相对寻址 | 操作码 / ModRM / SIB / 前缀 / 寄存器编码 |
| 基址为 `rip` 的 `disp` —— 全局/静态变量地址 | 语义立即数：`cmp ecx,-1`、`bt ecx,18h`、`shl rax,4`、`cmp dx,1` |
| `sub/add rsp, imm` —— 栈帧大小 | 结构体字段偏移：`[rax+14h]`、`[rdx+20h]`、`[r9+rcx*8+8]` |
| `mov reg, imm32` 紧跟 `call` —— `__chkstk` 帧大小 | 函数内条件短跳转 `je`/`jge`/`jbe` …（与帧大小无关） |
| `call`/`jmp rel` —— 代码地址 | |

> **[!] 人工圈定区间会漏字段 —— 这也是 v1.3 首版会失败的原因。**
> 首版 `L2_SPEC` 把 B Format 的通配圈成 `[10..13] [15..18] [20..23] [30..33]`，
> **漏掉了 `[40..43]`**（`mov qword ptr [rbp+2F50h], rax` 的第二个栈帧相对 disp32）。
> 该 disp = 帧大小 − 0x38，**与 `lea rbp` 的 disp 同源同漂**；而 MS Store 版之所以
> L1 落空，正是因为帧大小变了 —— 于是 L2 会以同样的原因再次落空。改为反汇编推导后
> 自动补齐（报告里标 `[自动补齐手工漏圈: 40,41,42,43]`）。
> 同类问题还修正了 E SrvResolve（`call rel32` 漏了最高字节、漏了 `sub/add rsp` 的 imm8）
> 与 A DrawWide / D TexObj 的越界通配（把 SIB / 相邻指令操作码一起通配了）。
>
> 推导过程的**双向差集**会写进报告：`[自动补齐手工漏圈: …]`（真 bug）与
> `[手工多圈(含操作码): …]`（降低特异性），人工只做复核，不再决定通配位。

工具再断言 **L1 与 L2 在三版可执行段内都「恰好 1 命中」**，不满足则拒绝出表。

定位顺序变为三级：`L1 精确 → L2 放宽 → 版本 VA 表`，日志标注
`via aob` / `via aob-l2` / `via va`。落点校验（`SigMatch`）必须使用**命中该地址的那一套
sig/mask**（`LocResult` 把 sig/mask/len 一起带出来），否则 L2 命中的地址会被 L1 判为不匹配。

#### 6.8.3.1 L1 与 L2 的分工边界

L2 只在 L1 落空时启用，因此**对 L2 只要求「不漏」**：

- 漏 → 该 hook 定位失败，退到 VA 表（地址是别的构建的）→ `ABORT`，功能缺失但**安全**
- 过宽 → 可能命中同构建里另一处字节序列 → **挂错函数 → 崩溃**（更危险）

所以宁可保留语义立即数（哪怕它在别的构建里可能会变），也不无脑把所有 imm/disp 通配。
`gen_aob_relaxed.py` 的「恰好 1 命中」断言就是这道闸门。

#### 6.8.4 可执行段落盘（`text_dump`）—— 加密 exe 的最后一条退路

MSIXVC 版离线只能拿到密文（§6.7.1），但**注入进程内的 DLL 看到的是明文映像**。
`DumpExecutableSections()` 把所有可执行段原样落盘：

- 产物：`scripts/SR4R_dump/SR4R_dump_text.bin` + `SR4R_dump_text.map`
  （map 记 `section va vsize bin_off size` + `timestamp/ep/sizeofimage`，VA 口径同 CFG 表）
- 上限 128MB，4MB 分块写，写失败只记日志不中断
- IDA 用法：以 raw binary (x64) 打开 `.bin`，装载基址填 map 里第一段的 `va`
- ini `text_dump = 0 | 1 | auto`，**默认 auto = 只有出现 hook 定位失败才落盘** ——
  正常构建零开销，问题构建自动留证据

#### 6.8.5 复现工具

| 脚本 | 作用 |
|------|------|
| `Tools/gen_aob_relaxed.py` | 读三版 exe → `operand_wild_set()` 由反汇编推导 L2 通配位 → 与手工参考规格 `L2_SPEC` 做双向差集 → 断言三版 L1/L2 唯一 → 写 `SR4R_I18N/aob_l2_arrays.inc` → 反向校验 `.inc` 无漂移（`--dis` 看反汇编） |
| `Tools/aob_relaxed_report.txt` | 上述工具的产物报告（三版 TDS/EP/SizeOfImage + 每 hook 的 L1/L2 命中数与差集标注） |

### 6.9 L3 换代构建精确特征码 + 两阶段安装（v1.4，2026-09-17）——面向「换了一代编译器」的构建

#### 6.9.1 实测现场（MS Store 版跑 v1.3 的日志）

```
exe: !! NEWBUILD file=sriv.exe TimeDateStamp=5E58CEF8 EntryPoint=00FC44FC SizeOfImage=07E6D000
globals: dynamic resolve OK | fontTab=0x146CFCDB0 fontCount=0x146CFCDA8
hook DrawWide/FontLookup/TexObj/SrvResolve/LangCur/LangTxt installed (via aob)
locate FORMAT:   L1 0 hits, L2 0 hits -> 均落空, 回退版本 VA 表
hook FORMAT   @0000000140CF9A00 (via va): signature mismatch, ABORT (game updated?)
locate SUBTITLE: L1 0 hits, L2 0 hits -> 均落空, 回退版本 VA 表
hook SUBTITLE @00000001403D18F0 (via va): signature mismatch, ABORT (game updated?)
SR4R v1.3 active: … hooks A=1 B=0 C=1 D=1 E=1 F=1 G=1 J=0
```

即 **L2 也救不了 B(Format) / J(Subtitle)**，而且回退到 VA 表时**用了别的构建的地址**
（`0x140CF9A00` / `0x1403D18F0` 是 Steam 的）。

#### 6.9.2 根因一：L2 解决「字段漂移」，解决不了「换代」

§6.8 的 L2 假设是**同一代编译器**、只是栈帧尺寸/地址类字段变了 —— 操作码序列与**指令长度**不变。
MS Store 版（TDS = 2020-02，比 Steam/GOG/EPIC 早 3~4 年）对这两个函数**换了一代 MSVC**，
前导指令的**形态和长度都不同**：

| | 新构建（Steam/GOG/EPIC） | MS Store（sriv.exe, 2020-02） |
|---|---|---|
| **B Format** 前导 | `push rbp; push rsi; push rdi; push r15`（4B）<br>`lea rbp,[rsp-2F88h]`（7B）<br>`mov eax,3088h; call __chkstk; sub rsp,rax` | `mov [rsp+20h],r9; mov [rsp+18h],r8; mov [rsp+8],rcx`（**参数先 home 到调用者区**, 15B）<br>`push rbp; push rbx; push rsi; push r14`（5B）<br>`lea rbp,[rsp-2F58h]`（7B）<br>`mov eax,3058h; call __chkstk; sub rsp,rax` |
| 前导总长 | **0x1B** | **0x29** |
| 帧大小 / chkstk | 0x2F88 / 0x3088 | 0x2F58 / 0x3058 |
| **J Subtitle** 前导 | `mov r11,rsp; push rbp; push rsi; push r12; lea rbp,[r11-458h]`（**有帧指针**） | `mov rax,rsp; push rdi; push r15; sub rsp,298h; movaps [rax-48h],xmm7; movaps [rax-58h],xmm8`（**无帧指针**, 寄存器保存槽改用 `[rax-disp8]`） |

关键点在最后一行：**指令长度本身就不同**（Format 前导差 0xE 字节），
且 Subtitle 连「有没有帧指针」都变了。

> **结论：字节级 mask 无论怎么放宽都不可能同时覆盖两代。**
> L2 是「同一套操作码模板 + 通配若干字段字节」，而这里连模板都不同 —— 属于
> `AobSig` 维度外的差异，需要**一套独立的精确特征码**（L3）。

#### 6.9.3 根因二：VA 回退路径只校验了 L1，把正确地址判成 mismatch

v1.3 的 `InstallHook` 在回退到版本 VA 表时，落点校验写死用 **L1**：

```cpp
// v1.3 (错)
if (!SigMatch(target, s.sig, s.mask, s.len)) { Log("signature mismatch, ABORT"); return false; }
```

即使把正确的 MS 地址填进 `CFG_MSSTORE`，**该地址上成立的只有 L3 那一套**，
L1 校验仍然失败 → 正确地址被 ABORT。这是 §6.8.3 那条
「落点校验必须使用命中该地址的那套 sig/mask」在**回退路径**上的漏网之处：
AOB 路径靠 `LocResult` 带出来了，回退路径没有。

#### 6.9.4 解法

**(a) `AobSig` 扩展第三层**（`sig3 / mask3 / len3`，纯精确，`mask3 = nullptr`）：

```cpp
AOB_ENTRY(FORMAT,   SIG_FORMAT, nullptr, 16, R2_FORMAT, RM_FORMAT, 48, R3_FORMAT,   nullptr, 48);
AOB_ENTRY(SUBTITLE, …          同上          …, R2_SUBTITLE, RM_SUBTITLE, 48, R3_SUBTITLE, nullptr, 48);
// 其余 6 条: sig3/mask3/len3 = nullptr,nullptr,0
```

- **只有 Format / Subtitle 带 L3**：其余 6 条 hook 在 MS Store 版 **L1 直接命中**
  （游戏日志 + 裸段 dump 双向验证），不需要多这一层
- L3 是**纯精确**（无通配）：同一构建内 RIP 相对位移与 `rel32` 都不随 ASLR 变化，
  且签名落在**同一文件内**，目标地址不会变 → 无需通配
- L3 窗口 48 字节；生成器从 48 起逐步加长（`L3_MAX = 128`），
  直到「在 MS dump 内恰好 1 命中 ∧ 其余三版命中 ≤ 1」

**(b) `MatchAnyLayerAt(va, s, out)`** —— 回退路径逐层试探，回填真正成立的那一层：

```cpp
static bool MatchAnyLayerAt(uint64_t va, const AobSig& s, LocResult* out) {
    const uint8_t* p = VA<const uint8_t*>(va);
    if (!p) return false;
    if (SigMatch(p, s.sig,  s.mask,  s.len))  { out->sig = s.sig;  out->mask = s.mask;  out->len = s.len;  return true; }
    if (s.sig2 && SigMatch(p, s.sig2, s.mask2, s.len2)) { … 回填 L2 …; return true; }
    if (s.sig3 && SigMatch(p, s.sig3, s.mask3, s.len3)) { … 回填 L3 …; return true; }
    return false;
}
```

**(c) 定位顺序**变为四级：`L1 → L2 → L3 → 版本 VA 表`，日志
`via aob` / `via aob-l2` / `via aob-l3` / `via va`。MS Store 版预期出现：

```
locate FORMAT:   L1/L2 落空(0/0 hits) -> L3 换代构建特征码命中 @0x140E92DA0
locate SUBTITLE: L1/L2 落空(0/0 hits) -> L3 换代构建特征码命中 @0x140488A60
hook FORMAT   @… (via aob-l3)
hook SUBTITLE @… (via aob-l3)
```

#### 6.9.5 如何从「加密 exe 的裸段 dump」反向定位换代入口（语义锚点法）

MSIXVC 版离线只有密文（§6.7.1），唯一可用的分析素材是 §6.8.4 落盘的裸段 dump。
但**没有 IDA、没有符号、没有 PE 头、也没有「函数边界」**，只有 19MB 连续字节。
两条路走过：

| 方法 | 结果 |
|------|------|
| **偏移投票法**（把参照函数里 imm/disp/rel 之外的「结构字节」串拿去目标里搜，按 `d = 命中位置 − 参照内偏移` 投票取众数，等价于「整体平移量」） | **失败**：Format 最佳解的 fixed 字节覆盖率仅 **2.3%**。因为 MS 版前导多了 0xE 字节（参数 home 区），**函数体内部又整体移位** —— 「单一位移量」模型根本不成立 |
| **函数内语义常量锚点法** | **成功**（下面） |

**语义锚点法**：找**函数体内跟构建无关的常量**（编译期算好的数学常量 / 语义立即数），
它们在两代编译器下**值相同**，而且通常在函数里**出现多次、相对间距稳定**：

| Hook | 锚点 | 含义 |
|------|------|------|
| **B Format** | `mov r8d, 0FFDFh` + `movabs r11, 3FF000100000200h` | 格式化用的字符合法区间上界 / 位掩码常量（源码里写死的字面量） |
| **J Subtitle** | `mov eax, 55555556h`（**÷3 魔法数**）与 `mov eax, 38E38E39h`（**÷9 魔法数**），两者**间距恰为 0x22** | 字幕计时换算里的整数除法（编译器把 `/3`、`/9` 优化成乘法） |

定位流程：
1. 在参照构建（Steam）的目标函数里找到这些常量的**字节序列及其相对偏移**；
2. 在 dump 里搜同一常量 → 得到若干候选位置；
3. 用「**多常量相对间距一致**」筛出唯一候选（Subtitle 就是靠 `÷3` 与 `÷9` 间距 0x22 定的）；
4. **用 CC 填充边界交叉确认**：x64 函数入口通常紧跟在 `int3`（`CC`）对齐填充之后 ——
   检查候选位置前方是否是对齐填充，以及 `ret` 之后的填充形态。

结果（与 `Tools/gen_aob_relaxed.py` 的 `MS_VA` 表一致）：

| Hook | MS Store 入口 VA | 依据 |
|------|-----------------|------|
| **B Format** | `0x140E92DA0` | 语义常量 `0FFDFh` + `3FF000100000200h` 共现 + CC 边界 |
| **J Subtitle** | `0x140488A60` | ÷3/÷9 魔法数间距 0x22 + CC 边界 |

> 另有**两条独立证据**证明 dump 的 VA 口径映射正确（这是整个方法的前提）：
> 1. dump 是 v1.3 在 **hook 安装之后**落盘的，6 个已安装 hook 的入口前 5 字节被改写成了
>    `E9 rel32`；把这段跳板**还原**成 L1 的前 5 字节后，**其后字节与 L1 逐字节相符**
>    （报告里标 `其后字节与 L1 相符 ✓`）—— 若 VA 映射差了哪怕一节，这不可能成立。
> 2. 生成的 `R3_FORMAT` / `R3_SUBTITLE` 字节序列在 dump 中**恰好出现一次**，
>    且位置正好落在上面两个 VA 上。

#### 6.9.6 两阶段安装：先定位 → 再落盘 → 后安装

v1.3 的可执行段落盘发生在 hook **安装之后**，于是 dump 里 6 个已安装 hook 的入口
首 5 字节都是 MinHook 的 `E9 rel32` 跳板（**失真映像**），离线侧不得不额外写一个
「还原跳板」步骤（`repair_minhook()`，靠「L1 前 5 字节 + 后续字节是否相符」反推）。

v1.4 改为**两阶段安装**：

```
MH_Initialize()
  ↓
[阶段 1] 预定位 8 条 hook（LocateHook 自带记忆化, 不重复付出扫描成本）
        统计 nLocateFail
  ↓
[阶段 2] 落盘可执行段（text_dump==1, 或 ==2(auto) 且 nLocateFail > 0）
        ← 此刻入口字节【未被任何 detour 改写】= 干净映像
  ↓
[阶段 3] 安装全部 8 条 hook
```

好处：
- 落盘得到的是**干净映像**，离线侧直接可用，`repair_minhook()` 退化为
  「兼容旧 dump」的选项（对 v1.3 留下的旧 dump 仍然有效）
- 顺带把「哪些 hook 定位失败」在落盘前就算清楚了，`text_dump=auto` 的判据更准确
  （不再依赖「安装阶段的失败标志」，安装晚于落盘）

#### 6.9.7 复现工具更新（`Tools/gen_aob_relaxed.py`）

| 新增 | 作用 |
|------|------|
| `class RawText` | 读 dump 的 `.bin` + `.map`（解析 `va_base`/`timestamp`/`ep`/`sizeofimage` 与 section 行），接口与 `Pe` 对齐（`at_va` / `exec_sections` / `off_of` / `set_buffer`），从而把 MS dump 当作「第 4 个构建」参与统一校验 |
| `MS_VA` | MS Store 的 8 个 hook 入口 VA（6 条来自 v1.2 日志 `via aob` 命中，2 条来自 §6.9.5 语义锚点） |
| `L3_HOOKS = {"Format","Subtitle"}` | 只有这两条生成 L3 |
| `L3_START=48, L3_MAX=128` | L3 窗口从 48B 起逐步加长，直到「MS 内恰好 1 命中 ∧ 其余三版 ≤1」 |
| `repair_minhook(ms, sigs, w)` | 把旧 dump 中 6 条 `E9 rel32` 跳板还原成 L1 前 5 字节，并**校验后续字节与 L1 相符**（同时充当 VA 映射的正确性证明） |

报告表格改为**四构建**（S/G/E/MS）× 三层：

```
hook        L1 hits(S/G/E/MS) L2 hits(S/G/E/MS) L3 hits(MS)   wild  结论
DrawWide    1/1/1/1         1/1/1/1         -             16    OK
Format      1/1/1/0         1/1/1/0         1             20    OK  L3=48B
Subtitle    1/1/1/0         1/1/1/0         1             18    OK  L3=48B
```

判据（生成器内断言，不满足拒绝出表）：

```
新构建族(Steam/GOG/EPIC): L1 与 L2 各恰好 1 命中
MS Store: 换代 hook(Format/Subtitle)  L1 落空 ∧ L3 恰好 1 命中
          其余 6 条             L1 恰好 1 命中
```

> **踩坑**：`RawText` 必须把 dump 读成 `bytearray` 而非 `bytes` ——
> `repair_minhook()` 要就地写回跳板，`bytes` 会抛
> `TypeError: 'bytes' object does not support item assignment`。

---

### 7.1 可直接复用（引擎无关）

| 模块 | 说明 |
|------|------|
| 词典层 | CRC32 哈希 + arena + 查表逻辑（与游戏无关） |
| 配置解析 | ini 手工解析（UTF-8 → UTF-16） |
| 字符集管理 | g_charSet bitmap + 频率排序 |
| DumpText | 未命中收集 + 批量落盘 |
| 日志系统 | CRITICAL_SECTION 保护的 fprintf |
| 主线程框架 | DllMain → MainThread → 安装 hook 的流程 |
| 安全机制 | 16 字节特征码校验 + idle 回退 |
| 折行重组状态机 | v7.3 WrapState（备用，如果 Hook J 足够则可省略） |

### 7.2 需要适配（SR4 地址/布局不同）

| 模块 | 改动 |
|------|------|
| 所有 VA 常量 | 已重新计算（见 §6.2） |
| 所有特征码 | 已提取（见 §6.2，7/7 完成） |
| MAGIC texId 安全区 | 理论可复用（0x60000000 段），需验证 SR4 纹理注册数 |
| 字体对象布局 | texId 偏移 +184 → **+568** |
| 纹理对象布局 | SrvResolve 改用 `[texObj+0x38]` 直接指针 |
| D3D11 全局指针 | 已定位：`qword_147667C70` (device) / `qword_147667C78` (context) |
| FONTTAB / FONTCOUNT | 已定位：`qword_146AE7060` / `dword_146AE504C` |
| Subtitle 文本编码 | UTF-16 → **UTF-8**（`char*` vs `wchar_t*`） |
| SrvResolve 架构 | 不含 SRV 全局表，改用 `[texObj+0x38]` 直接指针 |

### 7.3 需要从 SR3R 移植的文件

| 文件 | 来源 | 说明 |
|------|------|------|
| dllmain.cpp | SR3R_I18N/dllmain.cpp | 核心代码（~2800 行），改 VA + 特征码 |
| stb_truetype.h | SR3R_I18N/ | 字体光栅化库（不改动） |
| charset_data.h | SR3R_I18N/ | 内置字符集（font-only 模式备用） |
| pch.h / pch.cpp | SR3R_I18N/ | 预编译头 |
| framework.h | SR3R_I18N/ | Windows 头 |

### 7.4 已完成/待完成的工具

| 工具 | 说明 | 状态 |
|------|------|------|
| sr4_vpp.py | VPP v10 解包器 | ✅ 已验证 |
| sr4le_extract.py | le_strings → txt 解包 | ✅ 已验证（210文件/273028条） |
| sr4le_repack.py | txt → le_strings 回写 | ❌ 待建 |
| sr4_vpp_pack.py | txt → vpp_pc 打包 | ❌ 待建 |
| charlist 生成 | SR4 简体用字 charlist.txt | ❌ 待建 |
| exe_hardcoded 提取 | exe 内硬编码字符串 | ❌ 待做 |

---

## 8. 实施计划

### 阶段 1：文本工具链（无 DLL，纯 Python）

**目标**：建立 le_strings 解包/翻译/回写工具链

1. 移植 `sr3le_extract.py` → `sr4le_extract.py`
   - le_strings 格式完全相同，主要改动是 charlist 文件路径和 xtbl 目录
   - 用 SR4 misc.vpp_pc 解包后的 le_strings + misc_tables.vpp_pc 的 xtbl 测试
2. 移植 `le_strings_repack.py` → `sr4le_repack.py`
   - 验证回写后的 le_strings 二进制与原版结构一致
3. 从 misc.vpp_pc 中提取全部 us 版 le_strings → txt
4. 准备翻译工作流（KEY 翻译 → 回写 → 打包 vpp_pc）
5. 移植 `vpp_pack.py` → `sr4_vpp_pack.py`（SR4 v10 格式打包）

### 阶段 2：Hook 定位（IDA 逆向）

**目标**：在 sr_hv.exe 中定位全部 10 个 hook 点

1. 用 IDA 打开 sr_hv.exe（run_auto_analysis=false 加速）
2. 确认 DrawWide / Format / FontLookup 函数体与 SR3R 一致
3. 从 FontLookup 调用链定位 TexObj / SrvResolve
4. 筛选 Subtitle / LangCur / LangTxt 正确候选
5. 定位 FONTTAB / FONTCOUNT / D3D_DEVICE / D3D_CONTEXT 全局变量
6. 确认字体对象 / 纹理对象内存布局与 SR3R 是否一致
7. 提取所有 hook 的 16 字节特征码

### 阶段 3：DLL 代码移植与编译

**目标**：完成 SR4R_I18N.asi 编译

1. 复制 SR3R dllmain.cpp → SR4R dllmain.cpp
2. 替换所有 VA 常量（§6 + §7.2）
3. 替换所有特征码
4. 适配字体对象布局差异（如有）
5. 修改日志名/配置名/版本标识为 SR4R
6. 配置 vcxproj（v141_xp + /MT + C++17/20 + MinHook NuGet）
7. 编译 Release|x64

### 阶段 4：部署与测试

**目标**：实测汉化效果

1. 准备测试词典（少量菜单+字幕文本翻译）
2. 部署 DLL + 字体 + 词典到游戏目录
3. 用户本地测试：
   - 词典命中 → 文本替换是否正常
   - CJK 文本 → 字体图集是否正确构建
   - 字幕 → 整句替换是否正常
   - 稳定性 → 长时间运行是否崩溃
4. 检查日志（SR4R_I18N.log）排查问题
5. 修复迭代

### 阶段 5：完整翻译

**目标**：全部 le_strings 汉化

1. 提取全部 us 版 le_strings → txt
2. 翻译全部文本（机器翻译初稿 + 人工校对）
3. 软化敏感内容（如有）
4. 回写 le_strings → 打包 misc.vpp_pc
5. 与外挂 DLL 词典合并部署
6. 最终实测

---

## 9. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| 字体对象布局不同 | 中 | 高 | IDA 逆向确认；如有差异则适配代码 |
| MAGIC texId 不安全 | 低 | 高 | 验证 SR4 纹理注册数；必要时换安全区段 |
| TexObj/SrvResolve 函数不存在 | 低 | 高 | 从 FontLookup 调用链定位等效函数 |
| 引擎全局变量地址变化 | 高 | 中 | IDA 重新定位 |
| vpp 打包格式差异 | 低 | 中 | 已逆向验证，回写测试覆盖 |
| le_strings 回写哈希不一致 | 低 | 低 | 验证格式完全相同，可复用 SR3R repack 逻辑 |
| 注入方式兼容性 | 低 | 高 | 用户已解决 |
| 游戏更新导致特征码失效 | 低 | 中 | 16 字节特征码 + idle 回退（设计已考虑） |

---

## 10. 配置规格

### SR4R_I18N.ini（计划）

```ini
[settings]
dict_dir = dict
font_file = SourceHanSansHWSC-VF.ttf
dump_enabled = 0
lang_early = 1
subtitle_early = 1
charlist_file = charlist.txt
early_diag = 0
```

### 部署目录结构（计划）

```
游戏根目录/
├── sr_hv.exe
├── binkw64.dll          (原版或用户自备 loader)
└── scripts/
    ├── SR4R_I18N.asi
    ├── SR4R_I18N.ini
    ├── SR4R_I18N.log
    ├── dict/
    │   └── *.txt        (词典)
    ├── charlist.txt
    └── SourceHanSansHWSC-VF.ttf
```

---

## 11. 编译环境

| 项 | 值 |
|-----|-----|
| IDE | VS2017 |
| 工具集 | v141 |
| 平台 | x64 only |
| 运行库 | /MT (MultiThreaded) |
| 标准 | C++17 (或 C++20) |
| 字符集 | Unicode |
| 预编译头 | Use/Create (pch.h) |
| 依赖 | MinHook 1.3.3 (NuGet), stb_truetype (内置), d3d11 (SDK) |
| 输出 | SR4R_I18N.asi (DLL 改后缀) |

---

## 12. 当前进度

### 已完成

- [x] 摸清 SR4 游戏目录、主进程（sr_hv.exe, x64）
- [x] 逆向 vpp 格式 (v10: zlib 无 adler, 无对齐)
- [x] 编写并验证 vpp 解包器 (sr4_vpp.py, 317 文件 0 失败)
- [x] 确认 le_strings 格式与 SR3R 完全相同
- [x] 确认 vf3 字体格式与 SR3R 完全相同
- [x] 分析 charlist (jap 有 1893 CJK 汉字，用户选择外挂字库路线)
- [x] 编写本方案文档
- [x] **阶段 1：le_strings 提取工具 sr4le_extract.py 完成**（210文件/273028条字符串/18281个xtbl键名反查）
- [x] **阶段 2：全部 7 个 hook 定位完成**（A/B/C/D/E/F/G/J）
- [x] 全部 7 个特征码提取完成
- [x] 全局变量定位：纹理表、语言服务对象、字体表、D3D设备/上下文（SRV_CACHE 待定位）
- [x] SR4 架构变更分析：Subtitle 编码改 UTF-8、SrvResolve 改直接指针、字体 texId 偏移 +568
- [x] le_strings 回写工具（sr4le_repack.py）
- [x] vpp 打包工具（sr4_vpp_pack.py）
- [x] exe_hardcoded 硬编码字符串提取（5528 条，已译 1436 条）
- [x] **阶段 3：DLL 代码移植与编译**（Release|x64，vs2017 v141）
- [x] **阶段 4：部署实测通过**（词典命中、CJK 字形注入、字幕整句替换均正常）
- [x] **阶段 5：完整翻译**（voice 100%、le_data_supplement 94%、exe_hardcoded 26%）
- [x] 字幕 Hook J 修正（sub_1403D18F0 正确入口）
- [x] **GOG (sr_hv_gog.exe) 支持**：地址核对 + Subtitle 修正为 `0x1404B1E00`
- [x] **AOB 化重构**：8 个 hook 改扫描定位（唯一命中校验），VA 表降级为回退；
      DrawWide 48B / LangCur 27B 消除孪生函数歧义；MSVC v145 编译通过（见 §6.6）
- [x] **EPIC (sr_hv_epic.exe, 2024-02-28 构建) 支持**：8 AOB 三版唯一命中；
      fontTab/fontCount 由 EPIC FontLookup 反编译证实；D3D 槽由 EPIC D3D11 创建函数证实；
      CFG_EPIC 已加入 dllmain.cpp（见 §6.6.2.1）
- [x] **v1.2 全局变量运行时自解**（2026-09-17）：fontTab/fontCount 从 AOB 命中的
      FontLookup 函数体内按掩码模式反解；D3D 设备/上下文改为 SRV→`GetDevice()` 反查；
      自学习槽位表兜底；未知构建下全局指针置空以降级不崩溃。
      三版验证 `Tools/verify_runtime_globals.py` 全绿（解出值 == CFG 硬编码值）
- [x] **MS Store（MSIXVC）版调查结论**：`sriv_microsoft.exe` 为**加密密文**
      （熵 8.0000 / 无 MZ / 无明文串），离线取不到任何地址；v1.2 后无需离线地址即可支持
      （见 §6.7）
- [x] **v1.3 L1/L2 双层特征码 + 可执行段落盘**（2026-09-17）：L2 通配位由反汇编机械推导；
      `text_dump = 0/1/auto` 在定位失败时自动落盘可执行段（见 §6.8）
- [x] **v1.4 L3 换代构建精确特征码 + 两阶段安装**（2026-09-17）：MS Store 版
      Format/Subtitle 换了编译器（前导指令形态与长度均不同），L2 无法覆盖 → 新增
      `R3_FORMAT` / `R3_SUBTITLE` 纯精确特征码；修复 VA 回退路径只校验 L1 的 bug
      （`MatchAnyLayerAt`）；dump 改为**安装前**落盘（干净映像）。四构建唯一性由
      `Tools/gen_aob_relaxed.py` 离线断言（见 §6.9）

### 待处理

- [ ] SRV cache 全局变量定位（低优先级，可能不需要）
- [ ] le_data_supplement 剩余 ~6% 技术性条目（单字符/格式标签/品牌名）
- [ ] exe_hardcoded 剩余技术字符串（动画状态/调试标志/硬件品牌，暂保留英文）
- [ ] **MS Store 版实机验证 v1.4**：期望日志出现
      `locate FORMAT: L1/L2 落空(0/0 hits) -> L3 换代构建特征码命中 @0x140E92DA0`
      与 `locate SUBTITLE: … @0x140488A60`，以及 `hook FORMAT … (via aob-l3)`、
      `hook SUBTITLE … (via aob-l3)`、`active: … B=1 … J=1`
- [ ] MS Store 版注入方式本身已确认可用（binkw64 代理，DLL 正常加载并写日志）；
      但该包目录本会话**无写权限**，产物需用户手工拷贝
