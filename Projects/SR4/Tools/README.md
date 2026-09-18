# Tools 索引（SR4）

`Projects/SR4/Tools/` 下的脚本分工。全部为 Python 3 脚本，多数接受 `--game <游戏目录>` 参数；
未传参时使用脚本内 `DEFAULT_GAME` 默认路径（原开发者本机 `I:\SteamLibrary\...`，**换机需自行覆盖**）。

## 构建

| 脚本 | 作用 |
| --- | --- |
| `build.py` | 用 MSBuild 构建 `SR4R_I18N`，无需打开 VS IDE。自动探测多个 VS 安装位置，并对环境变量做大小写去重（规避本机实测到的两个坑）。`--syntax-only` 走 `cl /Zs` 仅做语法检查。 |

## AOB 特征码

| 脚本 | 作用 |
| --- | --- |
| `gen_aob_relaxed.py` | 从游戏可执行段生成「放宽通配」的 L2 层 AOB 特征数组，输出到 `aob_relaxed_report.txt` / `gen_aob_relaxed.out` / 工程的 `aob_l2_arrays.inc`。 |
| `verify_hooks.py` | 跨构建 hook 落点验证器。对 Steam / GOG / Epic 等多份 `sr_hv*.exe` 逐个重放 8 条 AOB 特征码，确认落点是否名副其实。 |
| `verify_runtime_globals.py` | 验证「运行时自解引擎全局变量」机制是否在各构建上成立（不依赖硬编码绝对地址）。 |

## 文本资源

| 脚本 | 作用 |
| --- | --- |
| `sr4le_extract.py` | 解包 `.le_strings`（SR4 与 SR3 Remastered 格式一致）。 |
| `sr4le_repack.py` | 把翻译后的 txt 回写为 `.le_strings` 二进制。 |
| `sr4_vpp.py` | vpp_pc v10 归档解包。 |
| `sr4_vpp_pack.py` | vpp_pc 归档重打包。 |
| `gen_keydict.py` | 生成「本地化 KEY -> 文本」词典（`Resource/SR4/CHS/dict/le_string_keys.txt` 的来源）。 |

## 调试与分析

| 脚本 | 作用 |
| --- | --- |
| `dump_xrefs.py` | 对段 dump 做交叉引用分析。 |

> 一次性诊断探针（`_*.py`，共 14 个）已归档至 `Archives/SR4/Tools_probes/`。
> 它们硬编码了旧仓库路径与具体 dump 文件，属研究过程产物，不参与构建。
