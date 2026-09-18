# SRTT3 内核汉化 · 技术成果与未解决问题总汇

> 冻结时间: 2026-09-04 20:30 (第二次启动崩溃后)
> 游戏: Saints Row The Third Remastered (Steam)
> 路线: **纯资源替换 (不改 EXE)**, 走 `cache/misc.vpp_pc` 包体
> 权威基准: `cache/misc.vpp_pc.orig` (官方原版 117,533,402 B, 375 文件)

---

## 0. 一句话状态

| 项 | 状态 |
|---|---|
| 内核级中文上屏 | ✅ **已实测成功** (v5/v6, 24 字 8 菜单键) |
| 大字库方案 (v1/v7 系, count 数千) | 💥 **启动崩溃**, 根因尚未最终定位 |
| 崩溃特征 | 两次 dump 同 RVA `0x859FEF`, 同加载 `font_body.vf3_pc`, 字体对象 NULL |
| 最新推断修正 | "count 超 3448 硬上限" 已被 **count=3136 仍崩** 证伪 |

---

## 1. 已被实测验证的成功配置 (金标准 v6ok)

`cache/misc.vpp_pc.v6ok` = 116,820,697 B, 用户实测能进游戏且主菜单显示中文。

**v6ok 与官方 orig 的字节级对比结论 (2026-09-04 实测):**

| 文件 | orig | v6ok | 判定 |
|---|---:|---:|---|
| `font_body.vf3_pc` | 8,272 B | 8,272 B | **同尺寸、内容改** (count 仍 336) |
| `font_body.gvbm_pc` | 2,097,152 B | 2,097,152 B | **同尺寸、内容改** (烘字覆写) |
| `font_body_nobdr.gvbm_pc` | 2,097,152 B | 2,097,152 B | **同尺寸、内容改** |
| `font_body.cvbm_pc` / `_nobdr.cvbm_pc` | 110/116 B | 一致 | 未动 |
| `menu_us.le_strings` | 68,930 B | 68,930 B | 同尺寸、内容改 (文本替换) |

**核心方法论 (v6 成功的原因):**
- **不改变任何官方容器的结构/尺寸** —— vf3 的 `count` 保持官方 336, 只覆写空洞槽 idx 132–184 的度量 + 图集内对应区域烘入中文字形 + 文本条目替换。
- 全部改动都是"原位覆写", 引擎加载路径完全不感知文件变长/布局变化。
- 代价: 容量天花板 = 官方空洞槽数量 ≈ 24 字 (路线 A)。

> 推论: 凡"大改 vf3 结构 (count 扩到数千) + 全新大图集"的版本 (v1 3448 / v7.2 4096 / v7 3136) 全部崩在 font_body 加载, 无一幸免。

---

## 2. 已攻克的格式逆向 (全部经 IDA 反编译 / 字节验证)

### 2.1 vpp_pc 打包容器 (Volition v6 packfile) — 完整
- header 0x188B: magic `0x51890ACE`, ver 6, name@0x08, path@0x49, flags@0x14C (bit0=Compressed)
- 之后全 u64: unk@0x150, count@0x158, packfileSize@0x160, dirSize@0x168, nameSize@0x170, uncompressedDataSize@0x178, compressedDataSize@0x180
- chunk = 0x1000; 布局: chunk0=header → directory(48B×N) → names → data
- entry 48B: `{nameOffset, 0, dataOffset(虚拟 usz 累积), usz, csz, 0}`
- 物理偏移按 **csz** 0x1000 对齐累积; 虚拟 dataOffset 按 **usz** 对齐 (官方语义, diagnose 验证 ORIG/V6OK/V7 三包一致)
- 未压缩特例: csz = `0xFFFFFFFFFFFFFFFF`, 数据原样 (按 usz 对齐)
- **>32MB 文件多块 LZ4 (重要发现)**: 每块独立 `16B 头 {0x0FEEDBEE, 0x00BADBEE, lz4len, blk_usz} + LZ4 block`, 每块 usz ≤ 32MB (游戏解压器 block_size=32MB)。官方 48MB/133MB gpeg 即此格式。
- 工具: `vpp_pack.py` (pack/unpack, 32MB 分块已修正), `vpp_verify.py`, `vpp_probe.py`, `vpp_selfcheck.py`

### 2.2 le_strings 文本容器
- 按语言主题分文件 (21 主题 × 语言, 共 375 文件中 249 个 le_strings)
- 文本明文 UTF-16 存槽码 (us 分支); 渲染时 槽码 → idx = 槽码 − baseChar → 字体度量
- `sr3le_extract.py` 可解包 + charlist 解析 (Remastered 为 16B bucket, 2013 版工具不兼容)
- `le_strings_repack.py` / `merge_translations.py` 重建管线

