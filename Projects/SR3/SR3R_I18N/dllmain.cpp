// dllmain.cpp : SR3R_I18N - 黑道圣徒3重制版 外挂汉化 DLL（v6：文本替换 + 中文字形层）
//
// v6 = v5 文本替换层 + 运行时中文字形渲染层（零资源文件改动）
//
//  [文本层 v5]
//   1. 加载 scripts\<dict>\*.txt（le_strings 格式: "英文原文": "中文译文"; SR3R_I18N.ini 可配置）
//   2. Hook A  sub_1408B5FF0  宽字符绘制核心   — R9  = 文本指针 -> 查词典替换
//   3. Hook B  sub_140812610  Volition formatter — RDX = 格式串 -> 查词典替换
//   4. DumpText.dtxt 未命中文本去重收集（仅英文，过滤 CJK）
//
//  [字形层 v6]（引擎结构 2026-09-05 IDA 逆向实证）
//   字体对象布局（cpeg cvbm_pc, 208B 头 + 变长区）:
//     +8  u32 字形数  +12 u32 baseChar  +16 i32 missAdvance  +20 u16 行高
//     +22 u16 cell高  +28 i32 全局字距 +32 u32 kern数  +36/+38 i16 顶部/左侧偏移
//     +104 图集名     +168 kern表(6B/项{u16 left,u16 right,i8 off})  +176 metrics(16B/项)
//     +184 u32 texId  +192 xtab(u32/项 图集X)  +200 ytab(u32/项 图集Y)
//   metrics 16B/项: +0 i32 advance  +4 i32 cell宽  +12 i16 kern起始idx(-1=无)
//   渲染(DrawWide): quad宽=metrics[+4], quad高=font[+22], UV=(xtab,ytab)+cell尺寸
//
//   方案:
//   5. Hook C sub_140859B10 字体对象查询 -> 命中含中文文本的字体时返回"伪字体对象"
//      （count 扩为 0xFFE0 覆盖 0x20..0xFFFF; 官方槽码区 metrics/xtab/ytab 照抄,
//        中文字符槽码=cp-0x20 填新图集坐标; kern 表直接指官方）
//   6. Hook D sub_14085DB30 纹理对象查询 -> MAGIC texId 返回伪纹理对象（宽/高）
//   7. Hook E sub_140915D60 texId->SRV 解析 -> MAGIC texId 返回自建图集 SRV
//      MAGIC = 0x60000000 + fontId: bit24=0 不入动态纹理分支, 远超纹理注册数
//      -> SRV 缓存表(sub_140916DD0)对界外 id 自动跳过读写, 无越界
//   8. 两阶段构建（避免渲染线程长卡顿）:
//      后台线程: stb_truetype 按 cellH 光栅化字符集 -> 位图缓存
//      渲染线程: 首次绘制该字体时读回官方图集(staging) + 拼接中文区
//                + CreateTexture2D/SRV + 组装伪对象（~15ms）
//   9. 官方图集原样 blit 保留 -> 未翻译内容(Credits 等)渲染不变
//
//  [字幕注入 v7.5]（Hook J: sub_1402D2BC0 字幕/HUD 绘制入口, 游侠方案同点位）
//   引擎在此函数内部对文本折行(sub_14085A1F0)后逐行绘制 —— 入口整串替换为
//   中文整句, 引擎按 CJK 宽度自然折行, 从根上消除"折行残段查词典 miss"。
//   （v7.3 状态机在 Format 层的折行后拼接仅作 UI 文本兜底, 与本 hook 互补）
//
// 安全性:
//   - 伪对象不在 fontTab, 引擎卸载(sub_14085A250)遍历不到, 无双重释放
//   - HookFontLookup 校验 fontTab[slot]==构建时官方指针, 引擎若重建字体自动回退
//   - 词典加载失败 -> 只装文本 hook 未装字体 hook, 行为=v5
//   - hook 安装前比对目标入口 16 字节特征, 防游戏更新后错位

#include "pch.h"
#include <intrin.h>          // _ReturnAddress (v7.4 Format 调用点分类诊断)
#include <MinHook.h>
#include <d3d11.h>

#define STB_TRUETYPE_IMPLEMENTATION
#include "stb_truetype.h"
#include "charset_data.h"

// ---------- 配置 ----------
static constexpr uint64_t GAME_BASE = 0x140000000ULL;

// ============ v7.6 目标定位：AOB 特征扫描（Steam / EPIC 双版本通用） ============
//   背景: v7.5.2 及以前把 10 个 hook 点与 4 个引擎全局写成硬编码 VA, 只对 Steam 版
//   SRTTR.exe 有效。EPIC 版 SRTTR_EPIC.exe 的代码/数据布局整体漂移, 且同一功能各自
//   的漂移量互不相同（.text +0x00830 ~ +0x48F9E0, .data +0x67F80 ~ +0x682A0）, 因此
//   不存在"统一加一个偏移就能对齐"的取巧解, 硬编码 VA 全部失效。
//
//   方案（两级定位）:
//     1) 函数入口: AOB 字节特征 + 通配符, 在主模块 .text 段内扫描, 强制唯一命中。
//     2) 引擎全局: 由「锚点函数入口 + 固定偏移」现场解码 rip-relative 操作数求地址。
//        （全局变量本身在数据区, 没有稳定字节可扫, 只能借引用它的代码反推）
//
//   IDA 实测对照（Steam -> EPIC, 2026-09-12; 两版同一函数入口字节逐字节相同,
//   只有 rip/call 的 disp32 不同 —— 这正是 AOB 能一版通吃的根据）:
//     DrawWide   1408B5FF0 -> 1408FE9D0 | Format      140812610 -> 14085B0F0
//     FontLookup 140859B10 -> 1408A2600 | TexObj      14085DB30 -> 1408A6620
//     SrvResolve 140915D60 -> 14095E740 | LangCur     140812060 -> 14085AB40
//     LangTxt    140812040 -> 14085AB20 | SetText     14082EA00 -> 1408774E0
//     Refresh    14082DD10 -> 1408767F0 | Subtitle    1402D2BC0 -> 1402D33F0
//     FONTCOUNT  142998160 -> 142A00400 | FONTTAB     142998168 -> 142A00408
//     D3D_DEVICE 1430784F8 -> 1430E0478 | D3D_CONTEXT 143078500 -> 1430E0480
//   上面 EPIC 一列仅供排查对照, 运行时不再使用任何硬编码 VA。

// AOB 特征描述: 文本形式 "48 8B 05 ?? ?? ?? ??", "??" = 通配一个字节。
//   启动时 ParseAob() 拆成 pat[]/msk[] 两列, 扫描时逐字节比对（msk=0 跳过）。
struct AobSig
{
    const char* name;
    const char* text;
    uint8_t     pat[64];
    uint8_t     msk[64];   // 1=须精确匹配, 0=通配
    int         len;
};

// ---- hook 点特征（函数入口; 两版唯一命中） ----
static AobSig SIG_DRAW_WIDE = { "DrawWide",
    "40 53 56 57 48 81 EC 10 01 00 00 8B BC 24 60 01 00 00 49 8B D9 0F 29 B4 24 ?? ?? ?? ?? 8B CF 44 0F 29 74 24" };
static AobSig SIG_FORMAT = { "Format",
    "40 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 88 D0 FF FF B8 78 30 00 00 E8 ?? ?? ?? ?? 48" };
static AobSig SIG_FONT_LOOKUP = { "FontLookup",
    "83 F9 FF 7D 3E 8D 81 FF FF FF 7F 83 F8 FF 7E 2E 3B 05 ?? ?? ?? ?? 7D ?? 48 63 C1 48 B9 F4 FF FF FF 07 00 00 00" };
static AobSig SIG_TEXOBJ = { "TexObj",
    "4C 63 C1 44 3B 05 ?? ?? ?? ?? 73 71 4C 8B 0D ?? ?? ?? ?? 4B 8D 14 40 49 8B 4C D1 08 49 8D 04 D1 48 85 C9 74 4E" };
static AobSig SIG_SRV_RESOLVE = { "SrvResolve",
    "48 83 EC 28 4C 63 C1 44 0F B6 D2 41 83 F8 FF 0F 84 E7 00 00 00 41 0F BA E0 18 73 1F 41 8B C8 E8 ?? ?? ?? ?? 48" };

// v7.4 早期整句替换（引擎原生支持日/韩 => CJK 布局/切行管线现成, 让引擎自己切中文行）
//   语言服务对象 qword_142906D28 的 vtable[0]/[1] 被两个 9 指令 thunk 尾调:
//     sub_140812040: mov rax,[qword_142906D28]; test rax,rax; jz +0B; mov rdx,[rax];
//                    test rdx,rdx; jz +3; jmp rdx; ret0          -> vtable[0]() 当前解析文本
//     sub_140812060: 同上但 mov rdx,[rax+8]; jz +0C              -> vtable[1]() 当前文本
//   文本对象刷新 sub_14082DD10 优先取 vtable[1]() 的返回串当 Format 的 fmt:
//   在此返回层把英文完整句替换为中文整句, Format 之后由布局引擎 sub_140834C30
//   按 CJK 字形宽度自切行 —— 无需再赌 wrap-rejoin 状态机。
//   （IDA 2026-09-06 实测字节; jz 偏移两 thunk 不同, 因 [rax] vs [rax+8] 长度差）
static AobSig SIG_LANG_CUR = { "LangCur",
    "48 8B 05 ?? ?? ?? ?? 48 85 C0 74 0C 48 8B 50 08 48 85 D2 74 03 48 FF E2 33 C0 C3" };
static AobSig SIG_LANG_TXT = { "LangTxt",
    "48 8B 05 ?? ?? ?? ?? 48 85 C0 74 0B 48 8B 10 48 85 D2 74 03 48 FF E2 33 C0 C3" };

// v7.4.3 诊断 hook H: 文本对象 SetText (sub_14082EA00, 文本子系统对象方法区)
//   char SetText(obj, TextDesc* desc);  desc: [0]=tag, [8]=文本指针(char*)
//   tag2=清空(释放模板 a1[0x128]); tag4/9=设置: a1+0x124=sub_140812240(文本)(行段不在
//   语言表=>0), 模板 a1+0x128=文本副本, a1+0x14C|=2 标脏 -> 下帧刷新 Format(模板)。
//   意图: 抓字幕播放器真实 SetText 序列(完整句 or 行段? 行序?) 决定早期替换落点。
//   注意: 头 16 字节在两版均有两个候选（EPIC: 140844920 与 1408774E0）, 必须取到
//   tag 分派那一段（+16 起的 "48 8B FA"）才能唯一 —— 故特征加长到 32 字节。
static AobSig SIG_SET_TEXT = { "SetText",
    "48 89 5C 24 08 48 89 74 24 10 57 48 83 EC 20 8B 02 48 8B FA 48 8B F1 83 F8 02 0F 84 E2 00 00 00 83 F8 04" };

// v7.4.4 诊断 hook I: 文本对象刷新 (sub_14082DD10, vtable 槽 0x1436782D8)
//   每帧被全局刷新器对文本对象调用; 内部: 标脏才 Format(模板/当前文本) -> 布局 sub_140834C30。
//   obj+0x124=dword hash, obj+0x128=模板窄串(char*)。无论模板谁写入(SetText/直接拷贝),
//   刷新层都能看到模板内容与变化时机。签名取函数头 32 字节（E8 = call alloca_probe, 通配）。
static AobSig SIG_TEXTOBJ_REFRESH = { "Refresh",
    "48 89 5C 24 10 48 89 74 24 18 48 89 7C 24 20 55 B8 80 72 00 00 E8 ?? ?? ?? ?? 48 2B E0 48 8D 6C" };

// v7.5.1 中文字符集扩充配置
//   字形层原始设计只收词典译文用字(2380), 内核汉化/官方 le_data 文本用字不在词典里
//   -> 部分字缺字形空白("齿"等). 修复: 启动时加载 charlist.txt 合并进 g_charSet,
//   来源覆盖 scripts/dict + le_data/schinese + le_data/voice_dumps_chinese 全量用字.
//   格式: UTF-8, ';' 开头注释行, 数据行按频率降序直接排列字符, 零宽字符/控制/空白忽略.
static constexpr size_t  CHARLIST_MAX_BYTES = (1 << 20);   // 1MB 上限
static constexpr uint32_t CHARLIST_FREQ_BASE = 60000;      // 权重基值(>词典真实频率上限, 保证 charlist 顺序优先)

// v7.5 字幕/HUD 绘制入口整串替换（游侠 ali213 SIG3 同点位; IDA 2026-09-06 实测）
//   sub_1402D2BC0(text, a2, a3, a4): a1=宽字符串, 内部 sub_14085A1F0 折行布局后
//   逐行绘制; a3<=0.1 时从尾部字面 \n<毫秒> 解析显示时长(atoi/1000), 否则 2s。
//   特征码前 32 字节在两版全映像唯一（已验证）。
static AobSig SIG_SUBTITLE_DRAW = { "Subtitle",
    "4C 8B DC 55 57 41 54 49 8D AB A8 FB FF FF 48 81 EC 40 05 00 00 41 0F 29 73 B8 45 0F 29 4B 98" };

// ---- 引擎全局的「锚点 + 偏移」（全局变量在数据区, 借引用它的代码反推地址）----
//   FontLookup 内部 +0x43: cmp ecx, cs:dword_XXX  (3B 0D disp32, 指令长 6) -> 字体数
//   FontLookup 内部 +0x57: mov rax,  cs:qword_XXX (48 8B 05 disp32, 指令长 7) -> 字体指针表
static AobSig* const     ANCHOR_FONTTAB   = &SIG_FONT_LOOKUP;
static constexpr ptrdiff_t OFF_FONTCOUNT  = 0x43;
static constexpr ptrdiff_t OFF_FONTTAB    = 0x57;
static constexpr int       LEN_CMP_RIP    = 6;    // 3B 0D disp32
static constexpr int       LEN_MOV_RIP    = 7;    // 48 8B 05/35 disp32

//   D3D 两个 slot 相距固定 8 字节（device 在前, context 在后）:
//   CtxGetter (sub_1409181B0 / sub_140960B90, 纯 getter)  +0x18: mov rax, cs:qword_XXX
//   DevGetter (sub_140918FD0 / sub_1409619B0, 渲染器构造) +0x44: mov rsi, cs:qword_XXX
static AobSig SIG_D3D_CTX_GETTER = { "D3dCtxGetter",
    "48 89 5C 24 08 57 48 83 EC 20 8B DA 48 8B F9 E8 ?? ?? ?? ?? 85 DB 75 0E" };
static AobSig SIG_D3D_DEV_GETTER = { "D3dDevGetter",
    "40 57 48 83 EC 30 48 C7 44 24 20 FE FF FF FF 48 89 5C 24 40 48 89 6C 24 48 48 89 74 24 50 8B EA 48 8B D9 48 8D 05 ?? ?? ?? ?? 48 89 01 48 8D 05" };
static constexpr ptrdiff_t OFF_D3D_CONTEXT = 0x18;
static constexpr ptrdiff_t OFF_D3D_DEVICE  = 0x44;

// 运行时解析结果（0 = 未定位）
static uint8_t*  g_fnDrawWide      = nullptr;
static uint8_t*  g_fnFormat        = nullptr;
static uint8_t*  g_fnFontLookup    = nullptr;
static uint8_t*  g_fnTexObj        = nullptr;
static uint8_t*  g_fnSrvResolve    = nullptr;
static uint8_t*  g_fnLangCur       = nullptr;
static uint8_t*  g_fnLangTxt       = nullptr;
static uint8_t*  g_fnSetText       = nullptr;
static uint8_t*  g_fnRefresh       = nullptr;
static uint8_t*  g_fnSubtitle      = nullptr;
static uint8_t*  g_fnD3dCtxGetter  = nullptr;
static uint8_t*  g_fnD3dDevGetter  = nullptr;
static uint64_t  g_vaFontTab       = 0;   // 字体对象指针表（VA）
static uint64_t  g_vaFontCount     = 0;   // 字体数（VA）
static uint64_t  g_vaD3dDevice     = 0;   // ID3D11Device*（VA）
static uint64_t  g_vaD3dContext    = 0;   // ID3D11DeviceContext*（VA）

static constexpr uint32_t MAGIC_TEXID_BASE = 0x60000000u;   // +fontId（bit24=0, 界外）
static constexpr uint32_t FAKE_FONT_MAX    = 256;
static constexpr uint32_t FAKE_GLYPHS      = 0xFFE0u;       // 0x20..0xFFFF 全覆盖
static constexpr uint32_t FONT_BASECHAR_DEFAULT = 0x20u;

static constexpr size_t ARENA_BYTES   = 128u << 20;   // 词典字符串区（几万条译文上限安全值）
static constexpr uint32_t DICT_BUCKETS = 1u << 15;
static constexpr uint32_t ORIG_BUCKETS = 1u << 14;   // origin 联表 16384 桶（1 万+ ID 键）
static constexpr uint32_t DUMP_BUCKETS = 1u << 12;
static constexpr size_t DUMP_MAX_CHARS = 256;
static constexpr DWORD  STATS_PERIOD_MS = 30000;

// v7.3 字幕折行重组（引擎按字幕框宽度 word-wrap 后逐行查词典, 完整句 key 永远 miss）
//   段1(前缀行, 末尾无标点) 先 miss -> 缓存; 段2(后缀行) 到来时拼接重查,
//   命中后按英文长度比例切分译文: 前缀行显示前半, 后缀行显示后半（每帧反复命中, 状态常驻）
static constexpr size_t   WRAP_MAX_CHARS  = 256;   // 缓存残段/拼接缓冲上限（wchar）
static constexpr uint64_t WRAP_TTL_MS     = 5000;  // 残段缓存过期（防不同句子串扰）
static constexpr size_t   WRAP_MIN_PREFIX = 8;     // 前缀残段最短长度（过滤短 UI 串噪音）
static constexpr size_t   WRAP_MIN_SUFFIX = 2;     // 后缀残段最短长度
// ---------- VA -> 本进程地址 ----------
static uint64_t s_exeBase = 0;   // 游戏模块实际基址（MainThread 内 GetModuleHandleW(nullptr); 诊断分类用）
template <typename T = uint8_t*>
static inline T VA(uint64_t va)
{
    return reinterpret_cast<T>(va - GAME_BASE + reinterpret_cast<uint64_t>(GetModuleHandleW(nullptr)));
}

// ---------- 日志 ----------
static FILE*             g_log = nullptr;
static CRITICAL_SECTION  g_logCS;

static void LogOpen(HMODULE hSelf)
{
    wchar_t path[MAX_PATH];
    GetModuleFileNameW(hSelf, path, MAX_PATH);
    wchar_t* slash = wcsrchr(path, L'\\');
    if (slash) *(slash + 1) = L'\0'; else path[0] = L'\0';
    wcscat_s(path, L"SR3R_I18N.log");
    _wfopen_s(&g_log, path, L"wb");
}

static void Log(const char* fmt, ...)
{
    if (!g_log) return;
    EnterCriticalSection(&g_logCS);
    SYSTEMTIME st;
    GetLocalTime(&st);
    fprintf(g_log, "[%02u:%02u:%02u.%03u T%lu] ",
            st.wHour, st.wMinute, st.wSecond, st.wMilliseconds, GetCurrentThreadId());
    va_list ap;
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
    LeaveCriticalSection(&g_logCS);
}

// ---------- 配置（SR3R_I18N.ini, 与 asi 同目录; 缺失时用默认值） ----------
struct Config
{
    wchar_t dictDir[MAX_PATH];    // 词典文件夹（相对 asi 目录）
    wchar_t originDir[MAX_PATH];  // 联表文件夹（ID/HASH_ -> 英文原文; 相对 asi 目录）
    wchar_t fontFile[MAX_PATH];   // 字体 TTF 文件名（相对 asi 目录）
    wchar_t charlistFile[MAX_PATH]; // v7.5.1: 字符清单文件名（相对 asi 目录; 空=禁用）
    bool     dumpEnabled;         // 未命中文本收集（DumpText.dtxt）
    bool     langEarly;           // v7.4: 语言服务返回层整句替换（引擎自切行）
    bool     earlyDiag;           // v7.4: 早期替换命中/Format miss 调用点诊断日志
    bool     subtitleEarly;       // v7.5: 字幕绘制入口整串替换（Hook J）
    wchar_t exeOverride[MAX_PATH]; // v7.6.1: 版本强制覆盖 (auto/steam/gog/epic); auto=PE 指纹判别
};

static Config g_cfg = {
    L"dict",                       // 默认: scripts/dict/
    L"origin",                     // 默认: scripts/origin/
    L"SourceHanSansHWSC-VF.ttf",   // 默认字体
    L"charlist.txt",               // 默认: scripts/charlist.txt
    false,                         // v7.5.2: dump 默认关（内核汉化已接管, 收集无意义; 开发期 ini 置 1）
    true,                          // lang_early
    false,                         // v7.5.2: early_diag 默认关（探针已退役; 开发期 ini 置 1）
    true,                          // subtitle_early
    L"auto",                       // exeOverride: 默认自动 (PE 指纹判别)
};

