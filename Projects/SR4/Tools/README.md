# Tools 索引（SR4）

`Projects/SR4/Tools/` 下的脚本分工。全部为 Python 3 脚本，无第三方依赖（仅标准库）。

## le_string 工具链（核心）

`.le_strings` 是引擎的本地化字符串表，SR4 与 SR3 Remastered **格式完全一致**：

```
header  12B  : ID u32(0xA84C7F73) | version u16 | bucketCount u16 | stringCount u32
bucket  16B  : count u32 | pad u32 | offsetTableOffset u32 | pad u32   (× bucketCount)
offset 表    : 从 bucket.offsetTableOffset 起, count 个 **8 字节** 条目
               = { 字符串绝对偏移 u32, 填充零 u32 }
字符串条目   : { hash u32, utf16le 文本, u16 0x0000 }
```

> ★★ **8 字节步长**是这套格式最容易踩的坑：offset 表每项是「u32 偏移 + u32 填充」共
> 8 字节，不是 4 字节。误用 4 字节步长会把填充零当成条目，导致一半条目被丢弃而
> `header.stringCount` 不变 —— 引擎按声明数索引越界 → NULL 指针 → 启动崩溃。
> 详见 `Documents/le_strings_repack_bugfix.md`。

| 脚本 | 作用 |
| --- | --- |
| `sr4le_extract.py` | 解包 `.le_strings` → `"KEY": "text"` 形式的 UTF-8 txt。 |
| `sr4le_repack.py` | 把翻译后的 txt 回写为 `.le_strings`。含 `repack`（全量重建）与 `repack_inplace`（原位覆盖）两种模式，带**写后自检 + 回读校验**。 |

**用法**

```bash
# 无参数 → 仅运行自检（CI 前置校验，不依赖任何游戏文件）
python sr4le_repack.py

# 全量重建（默认）：保留 bucket 分配，桶内按 hash 重排，允许更长文本
python sr4le_repack.py <原始le_strings目录> <翻译txt目录> <输出目录>

# 原位覆盖：布局完全不变，但要求每条文本 ≤ 原槽长，超槽即报错
python sr4le_repack.py <原始le_strings目录> <翻译txt目录> <输出目录> --inplace
```

## vpp 归档

| 脚本 | 作用 |
| --- | --- |
| `sr4_vpp.py` | vpp_pc v10 归档解包。 |
| `sr4_vpp_pack.py` | vpp_pc 归档重打包。 |

## 已归档（不在本目录）

原先在此的 `build.py` / `gen_aob_relaxed.py` / `verify_hooks.py` /
`verify_runtime_globals.py` / `dump_xrefs.py` / `gen_keydict.py` 等，以及一次性
诊断探针 `_*.py`，已移至 `Archives/SR4/Tools_redundant/`。
它们硬编码了旧仓库路径或已合入主构建脚本，不参与 le_string 构建链。

## 相关

- 顶层构建入口：`build_release_le_strings.py`（仓库根目录，SR3+SR4 一把梭）
- SR3 侧对应工具：`Projects/SR3/Tools/`（见该目录 README）
- 事故分析报告：`Documents/le_strings_repack_bugfix.md`
