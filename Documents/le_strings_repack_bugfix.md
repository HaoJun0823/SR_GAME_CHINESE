# le_strings 打包工具重大缺陷修复报告

**日期**: 2026-09-20
**影响范围**: SR3 Remastered (`SR3R_I18N`) / Saints Row IV (`SR4R_I18N`) 的全部 loose `*_us/_zh.le_strings`
**后果**: `loose_first=1` 时游戏启动即崩溃（`c0000005`），且汉化条目大面积丢失

---

## 1. 问题现象

`SR3R_I18N` 启用 `loose_first=1`（把磁盘项前移到挂载表头）后，游戏启动约 4 秒崩溃：

```
rip = SRTTR+0x105500
rax = 0            ← 崩溃指令 movzx edx,word ptr [rax]
rsi = SRTTR+0x1A179A0   (unk_141A179A0)
r14 = 0xC8              (200 = 装扮项大小)
r11 = 0
```

崩溃点归属函数 `sub_1403D4FE0`（装扮/服装定制项加载器，处理 `Customization_Item.xtbl`
的 `Wear_Options`）：遍历 wear-option 环形链表做 UTF-16 字符串比较时，
`*(qword*)(&unk_141A179A0 + v14 + 0x20)`（DisplayName 字符串指针）为 **NULL**，
代码无条件 `movzx edx,[rax]` 解引用。

崩溃时 `dword_141A17990 = 1`（只加载到第 2 个装扮项）。

---

## 2. 根因定位过程

### 2.1 排除项（非本因）

| 假说 | 证据 | 结论 |
|---|---|---|
| 字体图集重建导致 | 日志 `font0: LIVE atlas=8192x5184` 后 0.9s 崩 | ✗ 仅时间邻近 |
| `update\` 目录存在 | 移走 `update\` 后仍崩 | ✗ 但实验未完全受控 |
| DLL 的 10 个 hook 干扰 | hook 全为文本渲染（DrawWide/Format/SetText…），未触碰 customization | ✗ |
| 加载时序（注册序列未结束） | 日志 `count 3->3` 已是终态 | ✗ |

### 2.2 决定性证据链

**(a) 探针 `GetFileAttributesExA` 命中记录**（`SR3R_fsprobe.log`）

引擎的本地化表请求走**裸文件名**：

```
[hit ] static_us.le_strings        ← 裸名，无 cache\ 前缀
[hit ] customize_us.le_strings
[hit ] hud_us.le_strings
[hit ] menu_us.le_strings
... (共 11 个)
[hit ] cache\startup.vpp_pc        ← 容器包带 cache\ 前缀
[hit ] cache\misc.vpp_pc
```

→ 磁盘项排在 [0] 后，**引擎确实从 `update\` 读到了 loose le_strings**。
→ `customize_us.le_strings` 在命中列表内，与崩溃函数处理的装扮数据直接相关。

**(b) 文件体积对比 —— 全部缩水 55~67%**

| 文件 | `data/` 原始 | `update/` 产物（坏） | 比例 |
|---|---|---|---|
| `customize_us` | 130,090 | 59,020 | **45%** |
| `hud_us` | 134,728 | 44,474 | **33%** |
| `static_us` | 60,462 | 23,356 | **39%** |
| `menu_us` | 68,930 | 24,598 | **36%** |
| `patch0_us` | 4,662 | 1,860 | **40%** |

**(c) 文件头声明与实际内容不一致 —— 致命**

```
LOOSE update\customize_us    size= 59020   header = 73 7f 4c a8 | 01 00 | 00 02 | f5 0b 00 00
ORIG  data\...\customize_us  size=130090   header = 73 7f 4c a8 | 01 00 | 00 02 | f5 0b 00 00
                                             ^^^^^ magic ^ver ^buckets=512 ^nstr=3061