### 2.3 vf3_pc 字体度量表 (magic `TNFV`) — 完整
- `+0x08` count(u32), `+0x0C` baseChar(u32=32), `+0x16` u16 行高 L, `+0x20` kern_count
- `+0x68` 64B 内嵌图集名 (如 `font_body_nobdr.tga` → 实际加载 `font_body_nobdr.cvbm_pc`)
- metric 起点 = align16(0xD0 + 6×kern_count); kern=0 时 met=0xD0
- 度量 16B/项: `{+0 advance, +4 width, +8 0, +12 i16 kernIdx}` ← **+0 是 advance 不是宽**
- 度量后: 区1 = x_px u32 × count (对齐16), 区2 = y_px u32 × count (对齐16)
- 引擎绘制 (IDA sub_1408B5FF0): UV 矩形 = (表 x, 表 y, metric+4 字形宽, L 行高); 槽码→idx→font+176+16×idx 度量
- 槽码跨字体一致性约束: 文本槽码全局唯一, 所有候选字体表须有定义 (越界即崩源)

### 2.4 cvbm_pc (GEKV v13) / gvbm_pc 图集容器 — 完整
- cvbm record 布局 (绝对偏移): file_off u64@0, w@8, h@0xA, fmt@0xC, flags 6B@0x10 (勿动), hasalpha@0x16, flags2 12B@0x17 (勿动), **mip_count@0x23**, **data_size@0x24**, unk3 32B@0x28 (勿动), name@0x48
- 字段陷阱: mip_count 在 0x23 非 0x10; data_size 在 0x24 非 0x20; hasalpha 在 0x16 非 0x13
- **build 策略**: 保留 template cvbm 全部字节, 只替换 7 个已知字段 (file_off/w/h/fmt/hasalpha/mip_count/data_size), 不可自拼 record
- gvbm = 裸 DDS 数据流 (DXT5, mip0 only 或 mip 链)
- 工具: `volition_tex.py` (parse_cvbm/parse_vf3/decode_dxt5/encode_dxt5/write_png/build_cvbm)

### 2.5 引擎文本渲染链路 (IDA 反编译证实)
- 绘制链: 文本 u16 槽码 → idx = 槽−32 → 度量 advance/width + x/y → 4 顶点 UV 矩形渲染
- 字号缩放: fontId<0 时 scale = qword_142998180[16×(fontId+0x7FFFFFFF)]
- 所有 vf3 内嵌图集名均为 `*_nobdr.tga` → 引擎实际字形源 = **5 个 nobdr 图集**
- 引擎内建逻辑字体名: jap / fnt_chinese / font_sk / font_body / font_header_pc / ug-debug / debug / thin

### 2.6 语言切换链路 (Steam → 引擎)
- exe 双语言表 @0xDD7660 (字节 dump 铁证)
- **PC 无中文入口**: 语言表无 0xd (fnt_chinese 逻辑名存在但资源/Steam 无对应) → 中文化必须"借道" (替换 us 分支或 hook)
- 游侠路线 = 借 us 分支 (玩家零操作)

### 2.7 字符集禁区 (v3/v4 崩溃根因)
- `menu_us` hash=0x2c6dc5dc 条目 = "可渲染字符全表" (172 项), 明确跳过:
  - 禁区 A: `0x7F..0xA0`
  - 禁区 B: `0x145..0x168`
- 槽码落禁区 → 异常路径哈希查找崩溃 (v3/v4)

### 2.8 DXT5 编解码
- 每块 16B (8B alpha + 8B color), 向量化编码 round-trip 最大误差 18
- 4096×4096 图集 = 16,777,216 B

---

## 3. 工具链 (工作区 40+ 脚本, 均在 `C:/Users/haojun0823/WorkBuddy/2026-09-04-02-19-04/`)

| 工具 | 用途 |
|---|---|
| `vpp_pack.py` | 打包/解包 (32MB 分块 LZ4; **必须用 venv python 3.13, 有 lz4**) |
| `vpp_extract_all.py` / `reextract_orig.py` | 全量解包 (reextract 支持多块 LZ4 → 375/375) |
| `volition_tex.py` | cvbm/vf3 解析 + DXT5 编解码 + PNG |
| `bake_lib.py` | 字形烘焙核心库 (几何/槽位规划/字集) |
| `build_v7.py` | v7 构建器 (字集→图集→vf3→文本→打包 全流程) |
| `build_menu_zh_v*.py` | v5/v6 24 字路线构建器 (金标准路线) |
| `le_strings_repack.py` / `sr3le_extract.py` | le_strings 重建/解析 |
| `merge_translations.py` | 译文合并 |
| `diagnose_vpp.py` | vpp dataOffset 语义反推 |
| `verify_v7_roundtrip.py` / `verify_deployed.py` / `verify_render.py` | 回环/部署/离线渲染校验 |
| `probe_gpeg.py` / `probe_zh_branch.py` / `scan_vpp_fonts.py` 等 | 探查器 |
| 第三方 | `sr-tools.exe` (只解不包), ali213 汉化包 (`汉化/` 目录: 3dm64.dll + TTF 注入路线, 与本路线互补) |

