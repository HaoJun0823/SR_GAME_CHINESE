"""lint_exempt.py —— L1/L2 的豁免规则（可配置）。

为什么需要
----------
语料里大量条目的「译文 == 英文」是**故意的**，不是漏译：

  - ``RIM_179: "SD DCY14"``      轮毂型号，官方中文版亦保留
  - ``LOGO_KBOOM: "KBOOM WMD108"`` 电台台标/频率，必须保留
  - ``BTN_A: "A"``              手柄按键提示符
  - ``DPAD_UP``                方向键
  - ``PC_GS_PLAYER``            平台相关占位

若不豁免，L1 会报出 5000+ 条噪音，真问题被淹没 —— 这违背「零误报优先」。

用法
----
    python lint_exempt.py --list          # 打印全部豁免规则
    python lint_exempt.py --test BACK     # 测某key/文本是否被豁免

规则放在这里而不是散落在 lint脚本里，是为了让「豁免什么」可被审阅、可被质疑。
任何豁免都必须写明**理由**。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 键前缀豁免
# key 以这些前缀开头时，「译文==英文」属正常。理由逐条写明。
KEY_PREFIX_EXEMPT: dict[str, str] = {
    # 手柄/输入设备按键提示符：游戏内以图标+字母呈现，翻译反而不被识别
    "BTN": "手柄按键提示符（A/B/X/Y/LB…），保留",
    "DPAD": "方向键提示符，保留",
    "PC_": "PC 平台专属键（PC_GS_PLAYER 等），保留",
    "PLT": "平台相关占位（PLT_*），保留",
    "PS3": "PS3 平台键，保留",
    "PS4": "PS4 平台键，保留",
    "PS5": "PS5 平台键，保留",
    "XB1": "Xbox 平台键，保留",
    "XBOX": "Xbox 平台键，保留",
    "XBS": "Xbox 平台键，保留",
    "ANALOG": "模拟轴提示符，保留",
    "COMMUNITY": "平台/发行商社区功能键，保留",
    "MAINMENU": "主菜单功能键，保留",
    "MAIN": "主菜单功能键，保留",
    "COOP": "联机功能键，保留",
    # 外观件型号：轮毂/涂装/品牌 logo，官方中文亦保留拉丁字母
    "RIM": "轮毂型号（RIM_xxx=SD DCY14），保留",
    "LOGO": "电台台标/频率（LOGO_xxx），保留",
    "CREDITS": "制作人员署名，保留",
    "RADIO": "电台名，保留",
    # 地图图标：整条就是 [format][image:map_xxx][/format]，无可译文本
    "MAP_IMG": "地图图标标记模板，无可译文本",
    "MAP_ICON": "地图图标标记模板，无可译文本",
}

# ---------------------------------------------------------------- 文件级降级
# 某些文件整体噪音极大，且**绝大多数条目玩家在游戏里看不到**。
# 对这些文件把「未译」从 ERROR 降为 INFO，只在命中 VISIBLE_WHITELIST 时才报 ERROR。
#
# 依据（实测数据，见Documents/校对修复方案.md）：
#   exe_hardcoded.txt  ——从 exe 提取的字符串表。SR4 内未译 4060 条中，
#     绝大多数是动画状态机符号（``clip on`` / ``aim back`` / ``exit dive`` /
#     ``QTE start``）与引擎内部标识，玩家永不可见。
#   其余文件（voice_*.txt 等剧情对白）实测未译为 0，覆盖完整。
FILE_DOWNGRADE: dict[str, str] = {
    "exe_hardcoded.txt": "exe 提取的字符串表，多为动画状态机/引擎符号，玩家不可见",
}

# 文件被降级后，仍需按 ERROR 报告的「玩家可见」白名单。
#
# ⚠ 必须是**全词短语**（^…$ 或首尾空白/标点边界），不能用 \b 子串匹配。
#   实战教训：早先用 ``\bBACK\b`` 子串匹配，结果动画状态名
#   ``walk aim back lh`` / ``PO Back Upper`` / ``fire audio only on burst start``
#   全被误判成「玩家可见 UI」。这些是动画/音频状态标识，不该报 ERROR。
#   现只认「整条就是那个 UI 词」的形式。
VISIBLE_WHITELIST: list[tuple[str, str]] = [
    (r"^EXIT$", "退出"),
    (r"^BACK$", "返回"),
    (r"^BACK\s+TO\s+GAME$", "返回游戏"),
    (r"^SAVE$", "保存"),
    (r"^SAVE\s+GAME$", "保存游戏"),
    (r"^LOAD$", "读取"),
    (r"^LOAD\s+GAME$", "读取游戏"),
    (r"^PAUSE$", "暂停"),
    (r"^PAUSE\s+GAME$", "暂停游戏"),
    (r"^CANCEL$", "取消"),
    (r"^CONFIRM$", "确认"),
    (r"^SETTINGS?$", "设置"),
    (r"^OPTIONS?$", "选项"),
    (r"^CONTINUE$", "继续"),
    (r"^SELECT$", "选择"),
    (r"^START$", "开始"),
    (r"^QUIT$", "退出游戏"),
    (r"^ACHIEVEMENTS?$", "成就"),
    (r"^STORE$", "商店"),
    (r"^BUY$", "购买"),
    (r"^UNLOCK$", "解锁"),
    (r"^PRESS\s+START$", "按开始"),
    (r"^ARE\s+YOU\s+SURE\?$", "确定吗？"),
]

_VISIBLE_RE = [(re.compile(p, re.I), why) for p, why in VISIBLE_WHITELIST]


def file_downgraded(filename: str) -> tuple[bool, str]:
    """该文件是否整体降级。返回 (是否降级, 理由)。"""
    base = (filename or "").replace("\\", "/").split("/")[-1]
    if base in FILE_DOWNGRADE:
        return True, FILE_DOWNGRADE[base]
    return False, ""


def in_visible_whitelist(text: str) -> tuple[bool, str]:
    """降级文件里，是否仍属玩家可见文案。返回 (是否可见, 命中词)。"""
    t = text or ""
    for rx, why in _VISIBLE_RE:
        if rx.search(t):
            return True, why
    return False, ""


# 整条文本（剥掉所有标记后）不含任何「可读词」时豁免。
# 覆盖：纯 [format]/[image] 模板、纯 %d 占位、'{0}/{1} {2:text_tag_crc}'、'XX:XX:XX' 等。
#
# ⚠ 空白必须作为**独立分支**，不能写成每个分支前的 ``\s*``：
#   否则 ``{1} {2:...}`` 里右花括号后的空格无人消耗，整条匹配失败
#   （自检已抓到该回归，勿改回 ``\s*`` 前缀写法）。
_ONLY_MARKUP = re.compile(
    r"^(?:"
    r"\s|"                                # 空白独立成分支
    r"\[/?[a-z_]+[^\]]*\]|"                # 富文本标签 [format] [color:x] [/format]
    r"%[\d\$+\-\.# ]*[a-zA-Z%]|"           # printf 占位 %d %1$s %%
    r"\{[^{}]*\}|"                         # 花括号占位 {0} {2:text_tag_crc}
    r"\d+|[.:/,\-]"                        # 数字与分隔符
    r")*\s*$",
    re.I,
)


def is_markup_only(text: str) -> bool:
    """文本是否「剥掉标记后为空」——即根本没有任何可翻译内容。"""
    return bool(_ONLY_MARKUP.match((text or "").strip()))

# ---------------------------------------------------------------- 文本形态豁免
# 文本命中这些形态时，「译文==英文」属正常。
#
# ⚠ 型号正则必须要求「至少含一个数字」，否则 ``BACK``/``LOAD`` 这类纯大写单词
#    会被误豁免（自检已抓到该回归，勿改回 ``\d{0,4}``）。
TEXT_SHAPE_EXEMPT: list[tuple[str, str]] = [
    # 形如 SD DCY14 / R129 / V-F 603 / C-X 506：字母段 + **必含数字** + 可选连字符
    (r"^(?=.*\d)[A-Z]{1,4}[\s\-]?\d{1,4}[A-Z0-9\-]{0,8}$", "型号/代号形态（必含数字）"),
    # 纯频率：101.69 / 98.4
    (r"^\d{2,3}\.\d{1,3}$", "电台频率"),
    # 百分比已带 % 符号
    (r"^\d{1,3}%$", "百分比"),
    # 单个大写字母或数字（按键提示）
    (r"^[A-Z0-9]$", "单字符按键/占位"),
    # 电台台标 + 频率（KRHYME 95.4 / K12 97.6 / MAD DECENT 106.9）
    (r"^[A-Z][A-Z0-9\.\s]{1,20}\s?\d{2,3}\.\d{1,2}$", "电台台标+频率"),
    # 电台台标纯字母（R I T E / L E F T / DA WUB / TNSLB）
    (r"^([A-Z](?:\s+[A-Z]){1,4})$", "电台台标（字母间有空格）"),
    # 文件路径（\Documents\My Games\…）
    (r"^[A-Za-z]:?\\", "文件路径，不译"),
    # Windows 格式串（%0.2fft / %0.2fm）
    (r"^%[0-9.]+[a-z]{2,4}$", "数值格式串（英制单位后缀），不译"),
    # 纯变量名（HITMAN_DISPLAY_NAME / LV{0} / %ls）
    (r"^%[a-z]{1,3}$", "单个 printf 占位符"),
    (r"^(?=.*[_0-9{}])[A-Z0-9_]*(\{[A-Z0-9_]*\})?$", "含下划线/数字的全大写标识符"),
    # 证书/技术字符串
    (r"^(schannel|schannel:|schannel)", "Windows 证书错误串，技术文本不译"),
    # 武器型号：SR3 帮派武器有中文雅名（掘墓人/K-8 库鲁科夫/45 牧羊犬），
    # 但 TEK Z-10 / GL G20 在**全库 6 处一律保留英文**（召唤 TEK Z-10、
    # GL G20 发射器…），是既有一致做法，不是漏译（v1 曾误判，加自检防回归）。
    (r"^(TEK\s+[\w\-]+|GL\s+[\w\-]+)$", "武器型号，全库一致保留英文"),
    # 公司名 / 发行商 / 工作室（credits 里的法定实体名，保留原文）
    # ⚠ 必须含「公司特征词」，否则纯全大写单词（SNATCH / BACK）会被误吞。
    #   自检已两次抓到该回归，勿放宽。
    (r"^(?=.*(?:INC|LTD|LLC|CO|CORP|GAMES|GAMING|STUDIOS?|LABS?|"
     r"ENTERTAINMENT|AGENCY|SOFTWARE|PUBLISHER|DIGITAL|ART|POST|"
     r"PRODUCTION|SERVICES|SYSTEMS|VOLTA|SILVERLINK|BENELUX|NORDIC))"
     r"[A-Z][A-Za-z0-9\.\,\&'\-]*"
     r"(?:\s+[A-Z0-9][A-Za-z0-9\.\,\&'\-]*)*$",
     "公司/发行商法定名称，保留原文"),
    # 国名与地区（credits 里的发行地区）
    (r"^(NORTH AMERICA|SOUTH AMERICA|FRANCE|GERMANY|ITALY|SPAIN|JAPAN|BENELUX|"
     r"POLAND|NORDICS?|UNITED KINGDOM|AUSTRALIA|SWITZERLAND|BENELUX)$",
     "国名/地区，credits 用，保留原文"),
    # 技术缩写
    (r"^(VR|CPU|GPS|RPG|UFO|NASA|CPU|3D|APM)$", "技术缩写，保留"),
    # 图形/音频技术缩写（HDR / FXAA / TAA / VTOL / C.I.D. / XBOX LIVE）
    (r"^(HDR|FXAA|TAA|VTOL|STAG|C\.I\.D\.|XBOX LIVE|Xbox Live|OK|ALL|LIN|"
     r"WWGD|S\.T\.A\.G\.|AB|t)$", "技术缩写/按键/代号，保留"),
    # 含逗号或 & 的地区串（GERMANY, AUSTRALIA, SWITZERLAND）
    (r"^[A-Z][A-Z ,&\.]*[,&][A-Z ,&\.]*$", "地区列表串（credits），保留"),
    # 涂鸦标签（#FIAJ / #HOS 等街头涂鸦字样）
    (r"^#[A-Z0-9]{2,8}$", "涂鸦标签，保留原文"),
    # 占位符模板（{0} {1}(EQUIPPED)[/format]）
    (r"^\{[^}]*\}.*$", "以占位符开头的模板串"),
    # 未定稿标记
    (r"^<TBD>$", "未定稿标记（TODO 待补）"),
    # 字体测试串（!\"#$%&'()*+,-./0123456789:;<=>?@ABC…）
    # ⚠ 必须含**符号或数字**（用 (?=.*[^\w\s])  lookahead），否则纯大写单词
    #   BACK / SNATCH 会被误吞（自检已抓到该回归，勿放宽）。
    (r"^(?=.*[^A-Za-z\s])[!\"#\$%&'\(\)\*\+,-\./0-9:;<=>\?@A-Z\\]+$",
     "字体测试/符号串，不译"),
    # 混合大小写公司名（Silverlink / ZinTek）
    (r"^[A-Z][a-z]+[A-Za-z0-9\.\-]*$", "混合大小写专名（公司/品牌），保留"),
    # 地区列表带&（AUSTRALIA & NEW_ZEALAND）
    (r"^[A-Z][A-Z ]+&\s*[A-Z][A-Z ]+$", "地区列表串（credits），保留"),
    # 键盘/鼠标按键名（ALT / CTRL / Mouse 5）
    (r"^(ALT|CTRL|SHIFT|TAB|ESC|SPACE|ENTER|Mouse\s*\d+)$",
     "键鼠按键名，保留"),
    # 电台台标（MCMANUS 2020 / DA WUB / TNSLB / ONESITE）
    # ⚠ 只能用**显式枚举**，不能写泛匹配（如 ^[A-Z][A-Z0-9]*\s*\d*$）：
    #   那会把 SNATCH / BACK 这类该译的单词全部误吞（自检已抓到，勿改回泛匹配）。
    (r"^(MCMANUS|DA WUB|TNSLB|ONESITE|FRANKIE|FREQUENCY|FM|AM)$",
     "电台台标，保留"),
    # 带数字的公司名/商标（MCMANUS 2020 / ZinTek T.R.P.R. / FRAME MACHINE）
    # ⚠ 要求含「点号缩写」或 ≥2 词，纯单词仍走正常翻译（自检回归防线）。
    (r"^[A-Z][A-Za-z0-9\.\-]*(?:\s+[A-Z0-9][A-Za-z0-9\.\-]*){1,3}$",
     "多词公司名/商标，保留原文"),
    (r"^[A-Z][a-z]+[A-Za-z0-9\.]*(?:\s+[A-Z][A-Za-z0-9\.]*){1,3}$",
     "混合大小写多词商标（ZinTek T.R.P.R.），保留"),
    (r"^PLAION$", "发行商名 PLAION，保留"),
    # URL / 路径
    (r"^(https?://|www\.)", "URL，不译"),
    # 邮箱
    (r"^[\w.+-]+@[\w-]+\.\w+", "邮箱，不译"),
    # 引擎调试串：RenderWare 的 "RL: xxx" / "RL_xxx"，玩家永不可见
    (r"^RL[:_ ]", "RenderWare 引擎调试串，玩家不可见"),
    # DLL / 可执行文件名
    (r"^[\w\-]+\.(dll|exe|sys)$", "可执行/库文件名，不译"),
]

# ---------------------------------------------------------------- 完全跳过的 key
# 无论内容如何都不参与检查（如引擎内部标记）。
SKIP_KEY_EXACT: set[str] = set()

# key 子串包含即跳过
SKIP_KEY_SUBSTR: list[tuple[str, str]] = [
    ("CREDITS_", "制作人员署名"),
    ("_CREDIT", "制作人员署名"),
]

# ---------------------------------------------------------------- 编译

_KEY_PREFIX_RE = [(re.compile("^" + p), p, why)
                  for p, why in KEY_PREFIX_EXEMPT.items()]
_TEXT_SHAPE_RE = [(re.compile(pat), why) for pat, why in TEXT_SHAPE_EXEMPT]
_SKIP_SUBSTR = [(s.lower(), why) for s, why in SKIP_KEY_SUBSTR]


def is_exempt_key(key: str) -> tuple[bool, str]:
    """key 本身是否该被整体豁免。返回 (是否豁免, 理由)。"""
    k = (key or "").strip()
    if not k:
        return True, "空 key"
    # dict 载体的 key 就是英文原文；当「英文原文」本身是标记模板时同样豁免
    # （实测 SR4/dict 有 28 条 key 形如 [format][scale:1][image:ui_ctrl_360_dpad_…]）
    if is_markup_only(k):
        return True, "key 本身即富文本标记模板"
    if k in SKIP_KEY_EXACT:
        return True, "在 SKIP_KEY_EXACT 中"
    kl = k.lower()
    for sub, why in _SKIP_SUBSTR:
        if sub in kl:
            return True, f"key 含 {sub!r}（{why}）"
    for rx, prefix, why in _KEY_PREFIX_RE:
        if rx.match(k):
            return True, f"key 前缀 {prefix}（{why}）"
    return False, ""


def is_exempt_text(text: str) -> tuple[bool, str]:
    """文本形态是否属「不翻译」。返回 (是否豁免, 理由)。"""
    t = (text or "").strip()
    for rx, why in _TEXT_SHAPE_RE:
        if rx.match(t):
            return True, why
    if is_markup_only(t):
        return True, "仅含富文本标记/占位符，无可译文本"
    return False, ""


def should_skip_untranslated(key: str, text: str) -> tuple[bool, str]:
    """综合判断：这条「译文==英文」是否属故意保留。

    返回 (是否跳过, 理由)。``理由`` 供报告附注，便于人工抽查。
    """
    ok, why = is_exempt_key(key)
    if ok:
        return True, why
    ok, why = is_exempt_text(text)
    if ok:
        return True, why
    return False, ""


def _self_test() -> None:
    cases = [
        ("RIM_179", "SD DCY14", True, "型号"),
        ("LOGO_KBOOM", "KBOOM WMD108", True, "台标"),
        ("BTN_A", "A", True, "按键"),
        ("DPAD_UP", "UP", True, "方向键"),
        ("LOGO_SIZZURP", "Sizzurp 101.69", True, "频率"),
        ("ACT_VANDALISM_COMBO_NUMBER", "%i   ", True, "纯格式串"),
        ("DIVERSION_CHALLENGES_UNITS", "{0}/{1} {2:text_tag_crc}", True, "纯占位符"),
        ("RL: factory", "RL: factory", True, "引擎调试串"),
        ("MAP_IMG_CRIB", "[format][scale:1.0][image:map_other_crib][/format]", True, "图片标记"),
        ("ACT_FRAUD_HUD_MULT_X", "X", True, "单字符"),
        # ↓ 下面这些必须报为「真漏译」，是本脚本的回归防线
        ("TAT_REG_BACK", "BACK", False, "真漏译候选"),
        ("SMG_GANG", "TEK Z-10", True, "武器型号保留"),
        ("SNATCH", "SNATCH", False, "英文词应译"),
    ]
    bad = 0
    for k, t, want, label in cases:
        got, why = should_skip_untranslated(k, t)
        mark = "OK " if got == want else "FAIL"
        if got != want:
            bad += 1
        print(f"  {mark} {label:10s} {k:28s} {t[:18]!r:22s} -> skip={got} ({why})")
    print(f"\n自检 {'通过' if bad == 0 else f'失败 {bad} 项'}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="豁免规则查看/自检")
    ap.add_argument("--list", action="store_true", help="打印全部规则")
    ap.add_argument("--test", nargs="*", help="测试给定 key/text 是否豁免")
    a = ap.parse_args()

    if a.test:
        for i in range(0, len(a.test), 2):
            k = a.test[i]
            t = a.test[i + 1] if i + 1 < len(a.test) else ""
            got, why = should_skip_untranslated(k, t)
            print(f"  skip={got!s:5s} {k:28s} {t[:24]!r:26s} {why}")
    elif a.list:
        print("== key 前缀豁免 ==")
        for p, why in KEY_PREFIX_EXEMPT.items():
            print(f"  {p:12s} {why}")
        print("\n== 文本形态豁免 ==")
        for pat, why in TEXT_SHAPE_EXEMPT:
            print(f"  {pat:56s} {why}")
        print("\n== key 子串豁免 ==")
        for s, why in SKIP_KEY_SUBSTR:
            print(f"  {s:16s} {why}")
    else:
        _self_test()