```

**`header.stringCount = 3061` 完全相同，但文件只有一半大小。**
引擎按声明建立索引结构 → 大量条目偏移越界 / 为 0 → 取到 NULL 字符串指针 → 崩溃。

**(d) 逐条目解析对比（步长 4 vs 8）**

| 文件 | 步长 | 条目总数 | 空槽 | 可解析 |
|---|---|---|---|---|
| customize_us | **4**（旧代码） | 3061 | **1405** | **1656** ❌ |
| customize_us | **8**（正确） | 3061 | **0** | **3061** ✅ |
| hud_us | **4** | 1624 | **752** | **872** ❌ |
| hud_us | **8** | 1624 | **0** | **1624** ✅ |

---

## 3. 真正根因

**`Projects/SR3/Tools/le_strings_repack.py` 中 offset 表步长写错：4 字节 → 应为 8 字节。**

### le_strings 真实布局（SR3 Remastered / SR4 一致）

```
header  12B : ID u32(0xA84C7F73) | version u16 | bucketCount u16 | stringCount u32
bucket  16B : count u32 | pad u32 | offTableOffset u32 | pad u32     (× bucketCount)
offset 表    : 从 bucket.offTableOffset 起，count 个 **8 字节** 条目
               = { 字符串绝对偏移 u32, 填充零 u32 }        ★★ 8 字节，不是 4 字节
字符串条目   : { hash u32, utf16le 文本, u16 0x0000 }
```

### 错误后果链

1. 读路径用 **4 字节步长** → 每个 8 字节条目的「填充零 u32」被当成下一个条目
2. 真实条目的偏移被**错位读取**，一半条目读出 `s_off == 0`，被判为「空槽」丢弃
3. 写回时这些槽位 offset 写 0，但 `header.stringCount` **沿用原值不变**
4. 产物 `bucket` 内条目数减半，与 header 声明不一致
5. 引擎按声明索引 → 偏移 0 或越界 → **NULL 字符串指针** → `sub_1403D4FE0` 无条件下解引用
6. **`c0000005` 崩溃**

> `customize_us` 3061 条中 **1405 条被静默丢弃**，产物从 130,090B 缩到 59,020B（-55%）。

### 错误代码位置

`Projects/SR3/Tools/le_strings_repack.py`

```python
# read_with_bucket() 第 25 行（修复前）
so = off + j * 4          # ✗ 错！应为 j * 8