// UTF-8 无 BOM/带 BOM ini 行解析（手工实现, 避免路径中文问题）
static void LoadConfig(const wchar_t* iniPath)
{
    HANDLE f = CreateFileW(iniPath, GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) { Log("cfg: %ls not found, defaults", iniPath); return; }
    LARGE_INTEGER sz;
    GetFileSizeEx(f, &sz);
    if (sz.QuadPart <= 0 || sz.QuadPart > (1 << 20)) { CloseHandle(f); return; }
    auto* buf = static_cast<uint8_t*>(VirtualAlloc(nullptr, (SIZE_T)sz.QuadPart,
                                                   MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    DWORD rd = 0;
    BOOL ok = buf && ReadFile(f, buf, (DWORD)sz.QuadPart, &rd, nullptr);
    CloseHandle(f);
    if (!ok) { if (buf) VirtualFree(buf, 0, MEM_RELEASE); return; }

    // UTF-8 -> UTF-16（跳 BOM）
    int utf8Off = (rd >= 3 && buf[0] == 0xEF && buf[1] == 0xBB && buf[2] == 0xBF) ? 3 : 0;
    int wlen = MultiByteToWideChar(CP_UTF8, 0, (const char*)buf + utf8Off,
                                   (int)(rd - utf8Off), nullptr, 0);
    if (wlen <= 0 || wlen > 65536) { VirtualFree(buf, 0, MEM_RELEASE); return; }
    auto* wbuf = static_cast<wchar_t*>(VirtualAlloc(nullptr, (wlen + 1) * sizeof(wchar_t),
                                                    MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!wbuf) { VirtualFree(buf, 0, MEM_RELEASE); return; }
    MultiByteToWideChar(CP_UTF8, 0, (const char*)buf + utf8Off, (int)(rd - utf8Off), wbuf, wlen);
    wbuf[wlen] = L'\0';
    VirtualFree(buf, 0, MEM_RELEASE);

    // 逐行取 key=value（忽略节名/注释/空行）
    wchar_t* ctx = nullptr;
    wchar_t* line = wcstok_s(wbuf, L"\r\n", &ctx);
    while (line)
    {
        wchar_t* eq = wcschr(line, L'=');
        if (!eq) { line = wcstok_s(nullptr, L"\r\n", &ctx); continue; }
        *eq = L'\0';
        wchar_t* key = line;
        wchar_t* val = eq + 1;
        // 去首尾空白
        while (*key == L' ' || *key == L'\t') ++key;
        wchar_t* ke = key + wcslen(key);
        while (ke > key && (ke[-1] == L' ' || ke[-1] == L'\t')) *--ke = L'\0';
        while (*val == L' ' || *val == L'\t') ++val;
        wchar_t* ve = val + wcslen(val);
        while (ve > val && (ve[-1] == L' ' || ve[-1] == L'\t')) *--ve = L'\0';

        if (_wcsicmp(key, L"dict_dir") == 0)         wcscpy_s(g_cfg.dictDir, val);
        else if (_wcsicmp(key, L"origin_dir") == 0)  wcscpy_s(g_cfg.originDir, val);
        else if (_wcsicmp(key, L"font_file") == 0)   wcscpy_s(g_cfg.fontFile, val);
        else if (_wcsicmp(key, L"charlist_file") == 0) wcscpy_s(g_cfg.charlistFile, val);
        else if (_wcsicmp(key, L"dump_enabled") == 0) g_cfg.dumpEnabled = (*val != L'0');
        else if (_wcsicmp(key, L"lang_early") == 0)  g_cfg.langEarly  = (*val != L'0');
        else if (_wcsicmp(key, L"early_diag") == 0)  g_cfg.earlyDiag  = (*val != L'0');
        else if (_wcsicmp(key, L"subtitle_early") == 0) g_cfg.subtitleEarly = (*val != L'0');
        else if (_wcsicmp(key, L"exe") == 0)            wcscpy_s(g_cfg.exeOverride, val);

        line = wcstok_s(nullptr, L"\r\n", &ctx);
    }
    VirtualFree(wbuf, 0, MEM_RELEASE);
    Log("cfg: %ls loaded (dict_dir=%ls origin_dir=%ls font_file=%ls charlist=%ls dump=%d early=%d diag=%d sub=%d exe=%ls)",
        iniPath, g_cfg.dictDir, g_cfg.originDir, g_cfg.fontFile, g_cfg.charlistFile, (int)g_cfg.dumpEnabled,
        (int)g_cfg.langEarly, (int)g_cfg.earlyDiag, (int)g_cfg.subtitleEarly, g_cfg.exeOverride);
}

// ---------- 版本判别（PE 指纹; 与 SR4R 同思路。SR3R 目标定位全 AOB, 故仅用于日志标注与 ini 覆盖） ----------
//   关键: 判别绝不可靠 exe 文件名 —— EPIC 版官方安装也叫 SRTTR.exe（与 Steam 同名）,
//         若按文件名走会漏判（SR4R 曾踩此坑: EPIC 被误判 Steam 导致全局变量取错地址、文字不显示）。
//   指纹 = TimeDateStamp 主键 + AddressOfEntryPoint / SizeOfImage 消歧。
//     （DRM/loader 常会在内存改写 AddressOfEntryPoint, 故不能强求三字段全等, 以 TimeDateStamp 为准。）
//   实测（2026-09-12 由 Tools/verify_build_fp_sr3r.py 从 PE 头提取）:
//     Steam  SRTTR.exe       0x60EE85F4 0x0371F310 0x03759000
//     EPIC   SRTTR_EPIC.exe  0x617A5EF9 0x00D25760 0x0378A000
//     GOG    (暂缺主程序)     TODO        待用户补充 GOG 主程序指纹
struct ExeBuild
{
    const wchar_t* key;         // ini `exe=` 可用值
    const char*    name;        // 日志标签
    uint32_t       timeStamp;
    uint32_t       entryPoint;
    uint32_t       sizeOfImage;
};

static const ExeBuild kExeBuilds[] = {
    { L"steam", "Steam", 0x60EE85F4u, 0x0371F310u, 0x03759000u },
    { L"gog",   "GOG",   0u,          0u,          0u          },  // TODO: 待用户补充 GOG 主程序指纹
    { L"epic",  "EPIC",  0x617A5EF9u, 0x00D25760u, 0x0378A000u },
};

static const ExeBuild* FindBuildByName(const wchar_t* n)
{
    for (const ExeBuild& b : kExeBuilds)
        if (_wcsicmp(b.key, n) == 0) return &b;
    return nullptr;
}

// 读主模块 PE 头, 返回命中的构建描述; 未命中返回 nullptr (三字段仍回填供上报)
static const ExeBuild* DetectBuildByFp(uint32_t* outTs, uint32_t* outEp, uint32_t* outSoi)
{
    auto* base = reinterpret_cast<uint8_t*>(GetModuleHandleW(nullptr));
    if (!base) return nullptr;
    auto* dos = reinterpret_cast<PIMAGE_DOS_HEADER>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return nullptr;
    auto* nt = reinterpret_cast<PIMAGE_NT_HEADERS>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return nullptr;
    if (nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) return nullptr;

    const uint32_t ts  = nt->FileHeader.TimeDateStamp;
    const uint32_t ep  = nt->OptionalHeader.AddressOfEntryPoint;
    const uint32_t soi = nt->OptionalHeader.SizeOfImage;
    if (outTs)  *outTs  = ts;
    if (outEp)  *outEp  = ep;
    if (outSoi) *outSoi = soi;

    // TimeDateStamp 主键: 任一构建唯一即可; 另两字段仅作更严格消歧(DRM 改写 EP 也不影响判读)
    for (const ExeBuild& b : kExeBuilds)
        if (b.timeStamp && b.timeStamp == ts) return &b;
    return nullptr;
}

static const ExeBuild* g_build = nullptr;   // 运行时命中的构建（含 ini 强制覆盖）

// ---------- CRC-32 (IEEE 反射, 与 zlib.crc32 一致) ----------
static uint32_t g_crcTable[256];

static void CrcInit()
{
    for (uint32_t i = 0; i < 256; ++i)
    {
        uint32_t c = i;
        for (int k = 0; k < 8; ++k)
            c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        g_crcTable[i] = c;
    }
}

static uint32_t CrcText(const wchar_t* s, size_t len)
{
    uint32_t crc = 0xFFFFFFFFu;
    const uint8_t* p = reinterpret_cast<const uint8_t*>(s);
    const size_t n = (len + 1) * 2;
    for (size_t i = 0; i < n; ++i)
        crc = g_crcTable[(crc ^ p[i]) & 0xFFu] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFu;
}

// ---------- 词典（开链 CRC 哈希, 常驻 arena, 只读无锁） ----------
struct DictNode
{
    uint32_t      crc;      // 原文 CRC
    uint32_t      len;      // 原文长度（不含 NUL）
    const wchar_t* orig;    // 原文（碰撞校验）
    const wchar_t* trans;   // 译文（返回值）
    DictNode*     next;
    uint8_t       hasCjk;   // 译文含非 ASCII（触发字体升级）
};

static DictNode** g_dictBuckets = nullptr;
static uint32_t   g_dictMask    = 0;
static uint32_t   g_dictCount   = 0;

static uint8_t* g_arena     = nullptr;
static size_t   g_arenaUsed = 0;

static void* ArenaAlloc(size_t n)
{
    n = (n + 15) & ~size_t(15);
    if (g_arenaUsed + n > ARENA_BYTES) return nullptr;
    void* p = g_arena + g_arenaUsed;
    g_arenaUsed += n;
    return p;
}

// 词典加载后置 1（未加载时字形层不激活）
static volatile LONG g_dictReady = 0;

// 词典是否成功加载（MainThread 依据 LoadDictDir 返回值置位;
// 为 0 时 FontFileThread 无条件注入内置内核字符集, 不依赖 g_charCount 隐式推断）
static volatile LONG g_dictLoaded = 0;

// 中文/非 ASCII 字符集（65536 位 bitmap, 词典译文收集）
static uint8_t  g_charSet[8192];
static uint32_t g_charCount = 0;

// 字符使用频率（词典全文出现次数, RasterizeThread 按频率降序光栅化: 高频字优先入图集）
static uint16_t g_charFreq[65536];

static void CharSetAdd(wchar_t c)
{
    if (c < 0x80) return;
    if (c > 0xFFFD) return;
    uint32_t i = (uint32_t)c;
    if (!(g_charSet[i >> 3] & (1u << (i & 7))))
    {
        g_charSet[i >> 3] |= (uint8_t)(1u << (i & 7));
        ++g_charCount;
    }
    if (g_charFreq[i] < 0xFFFFu) ++g_charFreq[i];   // 频率饱和计数
}

// charlist 字符: 已有词典频率则取 max(现有, 权重), 否则直接赋权重
//   (不用 CharSetAdd 累加: 防同一字符多行重复推高排名)
static void CharSetAddWeighted(wchar_t c, uint16_t w)
{
    if (c < 0x80) return;
    if (c > 0xFFFD) return;
    uint32_t i = (uint32_t)c;
    if (!(g_charSet[i >> 3] & (1u << (i & 7))))
    {
        g_charSet[i >> 3] |= (uint8_t)(1u << (i & 7));
        ++g_charCount;
    }
    if (g_charFreq[i] < w) g_charFreq[i] = w;
}

// ---------- v7.5.1: charlist.txt 加载（扩充字形层字符集） ----------
// 返回: 合并的新增字符数（文件缺失/解析失败返回 0, 仅警告日志, 不影响主流程）
static uint32_t LoadCharList(const wchar_t* path)
{
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE)
    { Log("charlist: %ls not found (optional, skip)", path); return 0; }
    LARGE_INTEGER sz;
    GetFileSizeEx(f, &sz);
    if (sz.QuadPart <= 0 || sz.QuadPart > (LONGLONG)CHARLIST_MAX_BYTES)
    { Log("charlist: bad size %lld (skip)", sz.QuadPart); CloseHandle(f); return 0; }

    auto* buf = static_cast<uint8_t*>(VirtualAlloc(nullptr, (SIZE_T)sz.QuadPart,
                                                   MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    DWORD rd = 0;
    BOOL ok = buf && ReadFile(f, buf, (DWORD)sz.QuadPart, &rd, nullptr);
    CloseHandle(f);
    if (!ok || rd != (DWORD)sz.QuadPart)
    { Log("charlist: read failed"); if (buf) VirtualFree(buf, 0, MEM_RELEASE); return 0; }

    // UTF-8 -> UTF-16（跳 BOM; 与 ini 同一套手工解析, 避开 CRT locale）
    int utf8Off = (rd >= 3 && buf[0] == 0xEF && buf[1] == 0xBB && buf[2] == 0xBF) ? 3 : 0;
    int wlen = MultiByteToWideChar(CP_UTF8, 0, (const char*)buf + utf8Off,
                                   (int)(rd - utf8Off), nullptr, 0);
    if (wlen <= 0 || wlen > (int)CHARLIST_MAX_BYTES / 2)
    { Log("charlist: utf8 convert failed"); VirtualFree(buf, 0, MEM_RELEASE); return 0; }
    auto* wbuf = static_cast<wchar_t*>(VirtualAlloc(nullptr, (SIZE_T)(wlen + 1) * sizeof(wchar_t),
                                                    MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!wbuf) { VirtualFree(buf, 0, MEM_RELEASE); return 0; }
    MultiByteToWideChar(CP_UTF8, 0, (const char*)buf + utf8Off, (int)(rd - utf8Off), wbuf, wlen);
    wbuf[wlen] = L'\0';
    VirtualFree(buf, 0, MEM_RELEASE);

    uint32_t added = 0, lines = 0;
    uint32_t rank = 0;                       // 已遍历的有效字符数(频率降序假设)
    wchar_t* p = wbuf;
    while (*p)
    {
        wchar_t* eol = p;
        while (*eol && *eol != L'\n' && *eol != L'\r') ++eol;
        // 逐字符处理本行 [p, eol)
        for (wchar_t* q = p; q < eol; ++q)
        {
            wchar_t ch = *q;
            if (ch == L';') break;                       // 注释行(及行内注释)
            uint32_t cp = (uint32_t)ch;
            if (cp < 0x80 || cp < 0x20) continue;        // ASCII/控制
            if (cp >= 0x200B && cp <= 0x200D) continue;  // 零宽字符(水印)
            if (ch == L' ' || ch == L'\t') continue;
            // 频率权重: 越靠前越高(仅用于容量截断时的优先级), 饱和下限 1
            uint32_t w = CHARLIST_FREQ_BASE > rank ? CHARLIST_FREQ_BASE - rank : 1;
            if (w > 0xFFFFu) w = 0xFFFFu;
            if (!(g_charSet[cp >> 3] & (1u << (cp & 7)))) ++added;
            CharSetAddWeighted(ch, (uint16_t)w);
            ++rank;
        }
        ++lines;
        p = eol;
        if (*p == L'\r') ++p;   // CRLF / CR
        if (*p == L'\n') ++p;
    }
    VirtualFree(wbuf, 0, MEM_RELEASE);
    Log("charlist: %ls merged %u new chars (total %u, rankPos %u, lines %u)",
        path, added, g_charCount, rank, lines);
    return added;
}

static bool DictInsert(const wchar_t* key, uint32_t keyLen, const wchar_t* trans)
{
    auto* node = static_cast<DictNode*>(ArenaAlloc(sizeof(DictNode)));
    if (!node) return false;
    auto* keyCopy = static_cast<wchar_t*>(ArenaAlloc((keyLen + 1) * sizeof(wchar_t)));
    if (!keyCopy) return false;
    memcpy(keyCopy, key, keyLen * sizeof(wchar_t));
    keyCopy[keyLen] = L'\0';

    node->crc   = CrcText(keyCopy, keyLen);
    node->len   = keyLen;
    node->orig  = keyCopy;
    node->trans = trans;
    node->hasCjk = 0;
    for (const wchar_t* p = trans; *p; ++p)
        if (*p >= 0x80) { node->hasCjk = 1; break; }
    uint32_t h = node->crc & g_dictMask;
    node->next  = g_dictBuckets[h];
    g_dictBuckets[h] = node;
    ++g_dictCount;
    return true;
}

static const DictNode* DictLookup(const wchar_t* s, size_t len)
{
    if (!g_dictBuckets) return nullptr;
    uint32_t crc = CrcText(s, len);
    for (DictNode* n = g_dictBuckets[crc & g_dictMask]; n; n = n->next)
        if (n->crc == crc && n->len == len && wmemcmp(n->orig, s, len) == 0)
            return n;
    return nullptr;
}

// ---------- origin 联表（消息 ID/HASH_ -> 英文原文; 加载后只读无锁, 与词典同型） ----------
// scripts\origin\*.txt: "消息ID或HASH_xxx": "英文明文"
// 引擎运行时绘制的是解析后的英文明文（DumpText 实证）, 消息 ID 不会到达 hook 层;
// 词典键为消息 ID 时经此表消解为英文原文再入主表。
struct OrigNode
{
    uint32_t      crc;      // 键（消息 ID/HASH_）CRC
    uint32_t      keyLen;   // 键长度（不含 NUL, 碰撞校验）
    uint32_t      valLen;   // 英文原文长度（不含 NUL）
    const wchar_t* key;     // 键（碰撞校验）
    const wchar_t* val;     // 英文原文
    OrigNode*     next;
};

static OrigNode** g_origBuckets = nullptr;
static uint32_t   g_origMask    = 0;
static uint32_t   g_origCount   = 0;

static bool OrigInsert(const wchar_t* key, uint32_t keyLen, const wchar_t* val, uint32_t valLen)
{
    auto* node = static_cast<OrigNode*>(ArenaAlloc(sizeof(OrigNode)));
    if (!node) return false;
    auto* keyCopy = static_cast<wchar_t*>(ArenaAlloc((keyLen + 1) * sizeof(wchar_t)));
    if (!keyCopy) return false;
    auto* valCopy = static_cast<wchar_t*>(ArenaAlloc((valLen + 1) * sizeof(wchar_t)));
    if (!valCopy) return false;
    memcpy(keyCopy, key, keyLen * sizeof(wchar_t));
    keyCopy[keyLen] = L'\0';
    memcpy(valCopy, val, valLen * sizeof(wchar_t));
    valCopy[valLen] = L'\0';

    node->crc    = CrcText(keyCopy, keyLen);
    node->keyLen = keyLen;
    node->valLen = valLen;
    node->key    = keyCopy;
    node->val    = valCopy;
    uint32_t h = node->crc & g_origMask;
    node->next = g_origBuckets[h];
    g_origBuckets[h] = node;
    ++g_origCount;
    return true;
}

static const OrigNode* OrigLookup(const wchar_t* s, size_t len)
{
    if (!g_origBuckets) return nullptr;
    uint32_t crc = CrcText(s, len);
    for (OrigNode* n = g_origBuckets[crc & g_origMask]; n; n = n->next)
        if (n->crc == crc && n->keyLen == len && wmemcmp(n->key, s, len) == 0)
            return n;
    return nullptr;
}

static bool TrimRange(const wchar_t* s, size_t len, size_t* outStart, size_t* outLen)
{
    size_t b = 0, e = len;
    while (b < e && s[b] <= 0x20) ++b;
    while (e > b && s[e - 1] <= 0x20) --e;
    if (b >= e) return false;
    *outStart = b;
    *outLen   = e - b;
    return true;
}

// KEY 空格规范化: 引擎折行/查表前会把连续空格压缩为单空格（实测: 词典 1917/9630 条
// KEY 含连续空格, 引擎侧 text 全是单空格形态, 两侧永不匹配）。
// 加载时对每个 KEY 建规范化副本键; 运行时对未命中文本先规范化再查一次。
// 返回规范化后长度（源里无连续空格时返回 0, 无需副本）
static size_t NormalizeKey(const wchar_t* s, size_t len, wchar_t* out, size_t outCap)
{
    size_t w = 0;
    for (size_t i = 0; i < len; ++i)
    {
        wchar_t c = s[i];
        if (c == L' ' && w > 0 && out[w - 1] == L' ') continue;   // 压连续空格
        if (w >= outCap) return 0;
        out[w++] = c;
    }
    if (w == len) return 0;                                       // 未变化: 无需副本
    out[w] = L'\0';
    return w;
}

// ---------- DumpText（未命中收集, SRWLOCK 保护） ----------
struct DumpNode { uint32_t crc; DumpNode* next; };

static DumpNode** g_dumpBuckets = nullptr;
static SRWLOCK    g_dumpLock    = SRWLOCK_INIT;
static FILE*      g_dumpFile    = nullptr;
static uint32_t   g_dumpCount   = 0;

static bool DumpWorthy(const wchar_t* s, size_t len)
{
    if (len < 2 || len > DUMP_MAX_CHARS) return false;
    for (size_t i = 0; i < len; ++i)
    {
        wchar_t c = s[i];
        if (c >= 0x2E80) return false;
        if (c < 0x20 && c != L'\n' && c != L'\t') return false;
    }
    return true;
}

static void DumpText(const wchar_t* s, size_t len)
{
    if (!g_cfg.dumpEnabled) return;
    uint32_t crc = CrcText(s, len);
    AcquireSRWLockExclusive(&g_dumpLock);
    if (g_dumpBuckets && g_dumpFile)
    {
        bool seen = false;
        for (DumpNode* n = g_dumpBuckets[crc & (DUMP_BUCKETS - 1)]; n; n = n->next)
            if (n->crc == crc) { seen = true; break; }
        if (!seen)
        {
            auto* node = static_cast<DumpNode*>(ArenaAlloc(sizeof(DumpNode)));
            if (node)
            {
                node->crc  = crc;
                uint32_t h = crc & (DUMP_BUCKETS - 1);
                node->next = g_dumpBuckets[h];
                g_dumpBuckets[h] = node;
                ++g_dumpCount;

                char utf8[DUMP_MAX_CHARS * 3 + 8];
                int u = WideCharToMultiByte(CP_UTF8, 0, s, (int)len,
                                            utf8 + 1, sizeof(utf8) - 3, nullptr, nullptr);
                if (u > 0)
                {
                    utf8[0] = '"';
                    int w = u + 1;
                    utf8[w++] = '"';
                    utf8[w++] = '\n';
                    // 只写不刷: 逐条 fflush 同步磁盘 I/O 会卡渲染线程（切界面时新 miss 集中涌入）,
                    // 改由 StatsThread 周期 fflush + 进程退出 fclose 落盘（崩溃最多丢 STATS_PERIOD_MS 内的收集）
                    fwrite(utf8, 1, w, g_dumpFile);
                }
            }
        }
    }
    ReleaseSRWLockExclusive(&g_dumpLock);
}

// ---------- Hook 共用: 查词典 + miss 统计/dump ----------
static volatile LONG g_hitA = 0, g_missA = 0, g_hitB = 0, g_missB = 0;
static volatile LONG g_wrapHits = 0;   // v7.3: 折行重组命中次数

static const DictNode* LookupNode(const wchar_t* s, volatile LONG* hit, volatile LONG* miss)
{
    if (!s || !*s) return nullptr;
    size_t len = wcslen(s);
    if (len > DUMP_MAX_CHARS * 4) return nullptr;

    const DictNode* r = DictLookup(s, len);
    if (r) { InterlockedIncrement(hit); return r; }

    size_t b, tl;
    if (TrimRange(s, len, &b, &tl))
    {
        if (tl <= 512)
        {
            wchar_t tmp[513];
            wmemcpy(tmp, s + b, tl);
            tmp[tl] = L'\0';
            r = DictLookup(tmp, tl);
            if (r) { InterlockedIncrement(hit); return r; }
        }
    }

    // 空格规范化重查（引擎把连续空格压成单空格后到来, 词典原键含双空格）
    if (len <= 512)
    {
        wchar_t norm[513];
        size_t nl = NormalizeKey(s, len, norm, 512);
        if (nl)
        {
            r = DictLookup(norm, nl);
            if (r) { InterlockedIncrement(hit); return r; }
        }
    }

    InterlockedIncrement(miss);
    DumpText(s, len);
    return nullptr;
}

// =====================================================================
// v7.3 字幕折行重组（引擎 word-wrap 后逐行查词典 → 长句 key miss）
// =====================================================================
// 引擎折行行为（DumpText.dtxt 569/570 实测 + word-wrap 常规规则）:
//   段1(前缀行): 完整句前缀, 折行点前的空格被吞; 段2(后缀行): 头部空格被吞。
//   两行同帧先后 format, 之后每帧重复, 直到字幕换句。
//   期间穿插其他 HUD 文本 format（计数器等常量文本）—— 不得据此判定换句。
// 状态机（仅渲染线程调用, 无锁）:
//   IDLE  : 学习疑似段1（词典 miss 且以词中字符结尾）
//   LEARN : 段1 已缓存, 等段2 拼接验证（补空格/不补两种变体查词典）; 同文本重画刷新 TTL
//   STABLE: 拼接命中后进入。前缀行回译文前半, 后缀行回译文后半（切点按英文折行比例预计算）。
//           非匹配的长词中文本只作"暂定段1"暂存 —— 仅当它与后续文本拼接命中词典
//           才替换当前稳定态（防 HUD 常量噪音破坏切分）。
//
// WRAP_TTL_MS: LEARN 段1 过期时间; STABLE 无 TTL（靠精确匹配, 新句拼接命中自动接管）。

enum WrapMode { WRAP_IDLE = 0, WRAP_LEARN = 1, WRAP_STABLE = 2 };

struct WrapState
{
    int      mode;                          // WrapMode
    wchar_t  pend[WRAP_MAX_CHARS];          // LEARN=段1; STABLE=暂定新段1（pendLen=0 表示无）
    size_t   pendLen;
    uint64_t pendTick;                      // LEARN 段1 最近重画时刻（TTL 用）
    wchar_t  full[WRAP_MAX_CHARS];          // STABLE: 完整英文原文
    size_t   fullLen;
    size_t   preLen;                        // STABLE: 前缀行长度（不含被吞的空格）
    size_t   sufAt;                         // STABLE: 后缀行在完整句中的起点
    size_t   cut;                           // STABLE: 译文切点
    const DictNode* node;                   // STABLE: 命中的词典节点
};

static WrapState g_wrap;

// v7.4.2 wrap 状态诊断: 关键迁移频控日志 + 计数器（实证"段2 是否到达/被顶/TTL 过期/拼接失败"）
enum { WD_LEARN = 0, WD_TTLEXP, WD_CONCAT, WD_REPLACE, WD_STABLE, WD_TOKEOVER, WD_N };
static volatile LONG g_wrapCnt[WD_N];           // 事件计数
static uint64_t      g_wrapDiagTick[WD_N];      // 频控时间戳
static void WrapDiag(int ev, uint64_t now, const wchar_t* a, const wchar_t* b)
{
    InterlockedIncrement(&g_wrapCnt[ev]);
    if (now - g_wrapDiagTick[ev] < 3000) return;   // 每事件类型 3s 最多 1 条
    g_wrapDiagTick[ev] = now;
    switch (ev)
    {
    case WD_LEARN:    Log("wrap: LEARN  pend=\"%.60ls\"", a); break;
    case WD_TTLEXP:   Log("wrap: TTL-EXPIRED  pend=\"%.60ls\"", a); break;
    case WD_CONCAT:   Log("wrap: CONCAT-MISS  pre=\"%.44ls\" | suf=\"%.24ls\"", a, b); break;
    case WD_REPLACE:  Log("wrap: REPLACE-pend (新 prefix 顶掉旧段1)  old=\"%.36ls\" new=\"%.36ls\"", a, b); break;
    case WD_STABLE:   Log("wrap: STABLE full=\"%.48ls\" -> \"%.32ls\"", a, b); break;
    case WD_TOKEOVER: Log("wrap: STABLE takeover -> \"%.48ls\"", a); break;
    }
}

// STABLE 两行译文（命中时预计算好, 渲染线程直接返回指针）
static wchar_t g_half1[WRAP_MAX_CHARS + 1];
static wchar_t g_half2[WRAP_MAX_CHARS + 1];

static uint64_t NowTick()
{
    return GetTickCount64();
}

// 段1 候选: 够长 + 尾字符是词中字符（引擎折行断在词边界, 段1 不会以句读收尾;
// 数字结尾多为 HUD 计数器, 排除）
static bool WrapLooksLikePrefix(const wchar_t* s, size_t len)
{
    if (len < WRAP_MIN_PREFIX || len >= WRAP_MAX_CHARS) return false;
    wchar_t last = s[len - 1];
    return (last >= L'a' && last <= L'z') || (last >= L'A' && last <= L'Z') ||
           last == L'-' || last == 0x2019 || last == 0x201D ||   // - ' ”
           last == L'"' || last == L',' || last == L'*';
}

static bool WrapLooksLikeSuffix(size_t len)
{
    return len >= WRAP_MIN_SUFFIX && len < WRAP_MAX_CHARS;
}

// 拼接 pend + 段2 查词典。命中返回节点, 并把完整句写入 out（含 NUL）,
// outLen=完整句长度, outSufAt=后缀行在完整句中的起点。
// 变体1: pend + ' ' + seg（词间折行, 空格被吞）; 变体2: pend + seg（连字符断词/引擎去空格）;
// 变体3: 拼接结果空格规范化后重查（词典原键含双空格）。
// 命中时 out 内容为查表所用文本（与词典键对齐）, sufAt 按该文本计算。
static const DictNode* WrapTryConcat(const wchar_t* pend, size_t plen,
                                     const wchar_t* s, size_t len,
                                     wchar_t* out, size_t* outLen, size_t* outSufAt)
{
    if (plen + len + 1 >= WRAP_MAX_CHARS) return nullptr;
    wmemcpy(out, pend, plen);
    out[plen] = L' ';
    wmemcpy(out + plen + 1, s, len);
    out[plen + 1 + len] = L'\0';
    const DictNode* r = DictLookup(out, plen + 1 + len);
    if (r) { *outLen = plen + 1 + len; *outSufAt = plen + 1; return r; }

    wmemcpy(out + plen, s, len);
    out[plen + len] = L'\0';
    r = DictLookup(out, plen + len);
    if (r) { *outLen = plen + len; *outSufAt = plen; return r; }

    // 变体3: 规范化重查（覆盖词典键含连续空格的情形）
    {
        wchar_t norm[WRAP_MAX_CHARS];
        size_t total = plen + 1 + len;
        size_t nl = NormalizeKey(out, total, norm, WRAP_MAX_CHARS - 1);
        if (nl)
        {
            r = DictLookup(norm, nl);
            if (r)
            {
                wmemcpy(out, norm, nl + 1);          // 完整句以规范化形态入稳定态
                *outLen = nl;
                // 后缀行起点 = pend 长度（规范化只影响 pend 与段2 间空隙, 不变 pend 本身）
                // 精确算: 找 norm 中段2 的起始位置（从尾部回扫 len 字符）
                *outSufAt = (nl >= len) ? nl - len : 0;
                return r;
            }
        }
    }
    return nullptr;
}

// 切点评分: 越大越好。标点后 > CJK 边界 > 其他; 保证 [2, len-2] 界内
// （首尾各留 >=2 字符, 避免半截词/孤立标点行）; 同分取离中点最近的切点
// （译文两行长度更均衡, 避免把大半译文切给第一行）
static size_t PickSplitIndex(const wchar_t* t, size_t n)
{
    if (n < 6) return n / 2;
    size_t best = n / 2;
    size_t mid  = n / 2;
    int    bestSc = -1;
    for (size_t i = 2; i < n - 2; ++i)
    {
        // 优先标点后切: ,。!?;:、· （句读自然断点）
        int sc = (t[i - 1] >= 0x80 && t[i] >= 0x80) ? 1 : 0;
        if (wcschr(L",。!?;:、·", t[i - 1])) sc = 2;
        size_t dI    = (i > mid) ? i - mid : mid - i;
        size_t dBest = (best > mid) ? best - mid : mid - best;
        if (sc > bestSc || (sc == bestSc && dI < dBest)) { bestSc = sc; best = i; }
    }
    return best;
}

// 折行重组主流程。HookFormat 的 fmt 每次进来都过这里（仅渲染线程, 无锁）。
// 返回: nullptr = 按原文本走; 非 null = 替换文本指针（稳定态切分译文）
static const wchar_t* WrapProcess(const wchar_t* s, size_t len)
{
    uint64_t now = NowTick();

    // 暂定段1 过期清理（STABLE/LEARN 共用; 防陈旧 pend 与后续无关文本误拼接）
    if (g_wrap.pendLen && now - g_wrap.pendTick > WRAP_TTL_MS)
    {
        WrapDiag(WD_TTLEXP, now, g_wrap.pend, nullptr);
        g_wrap.pendLen = 0;
        if (g_wrap.mode == WRAP_LEARN) g_wrap.mode = WRAP_IDLE;
    }

    // ---------- STABLE: 持续切分, 直到新句拼接命中接管 ----------
    if (g_wrap.mode == WRAP_STABLE && g_wrap.node)
    {
        bool isPrefix = (len == g_wrap.preLen) &&
                        wmemcmp(s, g_wrap.full, g_wrap.preLen) == 0;
        bool isSuffix = (len == g_wrap.fullLen - g_wrap.sufAt) &&
                        wmemcmp(s, g_wrap.full + g_wrap.sufAt, len) == 0;
        if (isPrefix) return g_half1;
        if (isSuffix) return g_half2;

        // 新句的段1: 疑似前缀 -> 只暂存, 不破坏当前稳定态（防 HUD 常量噪音）。
        if (!g_wrap.pendLen && WrapLooksLikePrefix(s, len))
        {
            wmemcpy(g_wrap.pend, s, len);
            g_wrap.pend[len] = L'\0';
            g_wrap.pendLen   = len;
            g_wrap.pendTick  = now;
            return nullptr;
        }

        // 暂定段1 已武装: 当前文本疑似新句段2 -> 拼接重查, 命中则新句接管稳定态
        // （译文长度上界 guard: g_half1/g_half2 定长 256, 超长译文无法安全切分）
        if (g_wrap.pendLen && WrapLooksLikeSuffix(len))
        {
            wchar_t cand[WRAP_MAX_CHARS + 1];
            size_t  fullLen = 0, sufAt = 0;
            const DictNode* r = WrapTryConcat(g_wrap.pend, g_wrap.pendLen, s, len,
                                              cand, &fullLen, &sufAt);
            if (r && r->len >= 4 && r->len < WRAP_MAX_CHARS)
            {
                wmemcpy(g_wrap.full, cand, fullLen + 1);
                g_wrap.fullLen = fullLen;
                g_wrap.preLen  = g_wrap.pendLen;
                g_wrap.sufAt   = sufAt;
                g_wrap.node    = r;
                g_wrap.pendLen = 0;

                size_t tlen = r->len;
                size_t est  = (tlen * g_wrap.preLen + fullLen / 2) / fullLen; // 英文比例->译文字符
                if (est < 2) est = 2;
                if (est > tlen - 2) est = tlen - 2;
                size_t cut = PickSplitIndex(r->trans, tlen);
                if (cut > est + tlen / 4 || est > cut + tlen / 4)
                    cut = est;   // 标点离比例点太远就按比例硬切
                if (cut < 1) cut = 1;
                if (cut > tlen - 1) cut = tlen - 1;   // 两行至少各 1 字符
                g_wrap.cut = cut;

                wmemcpy(g_half1, r->trans, cut);
                g_half1[cut] = L'\0';
                wmemcpy(g_half2, r->trans + cut, tlen - cut);
                g_half2[tlen - cut] = L'\0';
                InterlockedIncrement(&g_wrapHits);
                WrapDiag(WD_TOKEOVER, now, cand, nullptr);

                // 静默吸收: 本帧段2不显示（前缀行本帧已显示英文, 下一帧起换译文前半）
                return nullptr;
            }
        }
        return nullptr;   // HUD 噪音等无关文本: 维持稳定态
    }

    // ---------- LEARN: 段1 已缓存, 等段2 拼接验证 ----------
    if (g_wrap.mode == WRAP_LEARN && g_wrap.pendLen)
    {
        // 同文本每帧重画: 刷新 TTL 保持缓存
        if (len == g_wrap.pendLen && wmemcmp(s, g_wrap.pend, len) == 0)
        {
            g_wrap.pendTick = now;
            return nullptr;
        }

        // 疑似段2: 补空格/直接连两种变体查词典
        // （译文长度上界 guard: g_half1/g_half2 定长 256, 超长译文无法安全切分）
        if (WrapLooksLikeSuffix(len))
        {
            wchar_t cand[WRAP_MAX_CHARS + 1];
            size_t  fullLen = 0, sufAt = 0;
            const DictNode* r = WrapTryConcat(g_wrap.pend, g_wrap.pendLen, s, len,
                                              cand, &fullLen, &sufAt);
            if (r && r->len >= 4 && r->len < WRAP_MAX_CHARS)
            {
                // 命中 -> 进入稳定态: 存完整原文/折行位置, 预计算两行译文
                wmemcpy(g_wrap.full, cand, fullLen + 1);
                g_wrap.fullLen = fullLen;
                g_wrap.preLen  = g_wrap.pendLen;
                g_wrap.sufAt   = sufAt;
                g_wrap.node    = r;
                g_wrap.mode    = WRAP_STABLE;
                g_wrap.pendLen = 0;

                size_t tlen = r->len;
                size_t est  = (tlen * g_wrap.preLen + fullLen / 2) / fullLen; // 英文比例->译文字符
                if (est < 2) est = 2;
                if (est > tlen - 2) est = tlen - 2;
                size_t cut = PickSplitIndex(r->trans, tlen);
                if (cut > est + tlen / 4 || est > cut + tlen / 4)
                    cut = est;   // 标点离比例点太远就按比例硬切
                if (cut < 1) cut = 1;
                if (cut > tlen - 1) cut = tlen - 1;   // 两行至少各 1 字符
                g_wrap.cut = cut;

                wmemcpy(g_half1, r->trans, cut);
                g_half1[cut] = L'\0';
                wmemcpy(g_half2, r->trans + cut, tlen - cut);
                g_half2[tlen - cut] = L'\0';
                InterlockedIncrement(&g_wrapHits);
                WrapDiag(WD_STABLE, now, cand, r->trans);

                // 静默吸收: 本帧段2不显示（前缀行本帧已显示英文, 下一帧起换译文前半）
                return nullptr;
            }
            // v7.4.2: 拼接尝试但未命中 —— 记录段1/段2 形态, 实证断行点/空格差异
            WrapDiag(WD_CONCAT, now, g_wrap.pend, s);
        }

        // 另一疑似段1（换句/别的 HUD 文本也以词中字符结尾）: 滚动替换缓存。
        // 不直接重置状态机 —— 每帧穿插的 HUD 常量文本若每帧都触发重置,
        // 后缀段将永远无法与段1 配对（IDLE->LEARN->IDLE 抖动, 永远拼不上）。
        if (WrapLooksLikePrefix(s, len))
        {
            WrapDiag(WD_REPLACE, now, g_wrap.pend, s);
            wmemcpy(g_wrap.pend, s, len);
            g_wrap.pend[len] = L'\0';
            g_wrap.pendLen   = len;
            g_wrap.pendTick  = now;
        }
        // 其他短文本/噪音: 不动状态机（保持 LEARN, 靠 TTL 自然过期）
        return nullptr;
    }

    // ---------- IDLE: 学习疑似段1（够长 + 尾字符是词中字符） ----------
    if (g_wrap.mode == WRAP_IDLE && WrapLooksLikePrefix(s, len))
    {
        WrapDiag(WD_LEARN, now, s, nullptr);
        wmemcpy(g_wrap.pend, s, len);
        g_wrap.pend[len] = L'\0';
        g_wrap.pendLen   = len;
        g_wrap.pendTick  = now;
        g_wrap.mode      = WRAP_LEARN;
    }
    return nullptr;
}

// =====================================================================
// v6 字形层
// =====================================================================

// 伪字体（每 fontId 一个, 两阶段构建）
//   state: 0=idle 1=后台光栅化中 2=live 3=光栅化完待D3D 4=失败
struct FakeFont
{
    volatile LONG   state;
    uint32_t        fontId;
    void*           official;   // 构建时的官方对象（fontTab[slot] 校验用）
    void*           obj;        // 伪字体对象 blob
    ID3D11ShaderResourceView* srv;
    ID3D11Texture2D*           tex;
    // 光栅化产物（后台线程填, D3D 阶段消费后释放）
    uint8_t*  cellBuf;          // nCells * cellW * cellH 灰度
    int32_t*  advances;         // nCells
    uint32_t* cps;              // nCells
    uint32_t  nCells;
    uint32_t  blankY;           // 预留空白 cell 的图集 Y（缺字槽位指到这里, 超容量字符空白渲染）
    uint16_t  cellW, cellH;     // cellH=官方行高(不可变); cellW=光栅化宽度(容量不足时收窄)
    // 伪纹理对象（sub_14085D930 读 +8/+10 宽高; +20 变体数; +34 速度）
    uint8_t   fakeTexObj[64];
};
static FakeFont g_fake[FAKE_FONT_MAX];

// stb 字体状态
static uint8_t       g_ttfData[1];  // 占位（实际 VirtualAlloc 到 g_ttfBuf）
static uint8_t*      g_ttfBuf = nullptr;
static stbtt_fontinfo g_stb;
static volatile LONG g_stbReady = 0;

// 引擎指针（安装时解析）
static void***        g_fontTabPtr   = nullptr;  // -> qword_142998168
static volatile int*  g_fontCountPtr = nullptr;  // -> dword_142998160
static ID3D11Device**        g_devSlot  = nullptr;
static ID3D11DeviceContext** g_ctxSlot  = nullptr;

// 原函数
using FontLookup_t = void* (__fastcall*)(int);
using TexObj_t     = void* (__fastcall*)(int);
using SrvResolve_t = void* (__fastcall*)(unsigned int, char);
static FontLookup_t g_origFontLookup = nullptr;
static TexObj_t     g_origTexObj     = nullptr;
static SrvResolve_t g_origSrvResolve = nullptr;

// 官方对象 -> fontTab 槽位号（找不到返回 0xFFFFFFFF）
static uint32_t ResolveSlot(void* off);
static bool     FinishFont(FakeFont* f);
static void     RequestFont(uint32_t slot);

// ---------- Hook C: 字体对象查询（两渲染器公共必经点） ----------
// 触发升级 + 伪对象替换
// ptr 缓存（官方对象 -> FakeFont）, 高频路径避免线性扫 fontTab
struct PtrCache { void* off; FakeFont* f; };
static PtrCache     g_ptrCache[16];
static volatile LONG g_ptrCacheN = 0;

static void EnsureFontReady(void* off)
{
    LONG n = g_ptrCacheN; if (n > 16) n = 16;
    for (LONG i = 0; i < n; ++i)
    {
        if (g_ptrCache[i].off != off) continue;
        return;   // D3D 阶段已移至后台线程, 渲染线程只读 state 不推进（防卡顿）
    }
    uint32_t slot = ResolveSlot(off);
    if (slot >= FAKE_FONT_MAX) return;
    FakeFont* f = &g_fake[slot];
    LONG idx = InterlockedIncrement(&g_ptrCacheN) - 1;
    if (idx < 16) { g_ptrCache[idx].off = off; g_ptrCache[idx].f = f; }
    if (f->state == 0) RequestFont(slot);
}

static void* __fastcall HookFontLookup(int fontId)
{
    void* off = g_origFontLookup(fontId);
    if (!off) return off;

    // 触发/推进升级（菜单文本走 sub_14016E7D0 渲染器, 不经 DrawWide,
    // 故在此公共必经点触发; 英文渲染不受影响, 官方 cell 照抄）
    if (g_stbReady && g_dictReady) EnsureFontReady(off);

    // 已 live 的伪对象替换（校验官方对象仍在槽位）
    uint32_t slot = (fontId >= 0 && (uint32_t)fontId < FAKE_FONT_MAX)
                    ? (uint32_t)fontId : ResolveSlot(off);
    if (slot < FAKE_FONT_MAX)
    {
        FakeFont* f = &g_fake[slot];
        if (f->state == 2)
        {
            void* cur = (*g_fontTabPtr)[slot];
            if (cur == f->official) return f->obj;
            f->state = 4;   // 引擎重建了字体, 回退官方
        }
    }
    return off;
}

// ---------- Hook D: 纹理对象查询 ----------
static void* __fastcall HookTexObj(int texId)
{
    uint32_t t = (uint32_t)texId;
    if (t >= MAGIC_TEXID_BASE && t < MAGIC_TEXID_BASE + FAKE_FONT_MAX)
    {
        FakeFont* f = &g_fake[t - MAGIC_TEXID_BASE];
        if (f->state == 2) return f->fakeTexObj;
    }
    return g_origTexObj(texId);
}

// ---------- Hook E: texId -> SRV ----------
static void* __fastcall HookSrvResolve(unsigned int texId, char useStream)
{
    if (texId >= MAGIC_TEXID_BASE && texId < MAGIC_TEXID_BASE + FAKE_FONT_MAX)
    {
        FakeFont* f = &g_fake[texId - MAGIC_TEXID_BASE];
        if (f->state == 2 && f->srv) return f->srv;
        return nullptr;
    }
    return g_origSrvResolve(texId, useStream);
}

// ---------- 后台阶段: 光栅化字符集 ----------
static DWORD WINAPI RasterizeThread(LPVOID arg)
{
    FakeFont* f = static_cast<FakeFont*>(arg);
    uint32_t fontId = f->fontId;

    void* off = g_origFontLookup((int)fontId);
    if (!off) { Log("font%u: official object null, abort", fontId); f->state = 4; return 0; }

    // 读官方头
    int      offCount  = *(int*)( (uint8_t*)off + 8);
    int      offBase   = *(int*)( (uint8_t*)off + 12);
    uint16_t cellH     = *(uint16_t*)((uint8_t*)off + 22);
    if (offCount <= 0 || offCount >= 0x10000 || cellH < 8 || cellH > 256)
    {
        Log("font%u: bad header count=%d cellH=%u, abort", fontId, offCount, cellH);
        f->state = 4; return 0;
    }

    uint16_t cellW = cellH;  // 默认 1:1（字形满尺寸）
    // v7.5.1: 容量自适应收窄（仅当默认容量装不下当前字符集时）
    //   背景: charlist 合并后字符集约 2996, font1(220px) 1:1 容量 37*64=2368 装不下,
    //   会截断低频字致缺字(齿 rank 2920 即被截). 字形 quad 高度锁 font+22(对象级,
    //   中英共享不可动), 但 UV 宽度=metrics[+4] 是每槽位独立的 -> 中文 cell 可只收窄宽度.
    //   方案: 等比缩小 -- 字形宽高都缩到 cellW/cellH 比例(不变形), 底部坐官方基线,
    //   官方英文区照抄不受影响. 源字体 CJK 是全宽字形(思源 1000/1000 em),
    //   横向压扁会变形, 必须等比.
    //   迭代收缩: 1:1 -> 装不下 -> 宽度减 8 再算, 直到装下或宽到 32 下限(极小字才可能发生)
    uint32_t nGuess = g_charCount;
    {
        // 官方图集真实尺寸: 引擎纹理对象 +8/+10 (u16 W/H; 不依赖 D3D 就绪)
        uint32_t capW = 8192, offHGuess = 2048;
        uint32_t offTexId = *(uint32_t*)((uint8_t*)off + 184);
        void* texObj = (offTexId != 0xFFFFFFFFu) ? g_origTexObj((int)offTexId) : nullptr;
        if (texObj)
        {
            uint32_t ow = *(uint16_t*)((uint8_t*)texObj + 8);
            uint32_t oh = *(uint16_t*)((uint8_t*)texObj + 10);
            if (ow >= 64 && ow <= 16384 && oh >= 64 && oh <= 16384)
            {
                if (ow > capW) capW = ow;          // 官方图集比 8192 还宽(罕见): 按原宽算
                offHGuess = oh;                    // 官方区真实高度(容量高度预算)
            }
        }
        while (nGuess > 1)
        {
            uint32_t per  = capW / cellW;                    // 图集每行列数
            uint32_t maxR = (16384 - offHGuess) / cellH - 1; // 行数上限(16384 高预算, 末行恒留空白)
            if (per >= 1 && (uint64_t)per * maxR >= nGuess) break;
            if (cellW <= 32 + 8) break;                       // 收窄下限(32px 以下无意义)
            cellW = (uint16_t)(cellW - 8);
        }
    }
    f->cellW = cellW; f->cellH = cellH;
    f->official = off;

    // 收集字符集 -> 列表（按词典使用频率降序: 高频字优先入图集, 低频字容量不足时被截断）
    uint32_t total = g_charCount;
    f->cps      = static_cast<uint32_t*>(malloc(sizeof(uint32_t) * (total ? total : 1)));
    f->advances = static_cast<int32_t*>(malloc(sizeof(int32_t) * (total ? total : 1)));
    f->cellBuf  = static_cast<uint8_t*>(malloc((size_t)cellW * cellH * (total ? total : 1)));
    if (!f->cps || !f->advances || !f->cellBuf)
    {
        Log("font%u: raster alloc failed", fontId);
        f->state = 4; return 0;
    }
    memset(f->cellBuf, 0, (size_t)cellW * cellH * total);

    uint32_t n = 0;
    for (uint32_t cp = 0x80; cp <= 0xFFFD && n < total; ++cp)
    {
        if (!(g_charSet[cp >> 3] & (1u << (cp & 7)))) continue;
        f->cps[n++] = cp;
    }
    // 插入排序按频率降序（数组初始升序, 近乎有序时接近 O(n)）
    for (uint32_t i = 1; i < n; ++i)
    {
        uint32_t kc = f->cps[i];
        uint32_t kf = g_charFreq[kc];
        uint32_t j = i;
        while (j > 0 && g_charFreq[f->cps[j - 1]] < kf)
        {
            f->cps[j] = f->cps[j - 1];
            --j;
        }
        f->cps[j] = kc;
    }

    // 光栅化（v7.5.1: 等比缩小方案）
    //   cellW 被收窄时(容量不足), 字形按 cellW 等比缩小(高=宽, 不变形),
    //   底部坐官方基线(baseline 按满比例 cellH 计算, 与英文基线一致),
    //   字形大小 ~cellW/cellH 比例(79%~100%), 略小于英文但排版和谐.
    float fullScale = stbtt_ScaleForPixelHeight(&g_stb, (float)cellH);   // 官方满比例(基线用)
    float scale     = stbtt_ScaleForPixelHeight(&g_stb, (float)cellW);   // 等比缩小(光栅化用, cellW<=cellH)
    int ascent, descent, gap;
    stbtt_GetFontVMetrics(&g_stb, &ascent, &descent, &gap);
    int baseline = (int)((float)ascent * fullScale + 0.5f);   // 基线=官方满比例 ascent
    if (baseline > cellH - 1) baseline = cellH - 1;
    if (baseline < 1) baseline = 1;

    uint32_t missGlyph = 0;
    for (uint32_t idx = 0; idx < n; ++idx)
    {
        uint32_t cp = f->cps[idx];
        uint8_t* cell = f->cellBuf + (size_t)idx * cellW * cellH;

        int g = stbtt_FindGlyphIndex(&g_stb, (int)cp);
        if (g == 0)
        {
            ++missGlyph;
            f->advances[idx] = cellW;   // 无字形: 空白格
            continue;
        }

        int x0, y0, x1, y1;
        stbtt_GetGlyphBitmapBox(&g_stb, g, scale, scale, &x0, &y0, &x1, &y1);
        int w = x1 - x0, h = y1 - y0;
        int ox = ((int)cellW - w) / 2;
        int oy = baseline + y0;

        int adv;
        stbtt_GetGlyphHMetrics(&g_stb, g, &adv, nullptr);
        // 步进按缩小后比例（等比: 字宽与字形一致; 槽位宽度 met+4 填 cellW）
        f->advances[idx] = adv > 0 ? (int)((float)adv * scale + 0.5f) : cellW;
        if (f->advances[idx] <= 0) f->advances[idx] = cellW / 2;

        // 等比缩小后位图自然 <= cellW, 直接 blit（无需重采样）
        if (w > 0 && h > 0)
        {
            // 光栅化到临时再拷入 cell（裁剪到 cell 内）
            uint8_t* tmp = static_cast<uint8_t*>(malloc((size_t)w * h));
            if (tmp)
            {
                stbtt_MakeGlyphBitmap(&g_stb, tmp, w, h, w, scale, scale, g);
                for (int row = 0; row < h; ++row)
                {
                    int dy = oy + row;
                    if (dy < 0 || dy >= (int)cellH) continue;
                    for (int col = 0; col < w; ++col)
                    {
                        int dx = ox + col;
                        if (dx < 0 || dx >= (int)cellW) continue;
                        cell[dy * cellW + dx] = tmp[row * w + col];
                    }
                }
                free(tmp);
            }
        }
    }
    f->nCells = n;

    if (offBase != (int)FONT_BASECHAR_DEFAULT)
        Log("font%u: warn official baseChar=%d != 0x20", fontId, offBase);
    Log("font%u: rasterized %u cells (cellW=%u cellH=%u glyphScale=%.2f baseline=%d, missGlyph=%u, official count=%d)",
        fontId, n, cellW, cellH, (double)(scale / fullScale), baseline, missGlyph, offCount);

    InterlockedExchange(&f->state, 3);  // 待 D3D 阶段

    // D3D 阶段也在本后台线程完成（FinishFont 内 CAS 3->5 防并发）:
    // 原设计在渲染线程做, 官方图集读回+大纹理上传会卡主界面首帧; 后台做完后伪对象才 live,
    // 未就绪期间引擎用官方字体渲染中文 -> 槽码越界被 sub_140858C10 边界检查挡住 -> 安全空白回退
    // 暂时性失败（D3D/官方SRV未就绪, state 回 3）: 后台重试至多 30s
    for (int retry = 0; retry < 300; ++retry)
    {
        if (FinishFont(f)) return 0;
        if (f->state != 3) return 0;   // 永久失败(4)/已被别的线程完成, 不再重试
        Sleep(100);
    }
    Log("font%u: D3D stage gave up after 30s (state=%ld)", fontId, f->state);
    return 0;
}

// ---------- DXGI 格式辅助（v6.4: 官方图集读回格式感知） ----------
// 返回每像素字节数; 压缩/未知格式返回 0
static uint32_t BppOf(DXGI_FORMAT fmt)
{
    switch (fmt)
    {
    case DXGI_FORMAT_B8G8R8A8_UNORM:
    case DXGI_FORMAT_B8G8R8X8_UNORM:
    case DXGI_FORMAT_R8G8B8A8_UNORM:
        return 4;
    case DXGI_FORMAT_B5G6R5_UNORM:
    case DXGI_FORMAT_B5G5R5A1_UNORM:
    case DXGI_FORMAT_B4G4R4A4_UNORM:
        return 2;
    case DXGI_FORMAT_R8_UNORM:
    case DXGI_FORMAT_A8_UNORM:
        return 1;
    default:
        return 0;
    }
}

static bool IsBcFormat(DXGI_FORMAT fmt)
{
    switch (fmt)
    {
    case DXGI_FORMAT_BC1_UNORM:
    case DXGI_FORMAT_BC1_TYPELESS:
    case DXGI_FORMAT_BC1_UNORM_SRGB:
    case DXGI_FORMAT_BC2_UNORM:
    case DXGI_FORMAT_BC2_TYPELESS:
    case DXGI_FORMAT_BC2_UNORM_SRGB:
    case DXGI_FORMAT_BC3_UNORM:
    case DXGI_FORMAT_BC3_TYPELESS:
    case DXGI_FORMAT_BC3_UNORM_SRGB:
    case DXGI_FORMAT_BC4_UNORM:
    case DXGI_FORMAT_BC4_TYPELESS:
    case DXGI_FORMAT_BC4_SNORM:
        return true;
    default:
        return false;
    }
}

static const char* FmtName(DXGI_FORMAT fmt)
{
    switch (fmt)
    {
    case DXGI_FORMAT_B8G8R8A8_UNORM: return "BGRA8";
    case DXGI_FORMAT_B8G8R8X8_UNORM: return "BGRX8";
    case DXGI_FORMAT_R8G8B8A8_UNORM: return "RGBA8";
    case DXGI_FORMAT_B5G6R5_UNORM:   return "B5G6R5";
    case DXGI_FORMAT_B5G5R5A1_UNORM: return "B5G5R5A1";
    case DXGI_FORMAT_B4G4R4A4_UNORM: return "B4G4R4A4";
    case DXGI_FORMAT_R8_UNORM:       return "R8";
    case DXGI_FORMAT_A8_UNORM:       return "A8";
    case DXGI_FORMAT_BC1_UNORM:
    case DXGI_FORMAT_BC1_TYPELESS:
    case DXGI_FORMAT_BC1_UNORM_SRGB: return "BC1";
    case DXGI_FORMAT_BC2_UNORM:
    case DXGI_FORMAT_BC2_TYPELESS:
    case DXGI_FORMAT_BC2_UNORM_SRGB: return "BC2";
    case DXGI_FORMAT_BC3_UNORM:
    case DXGI_FORMAT_BC3_TYPELESS:
    case DXGI_FORMAT_BC3_UNORM_SRGB: return "BC3";
    case DXGI_FORMAT_BC4_UNORM:
    case DXGI_FORMAT_BC4_TYPELESS:
    case DXGI_FORMAT_BC4_SNORM:      return "BC4";
    default:                          return "?";
    }
}

// BC 块字节数
static uint32_t BcBlockBytes(DXGI_FORMAT fmt)
{
    // BC1/BC4: 8B/块; BC2/BC3: 16B/块
    return (fmt == DXGI_FORMAT_BC1_UNORM || fmt == DXGI_FORMAT_BC1_TYPELESS ||
            fmt == DXGI_FORMAT_BC1_UNORM_SRGB ||
            fmt == DXGI_FORMAT_BC4_UNORM || fmt == DXGI_FORMAT_BC4_TYPELESS ||
            fmt == DXGI_FORMAT_BC4_SNORM)
               ? 8u : 16u;
}

static inline uint32_t Rgb565(uint16_t v, uint8_t* r, uint8_t* g, uint8_t* b)
{
    *r = (uint8_t)(((v >> 11) & 0x1F) * 255 / 31);
    *g = (uint8_t)(((v >> 5)  & 0x3F) * 255 / 63);
    *b = (uint8_t)(( v        & 0x1F) * 255 / 31);
    return 0;
}

// BC1(DXT1) 单块解码 -> 4x4 BGRA
static void DecodeBc1(const uint8_t* blk, uint32_t* out16)
{
    uint16_t c0 = blk[0] | (blk[1] << 8);
    uint16_t c1 = blk[2] | (blk[3] << 8);
    uint8_t r0, g0, b0, r1, g1, b1;
    Rgb565(c0, &r0, &g0, &b0);
    Rgb565(c1, &r1, &g1, &b1);
    uint32_t pal[4];
    pal[0] = 0xFF000000u | (b0 << 16) | (g0 << 8) | r0;
    pal[1] = 0xFF000000u | (b1 << 16) | (g1 << 8) | r1;
    if (c0 > c1)
    {
        pal[2] = 0xFF000000u | ((((b0 + b0 + b1) / 3) & 0xFF) << 16)
                           | ((((g0 + g0 + g1) / 3) & 0xFF) << 8)
                           | (((r0 + r0 + r1) / 3) & 0xFF);
        pal[3] = 0xFF000000u | (((b0 + b1 + b1) / 3) << 16)
                           | (((g0 + g1 + g1) / 3) << 8)
                           | (((r0 + r1 + r1) / 3) & 0xFF);
    }
    else
    {
        pal[2] = 0xFF000000u | (((b0 + b1) / 2) << 16) | (((g0 + g1) / 2) << 8) | ((r0 + r1) / 2);
        pal[3] = 0x00000000u;   // 透明黑
    }
    for (int i = 0; i < 4; ++i)
    {
        uint32_t bits = blk[4 + i];
        for (int j = 0; j < 4; ++j)
            out16[i * 4 + j] = pal[(bits >> (j * 2)) & 3];
    }
}

// BC2(DXT3) 单块解码: 显式 4bit alpha + BC1 色
static void DecodeBc2(const uint8_t* blk, uint32_t* out16)
{
    DecodeBc1(blk + 8, out16);
    for (int i = 0; i < 16; ++i)   // 像素 i 的 4bit alpha: 字节 i>>1, 半字节 (i&1)*4
    {
        uint8_t byte = blk[i >> 1];
        uint8_t nib  = (i & 1) ? (uint8_t)(byte >> 4) : (uint8_t)(byte & 0xF);
        uint8_t a    = (uint8_t)(nib * 17);   // 0..15 -> 0..255
        out16[i] = (out16[i] & 0x00FFFFFFu) | ((uint32_t)a << 24);
    }
}

// BC3(DXT5) 单块解码: 8-alpha 插值 + BC1 色
static void DecodeBc3(const uint8_t* blk, uint32_t* out16)
{
    DecodeBc1(blk + 8, out16);
    uint8_t a[8];
    a[0] = blk[0];
    a[1] = blk[1];
    if (a[0] > a[1])
    {
        for (int i = 0; i < 6; ++i) a[2 + i] = (uint8_t)(((6 - i) * a[0] + (1 + i) * a[1]) / 7);
    }
    else
    {
        for (int i = 0; i < 4; ++i) a[2 + i] = (uint8_t)(((4 - i) * a[0] + (1 + i) * a[1]) / 5);
        a[6] = 0; a[7] = 255;
    }
    // 16 个 3bit 索引, 48bit 从 blk[2..7], 每像素低位在前（跨字节时拼两字节, 尾块不越界）
    for (int i = 0; i < 16; ++i)
    {
        int bit = i * 3;
        int byteIdx = 2 + (bit >> 3);
        uint32_t v = blk[byteIdx];
        if (byteIdx < 7 && (bit & 7) > 5) v |= (uint32_t)blk[byteIdx + 1] << 8;
        uint8_t al = a[(v >> (bit & 7)) & 7];
        out16[i] = (out16[i] & 0x00FFFFFFu) | ((uint32_t)al << 24);
    }
}

// BC4 单块解码 -> 4x4, 取 R 通道复制到 BGRA（灰度语义）
static void DecodeBc4(const uint8_t* blk, uint32_t* out16)
{
    uint8_t r[8];
    r[0] = blk[0];
    r[1] = blk[1];
    if (r[0] > r[1])
    {
        for (int i = 0; i < 6; ++i) r[2 + i] = (uint8_t)(((6 - i) * r[0] + (1 + i) * r[1]) / 7);
    }
    else
    {
        for (int i = 0; i < 4; ++i) r[2 + i] = (uint8_t)(((4 - i) * r[0] + (1 + i) * r[1]) / 5);
        r[6] = 0; r[7] = 255;
    }
    for (int i = 0; i < 16; ++i)
    {
        int bit = i * 3;
        int byteIdx = 2 + (bit >> 3);
        uint32_t v = blk[byteIdx];
        if (byteIdx < 7 && (bit & 7) > 5) v |= (uint32_t)blk[byteIdx + 1] << 8;
        uint8_t g = r[(v >> (bit & 7)) & 7];
        out16[i] = 0xFF000000u | ((uint32_t)g << 16) | ((uint32_t)g << 8) | g;
    }
}

// BC 纹理按块行解码: 解一个 4 像素高条带（每块只解一次）, 写入 atlas 的 [y0,y0+4) 行
// （宽 W 裁剪, 高 hMax 裁剪; atlas 为 BGRA, pitch 字节）
static void DecodeBcStrip(DXGI_FORMAT fmt, const uint8_t* src, uint32_t srcRowBytes,
                          uint32_t blockY, uint32_t W, uint32_t hMax,
                          uint8_t* atlas, uint32_t pitch)
{
    uint32_t blocksX = (W + 3) >> 2;
    uint32_t bb      = BcBlockBytes(fmt);
    const uint8_t* rowBlk = src + (SIZE_T)blockY * srcRowBytes;
    uint32_t y0 = blockY * 4;
    uint32_t tmp[16];
    for (uint32_t bx = 0; bx < blocksX; ++bx)
    {
        const uint8_t* blk = rowBlk + (SIZE_T)bx * bb;
        switch (fmt)
        {
        case DXGI_FORMAT_BC1_UNORM: case DXGI_FORMAT_BC1_TYPELESS: case DXGI_FORMAT_BC1_UNORM_SRGB:
            DecodeBc1(blk, tmp); break;
        case DXGI_FORMAT_BC2_UNORM: case DXGI_FORMAT_BC2_TYPELESS: case DXGI_FORMAT_BC2_UNORM_SRGB:
            DecodeBc2(blk, tmp); break;
        case DXGI_FORMAT_BC3_UNORM: case DXGI_FORMAT_BC3_TYPELESS: case DXGI_FORMAT_BC3_UNORM_SRGB:
            DecodeBc3(blk, tmp); break;
        case DXGI_FORMAT_BC4_UNORM: case DXGI_FORMAT_BC4_TYPELESS:
            DecodeBc4(blk, tmp); break;
        default:
            memset(tmp, 0, sizeof(tmp)); break;
        }
        uint32_t x0 = bx * 4;
        for (uint32_t r = 0; r < 4; ++r)
        {
            uint32_t y = y0 + r;
            if (y >= hMax) break;
            uint32_t* dst = (uint32_t*)(atlas + (SIZE_T)y * pitch);
            for (uint32_t c = 0; c < 4; ++c)
                if (x0 + c < W) dst[x0 + c] = tmp[r * 4 + c];
        }
    }
}

// ---------- 渲染线程阶段: 官方图集读回 + 拼接 + D3D 创建 + 伪对象组装 ----------
static bool FinishFont(FakeFont* f)
{
    // 并发守卫: 只允许一个线程从 state 3 进入构建（5=building）
    if (InterlockedCompareExchange(&f->state, 5, 3) != 3) return false;
    uint32_t fontId = f->fontId;
    uint8_t* off = static_cast<uint8_t*>(f->official);

    // v7.6: D3D slot 由 AOB 反推, 解析失败时留空 -> 字形层安全降级（不崩溃）
    if (!g_devSlot || !g_ctxSlot)
    { Log("font%u: d3d slot unresolved, font layer disabled", fontId); f->state = 4; return false; }
    ID3D11Device* dev = *g_devSlot;
    ID3D11DeviceContext* ctx = *g_ctxSlot;
    if (!dev || !ctx) { Log("font%u: d3d not ready, retry later", fontId); f->state = 3; return false; }

    int      offCount = *(int*)(off + 8);
    uint32_t offTexId = *(uint32_t*)(off + 184);
    void*    offMet   = *(void**)(off + 176);
    void*    offXtab  = *(void**)(off + 192);
    void*    offYtab  = *(void**)(off + 200);
    if (!offMet || !offXtab || !offYtab || offTexId == 0xFFFFFFFFu)
    { Log("font%u: official blob bad, abort", fontId); f->state = 4; return false; }

    // 官方 SRV -> 纹理 -> 尺寸/格式
    ID3D11ShaderResourceView* offSrv =
        static_cast<ID3D11ShaderResourceView*>(g_origSrvResolve(offTexId, 0));
    if (!offSrv) { Log("font%u: official SRV null (texId=%u), retry", fontId, offTexId); f->state = 3; return false; }

    ID3D11Resource* res = nullptr;
    offSrv->GetResource(&res);
    ID3D11Texture2D* srcTex = static_cast<ID3D11Texture2D*>(res);
    if (!srcTex) { Log("font%u: official texture null", fontId); f->state = 4; return false; }

    D3D11_TEXTURE2D_DESC dd{};
    srcTex->GetDesc(&dd);

    uint32_t W = dd.Width;
    uint32_t offW = W;               // 官方图集宽（读回官方区按此宽; 加宽后官方区只占伪图集左侧）
    uint32_t offH = dd.Height;
    uint32_t perRow = W / f->cellW;
    if (perRow == 0) { Log("font%u: atlas width %u < cellW %u, abort", fontId, W, f->cellW); srcTex->Release(); f->state = 4; return false; }
    uint32_t nCellsWanted = f->nCells;   // 截断前记录（日志用）
    uint32_t rows = (f->nCells + perRow - 1) / perRow;
    uint32_t maxRows = (16384 - offH) / f->cellH - 1;   // 末尾恒留 1 行空白 cell（缺字槽位指向这里）
    if (rows > maxRows && W < 8192)
    {
        // 显存换全字覆盖: 加宽伪图集减少截断
        // （font1 官方 4096 宽仅 18 列; 8192 宽 37 列; 16384 宽曾致卡死, 回退）
        uint32_t oldW = W;
        W = 8192;
        perRow = W / f->cellW;
        rows = (f->nCells + perRow - 1) / perRow;
        maxRows = (16384 - offH) / f->cellH - 1;
        Log("font%u: atlas widened %u -> %u (perRow %u -> %u)", fontId, oldW, W, oldW / f->cellW, perRow);
    }
    if (rows > maxRows)
    {
        rows = maxRows;
        f->nCells = rows * perRow;   // 截断低频字（数组已按频率降序, 尾部被截）
        Log("font%u: atlas cells clamped %u -> %u (low-freq chars blank)",
            fontId, nCellsWanted, f->nCells);
    }
    uint32_t H = offH + (rows + 1) * f->cellH;
    f->blankY = offH + rows * f->cellH;   // 空白行: 图集该区已 memset 0
    if (H > 16384) { Log("font%u: H=%u overflow, abort", fontId, H); srcTex->Release(); f->state = 4; return false; }

    // 拼接 buffer
    uint32_t pitch = W * 4;
    uint8_t* atlas = static_cast<uint8_t*>(VirtualAlloc(nullptr, (SIZE_T)pitch * H,
                                                         MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!atlas) { Log("font%u: atlas alloc failed", fontId); srcTex->Release(); f->state = 4; return false; }
    memset(atlas, 0, (SIZE_T)pitch * H);

    // 1) 读回官方图集（staging）
    D3D11_TEXTURE2D_DESC sd = dd;
    sd.Usage          = D3D11_USAGE_STAGING;
    sd.BindFlags      = 0;
    sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    sd.MiscFlags      = 0;
    ID3D11Texture2D* stag = nullptr;
    if (FAILED(dev->CreateTexture2D(&sd, nullptr, &stag)))
    { Log("font%u: staging create failed", fontId); VirtualFree(atlas, 0, MEM_RELEASE); srcTex->Release(); f->state = 4; return false; }
    ctx->CopyResource(stag, srcTex);
    srcTex->Release();

    D3D11_MAPPED_SUBRESOURCE ms{};
    if (FAILED(ctx->Map(stag, 0, D3D11_MAP_READ, 0, &ms)))
    {
        Log("font%u: staging map failed, abort upgrade", fontId);
        stag->Release();
        VirtualFree(atlas, 0, MEM_RELEASE);
        f->state = 4;
        return false;
    }

    // 格式信息前置记录（拷贝循环前, 崩溃也能拿到; 宽高指官方源图集）
    Log("font%u: src atlas %ux%u fmt=%s(%u) mips=%u arr=%u RowPitch=%u",
        fontId, offW, offH, FmtName(dd.Format), (unsigned)dd.Format,
        dd.MipLevels, dd.ArraySize, ms.RowPitch);

    // 格式感知读回 -> atlas 统一为 BGRA8
    bool     bc  = IsBcFormat(dd.Format);
    uint32_t bpp = BppOf(dd.Format);
    if (!bc && bpp == 0)
    {
        Log("font%u: unsupported format %s(%u), graceful abort (official font kept)",
            fontId, FmtName(dd.Format), (unsigned)dd.Format);
        ctx->Unmap(stag, 0);
        stag->Release();
        VirtualFree(atlas, 0, MEM_RELEASE);
        f->state = 4;
        return false;
    }

    if (bc)
    {
        uint32_t blocksY = (offH + 3) >> 2;
        for (uint32_t by = 0; by < blocksY; ++by)
            DecodeBcStrip(dd.Format, (const uint8_t*)ms.pData, ms.RowPitch,
                          by, offW, offH, atlas, pitch);   // 源宽 offW, 写入加宽后的 pitch
    }
    else if (bpp == 4)
    {
        for (uint32_t row = 0; row < offH; ++row)
            memcpy(atlas + (SIZE_T)row * pitch,
                   (const uint8_t*)ms.pData + (SIZE_T)row * ms.RowPitch, (SIZE_T)offW * 4);
    }
    else if (bpp == 2)
    {
        for (uint32_t row = 0; row < offH; ++row)
        {
            const uint16_t* s = (const uint16_t*)((const uint8_t*)ms.pData + (SIZE_T)row * ms.RowPitch);
            uint32_t* d = (uint32_t*)(atlas + (SIZE_T)row * pitch);
            for (uint32_t x = 0; x < offW; ++x)
            {
                uint16_t v = s[x];
                uint8_t r = (uint8_t)(((v >> 11) & 0x1F) * 255 / 31);
                uint8_t g = (uint8_t)(((v >> 5)  & 0x3F) * 255 / 63);
                uint8_t b = (uint8_t)(( v        & 0x1F) * 255 / 31);
                uint8_t a = (dd.Format == DXGI_FORMAT_B5G5R5A1_UNORM) ? (uint8_t)(((v >> 15) & 1) * 255)
                          : (dd.Format == DXGI_FORMAT_B4G4R4A4_UNORM) ? (uint8_t)(((v >> 12) & 0xF) * 17)
                          : 255;
                d[x] = ((uint32_t)a << 24) | ((uint32_t)b << 16) | ((uint32_t)g << 8) | r;
            }
        }
    }
    else   // bpp == 1 (R8/A8): 灰度展开, 覆盖值进全部通道（兼容任意采样通道）
    {
        for (uint32_t row = 0; row < offH; ++row)
        {
            const uint8_t* s = (const uint8_t*)ms.pData + (SIZE_T)row * ms.RowPitch;
            uint32_t* d = (uint32_t*)(atlas + (SIZE_T)row * pitch);
            for (uint32_t x = 0; x < offW; ++x)
            {
                uint32_t c = s[x];
                d[x] = (c << 24) | (c << 16) | (c << 8) | c;
            }
        }
    }
    ctx->Unmap(stag, 0);
    stag->Release();

    // 官方图集像素样本（调试: 判断字形存储格式/覆盖通道语义）
    {
        uint32_t sx = *(uint32_t*)((uint8_t*)offXtab + 4 * ('W' - 0x20));
        uint32_t sy = *(uint32_t*)((uint8_t*)offYtab + 4 * ('W' - 0x20));
        if (sx + 8 < offW && sy + 8 < offH)
        {
            uint32_t* px = (uint32_t*)(atlas + (SIZE_T)sy * pitch + (SIZE_T)sx * 4);
            Log("font%u: sample W@(%u,%u): %08X %08X %08X %08X",
                fontId, sx, sy, px[0], px[1], px[pitch / 8], px[pitch / 8 + 1]);
        }
    }

    // 2) 中文区 blit（灰度 -> BGRA, 覆盖值进全部通道: 无论 shader 采 .r/.a 都正确）
    for (uint32_t i = 0; i < f->nCells; ++i)
    {
        uint32_t cx = (i % perRow) * f->cellW;
        uint32_t cy = offH + (i / perRow) * f->cellH;
        const uint8_t* cell = f->cellBuf + (SIZE_T)i * f->cellW * f->cellH;
        for (uint32_t r = 0; r < f->cellH; ++r)
        {
            uint32_t* dst = (uint32_t*)(atlas + (SIZE_T)(cy + r) * pitch + (SIZE_T)cx * 4);
            const uint8_t* src = cell + (SIZE_T)r * f->cellW;
            for (uint32_t c = 0; c < f->cellW; ++c)
            {
                uint32_t cov = src[c];
                dst[c] = (cov << 24) | (cov << 16) | (cov << 8) | cov;
            }
        }
    }

    // 3) 创建纹理 + SRV（显式 BGRA8: 不继承官方压缩格式, 上传数据即 atlas 布局）
    D3D11_TEXTURE2D_DESC nd = dd;
    nd.Width     = W;
    nd.Height    = H;
    nd.MipLevels = 1;
    nd.ArraySize = 1;
    nd.Format          = DXGI_FORMAT_B8G8R8A8_UNORM;
    nd.SampleDesc.Count = 1;
    nd.SampleDesc.Quality = 0;
    nd.Usage          = D3D11_USAGE_DEFAULT;
    nd.BindFlags      = D3D11_BIND_SHADER_RESOURCE;
    nd.CPUAccessFlags = 0;
    nd.MiscFlags      = 0;
    D3D11_SUBRESOURCE_DATA initData{ atlas, pitch, 0 };
    HRESULT hr = dev->CreateTexture2D(&nd, &initData, &f->tex);
    VirtualFree(atlas, 0, MEM_RELEASE);
    if (FAILED(hr)) { Log("font%u: CreateTexture2D failed hr=%08X", fontId, (unsigned)hr); f->state = 4; return false; }
    hr = dev->CreateShaderResourceView(f->tex, nullptr, &f->srv);
    if (FAILED(hr)) { Log("font%u: CreateSRV failed hr=%08X", fontId, (unsigned)hr); f->tex->Release(); f->tex = nullptr; f->state = 4; return false; }

    // 4) 伪字体对象 blob: 208B 头 + metrics(16B) + xtab(4B) + ytab(4B)
    size_t blobSize = 208 + (size_t)FAKE_GLYPHS * 16 + (size_t)FAKE_GLYPHS * 4 * 2;
    uint8_t* obj = static_cast<uint8_t*>(VirtualAlloc(nullptr, blobSize,
                                                      MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!obj) { Log("font%u: blob alloc failed", fontId); f->srv->Release(); f->srv = nullptr; f->tex->Release(); f->tex = nullptr; f->state = 4; return false; }
    memset(obj, 0, blobSize);

    memcpy(obj, off, 208);                          // 头照抄（行高/cellH/偏移/图集名/kern 指针域+计数）
    *(int*)(obj + 8)   = (int)FAKE_GLYPHS;          // count: 覆盖 0x20..0xFFFF
    *(int*)(obj + 12)  = (int)FONT_BASECHAR_DEFAULT;// baseChar=0x20（照官方）
    *(uint32_t*)(obj + 184) = MAGIC_TEXID_BASE + fontId;

    uint8_t* met = obj + 208;
    uint8_t* xt  = met + (size_t)FAKE_GLYPHS * 16;
    uint8_t* yt  = xt  + (size_t)FAKE_GLYPHS * 4;
    *(void**)(obj + 176) = met;
    *(void**)(obj + 192) = xt;
    *(void**)(obj + 200) = yt;
    // kern 表(+168 指针/+32 计数) 已随头照抄, 指官方 blob（官方槽码不变, kern 语义保持）

    // 官方槽码区照抄
    memcpy(met, offMet, (size_t)offCount * 16);
    memcpy(xt,  offXtab, (size_t)offCount * 4);
    memcpy(yt,  offYtab, (size_t)offCount * 4);

    // 1) 漏填槽位先填默认兜底（含超容量字符）: 空白 cell, 无 kern
    //    （必须先于中文字符区填充, 否则会把中文槽位的真实 UV/kernStart 覆盖掉）
    for (uint32_t slot = (uint32_t)offCount; slot < FAKE_GLYPHS; ++slot)
    {
        *(int32_t*)(met + (size_t)slot * 16 + 0)  = f->cellW;
        *(int32_t*)(met + (size_t)slot * 16 + 4)  = f->cellW;
        *(int16_t*)(met + (size_t)slot * 16 + 12) = -1;                  // 无 kern（防 kern 表空指针崩溃）
        *(uint32_t*)(yt + (size_t)slot * 4) = f->blankY;                 // 指向预留空白行（透明, 不遮挡）
    }
    // 2) 中文字符区后填, 覆盖默认兜底值（容量截断后的字符）
    for (uint32_t i = 0; i < f->nCells; ++i)
    {
        uint32_t cp = f->cps[i];
        uint32_t slot = cp - FONT_BASECHAR_DEFAULT;
        if (slot < (uint32_t)offCount || slot >= FAKE_GLYPHS) continue;  // 不覆盖官方区
        *(int32_t*)(met + (size_t)slot * 16 + 0)  = f->advances[i];
        *(int32_t*)(met + (size_t)slot * 16 + 4)  = f->cellW;
        *(int16_t*)(met + (size_t)slot * 16 + 12) = -1;                  // 无 kern
        *(uint32_t*)(xt + (size_t)slot * 4) = (i % perRow) * f->cellW;
        *(uint32_t*)(yt + (size_t)slot * 4) = offH + (i / perRow) * f->cellH;
    }

    f->obj = obj;

    // 伪纹理对象（+8 u16 W, +10 u16 H, +20 u16 变体=1, +34 u8 速度=0）
    memset(f->fakeTexObj, 0, sizeof(f->fakeTexObj));
    *(uint16_t*)(f->fakeTexObj + 8)  = (uint16_t)W;
    *(uint16_t*)(f->fakeTexObj + 10) = (uint16_t)H;
    *(uint16_t*)(f->fakeTexObj + 20) = 1;

    // 释放光栅化缓存
    free(f->cellBuf); f->cellBuf = nullptr;
    free(f->advances); f->advances = nullptr;
    free(f->cps); f->cps = nullptr;

    Log("font%u: LIVE atlas=%ux%u cells=%u kept=%u blob=%zuKB", fontId, W, H, nCellsWanted, f->nCells, blobSize >> 10);
    InterlockedExchange(&f->state, 2);
    return true;
}

// ---------- 请求升级字体（渲染线程, DrawWide 内调用） ----------
// 文本是否需要中文字形（词典字符集位图: 含任一 >=0x80 字符即需要）
static bool TextNeedsGlyphs(const wchar_t* s)
{
    if (!g_stbReady || !g_dictReady) return false;
    for (const wchar_t* p = s; *p; ++p)
        if (*p >= 0x80 && (g_charSet[(uint32_t)*p >> 3] & (1u << (*p & 7))))
            return true;
    return false;
}

// slot: fontTab 槽位号（已规范化, 非 DrawWide 原始 fontId）
static void RequestFont(uint32_t slot)
{
    if (!g_stbReady || !g_dictReady) return;
    if (slot >= FAKE_FONT_MAX) return;
    FakeFont* f = &g_fake[slot];
    LONG st = f->state;
    if (st != 0) return;
    if (InterlockedCompareExchange(&f->state, 1, 0) != 0) return;
    f->fontId = slot;
    Log("font%u: upgrade requested (first CJK text)", slot);
    CloseHandle(CreateThread(nullptr, 0, RasterizeThread, f, 0, nullptr));
}

// 官方对象 -> fontTab 槽位号（找不到返回 0xFFFFFFFF）
static uint32_t ResolveSlot(void* off)
{
    if (!g_fontTabPtr || !off) return 0xFFFFFFFFu;
    int n = *g_fontCountPtr;
    if (n < 0) return 0xFFFFFFFFu;
    if (n > (int)FAKE_FONT_MAX) n = (int)FAKE_FONT_MAX;
    for (int i = 0; i < n; ++i)
        if ((*g_fontTabPtr)[i] == off) return (uint32_t)i;
    return 0xFFFFFFFFu;
}

// DrawWide 命中含中文译文时调用: 规范化 fontId（-1/负组编码/槽位）-> 槽位 -> 触发/推进
// rawFontId -> FakeFont* 缓存（避免每帧线性扫 fontTab; DrawWide 端高频调用）
struct FontIdCache { unsigned int raw; FakeFont* f; };
static FontIdCache g_fidCache[16];
static volatile LONG g_fidCacheN = 0;

static void EnsureFontFor(unsigned int rawFontId)
{
    if (!g_stbReady || !g_dictReady) return;

    // 已缓存: D3D 阶段由后台线程推进, 此处不再调 FinishFont（渲染线程防卡顿）
    LONG n = g_fidCacheN;
    if (n > 16) n = 16;
    for (LONG i = 0; i < n; ++i)
    {
        if (g_fidCache[i].raw == rawFontId) return;
    }

    // 新 fontId: 解析官方对象 -> 反查槽位
    void* off = g_origFontLookup((int)rawFontId);
    if (!off) return;
    uint32_t slot = ResolveSlot(off);
    if (slot >= FAKE_FONT_MAX) return;
    FakeFont* f = &g_fake[slot];

    LONG idx = InterlockedIncrement(&g_fidCacheN) - 1;   // 1 起
    if (idx < 16)
    {
        g_fidCache[idx].raw = rawFontId;
        g_fidCache[idx].f   = f;
    }

    if (f->state == 0) RequestFont(slot);   // 首次: 起后台光栅化
}

// ---------- 字体初始化线程: 读 TTF + stbtt_InitFont ----------
static DWORD WINAPI FontFileThread(LPVOID hSelf)
{
    wchar_t dir[MAX_PATH];
    GetModuleFileNameW((HMODULE)hSelf, dir, MAX_PATH);
    wchar_t* slash = wcsrchr(dir, L'\\');
    if (slash) *slash = L'\0'; else *dir = L'\0';

    // 等词典就绪（字符集收集完毕）
    for (int i = 300; i--; ) { if (g_dictReady) break; Sleep(100); }
    if (!g_dictReady) { Log("font: dict not ready, glyph layer disabled"); return 0; }

    // 字符集来源二选一（显式标志, 不依赖执行顺序/隐式计数）:
    //   g_dictLoaded=1 -> 词典已收集译文 charset + 全角标点 extras
    //   g_dictLoaded=0 -> font-only 模式: 注入内核汉化固化字符集(charset_data.h) + extras
    static const wchar_t extra[] =
        L"，。？！：；、·—…“”‘’（）《》〈〉【】〔〕「」『』％℃°±×÷©®™"
        L"０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
        L"ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ";
    if (g_dictLoaded)
    {
        for (const wchar_t* p = extra; *p; ++p) CharSetAdd(*p);
        Log("font: charset %u chars (dict) + extras", g_charCount);
    }
    else
    {
        for (unsigned i = 0; i < kCharsetKernelCount; ++i)
        {
            CharSetAdd((wchar_t)kCharsetKernel[i].cp);
            g_charFreq[kCharsetKernel[i].cp] = kCharsetKernel[i].freq;  // 频率直接赋值(非累加)
        }
        for (const wchar_t* p = extra; *p; ++p) CharSetAdd(*p);
        Log("font: charset %u chars (kernel builtin) + extras", g_charCount);
    }

    // 读 TTF（ini 字体文件名; 兼容旧名 font.ttf）
    wchar_t ttf[MAX_PATH];
    _snwprintf_s(ttf, MAX_PATH, _TRUNCATE, L"%ls\\%ls", dir, g_cfg.fontFile);

    HANDLE f = CreateFileW(ttf, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE)
    {
        // 兼容: ini 未配置时的默认名
        wchar_t ttf1[MAX_PATH];
        wcscpy_s(ttf1, dir); wcscat_s(ttf1, L"\\font.ttf");
        f = CreateFileW(ttf1, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
        wcscpy_s(ttf, ttf1);
    }
    if (f == INVALID_HANDLE_VALUE)
    {
        Log("font: %ls not found, glyph layer disabled (GLE=%lu)", ttf, GetLastError());
        return 0;
    }
    LARGE_INTEGER sz;
    GetFileSizeEx(f, &sz);
    if (sz.QuadPart <= 0 || sz.QuadPart > (128 << 20))
    {
        Log("font: bad ttf size %lld", sz.QuadPart);
        CloseHandle(f); return 0;
    }
    g_ttfBuf = static_cast<uint8_t*>(VirtualAlloc(nullptr, (SIZE_T)sz.QuadPart,
                                                   MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    DWORD rd = 0;
    BOOL ok = g_ttfBuf && ReadFile(f, g_ttfBuf, (DWORD)sz.QuadPart, &rd, nullptr);
    CloseHandle(f);
    if (!ok || rd != (DWORD)sz.QuadPart)
    {
        Log("font: ttf read failed");
        if (g_ttfBuf) { VirtualFree(g_ttfBuf, 0, MEM_RELEASE); g_ttfBuf = nullptr; }
        return 0;
    }
    Log("font: %ls (%u bytes)", ttf, rd);

    if (!stbtt_InitFont(&g_stb, g_ttfBuf, 0))
    {
        Log("font: stbtt_InitFont failed, glyph layer disabled");
        VirtualFree(g_ttfBuf, 0, MEM_RELEASE); g_ttfBuf = nullptr;
        return 0;
    }
    int upem = 0;
    stbtt_GetFontVMetrics(&g_stb, &upem, nullptr, nullptr);  // 复用变量取 ascent
    Log("font: stbtt ok (numGlyphs=%d)", g_stb.numGlyphs);
    InterlockedExchange(&g_stbReady, 1);
    return 0;
}

// =====================================================================
// 文本层 Hook A/B（v5）
// =====================================================================

static void DiagFmtOrigin(uint64_t raCaller, const wchar_t* s);   // v7.4 前向声明

using DrawWide_t = __int64(__fastcall*)(void*, float, float, const wchar_t*, float, char, unsigned int, void*);
static DrawWide_t g_origDrawWide = nullptr;

static __int64 __fastcall HookDrawWide(void* a1, float x, float y, const wchar_t* text,
                                        float scale, char flag, unsigned int fontId, void* a8)
{
    if (text && *text)
    {
        const DictNode* r = LookupNode(text, &g_hitA, &g_missA);
        if (r) text = r->trans;
        // 文本含中文字符（无论替换来自 Hook A 还是 Hook B）-> 确保该字体已升级
        if (TextNeedsGlyphs(text)) EnsureFontFor(fontId);
    }
    return g_origDrawWide(a1, x, y, text, scale, flag, fontId, a8);
}

using Format_t = __int64(__fastcall*)(wchar_t*, const wchar_t*, unsigned long long, void*, unsigned int);
static Format_t g_origFormat = nullptr;

static __int64 __fastcall HookFormat(wchar_t* dst, const wchar_t* fmt,
                                      unsigned long long cap, void* args, unsigned int argc)
{
    // 必须先捕获: _ReturnAddress() 在函数入口 = Format 真正调用者的返回地址
    // （任何后续 call 都会覆盖它 -> 归因必须用此刻的值）
    uint64_t raCaller = reinterpret_cast<uint64_t>(_ReturnAddress());
    if (fmt && *fmt)
    {
        const DictNode* r = LookupNode(fmt, &g_hitB, &g_missB);
        if (r)
        {
            fmt = r->trans;
        }
        else if (!wcschr(fmt, L'%') && !wcschr(fmt, L'\n'))
        {
            // 整句/trim 均未命中: 走折行重组（含 % 的模板串与内嵌换行的完整文本不参与）
            const wchar_t* w = WrapProcess(fmt, wcslen(fmt));
            if (w) fmt = w;
            // v7.4 诊断: 仍未替换的英文长文本 -> 按返回地址归类（谁在把行段/长句喂给 Format）
            else if (g_cfg.earlyDiag) DiagFmtOrigin(raCaller, fmt);
        }
    }
    return g_origFormat(dst, fmt, cap, args, argc);
}

// =====================================================================
// v7.4 早期整句替换（语言服务返回层; 引擎布局自切行, 游戏原生支持日/韩）
//   文本对象刷新 sub_14082DD10 优先用 vtable[1]()(sub_140812060) 的返回串当 Format
//   的 fmt; vtable[0]()(sub_140812040) 供 Lua action 等取本地化文本。这两个 thunk
//   都只读全局语言服务对象并尾调其 vtable 槽, 是"完整句"在切行/展开前的最下游载体:
//   在此返回层把命中词典的英文完整句替换为中文整句, 引擎 Format 后由布局引擎
//   sub_140834C30 按 CJK 宽度自切行（wrap-rejoin 状态机仅作兜底）。
//   仅替换返回值、不改引擎内存; 译文在 arena 内永久有效。
// 调用点分类（诊断用; 渲染/UI 线程, 原子计数即可, 不逐条打日志防刷屏）。
// v7.4.2: raCaller 在 HookFormat 入口用 _ReturnAddress() 捕获（= Format 真正调用者返回地址）,
// 窗口匹配 [call, call+12) 覆盖 call rel32/rip/mem 不同长度; 常量本身是 call 指令地址。
static constexpr uint64_t RA_FMT_WRAPPER = 0x18E7E0ULL;  // char* 通用包装 -> Format
static constexpr uint64_t RA_FMT_CRIB     = 0x212035ULL;  // Crib 初始化（与字幕无关, 排除）
static constexpr uint64_t RA_FMT_LUA      = 0x81CB55ULL;  // Lua action: 本地化文本 -> Format
static constexpr uint64_t RA_FMT_TEXTCUR  = 0x82DDBDULL;  // 文本对象刷新: fmt = 语言服务"当前文本"
static constexpr uint64_t RA_FMT_TEXTTMPL = 0x82DE22ULL;  // 文本对象刷新: fmt = 对象模板 a1[37]
static volatile LONG g_fmtFrom[8];   // 分类计数（RA_FMT_* 顺序, idx0=其他/未知）

static volatile LONG g_earlyCurHit = 0, g_earlyCurMiss = 0;   // vtable[1] 当前文本
static volatile LONG g_earlyTxtHit = 0, g_earlyTxtMiss = 0;   // vtable[0] 当前解析文本

// v7.4.1: sub_140812060/040 是"隐式传参转发器"——调用者把 hash/描述块放进 rcx
// 再尾调语言服务槽位(IDA 无参声明是假象)。hook 必须原样透传 a1..a4, 否则槽位
// 收到垃圾参数返回 NULL, 文本对象刷新会 fallback 到 a1[37] 原始 KEY 模板(全 UI 变 KEY)。
using LangGet_t = const wchar_t* (__fastcall*)(uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4);
static LangGet_t g_origLangCur = nullptr;   // sub_140812060
static LangGet_t g_origLangTxt = nullptr;   // sub_140812040

// 调用点分类（诊断用; 渲染/UI 线程, 原子计数即可, 不逐条打日志防刷屏）
static void DiagFmtOrigin(uint64_t raCaller, const wchar_t* s)
{
    size_t n = wcslen(s);
    if (n < 24) return;                        // 短文本(常量/HUD 数字)不归因
    bool hasCjk = false;
    for (const wchar_t* p = s; *p; ++p) if (*p >= 0x80) { hasCjk = true; break; }
    if (hasCjk) return;                        // 已是中文/他语文本, 无需归因
    uint64_t ra = raCaller - s_exeBase;
    int idx = 0;                               // 0 = 其他
    if      (ra >= RA_FMT_WRAPPER && ra < RA_FMT_WRAPPER + 12) idx = 1;
    else if (ra >= RA_FMT_CRIB     && ra < RA_FMT_CRIB     + 12) idx = 2;
    else if (ra >= RA_FMT_LUA      && ra < RA_FMT_LUA      + 12) idx = 3;
    else if (ra >= RA_FMT_TEXTCUR  && ra < RA_FMT_TEXTCUR  + 12) idx = 4;
    else if (ra >= RA_FMT_TEXTTMPL && ra < RA_FMT_TEXTTMPL + 12) idx = 5;
    InterlockedIncrement(&g_fmtFrom[idx]);
}

// 返回层整句替换共享逻辑。约定:
//   - 词典未就绪(g_dictReady=0)或空串: 原样
//   - 含 % / \n: 模板或显式多行, 交给 Format hook / 引擎, 不提前拦
//   - 长度 <4(图标/占位) 或 >= WRAP_MAX_CHARS(超长): 原样
//   命中: 返回译文(中文整句, 引擎布局自行按 CJK 切行); miss: 返回原文。
static const wchar_t* EarlySub(LangGet_t orig,
                               uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4,
                               volatile LONG* hit, volatile LONG* miss)
{
    const wchar_t* s = orig(a1, a2, a3, a4);   // 完整透传调用者参数, 不破坏槽位语义
    if (!s || !*s || !g_dictReady) return s;
    if (wcschr(s, L'%') || wcschr(s, L'\n')) return s;
    size_t n = wcslen(s);
    if (n < 4 || n >= WRAP_MAX_CHARS) return s;
    const DictNode* r = DictLookup(s, n);
    if (r)
    {
        InterlockedIncrement(hit);
        if (g_cfg.earlyDiag)
        {
            static volatile LONG dbg = 0;
            if (InterlockedIncrement(&dbg) <= 8)
                Log("early: HIT  len=%zu  \"%.48ls\" -> \"%.48ls\"", n, s, r->trans);
        }
        return r->trans;
    }
    InterlockedIncrement(miss);
    if (g_cfg.earlyDiag)
    {
        static volatile LONG dbg = 0;
        bool asciiWordy = false;
        for (const wchar_t* p = s; *p; ++p)
            if (*p == L' ' && *(p + 1) >= L'a' && *(p + 1) <= L'z') { asciiWordy = true; break; }
        if (asciiWordy && InterlockedIncrement(&dbg) <= 6)
            Log("early: miss len=%zu  \"%.48ls\"", n, s);
    }
    return s;
}

static const wchar_t* __fastcall HookLangCur(uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4)
{
    return EarlySub(g_origLangCur, a1, a2, a3, a4, &g_earlyCurHit, &g_earlyCurMiss);
}

static const wchar_t* __fastcall HookLangTxt(uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4)
{
    return EarlySub(g_origLangTxt, a1, a2, a3, a4, &g_earlyTxtHit, &g_earlyTxtMiss);
}

// ---------- v7.4.4 诊断 hook H: SetText 探针（全 tag） ----------
using SetText_t = char(__fastcall*)(uint64_t obj, const uint8_t* desc);
static SetText_t g_origSetText = nullptr;   // sub_14082EA00
static volatile LONG64 g_setTextDiag = 0;   // 探针日志条数
static uint64_t       g_setTextTick  = 0;   // 频控时间戳
static volatile LONG  g_setTagCnt[16];      // tag 分布（探针所见 tag 计数, tag>=16 归 15）

// desc 是 TextDesc*: [0]=tag(dword), [8]=文本指针。tag4/9: 模板写入(char* UTF-8)。
//   tag2 = 清空对象模板（字幕切句/复用的时序信号）。
//   其他 tag 未知: desc[8] 可能是 char* 或 wchar_t* —— 双解析打印。
//   只读探针: 不改对象/desc, 抓字幕播放器 SetText 的真实序列与 tag。
static char __fastcall HookSetText(uint64_t obj, const uint8_t* desc)
{
    if (desc)
    {
        uint32_t tag = *reinterpret_cast<const uint32_t*>(desc);
        InterlockedIncrement(&g_setTagCnt[(tag < 15) ? tag : 15]);
        uint64_t now = GetTickCount64();
        LONG64 c = InterlockedIncrement64(&g_setTextDiag);
        // tag2(清空) 低频: 400ms 频控即可; 其余前 120 条全记, 之后 150ms 频控
        bool logIt = (tag == 2) ? (now - g_setTextTick > 400)
                                : (c <= 120 || now - g_setTextTick > 150);
        if (logIt)
        {
            g_setTextTick = now;
            const char* txt = *reinterpret_cast<const char* const*>(desc + 8);
            wchar_t wbuf[160];
            int wn = 0;
            if (txt)
            {
                size_t n = strnlen(txt, 240);
                if (n >= 1)
                {
                    wn = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                             txt, (int)((n < 140) ? n : 140), wbuf, 159);
                    if (wn <= 0)   // 非 UTF-8: 可能是宽串(wchar_t*) 或 ANSI
                    {
                        const wchar_t* wt = reinterpret_cast<const wchar_t*>(txt);
                        wn = 0;
                        bool looksWide = false;
                        for (size_t i = 0; i < 140 && wt[i]; ++i)
                        {
                            wchar_t ch = wt[i];
                            if (ch >= 0xD800 && ch <= 0xDFFF) break;   // 代理区: 非文本
                            if (ch < 0x20 && ch != L'\n' && ch != L'\t' && ch != L'\r') break;
                            if (ch == 0) break;
                            wbuf[wn++] = ch;
                            looksWide = true;
                        }
                        if (!looksWide)   // 兜底: ASCII 可打印段
                        {
                            wn = 0;
                            for (size_t i = 0; i < n && i < 140; ++i)
                            {
                                unsigned ch = (unsigned char)txt[i];
                                if (ch < 0x20 || ch >= 0x7F) break;
                                wbuf[wn++] = (wchar_t)ch;
                            }
                        }
                    }
                }
            }
            if (wn > 0)
            {
                wbuf[wn] = L'\0';
                Log("setText: obj=%08llX tag=%u len=%u \"%.100ls\"",
                    (unsigned long long)obj, tag, (unsigned)wn, wbuf);
            }
            else
            {
                Log("setText: obj=%08llX tag=%u CLEAR/EMPTY", (unsigned long long)obj, tag);
            }
        }
    }
    return g_origSetText(obj, desc);
}

// ---------- v7.4.4 诊断 hook I: 文本对象刷新探针 (sub_14082DD10) ----------
// 每帧被全局刷新器对文本对象调用(obj=this)。obj+0x128=模板窄串(char* UTF-8)。
// 只关注"句子样"长文本(含小写字母且 >=16 字符, 无 [image: HUD 噪音):
//   内容变化即报(字幕切句/倒计时), 内容稳定 5s 报一次(证明对象每帧进刷新)。
using RefreshObj_t = void(__fastcall*)(uint64_t obj);
static RefreshObj_t g_origRefresh = nullptr;   // sub_14082DD10
static volatile LONG g_refreshSeen = 0;        // 入口调用总量
static volatile LONG g_refreshLog  = 0;        // 实际日志条数

static uint32_t Fnv1a32(const char* s, size_t n)
{
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < n; ++i) { h = (h ^ (unsigned char)s[i]) * 16777619u; }
    return h;
}

struct RefreshSlotT { volatile LONG objLo, crc; volatile uint64_t tick; };
static RefreshSlotT g_refSlot[8];

static void __fastcall HookRefresh(uint64_t obj)
{
    InterlockedIncrement(&g_refreshSeen);
    if (obj)
    {
        const char* tmpl = *reinterpret_cast<const char* const*>(obj + 0x128);
        if (tmpl)
        {
            size_t n = strnlen(tmpl, 200);
            if (n >= 16)
            {
                bool sent = false, hasImg = false;
                for (size_t i = 0; i < n; ++i)
                {
                    char c = tmpl[i];
                    if (c == '[') { hasImg = true; break; }
                    if (c >= 'a' && c <= 'z') sent = true;
                }
                if (sent && !hasImg)
                {
                    uint32_t crc = Fnv1a32(tmpl, n);
                    uint32_t objLo = (uint32_t)obj;
                    uint64_t now = GetTickCount64();
                    int slot = -1, free = -1, oldest = 0;
                    for (int i = 0; i < 8; ++i)
                    {
                        if (g_refSlot[i].objLo == (LONG)objLo) { slot = i; break; }
                        if (!g_refSlot[i].objLo && free < 0) free = i;
                        if (g_refSlot[i].tick < g_refSlot[oldest].tick) oldest = i;
                    }
                    bool logIt = false;
                    if (slot >= 0)
                    {
                        if (g_refSlot[slot].crc != (LONG)crc) logIt = true;              // 内容变化
                        else if (now - g_refSlot[slot].tick > 5000) logIt = true;        // 稳定刷新 5s 报一次
                        if (logIt) { g_refSlot[slot].crc = (LONG)crc; g_refSlot[slot].tick = now; }
                    }
                    else
                    {
                        int use = (free >= 0) ? free : oldest;
                        g_refSlot[use].objLo = (LONG)objLo;
                        g_refSlot[use].crc = (LONG)crc;
                        g_refSlot[use].tick = now;
                        logIt = true;
                    }
                    if (logIt && InterlockedIncrement(&g_refreshLog) <= 500)
                    {
                        wchar_t wbuf[140]; int wn = 0;
                        wn = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, tmpl,
                                                 (int)((n < 120) ? n : 120), wbuf, 139);
                        if (wn <= 0)
                        {
                            wn = 0;
                            for (size_t i = 0; i < n && i < 120; ++i)
                            {
                                unsigned ch = (unsigned char)tmpl[i];
                                if (ch < 0x20 || ch >= 0x7F) break;
                                wbuf[wn++] = (wchar_t)ch;
                            }
                        }
                        if (wn > 0)
                        {
                            wbuf[wn] = L'\0';
                            Log("refresh: obj=%08llX n=%u \"%.90ls\"",
                                (unsigned long long)obj, (unsigned)n, wbuf);
                        }
                    }
                }
            }
        }
    }
    if (g_origRefresh) g_origRefresh(obj);
}

// =====================================================================
// v7.5 Hook J: 字幕/HUD 绘制入口整串替换 (sub_1402D2BC0, 游侠 SIG3 同点位)
// =====================================================================
// 逆向结论（IDA 2026-09-06, SRTTR.exe）:
//   double sub_1402D2BC0(wchar_t* text, float a2, double a3, int a4)
//   - a1 直接当宽字符串逐字符扫描; 返回值 = 显示时长(秒)
//   - 折行发生在函数内部: sub_14085A1F0(text, maxW, 64, ...) 按字幕框宽度布局
//     出行起止数组后逐行绘制 —— 折行前整串替换为中文, 引擎按 CJK 宽度自然折行,
//     完整句 KEY 直接命中, 从根上消除"折行残段查词典 miss"
//   - a3<=0.1 时从 text 尾部字面 \n<毫秒> 控制码解析时长(atoi/1000), 否则默认 2s
//   - 5 个调用者覆盖字幕与 HUD 文本显示（游侠同点位广撒网, 实测无副作用）
// 与其他 hook 的关系:
//   - 字幕链不经 Format（文本经 sub_1403FD180 字符串表直出）, F/G 语言服务层
//     收不到语音字幕 —— 本 hook 是该链路唯一替换点
//   - 文本若已在 Format 层(Hook B)替换为中文, 本层查词典 miss 原样放行, 无双重替换
//   - 字形升级无需在此触发: 字幕绘制链必经 FontLookup(Hook C) 公共点, 自动覆盖
using SubtitleDraw_t = double(__fastcall*)(const wchar_t*, float, double, int);
static SubtitleDraw_t g_origSubtitle = nullptr;
static volatile LONG g_hitJ = 0, g_missJ = 0;

// 译文输出缓冲（渲染线程专用, 与 g_wrap 状态机同线程假设, 无锁）
static constexpr size_t SUBBUF_CHARS = WRAP_MAX_CHARS + 32;
static wchar_t g_subBuf[SUBBUF_CHARS];

// 尾部字面 \n<数字> 时长尾码检测: 返回主体长度（尾码起点）; 无尾码返回 len
static size_t SubBodyLen(const wchar_t* s, size_t len)
{
    if (len < 4) return len;                      // 至少 \n + 1 数字 + 1 主体字符
    size_t e = len;
    while (e > 0 && s[e - 1] >= L'0' && s[e - 1] <= L'9') --e;
    if (e == len || e < 2) return len;            // 尾部无数字 / 前面放不下 \n
    if (s[e - 1] != L'n' || s[e - 2] != L'\\') return len;
    return e - 2;                                 // 主体 [0, e-2)
}

static double __fastcall HookSubtitle(const wchar_t* text, float a2, double a3, int a4)
{
    if (text && *text && g_dictReady && !wcschr(text, L'%'))
    {
        size_t len = wcslen(text);
        if (len < WRAP_MAX_CHARS)
        {
            size_t bodyLen = SubBodyLen(text, len);

            // 查词典: 有尾码 -> 按主体查（词典 9630 KEY 实测零尾码, 整串查只会 miss）;
            //          无尾码 -> 整串查。trim/规范化/miss-dump 由 LookupNode 统一处理。
            // （未来若词典加入含尾码 KEY, 仍走主体查询 + 原尾码回填, 语义自洽不重复追加）
            const DictNode* r;
            size_t hitLen;
            if (bodyLen < len)
            {
                wchar_t body[WRAP_MAX_CHARS];
                wmemcpy(body, text, bodyLen);
                body[bodyLen] = L'\0';
                r = LookupNode(body, &g_hitJ, &g_missJ);
                hitLen = bodyLen;
            }
            else
            {
                r = LookupNode(text, &g_hitJ, &g_missJ);
                hitLen = len;
            }
            if (r)
            {
                size_t tn = wcslen(r->trans);
                size_t tailLen = len - hitLen;
                if (tn + tailLen + 1 <= SUBBUF_CHARS)
                {
                    // 译文 + 原尾码重组（保留尾码 => 引擎 a3<=0.1 时时长语义不变）
                    wmemcpy(g_subBuf, r->trans, tn);
                    wmemcpy(g_subBuf + tn, text + bodyLen, tailLen);
                    g_subBuf[tn + tailLen] = L'\0';
                    if (g_cfg.earlyDiag)
                    {
                        static volatile LONG dbg = 0;
                        if (InterlockedIncrement(&dbg) <= 8)
                            Log("sub: HIT len=%zu \"%.48ls\" -> \"%.48ls\"", len, text, g_subBuf);
                    }
                    return g_origSubtitle(g_subBuf, a2, a3, a4);
                }
            }
            else if (g_cfg.earlyDiag)
            {
                static volatile LONG dbg = 0;
                bool asciiWordy = false;
                for (size_t i = 0; i + 1 < len; ++i)
                    if (text[i] == L' ' && text[i + 1] >= L'a' && text[i + 1] <= L'z')
                        { asciiWordy = true; break; }
                if (asciiWordy && InterlockedIncrement(&dbg) <= 6)
                    Log("sub: miss len=%zu \"%.64ls\"", len, text);
            }
        }
    }
    return g_origSubtitle(text, a2, a3, a4);
}

// ---------- txt 词典加载（le_strings 格式: "KEY": "VALUE", KEY=英文原文/槽位名） ----------
// 格式规则（与 schinese/*.txt 游戏原生格式一致）:
//   - 每行 "KEY": "VALUE";  引号内的 \\ \" \n 为转义
//   - HASH_xxxxxxxx 键 = 引擎字符串槽位名（值不是查表键）, 跳过不插入哈希表
//   - 值为空 / 纯 ASCII 值跳过（无翻译意义）; KEY 与值相同跳过
struct LeLine
{
    wchar_t* key;
    wchar_t* val;
};

// 解析 "KEY": "VALUE" 行（内存内原地反转义）; 返回 false = 非条目行
static bool ParseLeLine(wchar_t* line, LeLine* out)
{
    wchar_t* p = line;
    while (*p == L' ' || *p == L'\t') ++p;
    if (*p != L'"') return false;
    ++p;
    wchar_t* key = p;

    // KEY 扫描 + 原地反转义（\\ \" \n \r; 未知转义按原样）。
    // le_strings 内核键内无引号, 但 voice 词典键 = 英文原句, 可含转义引号
    // （如 "What happened to, \"I do my own stunts\"?"）; 遇非转义引号才算闭合。
    // 读指针 p / 写指针 w: 反转义后 w <= p, 写入不影响未读部分。
    wchar_t* w = p;
    while (*p)
    {
        if (*p == L'\\')
        {
            ++p;
            if      (*p == L'n')  *w++ = L'\n';
            else if (*p == L'r')  *w++ = L'\r';
            else if (*p == L'\\') *w++ = L'\\';
            else if (*p == L'"')  *w++ = L'"';
            else if (*p)          *w++ = *p;   // 未知转义按原样
            else break;
            ++p;
        }
        else if (*p == L'"')   // 非转义引号, KEY 闭合
            break;
        else *w++ = *p++;
    }
    if (*p != L'"') return false;
    *w = L'\0';      // 反转义后的 KEY 终结（写入位置 <= 闭合引号, 安全）
    ++p;
    while (*p == L' ' || *p == L'\t') ++p;
    if (*p != L':') return false;
    ++p;
    while (*p == L' ' || *p == L'\t') ++p;
    if (*p != L'"') return false;
    ++p;
    wchar_t* val = p;

    // 值反转义（\\ \" \n \r）, 原地写（sr3le_extract.py 输出含 \r 转义, 不处理会混入字母 r）
    // 注意: 上面 KEY 段已用过 w, 这里重绑到值起点
    w = p;
    while (*p)
    {
        if (*p == L'\\')
        {
            ++p;
            if      (*p == L'n')  *w++ = L'\n';
            else if (*p == L'r')  *w++ = L'\r';
            else if (*p == L'\\') *w++ = L'\\';
            else if (*p == L'"')  *w++ = L'"';
            else if (*p)          *w++ = *p;   // 未知转义按原样
            else break;
            ++p;
        }
        else if (*p == L'"')   // 闭合引号, 值结束
        {
            *w = L'\0';
            out->key = key;
            out->val = val;
            return true;
        }
        else *w++ = *p++;
    }
    return false;   // 未闭合
}

// 加载一个 origin txt（UTF-8, 带/不带 BOM; CRLF/LF; 格式: "消息ID": "英文原文"）
// 结果入 origin 表; 不收集字符集; HASH_ 键同样入表（联表后无法消解时才在 AddDictEntry 跳过）
static bool LoadOriginFile(const wchar_t* path)
{
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER sz;
    GetFileSizeEx(f, &sz);
    if (sz.QuadPart <= 0 || sz.QuadPart > (32 << 20))
    { Log("origin: %ls bad size %lld", path, sz.QuadPart); CloseHandle(f); return false; }

    auto* buf = static_cast<uint8_t*>(VirtualAlloc(nullptr, (SIZE_T)sz.QuadPart,
                                                   MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    DWORD rd = 0;
    BOOL ok = buf && ReadFile(f, buf, (DWORD)sz.QuadPart, &rd, nullptr);
    CloseHandle(f);
    if (!ok || rd != (DWORD)sz.QuadPart)
    { if (buf) VirtualFree(buf, 0, MEM_RELEASE); return false; }

    int utf8Off = (rd >= 3 && buf[0] == 0xEF && buf[1] == 0xBB && buf[2] == 0xBF) ? 3 : 0;
    int wlen = MultiByteToWideChar(CP_UTF8, 0, (const char*)buf + utf8Off,
                                   (int)(rd - utf8Off), nullptr, 0);
    if (wlen <= 0)
    { Log("origin: %ls not valid UTF-8", path); VirtualFree(buf, 0, MEM_RELEASE); return false; }
    auto* wbuf = static_cast<wchar_t*>(VirtualAlloc(nullptr, (wlen + 2) * sizeof(wchar_t),
                                                    MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!wbuf) { VirtualFree(buf, 0, MEM_RELEASE); return false; }
    MultiByteToWideChar(CP_UTF8, 0, (const char*)buf + utf8Off, (int)(rd - utf8Off), wbuf, wlen);
    wbuf[wlen] = L'\0';
    VirtualFree(buf, 0, MEM_RELEASE);

    uint32_t entries = 0;
    wchar_t* ctx = nullptr;
    wchar_t* line = wcstok_s(wbuf, L"\r\n", &ctx);
    while (line)
    {
        LeLine le;
        if (ParseLeLine(line, &le))
        {
            size_t kn = wcslen(le.key), vn = wcslen(le.val);
            if (kn > 0 && kn <= 512 && vn > 0 && vn <= 8192)
            {
                if (OrigInsert(le.key, (uint32_t)kn, le.val, (uint32_t)vn)) ++entries;
            }
        }
        line = wcstok_s(nullptr, L"\r\n", &ctx);
    }
    VirtualFree(wbuf, 0, MEM_RELEASE);
    return entries > 0;
}

// 扫描 origin 文件夹（*.txt; 后加载的同键覆盖前面 = 头插哈希表）
static bool LoadOriginDir(const wchar_t* dir)
{
    wchar_t pat[MAX_PATH];
    _snwprintf_s(pat, MAX_PATH, _TRUNCATE, L"%ls\\*.txt", dir);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h == INVALID_HANDLE_VALUE)
    {
        Log("origin: folder %ls not found (GLE=%lu), ID/HASH_ dict keys will be skipped",
            dir, GetLastError());
        return false;
    }
    uint32_t files = 0;
    do
    {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        wchar_t path[MAX_PATH];
        _snwprintf_s(path, MAX_PATH, _TRUNCATE, L"%ls\\%ls", dir, fd.cFileName);
        if (LoadOriginFile(path)) ++files;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    Log("origin: %u files, %u ids", files, g_origCount);
    return files > 0;
}

// 处理一条 key/val: ID/HASH_ 键经 origin 联表转英文原文; 其余按原文键入表
static void AddDictEntry(const wchar_t* key, const wchar_t* val,
                         uint32_t* loaded, uint32_t* hashKeys, uint32_t* cjkEntries)
{
    size_t on = wcslen(key), tn = wcslen(val);
    if (on == 0 || tn == 0 || on > 4096 || tn > 4096) return;

    // 键消解: origin 联表（ID/HASH_xxx -> 英文原文）
    //   - 命中   -> 用英文原文当键（引擎运行时只画解析后的文本, ID 不会到达 hook 层）
    //   - 未命中 -> 键即原文（"Blonde"类条目两条路线通吃）
    //   - 未命中且是 HASH_ -> 无法消解的槽位名, 跳过
    const wchar_t* effKey = key;
    size_t effLen = on;
    if (g_origBuckets)
    {
        const OrigNode* o = OrigLookup(key, on);
        if (o && o->valLen > 0) { effKey = o->val; effLen = o->valLen; }
        else if (!o && on > 5 && _wcsnicmp(key, L"HASH_", 5) == 0)
        { ++*hashKeys; return; }   // origin 里也查不到的 HASH_ 槽位名
    }
    else if (on > 5 && _wcsnicmp(key, L"HASH_", 5) == 0)
    { ++*hashKeys; return; }       // 无 origin 表时维持旧行为

    auto* transW = static_cast<wchar_t*>(ArenaAlloc((tn + 1) * sizeof(wchar_t)));
    if (!transW) return;
    memcpy(transW, val, tn * sizeof(wchar_t));
    transW[tn] = L'\0';

    // 收集译文非 ASCII 字符集
    bool hasCjk = false;
    for (const wchar_t* p = transW; *p; ++p)
        if (*p >= 0x80) { CharSetAdd(*p); hasCjk = true; }
    if (hasCjk) ++*cjkEntries;

    auto* origW = static_cast<wchar_t*>(ArenaAlloc((effLen + 1) * sizeof(wchar_t)));
    if (!origW) return;
    memcpy(origW, effKey, effLen * sizeof(wchar_t));
    origW[effLen] = L'\0';

    if (DictInsert(origW, (uint32_t)effLen, transW)) ++*loaded;
    // 空格规范化副本键（KEY 含连续空格时, 引擎侧永远以单空格形态到来）
    {
        wchar_t norm[4097];
        size_t nl = NormalizeKey(origW, effLen, norm, 4096);
        if (nl && DictInsert(norm, (uint32_t)nl, transW)) ++*loaded;   // 计入规范键
    }
    size_t b, tl;
    if (TrimRange(origW, effLen, &b, &tl) && !(b == 0 && tl == effLen))
    {
        if (DictInsert(origW + b, (uint32_t)tl, transW)) ++*loaded;   // 计入 trim 键
        // trim 后仍可能含连续空格, 同样补规范键
        wchar_t norm[4097];
        size_t nl = NormalizeKey(origW + b, tl, norm, 4096);
        if (nl && DictInsert(norm, (uint32_t)nl, transW)) ++*loaded;
    }
}

// 加载一个 txt 文件（UTF-8, 带/不带 BOM; CRLF/LF）
static bool LoadDictFile(const wchar_t* path, uint32_t* loaded, uint32_t* hashKeys,
                         uint32_t* cjkEntries, uint32_t* lineCount, uint32_t* badLines)
{
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER sz;
    GetFileSizeEx(f, &sz);
    if (sz.QuadPart <= 0 || sz.QuadPart > (32 << 20))
    { Log("dict: %ls bad size %lld", path, sz.QuadPart); CloseHandle(f); return false; }

    auto* buf = static_cast<uint8_t*>(VirtualAlloc(nullptr, (SIZE_T)sz.QuadPart,
                                                   MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    DWORD rd = 0;
    BOOL ok = buf && ReadFile(f, buf, (DWORD)sz.QuadPart, &rd, nullptr);
    CloseHandle(f);
    if (!ok || rd != (DWORD)sz.QuadPart)
    { if (buf) VirtualFree(buf, 0, MEM_RELEASE); return false; }

    int utf8Off = (rd >= 3 && buf[0] == 0xEF && buf[1] == 0xBB && buf[2] == 0xBF) ? 3 : 0;
    int wlen = MultiByteToWideChar(CP_UTF8, 0, (const char*)buf + utf8Off,
                                   (int)(rd - utf8Off), nullptr, 0);
    if (wlen <= 0)
    { Log("dict: %ls not valid UTF-8", path); VirtualFree(buf, 0, MEM_RELEASE); return false; }
    auto* wbuf = static_cast<wchar_t*>(VirtualAlloc(nullptr, (wlen + 2) * sizeof(wchar_t),
                                                    MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!wbuf) { VirtualFree(buf, 0, MEM_RELEASE); return false; }
    MultiByteToWideChar(CP_UTF8, 0, (const char*)buf + utf8Off, (int)(rd - utf8Off), wbuf, wlen);
    wbuf[wlen] = L'\0';
    VirtualFree(buf, 0, MEM_RELEASE);

    wchar_t* ctx = nullptr;
    wchar_t* line = wcstok_s(wbuf, L"\r\n", &ctx);
    while (line)
    {
        ++*lineCount;
        LeLine le;
        if (ParseLeLine(line, &le))
            AddDictEntry(le.key, le.val, loaded, hashKeys, cjkEntries);
        else
        {
            // 空行/尾逗号行等非条目不算错误; 只有含引号但解析失败才计
            wchar_t* q = wcschr(line, L'"');
            if (q) { ++*badLines; if (*badLines <= 5) Log("dict: %ls bad line: %.60ls", path, line); }
        }
        line = wcstok_s(nullptr, L"\r\n", &ctx);
    }
    VirtualFree(wbuf, 0, MEM_RELEASE);
    return true;
}

// 扫描词典文件夹（*.txt; 按 FindFirstFile 字典序, 后加载的同键覆盖前面 = 头插哈希表）
static bool LoadDictDir(const wchar_t* dir, uint32_t* outFiles, uint32_t* outLoaded,
                        uint32_t* outHash, uint32_t* outCjk)
{
    wchar_t pat[MAX_PATH];
    _snwprintf_s(pat, MAX_PATH, _TRUNCATE, L"%ls\\*.txt", dir);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pat, &fd);
    if (h == INVALID_HANDLE_VALUE)
    {
        Log("dict: folder %ls not found (GLE=%lu)", dir, GetLastError());
        return false;
    }
    uint32_t files = 0, loaded = 0, hashKeys = 0, cjkEntries = 0, badLines = 0, lines = 0;
    do
    {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        wchar_t path[MAX_PATH];
        _snwprintf_s(path, MAX_PATH, _TRUNCATE, L"%ls\\%ls", dir, fd.cFileName);
        if (LoadDictFile(path, &loaded, &hashKeys, &cjkEntries, &lines, &badLines))
            ++files;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    Log("dict: %u files, %u lines, %u keys, %u HASH_ skipped, %u cjk, %u bad, arena %zu/%zu KB",
        files, lines, loaded, hashKeys, cjkEntries, badLines,
        g_arenaUsed >> 10, ARENA_BYTES >> 10);
    *outFiles = files; *outLoaded = loaded; *outHash = hashKeys; *outCjk = cjkEntries;
    return files > 0 && loaded > 0;
}

// ---------- 统计线程 ----------
static DWORD WINAPI StatsThread(LPVOID)
{
    for (;;)
    {
        Sleep(STATS_PERIOD_MS);
        Log("stats: draw hit=%ld miss=%ld | format hit=%ld miss=%ld | wrap=%ld | sub hit=%ld miss=%ld | dumped=%u | fonts=%ld",
            g_hitA, g_missA, g_hitB, g_missB, g_wrapHits, g_hitJ, g_missJ, g_dumpCount, g_fidCacheN);
        // v7.5.2: v7.4 探针统计（early/setText/setTag/refresh/wrapEv）不再输出，计数仍维护;
        //   需要时临时恢复下三行 Log 调试。
        // 周期落盘 dump 收集（替代逐条 fflush, 防切界面卡顿; 崩溃最多丢本轮周期数据）
        if (g_dumpFile)
        {
            AcquireSRWLockExclusive(&g_dumpLock);
            fflush(g_dumpFile);
            ReleaseSRWLockExclusive(&g_dumpLock);
        }
    }
}

// ---------- AOB 定位（v7.6: 取代硬编码 VA, Steam / EPIC 双版本通用） ----------
//   三步: 解析特征文本 -> 在 .text 段扫描并要求唯一命中 -> 从锚点解码全局地址。
//   设计取向: 宁可"定位失败 -> 跳过该 hook"也绝不接受多命中时盲猜, 避免 hook 错函数。
static int HexVal(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

// "48 8B 05 ?? ?? ?? ??" -> pat[] / msk[]
static bool ParseAob(AobSig& s)
{
    const char* p = s.text;
    int n = 0;
    while (*p)
    {
        if (*p == ' ') { ++p; continue; }
        if (*p == '?')
        {
            s.pat[n] = 0; s.msk[n] = 0;
            ++p; if (*p == '?') ++p;
        }
        else
        {
            int hi = HexVal(p[0]);
            int lo = (hi >= 0 && p[1]) ? HexVal(p[1]) : -1;
            if (hi < 0 || lo < 0) { s.len = 0; return false; }
            s.pat[n] = static_cast<uint8_t>((hi << 4) | lo);
            s.msk[n] = 1;
            p += 2;
        }
        if (++n >= static_cast<int>(sizeof(s.pat))) { s.len = 0; return false; }
    }
    s.len = n;
    return n > 0;
}

// 主模块第一个可执行节（.text）的运行时范围
static bool GetTextRange(uint8_t** outBegin, uint8_t** outEnd)
{
    uint8_t* base = reinterpret_cast<uint8_t*>(GetModuleHandleW(nullptr));
    if (!base) return false;
    PIMAGE_DOS_HEADER dos = reinterpret_cast<PIMAGE_DOS_HEADER>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return false;
    PIMAGE_NT_HEADERS nt = reinterpret_cast<PIMAGE_NT_HEADERS>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return false;
    PIMAGE_SECTION_HEADER sec = IMAGE_FIRST_SECTION(nt);
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections; ++i)
    {
        if ((sec[i].Characteristics & IMAGE_SCN_MEM_EXECUTE) && sec[i].SizeOfRawData)
        {
            *outBegin = base + sec[i].VirtualAddress;
            *outEnd   = *outBegin + sec[i].SizeOfRawData;
            return true;
        }
    }
    return false;
}

// 扫描; 最多数到 2 个命中就收工（只关心"是否唯一"）
static uint8_t* AobFind(const AobSig& s, uint8_t* begin, uint8_t* end, int* outHits)
{
    uint8_t* first = nullptr;
    int hits = 0;
    for (uint8_t* p = begin; p + s.len <= end; ++p)
    {
        bool ok = true;
        for (int i = 0; i < s.len; ++i)
        {
            if (s.msk[i] && p[i] != s.pat[i]) { ok = false; break; }
        }
        if (!ok) continue;
        if (!first) first = p;
        if (++hits >= 2) break;
    }
    *outHits = hits;
    return first;
}

static uint8_t* ResolveFn(AobSig& s, uint8_t* tb, uint8_t* te)
{
    if (!ParseAob(s))
    {
        Log("aob %s: BAD PATTERN TEXT", s.name);
        return nullptr;
    }
    int hits = 0;
    uint8_t* f = AobFind(s, tb, te, &hits);
    if (!f)
    {
        Log("aob %s: NOT FOUND (%d bytes pattern), hook skipped", s.name, s.len);
        return nullptr;
    }
    if (hits != 1)
    {
        Log("aob %s: %d+ MATCHES (first %p), AMBIGUOUS -> hook skipped",
            s.name, hits, (void*)f);
        return nullptr;
    }
    Log("aob %s: %p  (RVA +0x%llX, %d bytes pattern)", s.name, (void*)f,
        static_cast<unsigned long long>(f - reinterpret_cast<uint8_t*>(GetModuleHandleW(nullptr))),
        s.len);
    return f;
}

// 从 insn 处（长度 len）取下尾 4 字节 disp32, 算出 rip-relative 目标 VA
//   target = insn + len + disp32  （x64 rip 相对寻址以"下一条指令"为基）
static uint64_t RipRelVA(uint8_t* insn, int len)
{
    int32_t disp = 0;
    memcpy(&disp, insn + len - 4, 4);
    uint64_t addr = reinterpret_cast<uint64_t>(insn + len) + static_cast<int64_t>(disp);
    return addr - reinterpret_cast<uint64_t>(GetModuleHandleW(nullptr)) + GAME_BASE;
}

// 校验锚点偏移处的操作码, 防止"函数体变了但入口还在"导致偏移落错位
static bool ExpectOpc(uint8_t* insn, const uint8_t* opc, int n, const char* what)
{
    for (int i = 0; i < n; ++i)
    {
        if (insn[i] != opc[i])
        {
            Log("aob: %s opcode mismatch at +%d (got %02X want %02X), global unresolved",
                what, i, insn[i], opc[i]);
            return false;
        }
    }
    return true;
}

// 一次性解析全部目标（函数入口 + 引擎全局）
static void ResolveTargets()
{
    uint8_t* tb = nullptr;
    uint8_t* te = nullptr;
    if (!GetTextRange(&tb, &te))
    {
        Log("aob: cannot locate .text section, ALL TARGETS UNRESOLVED");
        return;
    }
    Log("aob: .text %p..%p (%llu KB)",
        (void*)tb, (void*)te,
        static_cast<unsigned long long>((te - tb) >> 10));

    g_fnDrawWide     = ResolveFn(SIG_DRAW_WIDE,     tb, te);
    g_fnFormat       = ResolveFn(SIG_FORMAT,        tb, te);
    g_fnFontLookup   = ResolveFn(SIG_FONT_LOOKUP,   tb, te);
    g_fnTexObj       = ResolveFn(SIG_TEXOBJ,        tb, te);
    g_fnSrvResolve   = ResolveFn(SIG_SRV_RESOLVE,   tb, te);
    g_fnLangCur      = ResolveFn(SIG_LANG_CUR,      tb, te);
    g_fnLangTxt      = ResolveFn(SIG_LANG_TXT,      tb, te);
    g_fnSetText      = ResolveFn(SIG_SET_TEXT,      tb, te);
    g_fnRefresh      = ResolveFn(SIG_TEXTOBJ_REFRESH, tb, te);
    g_fnSubtitle     = ResolveFn(SIG_SUBTITLE_DRAW, tb, te);
    g_fnD3dCtxGetter = ResolveFn(SIG_D3D_CTX_GETTER, tb, te);
    g_fnD3dDevGetter = ResolveFn(SIG_D3D_DEV_GETTER, tb, te);

    // 引擎全局: 由锚点函数内部的 rip-relative 指令反推
    static const uint8_t OPC_CMP_ECX_RIP[2] = { 0x3B, 0x0D };  // cmp ecx, cs:dword_XXX
    static const uint8_t OPC_MOV_RAX_RIP[3] = { 0x48, 0x8B, 0x05 };
    static const uint8_t OPC_MOV_RSI_RIP[3] = { 0x48, 0x8B, 0x35 };

    if (g_fnFontLookup)
    {
        uint8_t* p = g_fnFontLookup + OFF_FONTCOUNT;
        if (ExpectOpc(p, OPC_CMP_ECX_RIP, 2, "FONTCOUNT"))
        {
            g_vaFontCount = RipRelVA(p, LEN_CMP_RIP);
            Log("aob: FONTCOUNT  VA %016llX", static_cast<unsigned long long>(g_vaFontCount));
        }
        p = g_fnFontLookup + OFF_FONTTAB;
        if (ExpectOpc(p, OPC_MOV_RAX_RIP, 3, "FONTTAB"))
        {
            g_vaFontTab = RipRelVA(p, LEN_MOV_RIP);
            Log("aob: FONTTAB    VA %016llX", static_cast<unsigned long long>(g_vaFontTab));
        }
    }
    if (g_fnD3dCtxGetter)
    {
        uint8_t* p = g_fnD3dCtxGetter + OFF_D3D_CONTEXT;
        if (ExpectOpc(p, OPC_MOV_RAX_RIP, 3, "D3D_CONTEXT"))
        {
            g_vaD3dContext = RipRelVA(p, LEN_MOV_RIP);
            Log("aob: D3D_CONTEXT VA %016llX", static_cast<unsigned long long>(g_vaD3dContext));
        }
    }
    if (g_fnD3dDevGetter)
    {
        uint8_t* p = g_fnD3dDevGetter + OFF_D3D_DEVICE;
        if (ExpectOpc(p, OPC_MOV_RSI_RIP, 3, "D3D_DEVICE"))
        {
            g_vaD3dDevice = RipRelVA(p, LEN_MOV_RIP);
            Log("aob: D3D_DEVICE  VA %016llX", static_cast<unsigned long long>(g_vaD3dDevice));
        }
    }
    // 交叉校验: 两版 device 与 context 恒相邻（device 在前, 相差 8）
    if (g_vaD3dDevice && g_vaD3dContext && (g_vaD3dContext - g_vaD3dDevice) != 8)
    {
        Log("aob: WARN d3d slot delta %lld (expected 8), device slot suspect",
            static_cast<long long>(g_vaD3dContext - g_vaD3dDevice));
    }
}

// ---------- 安装 ----------
static bool InstallHook(void* target, const char* name, void* detour, void** orig)
{
    if (!target)
    {
        Log("hook %s: target unresolved (aob miss), skipped", name);
        return false;
    }
    if (MH_CreateHook(target, detour, orig) != MH_OK)
    {
        Log("hook %s: MH_CreateHook failed", name);
        return false;
    }
    if (MH_EnableHook(target) != MH_OK)
    {
        Log("hook %s: MH_EnableHook failed", name);
        return false;
    }
    Log("hook %s @%p installed", name, (void*)target);
    return true;
}

// ---------- 主线程 ----------
static DWORD WINAPI MainThread(LPVOID hSelf)
{
    Log("==== SR3R_I18N v7.6.1: AOB 目标定位 (Steam+EPIC+GOG 通用, PE 指纹判别版本) ====");

    wchar_t dir[MAX_PATH], iniPath[MAX_PATH], dictDir[MAX_PATH], dtxt[MAX_PATH];
    GetModuleFileNameW((HMODULE)hSelf, dir, MAX_PATH);
    wchar_t* slash = wcsrchr(dir, L'\\');
    if (slash) *slash = L'\0'; else *dir = L'\0';
    s_exeBase = reinterpret_cast<uint64_t>(GetModuleHandleW(nullptr));   // v7.4 诊断 RA 换算用

    wcscpy_s(iniPath, dir); wcscat_s(iniPath, L"\\SR3R_I18N.ini");
    LoadConfig(iniPath);

    // --- 版本判别 (PE 指纹, 与文件名彻底解耦; EPIC 官方安装也叫 SRTTR.exe) ---
    wchar_t exeName[MAX_PATH];
    GetModuleFileNameW(nullptr, exeName, MAX_PATH);
    wchar_t* eslash = wcsrchr(exeName, L'\\');
    const wchar_t* exeBase = eslash ? eslash + 1 : exeName;

    uint32_t fTs = 0, fEp = 0, fSoi = 0;
    const ExeBuild* fp = DetectBuildByFp(&fTs, &fEp, &fSoi);

    const ExeBuild* chosen = nullptr;
    const char*     how    = nullptr;
    if (_wcsicmp(g_cfg.exeOverride, L"auto") != 0)
    {
        chosen = FindBuildByName(g_cfg.exeOverride);
        how    = "ini override";
        if (!chosen)
            Log("exe: ini exe=%ls 无效 (应为 auto/steam/gog/epic), 已忽略, 继续自动识别", g_cfg.exeOverride);
    }
    if (!chosen && fp) { chosen = fp; how = "PE fingerprint"; }

    if (chosen)
    {
        g_build = chosen;
        Log("exe: %s (via %s) | file=%ls TimeDateStamp=%08X EntryPoint=%08X SizeOfImage=%08X",
            chosen->name, how, exeBase, fTs, fEp, fSoi);
    }
    else
    {
        Log("exe: !! PE 指纹未命中, 已回退默认 (渲染异常时请把下面这行原文上报作者)");
        Log("exe: !! NEWBUILD file=%ls TimeDateStamp=%08X EntryPoint=%08X SizeOfImage=%08X",
            exeBase, fTs, fEp, fSoi);
    }

    _snwprintf_s(dictDir, MAX_PATH, _TRUNCATE, L"%ls\\%ls", dir, g_cfg.dictDir);
    wcscpy_s(dtxt, dir); wcscat_s(dtxt, L"\\DumpText.dtxt");

    // 1. 初始化（先建 arena/桶, LoadDictDir 直接入表）
    CrcInit();
    g_arena = static_cast<uint8_t*>(VirtualAlloc(nullptr, ARENA_BYTES,
                                                  MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    g_dictBuckets = static_cast<DictNode**>(VirtualAlloc(nullptr, DICT_BUCKETS * sizeof(DictNode*),
                                                          MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    g_dumpBuckets = static_cast<DumpNode**>(VirtualAlloc(nullptr, DUMP_BUCKETS * sizeof(DumpNode*),
                                                          MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    g_origBuckets = static_cast<OrigNode**>(VirtualAlloc(nullptr, ORIG_BUCKETS * sizeof(OrigNode*),
                                                          MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!g_arena || !g_dictBuckets || !g_dumpBuckets || !g_origBuckets)
    {
        Log("alloc failed, abort");
        return 0;
    }
    g_dictMask = DICT_BUCKETS - 1;

    // 2. origin 联表（消息 ID -> 英文原文; 缺失/失败时退化为旧行为, ID/HASH_ 键跳过）
    wchar_t originDir[MAX_PATH];
    _snwprintf_s(originDir, MAX_PATH, _TRUNCATE, L"%ls\\%ls", dir, g_cfg.originDir);
    g_origMask = ORIG_BUCKETS - 1;      // 必须先于 LoadOriginDir（OrigInsert 在加载中就要用）
    if (!LoadOriginDir(originDir))
        g_origBuckets = nullptr;   // 无 origin: OrigLookup 直接返 null, AddDictEntry 走旧行为

    // 3. 词典（scripts\<dict_dir>\*.txt, le_strings 格式; 可选——内核汉化(F1)已接管文本,
    //    词典目录缺失/为空时进 font-only 模式: hooks 照装, 字形层用内置字符集）
    uint32_t files = 0, loaded = 0, hashKeys = 0, cjkEntries = 0;
    bool dictOk = LoadDictDir(dictDir, &files, &loaded, &hashKeys, &cjkEntries);
    if (dictOk)
        InterlockedExchange(&g_dictLoaded, 1);   // 字形层 charset 以词典收集为准
    else
        Log("dict: load failed/skipped, font-only mode (kernel text + builtin charset)");

    // v7.5.1: charlist.txt 合并（内核汉化/官方 le_data 用字补全; 必须在 g_dictReady=1
    //   与 FontFileThread 启动前完成, charset 在此后冻结; 词典缺字如"齿"由此补上）
    if (g_cfg.charlistFile[0])
    {
        wchar_t clPath[MAX_PATH];
        _snwprintf_s(clPath, MAX_PATH, _TRUNCATE, L"%ls\\%ls", dir, g_cfg.charlistFile);
        LoadCharList(clPath);
    }

    // 3. DumpText（ini 可关）
    if (g_cfg.dumpEnabled)
    {
    _wfopen_s(&g_dumpFile, dtxt, L"ab");
    if (g_dumpFile) setvbuf(g_dumpFile, nullptr, _IOFBF, 1 << 20);   // 1MB 全缓冲: 避免 CRT 小块直写
    Log("dump: %ls %s", dtxt, g_dumpFile ? "opened (append)" : "open failed");
    }
    else
    {
        Log("dump: disabled by ini");
    }

    // 4. 目标定位: AOB 扫描函数入口 + 从锚点反推引擎全局（v7.6; 取代硬编码 VA）
    //    必须在 hook 安装与引擎指针绑定之前完成。
    ResolveTargets();

    // 4b. 引擎指针（任一解析失败 -> 该指针留空, 对应功能自动降级而非崩溃）
    if (g_vaFontTab)    g_fontTabPtr   = VA<void***>(g_vaFontTab);
    if (g_vaFontCount)  g_fontCountPtr = VA<volatile int*>(g_vaFontCount);
    if (g_vaD3dDevice)  g_devSlot      = VA<ID3D11Device**>(g_vaD3dDevice);
    if (g_vaD3dContext) g_ctxSlot      = VA<ID3D11DeviceContext**>(g_vaD3dContext);

    // 5. MinHook
    if (MH_Initialize() != MH_OK)
    {
        Log("MH_Initialize failed");
        return 0;
    }
    bool a = InstallHook(g_fnDrawWide,   "DrawWide",   (void*)HookDrawWide,   (void**)&g_origDrawWide);
    bool b = InstallHook(g_fnFormat,     "Format",     (void*)HookFormat,     (void**)&g_origFormat);
    bool c = InstallHook(g_fnFontLookup, "FontLookup", (void*)HookFontLookup, (void**)&g_origFontLookup);
    bool d = InstallHook(g_fnTexObj,     "TexObj",     (void*)HookTexObj,     (void**)&g_origTexObj);
    bool e = InstallHook(g_fnSrvResolve, "SrvResolve", (void*)HookSrvResolve, (void**)&g_origSrvResolve);
    // v7.4 早期整句替换（语言服务返回层; ini lang_early 可关）
    bool f = g_cfg.langEarly && InstallHook(g_fnLangCur, "LangCur",
                                            (void*)HookLangCur, (void**)&g_origLangCur);
    bool g = g_cfg.langEarly && InstallHook(g_fnLangTxt, "LangTxt",
                                            (void*)HookLangTxt, (void**)&g_origLangTxt);
    // v7.4.3 SetText 探针（随 lang_early; 纯诊断只读）
    bool h = g_cfg.langEarly && InstallHook(g_fnSetText, "SetText",
                                            (void*)HookSetText, (void**)&g_origSetText);
    // v7.4.4 文本对象刷新探针（随 lang_early; 纯诊断只读）
    bool i = g_cfg.langEarly && InstallHook(g_fnRefresh, "Refresh",
                                            (void*)HookRefresh, (void**)&g_origRefresh);
    // v7.5 字幕/HUD 绘制入口整串替换（游侠方案同点位; ini subtitle_early 可关）
    bool j = g_cfg.subtitleEarly && InstallHook(g_fnSubtitle, "Subtitle",
                                                (void*)HookSubtitle, (void**)&g_origSubtitle);
    if (!a && !b && !c && !d && !e && !f && !g && !h && !i && !j)
    {
        Log("no hooks installed, idle");
        return 0;
    }

    InterlockedExchange(&g_dictReady, 1);

    // 6. 字体文件后台线程（读 TTF + InitFont）
    CloseHandle(CreateThread(nullptr, 0, FontFileThread, hSelf, 0, nullptr));

    CloseHandle(CreateThread(nullptr, 0, StatsThread, nullptr, 0, nullptr));
    Log("v7.6.1 active: build=%s, dict=%u keys (%u files), hooks A=%d B=%d C=%d D=%d E=%d F=%d G=%d H=%d I=%d J=%d, idling",
        g_build ? g_build->name : "???", g_dictCount, files, (int)a, (int)b, (int)c, (int)d, (int)e, (int)f, (int)g, (int)h, (int)i, (int)j);
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved)
{
    switch (ul_reason_for_call)
    {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hModule);
        InitializeCriticalSection(&g_logCS);
        LogOpen(hModule);
        if (g_log)
        {
			Log("[Info] SR3R Font Extend By HaoJun0823 https://www.haojun0823.xyz | https://github.com/HaoJun0823/SR3R_I18N");
			Log("[DllMain] ATTACH v7");
            CloseHandle(CreateThread(nullptr, 0, MainThread, hModule, 0, nullptr));
        }
        break;
    case DLL_PROCESS_DETACH:
        if (g_dumpFile) { fclose(g_dumpFile); g_dumpFile = nullptr; }
        MH_DisableHook(MH_ALL_HOOKS);
        MH_Uninitialize();
        if (g_log) { fclose(g_log); g_log = nullptr; }
        DeleteCriticalSection(&g_logCS);
        break;
    }
    return TRUE;
}
