# Tools 索引（SR3）

`Projects/SR3/Tools/` 下的脚本分工。全部为 Python 3 脚本，无第三方依赖（仅标准库）。

## le_string 工具链（核心）

`.le_strings` 是引擎的本地化字符串表，SR3 Remastered 与 SR4 **格式完全一致**：

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
>
> 历史背景：原版 SR3（2011）的 bucket 是 8 字节 `{count, offset}`；Remastered（2020）
> 扩为 16 字节。官方 2013 工具读 Remastered 文件会错位崩溃，故本目录工具按新布局重写。

| 脚本 | 作用 |
| --- | --- |
| `sr3le_extract.py` | 解包 `.le_strings` → `"KEY": "text"` 形式 txt（含 charlist 解码 + xtbl 键名反查）。 |
| `le_strings_repack.py` | **底层库**：保桶版 repack 实现（`repack` 全量重建 / `repack_inplace` 原位覆盖 / `read_with_bucket` 解析 / `self_test` 自检）。被 `sr3le_repack.py` 与顶层构建脚本 `import` 使用。 |
| `sr3le_repack.py` | 批量 CLI 包装：给定「原始 le_strings 目录 + 翻译 txt 目录 + 输出目录」，逐表回写。 |

**用法**

```bash
# 底层库自检（不依赖任何游戏文件，供 CI 前置校验）
python le_strings_repack.py

# 批量回写（默认全量重建；--inplace 为原位覆盖，超槽即报错）
python sr3le_repack.py <原始le_strings目录> <翻译txt目录> <输出目录> [--inplace]
```

> 注意：`sr3le_repack.py` 是手工调试用的批处理入口；**正式发布构建请用仓库根目录的
> `build_release_le_strings.py`** —— 它会自动在「原位覆盖」与「超槽降级全量重建」之间
> 选择，并对产物做全量结构自检。

## vpp 归档

| 脚本 | 作用 |
| --- | --- |
| `vpp_pack.py` | Remastered (Version06-x64) VPP 打包器（含 LZ4 块格式与 0x1000 对齐规则）。 |
| `vpp_extract_all.py` | 通用 vpp 全量解包（物理偏移按 csz 对齐累积）。 |

## 已归档（不在本目录）

原先在此的 `bake_lib.py` / `build_v7.py` / `extract_ali.py` /
`le_string_dev/*`（约 40 个翻译分片脚本）等研究过程产物，已移至
`Archives/SR3/Tools_redundant/`，不参与构建。

## 相关

- 顶层构建入口：`build_release_le_strings.py`（仓库根目录，SR3+SR4 一把梭）
- SR4 侧对应工具：`Projects/SR4/Tools/`（见该目录 README）
- 事故分析报告：`Documents/le_strings_repack_bugfix.md`
