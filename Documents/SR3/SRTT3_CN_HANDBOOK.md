# SRTT3 Remastered 汉化 · 技术手册

> **文档性质**：全部结论基于实测数据或 IDA 反编译证据，凡推断处均已标注 `【推断】`。
> **生成时间**：2026-09-04
> **工作目录**：`C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04\`
> **游戏目录**：`I:\SteamLibrary\steamapps\common\Saints Row The Third Remastered\`

---

## 0. 一句话结论

**本项目的可行路线 = 复刻游侠（ali213）的 us 分支方案**：把 `font_body` / `font_header_pc` 的槽位从 336 扩到 ~3061，图集扩到 4096×4096，自建"汉字→槽码"字典，替换全部 `*_us.le_strings`。
**玩家无需切语言**（默认 English 即中文），零 exe 改动、零 DLL 注入。

---

## 1. 资源格式基础

### 1.1 vpp_pc 打包容器

`cache/*.vpp_pc` 是 Volition v6 packfile，375 个文件（misc 包）。

| 项 | 值 |
|---|---|
| header magic | `0x51890ACE` @0x00，ver=6 |
| 关键字段（全 u64） | count@0x158, packfileSize@0x160, dirSize@0x168, nameSize@0x170, uncompressedDataSize@0x178, compressedDataSize@0x180 |
| chunk | 0x1000 |
| 布局 | chunk0=header，chunk1..=directory(48B×N)，names，data |
| entry 48B | `{u64 nameOffset, u64 unk(0), u64 dataOffset, u64 usz, u64 csz, u64 unk(0)}` |
| 每文件数据 | `16B头{0x0FEEDBEE, 0x00BADBEE, u32 lz4len, u32 usz}` + 标准 LZ4 block |
| 物理偏移 | 按 **csz** 的 0x1000 对齐累积（最后文件不 pad） |
| 虚拟 dataOffset | 按 **usz** 的 0x1000 对齐累积（官方语义，游戏似未使用） |
| 未压缩特例 | csz = `0xFFFFFFFFFFFFFFFF`，数据原样存放（按 usz 对齐） |

**工作区工具**：
- `vpp_pack.py` — 打包（依赖 venv 的 lz4，见 §8 环境）
- `vpp_extract_all.py` — 解包（**注意**：大文件处物理流可能错位，见 §7 坑位）
- `vpp_probe.py` / `vpp_selfcheck.py` / `vpp_verify.py` — 解剖与校验

### 1.2 le_strings 文本容器

| 偏移 | 内容 |
|---|---|
| 0x00 | u32 file_id |
| 0x04 | u16 ver, u16 n_buckets |
| 0x08 | u32 n_strings |
| 0x0C | buckets，16B/项 `{u32 count, u32 pad, u32 offset, u32 pad}` |
| +4×n_strings | 字符串区 |
| 字符串区条目 | `u32 hash(CRC-Volition) + UTF-16LE 文本 + u16 0x0000 终止` |

**关键**：文本 u16 存的是**槽码**，不是 Unicode（游侠实测：CJK 区 0x4E00-0x9FFF 直出 **0 个**）。
槽码 → 字形索引：`idx = 槽码 - baseChar`（baseChar=32 实证）。

### 1.3 vf3_pc 字体度量表

| 偏移 | 字段 |
|---|---|
| 0x00 | magic `TNFV` |
| 0x04 | u32 ver |
| **0x08** | **u32 count（槽位总数）** |
| 0x0C | u32 baseChar（=32） |
| 0x14 | u16 行高宽 (font+20) |
| **0x16** | **u16 行高 L（font+22）** |
| 0x20 | u32 kern_count |
| **0x68** | **64B 内嵌图集名**（如 `font_body_nobdr.tga`）— 决定加载哪个 gvbm |

区段计算（kern=0 时）：
```
met = align16(0xD0 + 6*kern)        # 度量起点
z1  = align16(met + 16*count)       # x 坐标区
z2  = align16(z1  + 4*count)        # y 坐标区
```
- 度量 16B/项：`{+0 u32 advance, +4 u32 width, +8 u32 zero, +12 i16 kernIdx, +14 u16 pad}`
  - **+0 是 advance（步进），+4 是字形像素宽**，不是"宽、高"
- x 区 4B/项，y 区 4B/项，各按 16 对齐

**UV 矩形**（IDA 反编译 `sub_1408B5FF0` 证实）：
```
起点 = (x[i], y[i])
宽   = metric.width  （纹理像素）
高   = L             （行高，固定）
```

### 1.4 cvbm_pc / gvbm_pc 图集

- `cvbm_pc` = GEKV v13 容器（~110B），含 1 个 record
- `gvbm_pc` = 裸 DDS 数据流（DXT5）

**cvbm record 布局（绝对 cvbm 偏移，勿自行拼装）**：
```
+0x00 u64 file_offset
+0x08 u16 width
+0x0A u16 height
+0x0C u32 tex_fmt (低16位=D3DFMT, 高位 0x0100 = PC)
+0x10..0x15  6B flags       ← 勿动
+0x16 u8  hasalpha
+0x17..0x22 12B flags2      ← 勿动
+0x23 u8  mip_count
+0x24 u32 data_size
+0x28..0x47 32B unk3        ← 勿动
+0x48 name (null-terminated)
```

**字段位置陷阱**（历史踩坑）：
- `mip_count` 在 **0x23**，不是 0x10
- `data_size` 在 **0x24**，不是 0x20
- `hasalpha` 在 **0x16**，不是 0x13

**build_cvbm 唯一正确策略**：保留 template cvbm 全部字节，**只在上述 7 个已知字段位置替换**（file_off / w / h / fmt_raw / hasalpha / mip_count / data_size）。自己拼装 record 字节会导致字段错位 → 游戏解析失败 → fallback cache。已验证 5 套字体 byte-exact。

### 1.5 DXT5 处理

- 每块 **16B**（8B alpha 插值 + 8B color）
- 块步进 **16**（v5 的 bug 就在这里写成 `*8`）
- 块号 = `(y//4) * (W//4) + (x//4)`，块内序号 = `(y%4)*4 + (x%4)`
- 引擎只取 alpha 通道（RGB 可全白）

---

## 2. 官方字体资产实测全表

`cur_misc/` 下全部字体（2026-09-04 实测）：

| 字体 | vf3 B | count | base | L | kern | 内嵌图集 | gvbm B | 图集尺寸 |
|---|---:|---:|---:|---:|---:|---|---:|---|
| `font_body` | 8,272 | **336** | 32 | 104 | 0 | font_body_nobdr.tga | 2,097,152 | 2048×1024 mips=1 |
| `font_header` | 8,272 | 336 | 32 | 220 | 0 | font_header_nobdr.tga | 8,388,608 | 4096×2048 mips=1 |
| `font_header_pc` | 8,272 | 336 | 32 | 220 | 0 | font_header_pc_nobdr.tga | 8,388,608 | 4096×2048 mips=1 |
| `font_sk` | 38,784 | **1607** | 32 | 91 | 0 | font_sk_nobdr.tga | 16,777,216 | 4096×4096 mips=1 |
| `font_zh` | 9,568 | 389 | 32 | 91 | 0 | font_zh_nobdr.tga | 2,796,192 | 2048×1024 **mips=9** |
| `font_debug` | 18,288 | 336 | 32 | 36 | 1668 | font_body_nobdr.tga | 2,097,152 | 2048×1024 mips=1 |
| **`jap`** | 52,896 | **2195** | 32 | 74 | 0 | jap_nobdr.tga | 16,777,216 | **4096×4096 mips=1** |

> ⚠️ **命名陷阱**：日文字体叫 **`jap`**（无 `font_` 前缀），此前按 `font_*` 搜索长期漏掉。
> `jap` 五件套：`jap.vf3_pc` / `jap.cvbm_pc` / `jap.gvbm_pc` / `jap_nobdr.cvbm_pc` / `jap_nobdr.gvbm_pc`。

### charlist_*.dat 字符集字典

| 文件 | 项数 | 构成 |
|---|---:|---|
| `charlist_us` / `ru` / `pl` / `cz` / `de` / `fr` / `it` / `es` / `nl` | **336** | Latin1=128, ASCII=96, Cyrillic=66, LatinExt=36, Punct=10 |
| `charlist_se` / `dk` | 266 | — |
| `charlist_sk` | **1607** | **Hangul=1317**, Latin1=116, ASCII=96, Cyrillic=33 |
| `charlist_zh` | 377 | Latin1=116, ASCII=96, **CJK=92**, Cyrillic=33 |
| `charlist_jp` | **2195** | **CJK-Han=1661**, Kana=179, Latin1=128, ASCII=96, Fullwidth=38 |
| `charlist_ko` | 112 | ASCII=96, Latin1=8, LatinExt=8（废弃占位） |

**规律**：`charlist_xx` 项数 = 对应字体 count（us 336=body 336，sk 1607，jp 2195）。
**注意**：`charlist_us` 在游侠方案里**未被修改**（1835B 相同）→ charlist 疑似仅供构建工具链，运行时不参与渲染。【推断】

---

## 3. 语言切换链路（Steam → 引擎）

### 3.1 exe 内的双语言表 @0xDD7660（IDA/字节 dump 铁证）

```
0xDD7660: en-US  cs-CZ  nl-NL  fr-FR  de-DE  it-IT  ja-JP  ko-KR  pl-PL  ru-RU  es-ES
0xDD76B8: english czech dutch french german italian japanese koreana polish russian spanish latam
0xDD7720: US  JP
```

**11 个 locale + 12 个 Steam 名**，`schinese` / `tchinese` 出现 **0 次**。
（exe 中 `chinese` 仅 1 处 = 逻辑字体名 `fnt_chinese` @0xDAE370，非 Steam 语言名。）

### 3.2 切换流程

```
Steam 属性 → 语言 → 选 X
   ↓ Steamworks API 返回 X（如 "japanese"）
引擎匹配 Steam 名表 → 语言码（japanese = 0xb）
   ↓
加载 charlist_xx + font_xx.vf3 + font_xx_nobdr.gvbm + 全部 *_xx.le_strings
```

**不下载语言包的原理**：11 套语言资产**全部预置在 `cache/misc.vpp_pc`**（117MB）里，Steam 切语言只传参，不触发 depot 下载（用户实测确认）。

### 3.3 引擎内建逻辑字体名（exe 字符串）

`jap` / `fnt_chinese` / `font_sk` / `font_body` / `font_header_pc` / `ug-debug` / `debug` / `thin`

`fnt_chinese` 对应语言码 `0xd`，但 **PC 的语言表只有 11 项（0x0-0xa），没有 0xd** → zh 分支在 PC 上**永不触发**。

---

## 4. 游侠（ali213）方案 · 完整逆向

> 来源：`汉化/游侠/cache/misc.vpp_pc`（118MB）+ `binkw64.dll`（261KB 代理注入）

### 4.1 资源层改动对照（目录元数据，铁证）

| 文件 | 官方 usz | 游侠 usz | 改动 |
|---|---:|---:|---|
| `font_body.vf3_pc` | 8,272 | **73,696** | count 336→**3061**，L 104→**72** |
| `font_body_nobdr.gvbm_pc` | 2,097,152 | **22,369,648** | 2048×1024→**4096×4096 mips=11** |
| `font_header_pc.vf3_pc` | 8,272 | **73,696** | count 336→**3061**，L 220→**72**，**内嵌图集名改为 `font_body_nobdr.tga`** |
| `font_header.vf3_pc` | 8,272 | 20,560 | count 扩展（L=220 保持，图集未变） |
| `charlist_us.dat` | 1,835 | 1,835 | **未改** |
| `menu_us.le_strings` | 68,930 | 47,554 | 英文→中文 |
| 全部 `*_us.le_strings` | 886,886 | 621,614 | **70%**（中文更紧凑） |
| 文件总数 | 375 | **337** | 删除 38 个（`platform_ps5_us` / `platform_xbs_us` 等归零） |
| 其它语言 le_strings | — | — | **未动** |

### 4.2 游侠 font_body.vf3 槽位布局（实测）

```
count = 3061, base = 32, L = 72, kern = 0, 图集 = font_body_nobdr.tga
3061/3061 全部有度量，连续段 (0, 3060)  —— 无空洞
```

**UV 网格规律**（72px 步进）：
| idx | uv (x, y) | 验算 |
|---:|---|---|
| 0 | (0, 0) | 0 列 0 行 |
| 1 | (72, 0) | 1 列 0 行 |
| 400 | (576, 504) | 8 列 × 7 行 |
| 800 | (1152, 1008) | 16 列 × 14 行 |
| 1500 | (3168, 1872) | 44 列 × 26 行 |
| 3000 | (2304, 3816) | 32 列 × 53 行 |

→ **4096 / 72 = 56 列/行，56 行，共 56×56 = 3136 槽 ≥ 3061** ✓ 完美闭合

**字形尺寸分布**：`width=56`（1866 个，汉字主体）、`width=52`（798 个）、其余 28/48/32 等（拉丁/符号）。advance 与 width 同值（无额外步进）。

### 4.3 游侠文本编码（实测 `menu_us.le_strings`）

```
u16 值域 min=0x000A  max=0x0C13(3091)  唯一值 874
CJK 区 (0x4E00-0x9FFF) 直接出现：0 个
值分布：0x100-0xFFF = 748,  <0x100 = 126
```

→ **自建槽码字典**：`max 槽码 0x0C13 = 3091` → `idx = 3091 - 32 = 3059 < count 3061` ✓ 完美闭合
→ 汉字**不是** Unicode 直出，而是映射到 0x100+ 的自定义槽位

### 4.4 游侠方案总结

```
1. font_body_nobdr 图集扩到 4096×4096 全 mip 链（22.37MB）
2. font_body.vf3      count 336→3061, L=72
3. font_header_pc.vf3 count 336→3061, L=72, 内嵌图集名 → font_body_nobdr.tga（共享同一图集！）
4. 自建 3000 字"汉字→槽码"字典
5. 全部 *_us.le_strings 替换为槽码中文
6. 玩家无需切语言（默认 English）
```

**为什么这么设计**：
- `font_header_pc` 与 `font_body` **共享同一图集** → 只需烘一份 4096×4096，省一半工作量与显存
- L 统一为 72 → 两个字体用同一套 metric 布局，图集可共用
- 走 us 分支 → 玩家零操作

---

## 5. 本项目历程：崩溃诊断全记录

| 版本 | 做法 | 结果 | 根因 |
|---|---|---|---|
| v1 | 扩 count 到 3448 | 💥 崩 | **font gpu 池 17MB 超限**（按 count 预分配字形存储） |
| v2 | 覆写 idx 293-316 | 💥 崩 | 破坏官方已有字形数据 |
| v3 | 用"空洞槽"但选了禁区码点 | 💥 崩 | 槽码落字符集禁区 |
| v4 | 同上 | 💥 崩 | 同上 |
| **F1** | 官方 375 文件**零改动**重打包 | ✅ 正常 | 证明 `vpp_pack.py` 打包器干净、基准可信 |
| v5（初） | 合法码点 0xA4-0xD8，24 槽 | ⚪ 空白不崩 | **`patch_alpha_region` 内 `b0 = blk_off*8` 应为 `*16`**（DXT5 每块 16B） |
| **v5 修复** | 修 DXT5 块偏移 | ✅ **中文上屏** | 首个内核级成功 |
| **v6** | 字体源换思源黑体 HW-SC (OFL) | ✅ 通过 | v5 架构 + 开源字体 |
| v-exp | `font_body` count 336→700（新槽全 0） | ✅ **不崩** | count 扩展到 700 引擎无压力 |
| v7（首版） | cell=72 装 2807 字（拼音序截断） | ⚠️ 缺字 | GB2312 一级按拼音排序，截断丢掉 w/x/y/z 段（中/新/无/用全缺），113 条译文只中 11 条 |
| **v7.2** | cell=64/L=64/**count=4096**，GB2312 一级全 3755 字，4 字体同步 | 💥 **启动即崩** | **font gpu 字形池硬上限 ≈3448 再次触发**（与 v1 同根因，只是把 count 推到 4096）；`SRTTR_20260904-184210.dmp` 栈上 `font_body.vf3_pc` + 字体对象 NULL（读 0x4）证实 |
| **v7 修复** | cell=72/L=72/**count=3136**（回游侠几何），**只改 font_body+font_header_pc 两字体**，字集优先纳入译文用字 | ✅ 修复并部署 | 绕开 3448 上限；113→49 条 menu_us 中文替换、缺字 0；多块 LZ4 修正见下 |

### v7.2 → v7 崩溃根因与修复（dump `SRTTR_20260904-184210`）

- **现象**：启动 17 秒后 `c0000005` 读地址 `0x4`（NULL+4）。崩溃点 `SRTTR.exe` RVA `0x859FEF`（落在 `sub_140858C10` 字体/vf3 解析区），指令 `cmp dword ptr [rdi+4],4`，`rdi=0x2`（NULL 字体对象）。
- **调用栈**：`AK::StreamMgr::SetFileLocationResolver → AK::MemoryMgr::StopProfileThreadUsage → AK::WriteBytesMem::Count`（资源异步加载线程初始化字体对象时拿到 NULL）。
- **栈上铁证**：`0x528ff570` 处明文字符串 `font_body.vf3_pc` → 崩溃正在加载 **font_body** 字体。
- **根因**：`font_body.vf3_pc` 的 `count=4096` 超过引擎 GPU 字形池硬上限 ≈3448 → 字体对象分配失败返回 NULL → 解引用崩。
- **修复**：`bake_lib.py` 几何回退 `cell=72 / N_SLOT=3136 / L=72`（游侠已验证安全量级）；`build_v7.py` 改动范围收窄到 **font_body + font_header_pc** 两个字体（游侠即如此，复刻其精确方案），其余官方字体保持原样。
- **排查顺带发现**：`vpp_pack.py` 原单块 LZ4 压缩对 >32MB 文件会把整文件压成单块，而游戏解压器 `block_size=32MB` 无法解压 → 改为**按 32MB 分块**，每块独立 16B 头 `{magic1,magic2,lz4len,blk_usz}`。原厂 `interface-backend.gpeg_pc`(48MB) / `always_loaded_veh.gpeg_pc`(133MB) 即此格式，回环校验与原厂字节 100% 一致。

> ⚠️ **count 红线**：任何字体 `count` 绝不可 ≥ 3448。字形池按 count 预分配，超限即字体对象 NULL → 启动崩溃。游侠安全值 ≈3061~3136。

### 字符集禁区（v3/v4 崩溃根因）

官方 `menu_us.le_strings` 中 hash=`0x2c6dc5dc` 的条目是**"可渲染字符全表"**（172 项 u16，唯一值 156，覆盖 cp 0x20..0x16F），**明确跳过两段**：
- **禁区 A**：`0x7F .. 0xA0`
- **禁区 B**：`0x145 .. 0x168`

文本槽码落禁区 → 走异常路径 → `sub_1403FD0F0`（14 槽 type 注册表哈希查找）崩溃，崩溃 type id `0xa8947c8c` = `crc_volition("BTN_A_TXT")`（IDA 栈现场 +0x2A0 处完整串证实）。

> ⚠️ **开放问题**：游侠的槽码范围 `0x100..0xC13` **会穿过禁区 B (0x145-0x168)**，但游侠能正常运行。
> 两种可能 —— ①游侠也改了 `0x2c6dc5dc` 全表条目；②禁区非硬崩溃，v3/v4 崩溃另有原因。
> **待验证**：解出游侠 `menu_us` 的 `0x2c6dc5dc` 条目对比官方。【待验证，优先级高】

### v6 成功配置（24 字）

槽位 idx `132..184` → 槽码 `164..216`（码点 0xA4-0xD8，全在合法区）。
8 条菜单键：`单人游戏` / `合作模式` / `主线战役` / `合作战役` / `查看信息` / `社区` / `下载内容` / `选项`。
字体源：`G:\Archives\Fonts\02_SourceHanSans-VF\Variable\TTF\HW\SourceHanSansHWSC-VF.ttf`（OFL 1.1，可商用分发）。

**字体选型证据**：
| 字体 | 默认 style | 评估 |
|---|---|---|
| SourceHanSansSC-VF.ttf | **ExtraLight**（超细） | ❌ 太细不可读，且 freetype-py 无 `set_var_design_coordinates` 无法拉回 Regular |
| **SourceHanSansHWSC-VF.ttf** | **Regular（400）** | ✅ 选定 |
| msyh.ttc（闭源） | Regular | v5 基线 |

> HW 子族与标准 SC 的**汉字字形完全一致**（Adobe ReadMe：HW 仅改 ASCII 半角拉丁字宽），纯中文看不出差别。

---

## 6. 路线决策记录（含被否决方案）

| 路线 | 做法 | 容量 | 判定 |
|---|---|---|---|
| **A. 挤公共空洞**（v6 当前） | 4 字体公共空洞 ∩ 合法码点 | **24 槽**（已满） | ✅ 已完成，但天花板极低 |
| B. zh 分支 | 启用 `fnt_chinese`（语言码 0xd） | 389 槽 | ❌ PC 语言表无 0xd，Steam 无中文名 → 需 hook exe |
| C. jap 分支 | Steam 切 japanese，用 `jap` 2195 槽 | 2195 槽 | ⚠️ 可行但**玩家需手动切日语**，且要动全部 `*_jp` 文本 |
| D. sk 分支 | 韩文字体槽位 | 1607 槽 | ❌ 纯谚文字形，无 CJK 先例 |
| E. DLL 注入 | hook 渲染层做 Unicode 字典 | 无上限 | ⚠️ 最彻底但工作量最大（同 EnclaveCJK 技术栈） |
| **F. 游侠 us 分支** | 复刻 §4 方案 | **~3061 槽** | ✅ **推荐**：玩家零操作、已验证可跑、容量足够 |

### 路线 F 的实施步骤（建议）

| 阶段 | 任务 | 产出 |
|---|---|---|
| P0 | 解出游侠 `menu_us` 的 `0x2c6dc5dc` 全表条目，对比官方 | 澄清 §5 开放问题 |
| P1 | 生成 3000 字汉字表（GB2312 一级 3755 字 ∩ 译文用字） | `han_dict.json` |
| P2 | 烘 4096×4096 图集（72px 网格，56×56） | `font_body_nobdr.gvbm_pc` 22.4MB |
| P3 | 重建 `font_body.vf3` / `font_header_pc.vf3`（count=3061, L=72） | 2×73,696B |
| P4 | 编码器：中文文本 → 槽码序列 | `encode_zh.py` |
| P5 | 全量替换 21 个 `*_us.le_strings`（5763 条译文） | 621KB 文本 |
| P6 | 打包 + 部署 + 实测 | `cache/misc.vpp_pc` |

---

## 7. 工具清单与已知坑位

### 工作区工具

| 文件 | 用途 |
|---|---|
| `vpp_pack.py` | 打包（**必须用 venv python**，见 §8） |
| `vpp_extract_all.py` | 通用解包 |
| `vpp_probe.py` / `vpp_selfcheck.py` / `vpp_verify.py` | 解剖 / 自校验 / 闭环校验 |
| `volition_tex.py` | `parse_cvbm` / `parse_vf3` / `decode_dxt5` / `encode_dxt5` / `write_png` / `build_cvbm` / `render_mip` |
| `le_strings_repack.py` | `read_with_bucket` / `repack_inplace`（**原位覆盖**，官方布局 100% 不变） |
| `sr3le_extract.py` | le_strings 解包 + `crc_volition` |
| `build_menu_zh_v5.py` / `v6` | v5/v6 构建器（含 DXT5 块偏移修复） |
| `build_count_exp.py` | count 扩展实验 |
| `probe_zh_branch.py` | zh 分支侦查 |
| `vpp_scan_all.py` / `vpp_list_fonts.py` | vpp 全盘扫描 |

### 已知坑位（血泪）

1. **DXT5 块偏移**：`b0 = blk_off * 16`，写成 `*8` 会导致字形数据错位（v5 空白 bug）
2. **vf3 区段对齐**：met/z1/z2 **都必须 align16**，kern≠0 时（如 font_debug kern=1668）偏移会变
3. **cvbm 不可自拼**：保留 template 字节只改 7 个已知字段（见 §1.4）
4. **le_strings 重排会出事**：全量重排为 hash 序曾被视为崩溃元凶 → 改用 `repack_inplace` 原位覆盖
5. **PNG 查看假阳性**：Read 工具对 RGBA 图会把 alpha 当灰度 → 应构造"alpha 当亮度"的图再查看
6. **vpp 解包物理流错位**：`vpp_extract_all.py` 按目录序解包时，遇到大文件（22MB 图集）后物理流错位 → **应改用"扫全部 LZ4 块头，按 usz 匹配目录"的方式**（本次已验证有效）
7. **jap 不带 font_ 前缀**：搜索字体时务必同时搜 `jap*`

---

## 8. 环境

```
Python (venv, 含 lz4):  C:\Users\haojun0823\.workbuddy\binaries\python\envs\default\Scripts\python.exe
Python (system):        C:\Python314\python.exe
Node (managed):         C:\Users\haojun0823\.workbuddy\binaries\node\versions\22.22.2-2\node.exe
```

> ⚠️ **lz4 只装在 venv**，`vpp_pack.py` 必须用 venv python 运行，否则 `ModuleNotFoundError`。

### 关键依赖
- `numpy` — 像素矩阵
- `freetype-py` — 字形渲染（**无 `set_var_design_coordinates`，VF 可变字体无法设轴**）
- `lz4` — vpp 压缩

---

## 9. 当前部署状态（2026-09-04 18:00 核实）

`cache/misc.vpp_pc` = **v-exp count 扩展实验包**（116,738,777 B，08:32）

### 留档清单

| 文件 | 大小 | 说明 |
|---|---:|---|
| `misc.vpp_pc.orig` | 117,533,402 | 官方原版（**最权威基准**） |
| `misc.vpp_pc.official_ok` | 117,533,402 | 官方原版副本 |
| `misc.vpp_pc.f1` | 116,738,777 | F1 零改动重打包（验证打包器） |
| `misc.vpp_pc.bisectA` | 116,730,585 | 二分 A 纯文本版（崩） |
| `misc.vpp_pc.v2crash` | 116,742,873 | v2（崩） |
| `misc.vpp_pc.v3crash` | 116,775,641 | v3（崩） |
| `misc.vpp_pc.v4crash` | 116,783,833 | v4（崩） |
| `misc.vpp_pc.v5blank` | 116,783,833 | v5 空白版（DXT5 bug） |
| **`misc.vpp_pc.v5success`** | **116,820,697** | **首个成功版（msyh）** |
| **`misc.vpp_pc.v6ok`** | **116,820,697** | **v6 思源黑体（当前最佳）** |
| `misc.vpp_pc.crash0506` | 48,057,049 | 早期崩溃留档 |

> **回滚任意版本**：`cp cache/misc.vpp_pc.v6ok cache/misc.vpp_pc`

### 基准目录

- `cur_misc/` — 官方 375 文件干净基准（构建前 `robocopy /MIR` 恢复，须校验文件数 == 375）
- `unpack/misc/` — 工作目录（构建时被覆盖）
- `yx_misc/` — 游侠解包（**部分文件因物理流错位不可信**，仅 vf3/cvbm 可信）

---

## 10. 翻译资产

| 项 | 值 |
|---|---|
| 位置 | `unpack/text/_handoff/` |
| `source.jsonl` | 905,547 B |
| `stats.json` | tasks=**5763**, keys=**6224**, uniq_texts=5763 |
| tag 分布 | plain 5157, rich 279, nl 176, fmt 110, fmt+nl 26, rich+fmt 6, rich+fmt+nl 9 |
| 主要文件 | customize_us 1465, hud_us 797, static_us 555, menu_us 484, activity_us 426, subtitle_us 358, diversion_us 350, mission_us 334 …（共 21 个 us 文件） |
| `terms_cand.md` | 13,958 B（术语候选） |
| `ali_text/` | 213 个 txt（各语言对照源），共 67,330 行 |

**译文覆盖**：5763 条已具备，足以支撑全量替换（阶段 P5）。

---

## 11. 关键待办

| # | 事项 | 优先级 |
|---|---|---|
| 1 | 解出游侠 `menu_us` 的 `0x2c6dc5dc` 字符全表条目，对比官方 → 澄清禁区 B 疑问 | ★★★ |
| 2 | 确认 `charlist_us` 运行时是否参与渲染（游侠未改它却能跑） | ★★★ |
| 3 | 生成 3000 字汉字表（从 5763 条译文提取实际用字，而非硬套 GB2312） | ★★ |
| 4 | 编写 4096×4096 图集烘字器（72px 网格，56×56，mips=11） | ★★ |
| 5 | 编写槽码编码器 + 全量 le_strings 替换 | ★★ |
| 6 | 验证 mips=11 全 mip 链是否必需（官方 jap 是 mips=1，游侠是 mips=11） | ★ |

---

## 12. v7 包体设计（当前主线实现）

> 脚本：`bake_lib.py`（烘焙核心库）+ `build_v7.py`（构建器，39s 全流程）

### 12.1 几何参数

```
图集  : font_body_nobdr 4096×4096 DXT5 mips=1 (16,777,216B)  ← 官方 jap 同配置先例
cell  : 64×64, 64 列 × 64 行 = 4096 槽
L     : 64 (vf3 +0x16, 必须 ≤ cell)
count : 4096, base=32, kern=0, met=0xD0
字形  : P=56 渲染 → 实际位图 ~50px（占 cell 78%）
```

**cell=64 的由来**：cell=72 只能装 2807 汉字槽，按拼音序截断会丢掉 w/x/y/z 拼音段常用字；cell=64 → 64×64=4096 槽 → 汉字槽 3767 ≥ GB2312 一级 3755，**完整覆盖一级字库**。

### 12.2 槽位规划（idx = 槽码 − 0x20）

| idx 范围 | 槽码范围 | 内容 | 数量 |
|---|---|---|---:|
| 0–94 | 0x20–0x7E | ASCII 可打印 | 95 |
| 95–127 | 0x7F–0x9F | **禁区 A → 留空** | (33) |
| 128–292 | 0xA0–0x144 | 符号 + 中文标点 + Latin 补充 | 165 |
| 293–328 | 0x145–0x168 | **禁区 B → 留空** | (36) |
| 329–4095 | 0x169–0x103F | 汉字（GB2312 一级全） | 3767 |

### 12.3 四字体同步（关键约束）

**凡内嵌图集名 = `font_body_nobdr.tga` 的 vf3，必须全部同步 count=4096 / L=64**：

| 字体 | 原状态 | v7 状态 |
|---|---|---|
| `font_body` | 336 / L=104 | 4096 / L=64 |
| `font_header_pc` | 336 / L=220（官方独立图集） | 4096 / L=64（指向 body 图集） |
| `font_header` | 336 / L=220（官方独立图集） | 4096 / L=64（指向 body 图集） |
| `font_debug` | 336 / L=36 / **kern=1668** | 4096 / L=64 / **kern=0** |

**为什么 font_header 必须改**：汉字槽码 idx 329+，若某 UI 走 count=336 的 font_header 渲染中文 → idx 336 以上越界 → 崩溃源。
**为什么 font_debug 必须改**：它也引用 `font_body_nobdr.tga`，图集已被替换，原 metric 会采到错误位置。

### 12.4 v7 构建中抓到的新雷（已写进脚本注释）

1. **GB2312 一级按拼音排序，绝不可截断取前 N 个**——会整段丢掉 w/x/y/z 拼音的常用字。教训催生了 `fail-fast` 缺字检查（译文用字全部命中字表才允许构建）。
2. **字形必须水平左对齐**：UV 矩形 = (cell_x, cell_y, width, L)，从 cell 起点算起；水平居中会让字形右移超出 UV 矩形右边界被裁切。
3. **vf3 头部 kern 字段必须与 metric 写入位置一致**：v7.1 曾按 template 原 kern（font_debug=1668）算 met=0x27f0 写 metric，又把头 kern 写 0 → 引擎按 met=0xD0 读到 kern 表数据，字形全乱。终版统一 kern=0 / met=0xD0，4 字体文件布局完全一致（均 98,512B）。

### 12.5 验证方法论（引擎视角自检）

构建后不要直接交给玩家，先按引擎采样方式验证：
1. `parse_cvbm` → `render_mip` 解码图集 alpha
2. 对每个测试字：按 vf3 metric 读 (adv, wid, x, y)，取 UV 矩形 (x, y, wid, L) 内像素数
3. 4 个 vf3 对同一字符的采样数必须**完全一致**
4. 用 metric + advance 拼接渲染多行文本预览图（`v7_preview.png`），肉眼确认字形完整无裁切

### 12.6 文本替换现状

译文 `source.jsonl`（5763 条）zh 字段全为 null（翻译 AI 未交付）。
v7 使用内置 EN2ZH 对照表（113 条常见 UI 词）按**原文精确匹配**替换 menu_us，命中 49 条原位覆盖。
全量替换管线已就绪：译文到位后扩展 `EN2ZH`（或从 jsonl 读 zh 字段）即可。

---

按 usz 匹配解压指定文件（绕开物理流错位）：

```python
import sys, struct, os
sys.path.insert(0, r'C:\Users\haojun0823\WorkBuddy\2026-09-04-02-19-04')
import vpp_pack as vp

vpp = r'<path>\misc.vpp_pc'
d = open(vpp, 'rb').read()

# 1) 扫全部 LZ4 块头
pos, i = [], 0x7000
while True:
    i = d.find(b'\xee\xdb\xee\x0f\xee\xdb\xba\x00', i)
    if i == -1: break
    m1, m2, clen, usz = struct.unpack_from('<IIII', d, i)
    if 0 < clen < 0x2000000 and 0 < usz < 0x10000000:
        pos.append((i, clen, usz))
    i += 8

# 2) 按目标 usz 解压
by_usz = {}
for p, clen, usz in pos:
    by_usz.setdefault(usz, []).append((p, clen))

target = 47554                      # 目标文件 usz
p, clen = by_usz[target][0]
payload = vp.lz4_decompress(d[p+16:p+16+clen], clen, target)
```