---

## 4. 关键经验 / 雷区 (血泪)

1. **GB2312 一级按拼音排序, 绝不可截断取前 N** —— 会整段丢 w/x/y/z 拼音常用字 (中/新/无/用/战/选…)
2. 字形必须**水平左对齐**: UV 矩形 = (cell_x, cell_y, width, L), 居中会被右边界裁切
3. vf3 kern 字段必须与 metric 写入位置一致 (kern=0 → met=0xD0 统一)
4. cvbm record 字段位置陷阱 (见 2.4), 只能 template 覆写 7 字段
5. 打包 >32MB 文件必须**32MB 分块** (见 2.1), 否则游戏读坏
6. vf3 度量 +0 是 advance, +4 才是字形宽 —— 反了会全乱
7. 字符集禁区 A/B 槽位必须留空
8. 删除/移动操作会被沙箱拦截 → 用覆盖写 (os.makedirs exist_ok) 代替 rmtree
9. 运行带 numpy/PIL/lz4 的脚本必须用 **venv python 3.13** (`C:\Users\haojun0823\.workbuddy\binaries\python\envs\default\Scripts\python.exe`)
10. cdb 离线分析 dump 时: 路径须原生 Windows 格式 (msys `/i` 会被误转), 命令用 `-cf` 文件逐行

---

## 5. 未解决问题 (按优先级)

### ★★★ P0: v7 系大字库启动崩溃 —— 真实根因未定位

**现象**: 两次 dump (`SRTTR_20260904-184210` / `SRTTR_20260904-201605`) 完全同特征:
- `c0000005` 读地址 `0x4` (NULL+4), SRTTR.exe RVA `0x859FEF`, 指令 `cmp dword ptr [rdi+4],4`, rdi=NULL
- 栈上明文字符串 `font_body.vf3_pc` → 崩在加载 font_body
- 调用栈: 资源异步加载线程 (StreamMgr/WriteBytesMem 路径), 进程存活 4–17 s
- 崩溃函数已定位到字体/vf3 解析区, 字体对象来自虚调用 `[r10+0x88]` 返回 NULL → 字形池/字体对象创建失败

**已被推翻的推断**: "count 超引擎 GPU 字形池上限 ≈3448" — v7 修复版把 count 降到 **3136 仍崩**, 上限推断不成立 (且 v1 在 3448 崩、v-exp 在 700 不崩, 无单调证据)。

**现行最可能的方向** (待验证):
1. **容器结构大改是崩因** —— v6ok (金标准) 完全不改结构只覆写; 而 v7 改 count→3136 使 vf3 文件 8,272B→75,472B、图集换成全新 16MB 重烘。引擎可能在加载时对 vf3/gvbm 的尺寸或内部布局有硬校验, 超限/不匹配 → 字体对象 NULL。
2. **图集尺寸/格式不被接受**: v6 图集保持官方 2MB 覆写; v7 图集 16MB (4096×4096, mips=1)。若引擎按 cvbm 头解析 4096² DXT5 失败 → 字体创建失败。游侠同尺寸能进的说法**未经本机实测** (仅解包分析)。
3. 次要: metric 表某区间越过引擎预分配, 或 L=72 与图集行布局不匹配。

**验证建议** (按序):
- A. 构造"count 最小增量实验": v6ok 基础上仅把 font_body count 336→700 (v-exp 已证明不崩) → 1024 → 2048 → 3072, 找崩溃拐点
- B. 在 v6ok 结构上只换 gvbm 为 16MB (不动 vf3 count), 隔离"图集尺寸"变量
- C. 在 v6ok 结构上只改 vf3 count (不动图集), 隔离"vf3 结构"变量
- D. 若 A–C 均不崩 → 崩因在 v7 的某种组合/字段, 用二分逐项还原 v7 改动

### ★★ P1: 大字库容量路线如何在不改结构的前提下扩展
- v6 路线 (覆写空洞槽) 容量 ≈24 字, 已满。要装 3000 字必须扩 count 或换图集 → 与 P0 直接冲突
- 若 P0 证实"引擎不接受结构大改", 则大字库需走 DLL 注入路线 (E) 或研究 jap/sk 分支槽位复用 (C/D, 见 §6)

