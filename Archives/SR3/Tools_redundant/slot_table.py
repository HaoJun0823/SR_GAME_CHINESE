# -*- coding: utf-8 -*-
"""
SRTT 字形分配表 (字符 <-> 槽码映射) 生成器
============================================
策略 (基于游侠成品逆向):
  ASCII (0x20..0x7E)   -> 明文 u16 (引擎原生支持)
  半角扩展 (0x80..0xFF) -> 明文 u16 (font_body 表已含)
  追加字符              -> 槽码 0x170+k (font_body 表 idx=336+k)
                          即 utf16 u16 = 368 + k
存于 JSON: {ch_str: slot_int}
"""
import json, os

BASE_SLOT = 0x170                  # 起始槽码 (= idx 336)
PREFIX_OFFSET = BASE_SLOT - 0x20   # ASCII 后追加段, 槽码 - 0x20 = idx - 224 ... 不直接


def build(chars):
    """输入: 可迭代字符. 返回 {ch: slot} dict. 跳过 ASCII (按引擎策略明文)."""
    out = {}
    next_slot = BASE_SLOT
    for ch in chars:
        if isinstance(ch, str):
            ch_int = ord(ch)
        else:
            ch_int = ch
        if 0x20 <= ch_int <= 0xFF:
            continue            # 原生 ASCII/西欧
        if ch_int in out:
            continue
        out[ch_int] = next_slot
        next_slot += 1
    return out


def slot_to_u16_bytes(slot):
    """单槽码 -> LE u16 字节 (不含终止符, 用 bytes() 链)"""
    return slot.to_bytes(2, "little")


def text_to_bytes(text, table_map):
    """整段文本 -> LE u16 字节流 (含末尾 0x0000); 未知字符按 '?' 跳过? 严格: 抛错"""
    out = bytearray()
    for ch in text:
        if isinstance(ch, str):
            ci = ord(ch)
        else:
            ci = ch
        if 0x20 <= ci <= 0xFF:
            out += ci.to_bytes(2, "little")
        elif ci in table_map:
            out += table_map[ci].to_bytes(2, "little")
        else:
            raise KeyError(f"字符 U+{ci:04X} ({chr(ci)}) 未在分配表中, 请先 build")
    out += b"\x00\x00"           # 终止符
    return bytes(out)


def save(table, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({f"U+{k:04X}": v for k, v in table.items()}, f, ensure_ascii=False, indent=1)


def load(path):
    raw = json.load(open(path, encoding="utf-8"))
    return {int(k[2:], 16): v for k, v in raw.items()}


def demo():
    chars = list("测试中文显示")
    table = build(chars)
    save(table, "slot_table.json")
    print("分配表:", {hex(k): hex(v) for k, v in table.items()})
    print("文本 '测试中文显示' 字节:", text_to_bytes("测试中文显示", table).hex())
    print("已存 slot_table.json")


if __name__ == "__main__":
    demo()