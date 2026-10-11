# 语音字幕提取归档（Archives/Subtitle_Extraction）

本目录是 **2026-10-11 语音字幕逆向提取**的一次性产物留档。
归档原因：这是"游戏内语音字幕"这一**此前无任何记录的文本来源**的完整取样，
日后新增翻译、复核译文、或核对某句台词的官方多语言原文时都要用到，
重新提取需读懂 `VWSBPC`/`DMAV` 二进制结构，故原样留档。

提取工具已正式入库：`Projects/Common/tools/cli/vwsb_subtitle_extract.py`
（可随时重跑复现本目录全部文件）

---

## 一、这批数据是什么

游戏里玩家看到的**语音字幕**（角色说话时屏幕下方那行字）**不在** `le_strings` 文本表里，
而是存在 `voices.vpp_pc` 内的 `voc_*_media.bnk_pc` 音频包里。因此：

- 改 `le_strings` / `subtitle_us.le_strings` **影响不到**语音字幕
- 语音字幕必须在 `dict/voice_*.txt` 里按"英文原句 → 中文"查表替换（运行时 DLL 行为）

**本目录是全量语音字幕的唯一权威取样。**

## 二、文件清单

| 文件 | 内容 |
| --- | --- |
| `SR3R/subtitles_all.tsv` | SR3R 全部语音字幕 9,642 条（去重后唯一英文），含官方 JP/RU/DE/FR/IT/ES/PL/CZ 译文 |
| `SR3R/subtitles_gap.tsv` | 补齐前 SR3R 未被 dict 覆盖的字幕（12 条） |
| `SR4/subtitles_all.tsv` | SR4 全部语音字幕 9,718 条，含官方多语言译文 |
| `SR4/subtitles_gap.tsv` | 补齐前 SR4 未被 dict 覆盖的字幕（884 条） |

> `*_gap.tsv` 是**补齐前的快照**，保留它是为了留下"当初缺了什么"的证据。
> 补齐后可用 `vwsb_subtitle_extract.py --gap-only` 复算，当前结果为 0 条。

TSV 列义：`EN` = 英文原文；`JP/RU/DE/FR/...` = 该句在游戏内的**官方对应语言译文**。

## 三、数据结构（逆向结论）

```
voices.vpp_pc
  └─ voc_*_media.bnk_pc            签名 "VWSBPC  "
       ├─ +0x14 u32 事件表字节数
       ├─ +0x18 u32 事件数
       ├─ +0x20 事件表（每项 16B）
       │     SR3R(v6)  : 偏移字段在 +4
       │     SR4 (v10) : 偏移字段在 +0      ← 两者不同，勿混
       └─ DMAV 元数据块
            +0x04 version      +0x0C voiceline_id
            +0x18 subtitle_size  +0x1C subtitle_offset
            字幕表 = 0x24 + subtitle_offset
              u32 version
              version==2 → 28 槽（男/女两套 14；官方未实际启用，数据为复制）
              version==3 → 14 槽
              槽表后紧跟 UTF-16 文本池
```

### 语言槽序 = 引擎 Language 枚举

| 槽 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | **13** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 语言 | 英 | 西 | 意 | **日** | 德 | 法 | 荷 | 瑞 | 丹 | 捷 | 波 | **韩** | 俄 | **中** |

### 关键坑：非拉丁语言是"槽码"，不是 Unicode

日/韩/俄/中的槽内**不是** UTF-16 明文，而是查表码：
码点 `>= 0x100` 时需经 `misc.vpp_pc` 内的 `charlist_<code>.dat` 还原。

```
原始字节 D1 04 AD 01 41 01 ...  直接 UTF-16 读 = 'ӑƭŁŉפƤƅşƂŪƧŁ'（乱码）
                                 经 charlist_jp = '服を…　破らないでくれ…'（正确）
```

**这也是历史遗留死键的成因**：早期提取漏做该解码，
把 `ŭ`(U+016D) / `Ū`(U+016A) / `ū`(U+016B) 这类槽码原样写进了 dict 键，
而 DLL 查表是 `wmemcmp` 逐字符严格比对（**大小写、标点、空格全敏感**），
导致这些条目在游戏中永远不命中。已在本次补齐中一并修复。

## 四、当前覆盖率（2026-10-11 补齐后）

| 游戏 | 语音字幕总条数 | dict 已覆盖 | 缺口 |
| --- | --- | --- | --- |
| SR3R | 9,642 | 9,642 | **0** |
| SR4 | 9,718 | 9,718 | **0** |

## 五、复现方式

```bash
# 全量导出（含官方多语言参照）
python Projects/Common/tools/cli/vwsb_subtitle_extract.py --game SR3R \
    --vpp "<游戏>/cache/voices.vpp_pc" \
    --misc "<游戏>/cache/misc.vpp_pc" \
    --out SR3R/subtitles_all.tsv

# SR4 路径为 packfiles/pc/cache/voices.vpp_pc
python Projects/Common/tools/cli/vwsb_subtitle_extract.py --game SR4 \
    --vpp "<游戏>/packfiles/pc/cache/voices.vpp_pc" \
    --misc "<游戏>/packfiles/pc/cache/misc.vpp_pc" \
    --out SR4/subtitles_all.tsv

# 只查缺口（与 Resource 下 dict 比对，复刻 DLL 匹配规则）
python Projects/Common/tools/cli/vwsb_subtitle_extract.py --game SR4 \
    --vpp ... --misc ... --dict-dir Resource/SR4/CHS/dict \
    --gap-only --out gap.tsv
```

## 六、遗留事项

- **中文槽（13）在游戏中是空的**。官方给 14 种语言都留了槽，但中文槽全库 0 条数据
  （SR3R 有 `charlist_zh.dat` 但仅 92 个汉字、`font_zh` 也在；SR4 连 zh 资产都没有）。
  现有汉化走的是运行时 DLL 查表替换，不走原生中文槽。
- 官方 JP 译文与英文原文**并非总能对应**（部分 DMAV 事件的语言槽疑有漂移，
  如 `Lasers! Merde.` 的 RU 与 EN 一致而 JP 讲的是另一句）。
  翻译时**以英文为准**，官方多语言仅作参考。
- SR4 的 `voiceline_hash` 在 bank 之间**不唯一**，不能当作全局主键使用。
