# 许可证索引 / License Index

本仓库（SR_GAME_CHINESE）及其两个子工程（`Projects/SR3`、`Projects/SR4`）的授权说明。

## 本项目协议 / This project

- **`GPLv3.txt`** — GNU General Public License v3.0
  本项目全部原创代码（汉化 DLL 源码、工具链脚本、翻译资产）均以 GPL-3.0 授权。

## 第三方依赖 / Third-party dependencies

| 文件 | 组件 | 协议 | 用途 |
|---|---|---|---|
| `MinHook.txt` | [MinHook](https://github.com/TsudaKageyu/minhook) — Tsuda Kageyu | BSD-2-Clause | 运行期 API Hook 库（经 NuGet `minhook.1.3.3` 引入，见 `Projects/*/packages/`，该目录不随仓库分发，由 `nuget restore` 还原） |
| `ASILoader.txt` | [Ultimate ASI Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader) — ThirteenAG | MIT | `binkw64.dll` 代理注入器：加载 `scripts\*.asi`，是汉化 DLL 的注入载体 |

## 随源码分发的第三方头文件 / Bundled headers

| 组件 | 协议 | 位置 |
|---|---|---|
| [stb_truetype](https://github.com/nothings/stb) — Sean Barrett | Public Domain / MIT（双授权） | `Projects/SR3/SR3R_I18N/stb_truetype.h`、`Projects/SR4/SR4R_I18N/stb_truetype.h` |

## 游戏资产 / Game assets

游戏本体及其资源（`*.vpp_pc`、`le_strings`、字体等）的版权归原厂商
（Deep Silver / Volition / THQ Nordic）所有。本仓库仅包含**汉化工程自身的产物**
（译文文本、工具链、注入 DLL 源码），不包含任何原版游戏资源文件。