# repack_inplace() 第 117 行（修复前）
s_off = struct.unpack_from("<I", buf, off + j * 4)[0]   # ✗ 同上
```

> `sr3le_extract.py`（第 108 行 `off + j * 8`）与 `sr4le_repack.py`（第 157 行 `off + j * 8`）
> **均正确**。唯独 `le_strings_repack.py` 这一处写错，属孤立缺陷。

---

## 4. 修复内容

### 4.1 `Projects/SR3/Tools/le_strings_repack.py`

| 位置 | 修改 |
|---|---|
| `read_with_bucket()` | `j * 4` → **`j * 8`**；并新增越界抛错、`total_count != nstr` 校验、`len(out) != nstr` 校验 |
| `repack_inplace()` | `j * 4` → **`j * 8`** |
| `repack()` | 新增**写后自检**：写回条目数必须 == `nstr`，offset 表长度必须 == `8*nstr` |
| `repack()` | 新增**回读校验**：产物重新解析，条目数必须 == `nstr`，否则抛错拒绝写出 |
| 文档字符串 | 补全布局说明 + 本次事故的「血的教训」注释 |

**关键改进**：以前是「错了也静默产出坏文件」，现在是「错了直接抛异常，拒绝写出」。

### 4.2 `build_release_le_strings.py`

| 位置 | 修改 |
|---|---|
| SR3 分支 | 由 `repack()`（全量重建）改为**优先 `repack_inplace()`**（原位覆盖，与原版布局字节级一致） |
| SR4 分支 | 同步改为**优先 `repack_inplace()`** |
| 降级逻辑 | 中文超槽（新文本 > 原槽长）时**自动降级为全量重建**，并记录到 `_overslot` 报告 |
| 输出 | 构建结束打印超槽文件清单 |

**为何优先 inplace**：布局与原版完全一致（引擎零风险），且避免了历史上
「SR3 Remastered 全量重排疑似引发 v3 崩溃」的隐患。

---

## 5. 验证结果

### 5.1 round-trip 无损性

`repack_inplace(src, {}, out)`（空 pairs）应与原文件**字节级一致**：

| 文件 | 原大小 | 产物大小 | 字节级一致 |
|---|---|---|---|
| customize_us | 130,090 | 130,090 | ✅ True |
| hud_us | 134,728 | 134,728 | ✅ True |
| static_us | 60,462 | 60,462 | ✅ True |
| menu_us | 68,930 | 68,930 | ✅ True |
| patch0_us | 4,662 | 4,662 | ✅ True |

### 5.2 构建产物条目数（修复后 vs 修复前）

| 文件 | 修复后 | 修复前（坏） | 原始 | 中文串 |
|---|---|---|---|---|
| customize_us | **3061** | 1656 | 3061 | 3006 |
| hud_us | **1624** | 872 | 1624 | 1616 |
| static_us | **1089** | 576 | 1089 | 1071 |
| menu_us | **967** | 508 | 967 | 949 |
| subtitle_us | **675** | 369 | 675 | 665 |

**条目数 100% 恢复。**

### 5.3 全量结构自检

部署后对 `update\` 全部 42 个文件复核：

```
合格: 42 / 42
★ 全部 42 个文件结构自检通过 (magic/bucket总数/无越界/条目数一致)
```

### 5.4 构建汇总

```
===== SR3 =====  完成: 21 个文件    (4 个文件有中文超槽, 已自动降级, 条目不丢)
===== SR4 =====  完成: 21 个文件    (8 个文件有中文超槽, 已自动降级, 条目不丢)
```

---

## 6. 已知遗留（中文超槽条目）

| 游戏 | 文件 | hash | 原英文 | 原槽 | 新中文 | 需 |
|---|---|---|---|---|---|---|
| SR3 | customize_us | `F60D5188` | `HO` | 6B | 街头客 | 8B |
| SR3 | menu_us | `EA270BB8` | `EULA` | 10B | 最终用户许可协议 | 18B |
| SR3 | static_us | `68078CBB` | `80S` | 8B | 80 年代风 | 14B |
| SR3 | subtitle_us | `79E8A8A6` | `I had him!` | 22B | 我刚才差一点就得手了！ | 24B |
| SR4 | customize_us | `B9B5536B` | — | 58B | — | 62B |
| SR4 | dlc2_us | `13749FDF` | — | 8B | — | 14B |
| SR4 | hud_us | `3D7E064B` | — | 22B | — | 24B |
| SR4 | menu_us | `AE3004A7` | — | 6B | — | 8B |
| SR4 | new_sr35_us | `E5A577CC` | — | 18B | — | 20B |
| SR4 | static_us | `68078CBB` | — | 8B | — | 10B |
| SR4 | subtitle_us | `0E154711` | — | 8B | — | 10B |
| SR4 | voice_us | `CC433000` | — | 8B | — | 10B |

这些条目已由**全量重建**兜底写入（条目不丢、不崩），但该文件会偏离原版布局。
若要恢复纯 inplace，需将中文文案压缩到原槽内，例如：

- `街头客` → `街头`（6B）
- `最终用户许可协议` → `许可协议`（10B）
- `80 年代风` → `80 年代`（8B）或 `复古`
- `我刚才差一点就得手了！` → `差一点就得手了！`（22B）

---

## 7. 部署清单

| 位置 | 内容 |
|---|---|
| 游戏 `update\`（SR3R） | 42 文件已部署；旧坏文件备份在 `update\_bak_broken_20260920\` |
| `release/sr3r_common/update/` | 42 文件（构建产出，不入库） |
| `release/sr4r_common/update/` | 42 文件（构建产出，不入库） |

> 注：早期的 `dist/common/update/{SR3,SR4}/` 副本已于仓库清理时删除。
> le_strings 现在**只由 `build_release.py` 的 step3 现场构建**到 `release/{gv}/update/`，
> 不再在 `dist/` 下维护一份静态副本（那是历史遗留的死数据，app 从不读取）。

---

## 8. 教训与防护

1. **二进制格式工具必须做「写后回读校验」** —— 本次缺陷静默存在，产物虽然"能生成、
   不报错"，但内容已损坏 55%；直到引擎崩溃才暴露。现已强制校验：条目数不符即抛异常。
2. **固定长度结构体的步长必须与文档一致** —— `sr3le_extract.py` 注释里其实已写明
   「offset 表为 8 字节步长，4 字节步长会把填充零当空槽跳掉、漏一半条目」，
   但 `le_strings_repack.py` 未同步。**同一格式的多份实现必须交叉校验。**
3. **优先「原位覆盖」而非「全量重建」** —— 前者天然保持与原版布局一致，
   不给引擎的隐含假设（如固定步长、线性排布）任何被触碰的机会。
4. **崩溃定位要用「数据流」而非「调用栈」** —— 本次调用栈符号全错（`AK::WriteBytesMem::Count`
   是导出符号最近匹配），真正有价值的是 `rip` 处的指令语义 + 寄存器 vs 全局地址
   （`rsi == unk_141A179A0`、`r14 == 200`），由此锁定「装扮项 DisplayName 为 NULL」。