### ★★ P2: 游侠 (ali213) 方案可信度待实测
- 游侠 font_body count≈3061、图集 16MB 的"能进游戏"仅来自解包分析,**未被本机启动实测**
- 待办: 把游侠整套字体文件直接放进官方 orig 重打包 → 启动验证
- 若游侠真能进 → P0 的图集/结构方向被推翻, 需重新找 v7 与游侠的差异
- 开放问题: 游侠槽码 `0x100..0xC13` 穿过禁区 B (0x145–0x168) 却能跑 → ① 游侠改了 0x2c6dc5dc 全表? ② 禁区非硬崩溃? (解游侠 menu_us 该条目对比即可澄清)

### ★ P3: 引擎内部容量模型未知
- "font gpu 池 17MB" 早期记录 vs 各版本实测无单调关系 (3448 崩 / 4096 崩 / 3136 崩 / 700 不崩 / 336 不崩)
- 需 IDA 精确定位字形池分配函数与上限算法 (非推测)

### ★ P4: 文本全量替换未完成
- 译文资产 `unpack/text/_handoff/source.jsonl`: tasks=5763, keys=6224, **zh 字段全为 null (翻译 AI 未交付)**
- v7 现用内置 EN2ZH 113 条精确匹配, 命中 menu_us 49 条; 全量替换需译文到位

### ★ P5: 细节开放问题
- charlist_us 运行时是否参与渲染 (游侠未改它却能跑)
- mips=11 全 mip 链是否必需 (官方 jap mips=1, 游侠 mips=11)
- `汉化/` 3dm 注入路线与字库路线的合并策略 (短期应急 vs 长期)

---

## 6. 路线决策记录

| 路线 | 做法 | 容量 | 判定 |
|---|---|---|---|
| **A. 挤公共空洞** (v6 金标准) | 官方结构覆写空洞槽 | 24 槽 (已满) | ✅ 唯一实测成功 |
| B. zh 分支 | 启用 fnt_chinese (语言码 0xd) | 389 槽 | ❌ PC 无 0xd 入口 |
| C. jap 分支 | Steam 切 japanese | 2195 槽 | ⚠️ 需玩家切日语 |
| D. sk 分支 | 韩文字体槽位 | 1607 槽 | ❌ 谚文无 CJK 先例 |
| E. DLL 注入 | hook 渲染层 Unicode 字典 | 无上限 | ⚠️ 最彻底但工作量大 (3DM 已有成品) |
| **F. 游侠 us 分支** | 复刻游侠 (大字库借 us) | ~3061 槽 | ⚠️ **待本机实测** (P2), 若可行即为主线 |

---

## 7. 留档清单 (cache/)

| 文件 | 大小 | 说明 |
|---|---:|---|
| `misc.vpp_pc.orig` | 117,533,402 | 官方原版 (最权威基准) |
| `misc.vpp_pc.official_ok` | 117,533,402 | 官方副本 |
| `misc.vpp_pc.f1` | 116,738,777 | 零改动重打包 (验证打包器 ✅) |
| `misc.vpp_pc.v5success` | 116,820,697 | 首个中文上屏版 (msyh, 24 字) |
| **`misc.vpp_pc.v6ok`** | **116,820,697** | **思源黑体 24 字 (金标准, 实测可进)** |
| `misc.vpp_pc.v2crash/v3crash/v4crash` | ~116.7M | 禁区/覆写崩溃版 |
| `misc.vpp_pc.v5blank` | 116,783,833 | DXT5 bug 空白版 |
| `misc.vpp_pc.v7crash` | 118,581,977 | v7.2 count=4096 崩溃版 |
| `misc.vpp_pc` (当前部署) | 118,127,321 | v7 fix count=3136 → **仍崩 (201605)** |
| `misc.vpp_pc.bisectA` / `crash0506` | — | 早期二分/崩溃留档 |

> 回滚到可玩状态: `cp cache/misc.vpp_pc.v6ok cache/misc.vpp_pc`

---

## 8. 建议下一步 (待用户裁决)

1. **回滚 v6ok** 恢复可玩 (立即)
2. 执行 §P0 验证 A–D 二分实验, 精确定位崩溃变量 (需多次启动游戏)
3. 若确认"结构大改必崩" → 决定大字库改走 E (注入) 或实测 F (游侠包)
4. 译文 5763 条到位后, 在可行路线上做全量文本替换

详细技术手册见 `SRTT3_CN_HANDBOOK.md` (格式逆向逐字段); 本文件为成果与问题总汇。
