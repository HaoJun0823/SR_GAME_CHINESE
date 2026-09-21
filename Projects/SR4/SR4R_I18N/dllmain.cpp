// dllmain.cpp : SR4R_I18N - Saints Row IV 外挂汉化 DLL（v1: 文本替换 + 中文字形层）
//
// v1 = SR3R v7.5.2 移植 + SR4 架构适配
//
//  [文本层]
//   1. 加载 scripts\<dict>\*.txt（le_strings 格式: "英文原文": "中文译文"; SR4R_I18N.ini 可配置）
//   2. Hook A  sub_140DC36A0  宽字符绘制核心   — R9  = 文本指针 -> 查词典替换
//   3. Hook B  sub_140CF9A00  Volition formatter — RDX = 格式串 -> 查词典替换
//   4. DumpText.dtxt 未命中文本去重收集（仅英文，过滤 CJK）
//
//  [字形层]（引擎结构 2026-09-07 IDA 逆向实证）
//   字体对象布局（SR4 cpeg cvbm_pc, ≥592B 头 + 变长区）:
//     +8  u32 字形数  +12 u32 baseChar  +16 i32 missAdvance  +20 u16 行高
//     +22 u16 cell高  +28 i32 全局字距 +32 u32 kern数  +36/+38 i16 顶部/左侧偏移
//     +552 kern表(6B/项{u16 left,u16 right,i8 off})  +560 metrics(16B/项)
//     +568 u32 texId  +576 xtab(u32/项 图集X)  +584 ytab(u32/项 图集Y)
//   metrics 16B/项: +0 i32 advance  +4 i32 cell宽  +12 i16 kern起始idx(-1=无)
//   渲染(DrawWide): quad宽=metrics[+4], quad高=font[+22], UV=(xtab,ytab)+cell尺寸
//   (SR3R 对比: kern +168→+552, metrics +176→+560, texId +184→+568, xtab +192→+576, ytab +200→+584)
//
//   方案:
//   5. Hook C sub_140BF8550 字体对象查询 -> 命中含中文文本的字体时返回"伪字体对象"
//      （count 扩为 0xFFE0 覆盖 0x20..0xFFFF; 官方槽码区 metrics/xtab/ytab 照抄,
//        中文字符槽码=cp-0x20 填新图集坐标; kern 表直接指官方）
//   6. Hook D sub_140B7AF30 纹理对象查询 -> MAGIC texId 返回伪纹理对象（宽/高）
//   7. Hook E sub_140E2C9E0 texId->SRV 解析 -> MAGIC texId 返回自建图集 SRV
//      MAGIC = 0x60000000 + fontId: bit24=0 不入动态纹理分支, 远超纹理注册数
//   8. 两阶段构建（避免渲染线程长卡顿）:
//      后台线程: stb_truetype 按 cellH 光栅化字符集 -> 位图缓存
//      渲染线程: 首次绘制该字体时读回官方图集(staging) + 拼接中文区
//                + CreateTexture2D/SRV + 组装伪对象（~15ms）
//   9. 官方图集原样 blit 保留 -> 未翻译内容(Credits 等)渲染不变
//
//  [字幕注入]（Hook J: 字幕/HUD 绘制入口）
//   签名: double(__fastcall*)(const wchar_t*, float, double, int) — 宽字符, 返回 double
//   Steam 0x1403D18F0 / GOG 0x1404B1E00（两版逐字节同源, size 0x458=1112）
//   入口整串替换为中文整句, 引擎按 CJK 宽度自然折行
//   注意: 引擎另有一个 char* 版同名孪生函数（Steam 0x140476D80 / GOG 0x140574250,
//         size 0x3CF=975, 签名 __int64(uint,int,int64,int64,int)）—— 那不是本 hook 的落点。
//         旧版 CFG_GOG 误填了它的 GOG 对应物, 导致 GOG 字幕 hook 静默失效。

//
// 安全性:
//   - 伪对象不在 fontTab, 引擎卸载遍历不到, 无双重释放
//   - HookFontLookup 校验 fontTab[slot]==构建时官方指针, 引擎若重建字体自动回退
//     （fontTab 不可用时改用 FontLookup(slot) 复核, 语义等价）
//   - 词典加载失败 -> 只装文本 hook 未装字体 hook, 行为=纯文本替换
//   - hook 落点由 AOB 扫描定位（主模块可执行段内要求唯一命中）, 失败回落到版本 VA 表,
//     任一路径都需通过入口特征码校验, 防游戏更新后错位
//   - 引擎全局变量(fontTab/fontCount/D3D)运行时自解, 未知构建下失效即**降级不崩溃**
//     （详见 §运行时自解引擎全局变量）

#include "pch.h"
#include <intrin.h>          // _ReturnAddress (v7.4 Format 调用点分类诊断)
#include <MinHook.h>
#include <d3d11.h>

#define STB_TRUETYPE_IMPLEMENTATION
#include "stb_truetype.h"
#include "charset_data.h"

// ---------- 配置 ----------
static constexpr uint64_t GAME_BASE = 0x140000000ULL;

// ======================================================================
//  AOB 定位（2026-09-12 重构）
//  ---------------------------------------------------------------------
//  旧实现: 硬编码 VA（Steam/GOG 两套）+ 入口 16 字节校验。
//  旧实现的两个问题:
//    (1) 16 字节特征码在双版本里并非唯一 —— DrawWide 有 2 个命中(宽字符版 / char 版孪生),
//        LangCur 有 3 个命中。若直接拿它做全模块扫描会产生歧义。
//    (2) GOG 版 Subtitle 沿用了技术文档 §6.2 中已被推翻的旧定位(char* 版, size 0x3CF),
//        实际 Steam 版 hook 的是 wchar_t 版(size 0x458)。特征码校验失败 -> 字幕 hook 静默不装。
//  新实现: 主模块可执行段扫描为主(要求恰好 1 命中), VA 表降级为回退。
//          特征码长度取「双版本各自唯一」所需的最小长度, 相对地址字节一律 mask 通配。
//          解析规则见 Tools/gen_aob2.py（可重跑复现全部特征码）。
// ======================================================================

struct AobSig {
    const char*    name;
    const uint8_t* sig;
    const uint8_t* mask;   // nullptr = 全部精确匹配; 否则 mask[i]==0 跳过该字节
    size_t         len;
    // --- L2 放宽特征码（L1 落空时才尝试） ---
    //   L1 从函数入口逐字节精确; 但其中若干字节是**编译期布局字段**:
    //     lea rbp,[rsp-imm32]   栈帧大小        mov eax,imm32   __chkstk 帧大小
    //     mov rax,[rip+disp32]  /GS cookie 位置 call rel32      目标地址
    //     mov [rsp+imm8],reg    参数溢出槽
    //   同一份源码换编译器/优化档, 或加一个局部变量, 这些 imm 就会变, 而操作码
    //   序列不变。L2 = 只保留指令结构, 把上述字段通配。
    //   生成/校验脚本: Tools/gen_aob_relaxed.py
    const uint8_t* sig2;
    const uint8_t* mask2;
    size_t         len2;
    // --- L3 换代构建精确特征码（L1/L2 都落空时才尝试） ---
    //   L2 解决的是「同一代编译器, 帧尺寸/地址类字段漂移」; 它解决不了
    //   「换了一代编译器, 前导指令形态不同」。Microsoft Store 版
    //   (sriv.exe, TDS=5E58CEF8, 2020-02, 比三版早三年) 的 Format / Subtitle
    //   正是后者:
    //     新构建: push rbp; push rsi; push rdi; push r15;
    //             lea rbp,[rsp-2F88h]; mov eax,3088h; call __chkstk; sub rsp,rax
    //     MS 构建: mov [rsp+20h],r9; mov [rsp+18h],r8; mov [rsp+8],rcx  ← 参数
    //             push rbp; push rbx; push rsi; push r14                home 区
    //             lea rbp,[rsp-2F58h]; mov eax,3058h; call __chkstk; sub rsp,rax
    //                                                                  先落地
    //   **指令长度本身就不同** ⇒ 字节级 mask 无论怎么放宽都不可能同时覆盖两代。
    //   所以这一层是「构建族各一套精确特征码」。它是**纯精确**的(mask3=nullptr):
    //   同一构建内 RIP 相对位移与 rel32 都不随 ASLR 变化, 无需通配。
    //   其余 6 条 hook 在 MS Store 上 L1 直接命中(游戏日志 + 裸段 dump 双向验证),
    //   故只有 Format / Subtitle 带这一层。
    const uint8_t* sig3;
    const uint8_t* mask3;
    size_t         len3;
};

// --- 特征码（长度与 mask 均为双版本唯一性验证后定稿） ---
// A DrawWide: 48B。消歧点在 [0x21..]: 44 0F 29 74 24 70 = 宽字符版;
//   char 版孪生函数为 44 0F 29 AC 24 80 00 00 00（尾部 +0x45 是 66 83 3B 00 vs 80 3B 00）。
//   [44..47] = call rel32 -> FontLookup，通配。
static const uint8_t SIG_DRAW_WIDE[48] = {
    0x40,0x53,0x56,0x57,0x48,0x81,0xEC,0x10,0x01,0x00,0x00,0x8B,0xBC,0x24,0x60,
    0x01,0x00,0x00,0x49,0x8B,0xD9,0x0F,0x29,0xB4,0x24,0xF0,0x00,0x00,0x00,0x8B,
    0xCF,0x44,0x0F,0x29,0x74,0x24,0x70,0x0F,0x28,0xF2,0x44,0x0F,0x28,0xF1,
    0x00,0x00,0x00,0x00 };
static const uint8_t MASK_DRAW_WIDE[48] = {
    1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,1,1,1,1,1,1,
    1,1,1,1,1,1,1,1,1,1,1,1,1,1, 0,0,0,0 };

// B Format: 16B 已足够，两版各 1 命中
static const uint8_t SIG_FORMAT[16] = {
    0x40,0x55,0x56,0x57,0x41,0x57,0x48,0x8D,0xAC,0x24,0x78,0xD0,0xFF,0xFF,0xB8,0x88 };

// C FontLookup: 16B 已足够
static const uint8_t SIG_FONT_LOOKUP[16] = {
    0x83,0xF9,0xFF,0x7D,0x3E,0x8D,0x81,0xFF,0xFF,0xFF,0x7F,0x83,0xF8,0xFF,0x7E,0x2E };

// D TexObj: [10..13] = RIP disp32（字体表计数比较），通配
static const uint8_t SIG_TEXOBJ[16] = {
    0x4C,0x63,0xC1,0x85,0xC9,0x78,0x77,0x44,0x3B,0x05,0,0,0,0,0x7D,0x6E };
static const uint8_t MASK_TEXOBJ[16] = {
    1,1,1,1,1,1,1,1,1,1,0,0,0,0,1,1 };

// E SrvResolve: 16B 已足够
static const uint8_t SIG_SRV_RESOLVE[16] = {
    0x40,0x53,0x48,0x83,0xEC,0x20,0x0F,0xB6,0xDA,0x83,0xF9,0xFF,0x74,0x4F,0x0F,0xBA };

// F LangCur: 27B（16B 有 3 命中）。[3..6] = RIP disp32 -> 语言服务单例，通配。
//   语义: mov rax,[singleton]; test; jz; mov rdx,[rax+8] (vtable[1]); jmp rdx; xor eax,eax; ret
static const uint8_t SIG_LANG_CUR[27] = {
    0x48,0x8B,0x05,0,0,0,0,0x48,0x85,0xC0,0x74,0x0C,0x48,0x8B,0x50,0x08,
    0x48,0x85,0xD2,0x74,0x03,0x48,0xFF,0xE2,0x33,0xC0,0xC3 };
static const uint8_t MASK_LANG_CUR[27] = {
    1,1,1,0,0,0,0,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,1,1,1 };

// G LangTxt: 16B 已足够。[3..6] = RIP disp32，通配
static const uint8_t SIG_LANG_TXT[16] = {
    0x48,0x8B,0x05,0,0,0,0,0x48,0x85,0xC0,0x74,0x0B,0x48,0x8B,0x10,0x48 };
static const uint8_t MASK_LANG_TXT[16] = {
    1,1,1,0,0,0,0,1,1,1,1,1,1,1,1,1 };

// J Subtitle: 16B 已足够（wchar_t 版，Steam 0x1403D18F0 / GOG 0x1404B1E00）
static const uint8_t SIG_SUBTITLE_DRAW[16] = {
    0x4C,0x8B,0xDC,0x55,0x56,0x41,0x54,0x49,0x8D,0xAB,0xA8,0xFB,0xFF,0xFF,0x48,0x81 };

// ---------------------------------------------------------------------
//  K 资源挂载表注册器 (v1.9 起改为「直接调用」) —— loose le_string 加载核心
// ---------------------------------------------------------------------
//  目标函数: sub_140B7E7A0(int type, int param, char insertFront)
//    Steam VA = 0x140B7E7A0（其它构建由 AOB 定位）
//
//  【引擎资源解析架构 —— 为什么 loose 文件会被 vpp 盖掉】
//    所有资源(xtbl / le_strings / …)都经由统一打开器 sub_140B82670 解析:
//      ① 表为空(dword_146998D80==0) -> 直接失败, 没有兜底
//      ② 遍历挂载表 sub_140B81070, 按「表序」逐项尝试, **首个成功即 return**
//      ③ 每项 = {u32 type, u32 param, u8 enabled} (12B/项, 最多 16 项)
//           type==0 -> 磁盘 loose 探测 (sub_140C09400 / GetFileAttributesExA)
//           type==1 -> vpp_pc 查询 (param==6 走流式 sub_140B82AD0)
//           type==2 -> 容器2   (sub_140BB3860)
//           type==4 -> 容器4   (sub_140C092E0, SR4 独有)
//      ④ param 匹配规则: 请求 param == -1(0xFFFFFFFF) 表示「通配任意 param」;
//         否则必须 == 表项 param 才参与 (sub_140B81070 @ loc_140B81128)。
//         ★ le_string 请求正好走 param == -1 通配路径
//           (sub_140B82670 开头 movzx r15d,r9b, 调用方 sub_1404B5E20 传 -1)
//
//    挂载表由 WinMain -> sub_140235550 一次性建立, 注册顺序 (= 表序):
//       [0] {0,0} 磁盘   <- **insertFront 硬编码 0** (xor r8d,r8d @ 0x14023597d)
//       [1] {2,0}       <- insertFront = dil
//       [2] {edi,0}     <- insertFront = dil
//       [3] {edi,6}     <- insertFront = dil   (vpp 流式)
//       [4] {4,0}       <- insertFront = dil
//    其它项都插队(dil=1)而磁盘项排在原位(0) -> 磁盘项被挤到**表尾**,
//    于是 vpp 永远先命中, loose 文件形同虚设 —— 这正是实测 "loose 无效" 的根因。
//
//  【v1.9 的做法: 直接调用注册器, 不再 hook】
//    v1.8 曾用 MinHook 拦截注册器改写 insertFront, 但实测 mrCalls=0 ——
//    ASI Loader 注入 DllMain 时 WinMain 早已跑完注册, hook 永不触发。**已废弃。**
//
//    v1.9 改为: 直接调 sub_140B7E7A0(0, 0, 1) —— 表里已有 {0,0} 磁盘项,
//    insertFront=1 会把它从表尾前移到表头 [0]。
//    sub_140B7E7A0 的既有语义（IDA 实测）:
//        在表中按 (type,param) 线性查到下标 v6;
//        if (insertFront && v6 != 0) { 把 [0..v6-1] 整体后移一格; 本项写到 [0]; }
//        v6==0 或 insertFront==0 时原位置不动（幂等, 可重复调用）。
//    → 这是引擎自带代码, 零改写风险, 也不需要 MinHook。
//
//    为什么 DllMain 里调就来得及: 挂载表是**运行时活表**, 我们只需赶在
//    第一次资源访问之前把表头换掉, 不必抢在注册之前。
//
//  特征码: 32B —— 逐字节对齐 IDA 实测的 sub_140B7E7A0 序言:
//    +0  48 89 5C 24 08   mov [rsp+arg_0], rbx
//    +5  48 89 74 24 10   mov [rsp+arg_8], rsi
//    +10 57               push rdi
//    +11 48 83 EC 20      sub rsp, 20h
//    +15 8B D9            mov ebx, ecx        ; type  <- 第1参数
//    +17 41 0F B6 F0      movzx esi, r8b      ; ★ insertFront <- 第3参数
//    +21 48 8D 0D xx*4    lea rcx, [临界区]   ; rip disp32 @[24..27] 通配
//    +28 8B FA            mov edi, edx        ; param <- 第2参数
//    +30 E8 xx*4          call EnterCriticalSection ; rel32 @[31..34] 通配
//  L1 只取前 31 字节（到 call 操作码 E8 为止）—— 再多一个字节就要碰 call 的
//  相对偏移，偏移值会随构建变化, 通配不掉。唯一性锚点 = 序言 + movzx(第3参数)
//  + lea rcx 临界区 + mov edi(第2参数) + call。
//
//  挂载表两个全局地址（用于日志核对 / 直接读表）由函数体内部 rip disp32 解出:
//    +0x23 : 44 8B 15 disp32  mov r10d, cs:XXX  长 7 -> 目标 = fn+0x2A+d = 【计数】
//    +0x35 : 48 8D 05 disp32  lea rax,  [XXX]   长 7 -> 目标 = fn+0x3C+d = 【数组+4】
//    ★ 注意这两个偏移与 SR3R 不同（SR3R 是 +0x30 / +0x3F）—— SR3R 序言多一条
//      mov [rsp+20h],-2 且 xor r9d 的位置也不同, 导致整体偏移错位。切勿跨版本套用。
//  ★ 通配区间必须是 lea 的 disp32 = 下标 0x18..0x1B（共 4 项）。
//     曾经的错版把区间整体前移一格(0x17..0x1A) —— 下标 0x17 是 lea 的操作码 0x0D
//     被误标成通配(该精确却不查), 下标 0x1B 是 disp32 末字节被要求 == 0x00。
//     disp32 > 0xFFFFFF 时末字节非 0 -> L1 永不命中, 而旧地址恰好末字节为 0 时
//     侥幸命中, 导致 bug 长期潜伏。
//  ★ 铁律: 数组长度必须 == AOB_ENTRY 声明的 len(=31)。原先写死 [32] 而只填 31 项,
//     第 32 项被 C 隐式补 0 —— 编译器不报错, 是最阴的一类缺陷。现改为 [ ] 自动定长
//     + 下方 static_assert 强校验, 让「长度不符」在编译期就报错。
static const uint8_t SIG_MOUNT_REG[] = {
    0x48,0x89,0x5C,0x24,0x08,0x48,0x89,0x74,0x24,0x10,0x57,0x48,0x83,0xEC,0x20,0x8B,
    0xD9,0x41,0x0F,0xB6,0xF0,0x48,0x8D,0x0D,0x00,0x00,0x00,0x00,0x8B,0xFA,0xE8 };
static const uint8_t MASK_MOUNT_REG[] = {
    1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1, 0,0,0,0, 1,1,1 };
static_assert(sizeof(SIG_MOUNT_REG) == 31,  "SIG_MOUNT_REG 必须 31 字节 (见 AOB_ENTRY MOUNT_REG)");
static_assert(sizeof(MASK_MOUNT_REG) == 31, "MASK_MOUNT_REG 必须 31 字节 (见 AOB_ENTRY MOUNT_REG)");

// ---------------------------------------------------------------------
//  K3 资源挂载表注册器 —— MS Store 换代构建精确特征码（L3）
// ---------------------------------------------------------------------
//  ★ 为什么必须有这一层（2026-09-20 实测, MS Store loose 失效的真根因）:
//    MS Store (sriv.exe, TDS=5E58CEF8, 2020-02) 比 Steam/GOG/EPIC 早三年,
//    **换了一代 MSVC**。同一份源码编出的注册器:
//      新构建(Steam/GOG/EPIC): mov [rsp+8],rbx; mov [rsp+0x10],rsi; push rdi
//                              sub rsp,0x20; mov ebx,ecx; movzx esi,r8b; ...
//      MS 构建               : push rdi; sub rsp,0x30
//                              mov [rsp+0x20],-2   <- SEH 展开槽(老 MSVC 才有)
//                              mov [rsp+0x40],rbx; mov [rsp+0x48],rsi
//                              movzx esi,r8b; mov edi,edx; mov ebx,ecx; ...
//    **指令序列本身不同 ⇒ 字节级 mask 无论怎么放宽都不可能同时覆盖两代。**
//    实测: 新构建的 17B 全精确序言在 MS dump 命中 **0 次**。
//    → 只能为 MS 构建另备一套「纯精确」特征码（同代内 rel32/disp32 不随 ASLR 变化）。
//
//  语义已逐条与 Steam 版对照, 确认是同一函数（仅编译布局不同）:
//    表项步长 add rax,0xc (12B) / 上限 cmp r10d,0x10 (16 项) / movsd 后移循环 /
//    mov bl,1 返回 true / test sil,sil 的 insertFront 分支 —— 全部一致。
//  离线断言: MS dump 恰好 1 命中 @0x140DF0AB0; Steam/GOG/EPIC 各 0 命中（不干扰）。
//
//  ★ 2026-09-20: R3_MOUNTREG 已**改由 Tools/gen_aob_relaxed.py 生成**并随
//    aob_l2_arrays.inc 一并提供（与 R3_FORMAT / R3_SUBTITLE 同处）。原因:
//    本条曾是全项目唯一的「手写 AOB 项」, 正因如此漏了 L3 —— 手写项不经过
//    生成器的四构建强制校验, 极易漏层。并入脚本后, 每次重生成都会自动重申
//    四构建断言（MS 恰好 1 / 新族各 0）, 事故无法复发。
//    生成器同时机械推导出 R2_MOUNTREG/RM_MOUNTREG, 与旧手写 mask **逐位一致**,
//    故旧手写版本已退役 —— 现全部来自 inc, 本文件不再定义。

// 注册器内部相对偏移（**按构建族不同, 运行时从 g_exe 判定**）
//   新构建族 (Steam/GOG/EPIC): 0x11 / 0x23 / 0x35
//   MS Store (老 MSVC)       : 0x19 / 0x30 / 0x3F
//   ★ SR3R 又是另一套(0x19/0x30/0x3F 之外), 切勿跨版本套用。
static constexpr ptrdiff_t MOUNTREG_OFF_MOVZX_ESI_NEW = 0x11;   // movzx esi, r8b
static constexpr ptrdiff_t MOUNTREG_OFF_COUNT_INSN_NEW = 0x23;  // mov r10d, cs:计数
static constexpr ptrdiff_t MOUNTREG_OFF_ARRAY_INSN_NEW = 0x35;  // lea rax, cs:数组+4
static constexpr ptrdiff_t MOUNTREG_OFF_MOVZX_ESI_MS = 0x19;    // MS: movzx esi, r8b
static constexpr ptrdiff_t MOUNTREG_OFF_COUNT_INSN_MS = 0x30;   // MS: mov r10d, cs:计数
static constexpr ptrdiff_t MOUNTREG_OFF_ARRAY_INSN_MS = 0x3F;   // MS: lea rax, cs:数组+4

// 定位时按「命中该地址的那一层」选偏移 —— 由 LocateMountReg 填充
static ptrdiff_t g_mrOffMovzx = MOUNTREG_OFF_MOVZX_ESI_NEW;
static ptrdiff_t g_mrOffCount = MOUNTREG_OFF_COUNT_INSN_NEW;
static ptrdiff_t g_mrOffArray = MOUNTREG_OFF_ARRAY_INSN_NEW;

// ---------------------------------------------------------------------
//  L2/L3 多层特征码 —— 由 Tools/gen_aob_relaxed.py 生成并校验（请勿手改）
//  L2 通配原则: 只通配「地址/尺寸字段」(栈帧 imm、rip disp32、call rel32、
//   参数溢出槽 disp8), 绝不碰操作码/ModRM。窗口里的 0 是通配占位, 不参与比较。
//  L3 为换代构建的**纯精确**特征码（无 mask）。
//  重新生成: python Tools/gen_aob_relaxed.py
// ---------------------------------------------------------------------
#include "aob_l2_arrays.inc"

#define AOB_ENTRY(nm, s, m, l, s2, m2, l2, s3, m3, l3) \
    static const AobSig AOB_##nm = { #nm, s, m, l, s2, m2, l2, s3, m3, l3 }

AOB_ENTRY(DRAW_WIDE,   SIG_DRAW_WIDE,    MASK_DRAW_WIDE,   48, R2_DRAWWIDE,   RM_DRAWWIDE,   48, nullptr, nullptr, 0);
AOB_ENTRY(FORMAT,      SIG_FORMAT,       nullptr,          16, R2_FORMAT,     RM_FORMAT,     48, R3_FORMAT,   nullptr, 48);
AOB_ENTRY(FONT_LOOKUP, SIG_FONT_LOOKUP,  nullptr,          16, R2_FONTLOOKUP, RM_FONTLOOKUP, 48, nullptr, nullptr, 0);
AOB_ENTRY(TEXOBJ,      SIG_TEXOBJ,       MASK_TEXOBJ,      16, R2_TEXOBJ,     RM_TEXOBJ,     48, nullptr, nullptr, 0);
AOB_ENTRY(SRV_RESOLVE, SIG_SRV_RESOLVE,  nullptr,          16, R2_SRVRESOLVE, RM_SRVRESOLVE, 48, nullptr, nullptr, 0);
AOB_ENTRY(LANG_CUR,    SIG_LANG_CUR,     MASK_LANG_CUR,    27, R2_LANGCUR,    RM_LANGCUR,    27, nullptr, nullptr, 0);
AOB_ENTRY(LANG_TXT,    SIG_LANG_TXT,     MASK_LANG_TXT,    16, R2_LANGTXT,    RM_LANGTXT,    27, nullptr, nullptr, 0);
AOB_ENTRY(SUBTITLE,    SIG_SUBTITLE_DRAW,nullptr,          16, R2_SUBTITLE,   RM_SUBTITLE,   48, R3_SUBTITLE, nullptr, 48);
// K 资源挂载表注册器 (v1.9 loose le_string) —— 见下方 ApplyLooseFirst 说明
//   ★ 注意本条的 L1/L2 长度与其它 hook 不同:
//     L1 = 31（收在 call 操作码处, 不含 call 的 rel32 —— 那个偏移会随构建变）
//     L2 = 48（放宽窗口）
//   L1/L2/L3 全部来自 aob_l2_arrays.inc + 上方 SIG_/MASK_MOUNT_REG（脚本托管）。
//   L3 = MS Store 换代构建精确码（老 MSVC, 序言形态不同）—— 见行 240 附近的说明。
AOB_ENTRY(MOUNT_REG,   SIG_MOUNT_REG,    MASK_MOUNT_REG,   31, R2_MOUNTREG,   RM_MOUNTREG,   48, R3_MOUNTREG, nullptr, 48);


// 字符集扩充配置
static constexpr size_t  CHARLIST_MAX_BYTES = (1 << 20);   // 1MB 上限
static constexpr uint32_t CHARLIST_FREQ_BASE = 60000;      // 权重基值(>词典真实频率上限, 保证 charlist 顺序优先)

// --- VA 地址表 (两套: Steam / GOG) ---
struct ExeConfig {
    bool        isGog;
    uint64_t    vaDrawWide;
    uint64_t    vaFormat;
    uint64_t    vaFontLookup;
    uint64_t    vaTexObj;
    uint64_t    vaSrvResolve;
    uint64_t    vaLangCur;
    uint64_t    vaLangTxt;
    uint64_t    vaSubtitleDraw;
    uint64_t    vaFontTab;
    uint64_t    vaFontCount;
    uint64_t    vaD3dDevice;
    uint64_t    vaD3dContext;
};

// Steam (sr_hv.exe) — IDA 2026-09-07/08 实测
static constexpr ExeConfig CFG_STEAM = {
    false,
    0x140DC36A0ULL,  // DrawWide
    0x140CF9A00ULL,  // Format
    0x140BF8550ULL,  // FontLookup
    0x140B7AF30ULL,  // TexObj
    0x140E2C9E0ULL,  // SrvResolve
    0x140CF1980ULL,  // LangCur
    0x140CF1960ULL,  // LangTxt
    0x1403D18F0ULL,  // SubtitleDraw
    0x146AE7060ULL,  // fontTab
    0x146AE504CULL,  // fontCount
    0x147667C70ULL,  // D3DDevice
    0x147667C78ULL,  // D3DContext
};

// GOG (sr_hv_gog.exe) — IDA 2026-09-08 实测 + 2026-09-12 复核修正
//   注意: vaSubtitleDraw 原填 0x140574250 是错的 —— 那是 char* 版字幕函数
//   (size 0x3CF=975, 签名 __int64(uint,int,int64,int64,int)), 与技术文档 §6.2 里
//   已被推翻的旧定位 sub_140476D80 对应。实际应为 0x1404B1E00
//   (size 0x458=1112, 签名 double(__int64,float,double,int), 处理 wchar_t),
//   与 Steam 版 0x1403D18F0 逐字节同源。现改为 AOB 优先定位, 此表仅作回退。
static constexpr ExeConfig CFG_GOG = {
    true,
    0x140D4E520ULL,  // DrawWide
    0x140C88810ULL,  // Format
    0x140BBBBB0ULL,  // FontLookup
    0x140B99ED0ULL,  // TexObj
    0x140DC6E00ULL,  // SrvResolve
    0x140C88120ULL,  // LangCur
    0x140C88100ULL,  // LangTxt
    0x1404B1E00ULL,  // SubtitleDraw  (修正: 原 0x140574250 为 char* 版, 签名不符)
    0x14698A5D0ULL,  // fontTab
    0x14698A5C8ULL,  // fontCount
    0x14761DEF8ULL,  // D3DDevice
    0x14761DF00ULL,  // D3DContext
};

// EPIC (sr_hv_epic.exe) — 2026-09-12 实测
//   hook VA: 8 个定稿 AOB 在 EPIC .text 全部唯一命中 (Tools/scan_epic.py)
//   全局变量: fontTab/fontCount 由 IDA 反编译 EPIC FontLookup(0x140BF3D00) 证实;
//             D3DDevice/Context 由 EPIC D3D11 创建函数 sub_140E4A070 内
//             "qword_14764DF70=ppDevice; qword_14764DF78=ppImmediateContext" 证实。
//             推导脚本: Tools/epic_globals.py / epic_d3d.py / epic_ripdiag.py
static constexpr ExeConfig CFG_EPIC = {
    false,
    0x140DBE570ULL,  // DrawWide
    0x140CF48D0ULL,  // Format
    0x140BF3D00ULL,  // FontLookup
    0x140B766D0ULL,  // TexObj
    0x140E278B0ULL,  // SrvResolve
    0x140CEC850ULL,  // LangCur
    0x140CEC830ULL,  // LangTxt
    0x1403D0850ULL,  // SubtitleDraw (wchar_t 版, size 0x458, 签名 double(int64,float,double,int))
    0x146ACD308ULL,  // fontTab     (qword_146ACD308)
    0x146ACD304ULL,  // fontCount   (dword_146ACD304, = fontTab-4; 注意 Steam/GOG 布局不同)
    0x14764DF70ULL,  // D3DDevice
    0x14764DF78ULL,  // D3DContext
};

// 运行时填充 (MainThread 中检测 exe 后设置)
static ExeConfig g_exe;

// Microsoft Store (磁盘 sriv_microsoft.exe → 进程内 sriv.exe) — 2026-09-17 逆向定位
//   指纹: TimeDateStamp=5E58CEF8 EntryPoint=00FC44FC SizeOfImage=07E6D000
//         构建时间 2020-02, 比 Steam/GOG/EPIC 早三年 —— **换了一代 MSVC**。
//   来历: 磁盘文件是 MSIXVC 密文(非授权进程只读到随机字节), 离线取不到任何地址;
//         这张表由 ① v1.2 游戏日志里 6 条 `hook ... installed (via aob)` 的命中地址
//         + ② DLL 落盘的可执行段裸映像(SR4R_dump/SR4R_dump_text.bin) 的语义锚点
//         共同反推出来, 详见 Tools/gen_aob_relaxed.py 顶部注释。
//   全局变量四字段**故意留 0**: 本构建的 fontTab/fontCount/D3D 槽从未离线取到。
//         它们已被「运行时自解」覆盖(日志 globals: dynamic resolve OK);
//         留 0 会被调用处当作「无可信静态值」从而不走 VA 回退, 避免解引用垃圾指针。
static constexpr ExeConfig CFG_MSSTORE = {
    false,
    0x140EF6D40ULL,  // DrawWide   （v1.2 日志 aob 命中地址）
    0x140E92DA0ULL,  // Format     （裸段语义锚点: mov r8d,0FFDFh + movabs r11,3FF000100000200h）
    0x140E0A530ULL,  // FontLookup （v1.2 日志 aob 命中地址）
    0x140DEAF00ULL,  // TexObj     （v1.2 日志 aob 命中地址）
    0x1411DFE30ULL,  // SrvResolve （v1.2 日志 aob 命中地址）
    0x140E92770ULL,  // LangCur    （v1.2 日志 aob 命中地址）
    0x140E92750ULL,  // LangTxt    （v1.2 日志 aob 命中地址）
    0x140488A60ULL,  // SubtitleDraw（裸段语义锚点: ÷3/÷9 魔法数共现 + 参数搬运形态）
    0ULL,            // fontTab    — 未知, 依赖运行时自解
    0ULL,            // fontCount  — 未知, 依赖运行时自解
    0ULL,            // D3DDevice  — 未知, 依赖 SRV 反查
    0ULL,            // D3DContext — 未知, 依赖 SRV 反查
};

// ============ 构建识别：按【内容】而非文件名 ============
//   [!] 为什么不能按文件名判:
//       EPIC 版官方安装的可执行文件同样叫 `sr_hv.exe`(与 Steam 同名),
//       仓库里的 `sr_hv_epic.exe` 只是当初为做 AOB 分析而复制改名的产物。
//       → 按文件名判别时 EPIC 必然落进 else 分支被当成 Steam,
//         于是 hook 走 AOB 仍正确, 但走配置表的全局变量(fontTab/fontCount/
//         D3DDevice/D3DContext)全部指向 Steam 的地址 → 字形表读成垃圾 →
//         文字完全不显示(不崩溃、不报错, 极难定位)。
//   指纹 = TimeDateStamp + AddressOfEntryPoint + SizeOfImage。
//   实测三版互异(2026-09-12 由 Tools 脚本从 PE 头提取):
//       Steam  sr_hv.exe      0x642AD908 0x013635C4 0x07CF8000
//       GOG    sr_hv_gog.exe  0x63FCCC27 0x0130DC54 0x07CAD000
//       EPIC   sr_hv.exe      0x65DE7B82 0x0135E464 0x07CDE000
//       MSStore sriv.exe      0x5E58CEF8 0x00FC44FC 0x07E6D000  (MSIXVC, 2020-02)
//   [!] MSStore 的 EP/SizeOfImage 来自游戏进程内读到的内存镜像头(磁盘是密文),
//       而它的 SizeOfImage 与三版差得远(0x07E6D000 vs 0x07CFxxxx) —— 判别无歧义。
//   匹配策略(顺序很重要):
//     1) **主键 = FileHeader.TimeDateStamp** —— 时间戳在加载后不会被任何人改写,
//        是唯一可靠的字段。
//     2) AddressOfEntryPoint / SizeOfImage 只作**消歧加分项, 不作硬性要求**。
//        原因: 部分 DRM / 加壳 / 自建 loader 会在【内存中】改写入口点甚至映像大小
//        (磁盘文件不变)。实测本项目的真身 harness 就会改写这两个字段 ——
//        若强求三字段全等, 那类环境会被误判为"指纹未命中"而回退。
//     3) 时间戳为 0 (如 /Brepro 可重现构建) 时, 退回用 EP+SOI 全等匹配。
//   如需支持新构建: 看日志 "指纹未命中" 那行给出的三个值, 加一条即可。
struct BuildFp
{
    const wchar_t*   key;          // ini `exe=` 可用值
    const char*      name;         // 日志标签
    uint32_t         timeStamp;
    uint32_t         entryPoint;
    uint32_t         sizeOfImage;
    const ExeConfig* cfg;
};

static const BuildFp kBuildFp[] = {
    { L"steam", "Steam", 0x642AD908u, 0x013635C4u, 0x07CF8000u, &CFG_STEAM   },
    { L"gog",   "GOG",   0x63FCCC27u, 0x0130DC54u, 0x07CAD000u, &CFG_GOG     },
    { L"epic",  "EPIC",  0x65DE7B82u, 0x0135E464u, 0x07CDE000u, &CFG_EPIC    },
    { L"msstore", "MSStore", 0x5E58CEF8u, 0x00FC44FCu, 0x07E6D000u, &CFG_MSSTORE },
};

static const BuildFp* FindBuildByName(const wchar_t* n)
{
    for (const BuildFp& b : kBuildFp)
        if (_wcsicmp(b.key, n) == 0) return &b;
    return nullptr;
}

// 读主模块 PE 头判别构建。
//   返回命中的构建描述(未命中返回 nullptr), 三字段与匹配得分回填供日志/上报。
//   得分 = 1(时间戳命中) + 1(入口点相符) + 1(映像大小相符); 时间戳为 0 时走 EP+SOI 全等。
static const BuildFp* DetectBuildByFp(uint32_t* outTs, uint32_t* outEp, uint32_t* outSoi,
                                      int* outScore)
{
    if (outTs)    *outTs    = 0;
    if (outEp)    *outEp    = 0;
    if (outSoi)   *outSoi   = 0;
    if (outScore) *outScore = 0;

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

    const BuildFp* best      = nullptr;
    int            bestScore = 0;
    for (const BuildFp& b : kBuildFp)
    {
        if (ts != 0)
        {
            if (b.timeStamp != ts) continue;          // 主键不符 -> 淘汰
            int score = 1;
            if (b.entryPoint  == ep)  ++score;        // 加分(可能被 DRM 改写, 不强制)
            if (b.sizeOfImage == soi) ++score;
            if (score > bestScore) { bestScore = score; best = &b; }
        }
        else
        {
            if (b.entryPoint == ep && b.sizeOfImage == soi)
            {
                bestScore = 1; best = &b; break;      // 时间戳不可用: 要求 EP+SOI 全等
            }
        }
    }
    if (outScore) *outScore = bestScore;
    return best;
}

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

// 字幕折行重组（引擎按字幕框宽度 word-wrap 后逐行查词典, 完整句 key 永远 miss）
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

// ======================================================================
//  运行时自解引擎全局变量（2026-09-17 新增, 面向「未知构建」）
//  ---------------------------------------------------------------------
//  动机: 版本 VA 表只能覆盖已知构建。Microsoft Store / MSIXVC 版的主程序在
//        授权进程之外读取只能拿到密文（全局熵 8.0000、无 MZ/PE 头、无明文字符串）,
//        离线根本取不到地址; 未来任何新构建同理。故改为「运行时从已定位的
//        hook 函数体内反解」。
//  原理: AOB 定位到 FontLookup 后, 该函数本体在 Steam / GOG / EPIC 三版中
//        字节布局完全一致 —— 只有 RIP 相对位移不同。于是可在函数头 0x100 字节内
//        按掩码模式匹配「引用全局的那条指令」, 由 disp32 反算全局地址。
//  已验证(Tools/verify_runtime_globals.py 逐条比对硬编码 VA 表, 三版全部吻合):
//      FontLookup+0x43  cmp ecx,[rip+disp]  -> fontCount
//      FontLookup+0x57  mov rax,[rip+disp]  -> fontTab
//  ---------------------------------------------------------------------
//  注意: 本机制只解决「全局变量」。hook 落点本身仍靠 §AOB 扫描。
// ======================================================================

// 指针 -> GAME_BASE 相对 VA（VA() 的逆运算; 结果可直接喂回 VA<T>()）
static inline uint64_t VaOfPtr(const void* p)
{
    return reinterpret_cast<uint64_t>(p)
         - reinterpret_cast<uint64_t>(GetModuleHandleW(nullptr)) + GAME_BASE;
}

// 掩码匹配: msk[k]==0 的字节跳过。返回首个命中首地址, 未命中返回 nullptr。
static const uint8_t* FindMasked(const uint8_t* p, size_t span,
                                 const uint8_t* pat, const uint8_t* msk, size_t n)
{
    if (!p || span < n) return nullptr;
    for (size_t i = 0; i + n <= span; ++i)
    {
        size_t k = 0;
        for (; k < n; ++k)
            if ((!msk || msk[k]) && p[i + k] != pat[k]) break;
        if (k == n) return p + i;
    }
    return nullptr;
}

// fontTab 取用点: mov rax,[rip+disp32]; movsxd rcx,ecx; mov rax,[rax+rcx*8]; ret
//   尾部 48 8B 04 C8 C3 是「返回 fontTab[id]」的定式, 足以排除孪生引用
static const uint8_t PAT_FONTTAB[15] = {
    0x48,0x8B,0x05, 0,0,0,0, 0x48,0x63,0xC9,0x48,0x8B,0x04,0xC8, 0xC3 };
static const uint8_t MSK_FONTTAB[15] = {
    1,1,1, 0,0,0,0, 1,1,1,1,1,1,1, 1 };

// fontCount 取用点: cmp ecx,[rip+disp32]; jge; test ecx,ecx; jns; mov rax,[rip+disp32]
static const uint8_t PAT_FONTCOUNT[15] = {
    0x3B,0x0D, 0,0,0,0, 0x7D,0, 0x85,0xC9,0x79,0, 0x48,0x8B,0x05 };
static const uint8_t MSK_FONTCOUNT[15] = {
    1,1, 0,0,0,0, 1,0, 1,1,1,0, 1,1,1 };

// 由「RIP 相对内存引用指令」的地址反算目标全局地址（GAME_BASE 相对 VA）
//   instr      = 指令首地址; instrLen = 该指令长度（disp32 恒为最后 4 字节）
static inline uint64_t RipTargetVa(const uint8_t* instr, size_t instrLen)
{
    int32_t disp = 0;
    memcpy(&disp, instr + instrLen - 4, 4);
    return VaOfPtr(instr + instrLen + disp);
}

struct DynGlobals { uint64_t fontTab; uint64_t fontCount; bool resolved; };

// 自解 fontTab / fontCount; 任一条失败即 resolved=false（调用方决定回退策略）
static DynGlobals ResolveFontGlobalsDyn(uint64_t fontLookupVa)
{
    DynGlobals r{ 0, 0, false };
    const uint8_t* fn = VA<const uint8_t*>(fontLookupVa);
    if (!fn) return r;

    constexpr size_t SPAN = 0x100;   // FontLookup 本体仅 0x66 字节
    const uint8_t* pTab = FindMasked(fn, SPAN, PAT_FONTTAB,   MSK_FONTTAB,   sizeof(PAT_FONTTAB));
    const uint8_t* pCnt = FindMasked(fn, SPAN, PAT_FONTCOUNT, MSK_FONTCOUNT, sizeof(PAT_FONTCOUNT));
    if (pTab) r.fontTab   = RipTargetVa(pTab, 7);   // mov rax,[rip+disp32] = 7 字节
    if (pCnt) r.fontCount = RipTargetVa(pCnt, 6);   // cmp ecx,[rip+disp32] = 6 字节

    // 落点必须落在主模块映像内, 否则判为不可信（防模式误匹配到别处）
    HMODULE base = GetModuleHandleW(nullptr);
    const uint8_t* bp = reinterpret_cast<const uint8_t*>(base);
    auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(bp);
    if (!dos || dos->e_magic != IMAGE_DOS_SIGNATURE) { r.fontTab = r.fontCount = 0; return r; }
    const IMAGE_NT_HEADERS64* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(bp + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE ||
        nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC)
    { r.fontTab = r.fontCount = 0; return r; }

    const uint64_t lo = GAME_BASE;
    const uint64_t hi = GAME_BASE + nt->OptionalHeader.SizeOfImage;
    if (r.fontTab   && (r.fontTab   < lo || r.fontTab   >= hi)) r.fontTab   = 0;
    if (r.fontCount && (r.fontCount < lo || r.fontCount >= hi)) r.fontCount = 0;

    r.resolved = (r.fontTab != 0 && r.fontCount != 0);
    return r;
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
    wcscat_s(path, L"SR4R_I18N.log");
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

// ---------- 配置（SR4R_I18N.ini, 与 asi 同目录; 缺失时用默认值） ----------
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
    int      textDump;            // v1.3: 可执行段落盘 0=关 1=总是 2=auto(仅定位失败时)
    bool     looseFirst;          // v1.9: 挂载表磁盘项插队(loose 文件优先于 vpp_pc)
    wchar_t  exeOverride[16];     // 强制指定构建 (auto/steam/gog/epic/msstore); 默认 auto=按 PE 指纹识别
};

static Config g_cfg = {
    L"dict",                       // 默认: scripts/dict/
    L"origin",                     // 默认: scripts/origin/
    L"SourceHanSansHWSC-VF.ttf",   // 默认字体
    L"charlist.txt",               // 默认: scripts/charlist.txt
    false,                         // dump 默认关
    true,                          // lang_early
    false,                         // early_diag 默认关
    true,                          // subtitle_early
    2,                             // text_dump = auto
    true,                          // loose_first = 开（loose 资源优先于 vpp_pc）
    L"auto",                       // exe: 按 PE 指纹自动识别
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
        else if (_wcsicmp(key, L"text_dump") == 0)
        {
            // 0/off/no = 关; 1/on/yes/always = 总是; auto 或其它 = 仅定位失败时
            if (*val == L'0' || !_wcsicmp(val, L"off") || !_wcsicmp(val, L"no"))      g_cfg.textDump = 0;
            else if (*val == L'1' || !_wcsicmp(val, L"on") || !_wcsicmp(val, L"yes") ||
                     !_wcsicmp(val, L"always"))                                       g_cfg.textDump = 1;
            else                                                                      g_cfg.textDump = 2;
        }
        else if (_wcsicmp(key, L"loose_first") == 0)  g_cfg.looseFirst = (*val != L'0');
        else if (_wcsicmp(key, L"exe") == 0)  wcsncpy_s(g_cfg.exeOverride, val, _TRUNCATE);

        line = wcstok_s(nullptr, L"\r\n", &ctx);
    }
    VirtualFree(wbuf, 0, MEM_RELEASE);
    Log("cfg: %ls loaded (dict_dir=%ls origin_dir=%ls font_file=%ls charlist=%ls dump=%d early=%d diag=%d sub=%d textdump=%d loose=%d exe=%ls)",
        iniPath, g_cfg.dictDir, g_cfg.originDir, g_cfg.fontFile, g_cfg.charlistFile, (int)g_cfg.dumpEnabled,
        (int)g_cfg.langEarly, (int)g_cfg.earlyDiag, (int)g_cfg.subtitleEarly, g_cfg.textDump,
        (int)g_cfg.looseFirst, g_cfg.exeOverride);
}

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
            size_t tlen0 = r ? wcslen(r->trans) : 0;
            if (r && tlen0 >= 4 && tlen0 < WRAP_MAX_CHARS)
            {
                wmemcpy(g_wrap.full, cand, fullLen + 1);
                g_wrap.fullLen = fullLen;
                g_wrap.preLen  = g_wrap.pendLen;
                g_wrap.sufAt   = sufAt;
                g_wrap.node    = r;
                g_wrap.pendLen = 0;

                size_t tlen = tlen0;
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
            size_t tlen1 = r ? wcslen(r->trans) : 0;
            if (r && tlen1 >= 4 && tlen1 < WRAP_MAX_CHARS)
            {
                // 命中 -> 进入稳定态: 存完整原文/折行位置, 预计算两行译文
                wmemcpy(g_wrap.full, cand, fullLen + 1);
                g_wrap.fullLen = fullLen;
                g_wrap.preLen  = g_wrap.pendLen;
                g_wrap.sufAt   = sufAt;
                g_wrap.node    = r;
                g_wrap.mode    = WRAP_STABLE;
                g_wrap.pendLen = 0;

                size_t tlen = tlen1;
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

// 自学习槽位表: slot -> 官方字体对象。HookFontLookup 每次调用都会带
// (fontId, 官方对象) 进来顺手记录, ResolveSlot 即可反查 —— 使字形层在
// 「未知构建 + 自解失败 + 无 fontTab」时依然可用。fontId 即 fontTab 下标
// （已由三版反汇编证实: FontLookup(id) 就是 return fontTab[id]）。
static void*         g_slotMap[FAKE_FONT_MAX];
static volatile LONG g_slotMapN = 0;

// ---------- D3D 设备/上下文获取（2026-09-17 改为版本无关） ----------
//  旧做法: 直接读游戏 .data 里的 D3D 全局槽（VA 表）—— 未知构建下必然读错。
//  新做法: 优先用任意 D3D COM 对象反查 —— 官方图集 SRV 本身就是 ID3D11DeviceChild,
//          GetDevice() 必然拿到创建它的设备, 再取 immediate context 即可。
//          这条路径与构建/版本无关, 无需任何离线逆向。
//  版本 VA 表仅作兜底, 且只在「指纹命中已知构建」时才可信（未识别构建下
//  MainThread 会把 g_devSlot/g_ctxSlot 置空, 使兜底自动失效）。
//  引用计数: 只取一次并存进程级缓存, 不做 Release（生命周期同进程）。
static ID3D11Device*        g_devCache = nullptr;
static ID3D11DeviceContext* g_ctxCache = nullptr;

static void EnsureD3D(ID3D11ShaderResourceView* srv,
                      ID3D11Device** outDev, ID3D11DeviceContext** outCtx)
{
    if (!g_devCache || !g_ctxCache)
    {
        // 1) 版本无关路径: SRV -> device -> immediate context
        if (srv)
        {
            ID3D11Device* d = nullptr;
            srv->GetDevice(&d);
            if (d)
            {
                ID3D11DeviceContext* c = nullptr;
                d->GetImmediateContext(&c);
                if (c)
                {
                    g_devCache = d;
                    g_ctxCache = c;
                    Log("d3d: device/context captured from SRV (version-agnostic path)");
                }
                else
                {
                    d->Release();
                }
            }
        }
        // 2) 兜底: 版本 VA 表
        if ((!g_devCache || !g_ctxCache) && g_devSlot && g_ctxSlot)
        {
            ID3D11Device*        d = *g_devSlot;
            ID3D11DeviceContext* c = *g_ctxSlot;
            if (d && c)
            {
                g_devCache = d;
                g_ctxCache = c;
                Log("d3d: device/context taken from VA table (fallback, known build only)");
            }
        }
    }
    *outDev = g_devCache;
    *outCtx = g_ctxCache;
}

// 原函数
using FontLookup_t = void* (__fastcall*)(int);
using TexObj_t     = void* (__fastcall*)(int);
using SrvResolve_t = void* (__fastcall*)(unsigned int, char);
static FontLookup_t g_origFontLookup = nullptr;
static TexObj_t     g_origTexObj     = nullptr;
static SrvResolve_t g_origSrvResolve = nullptr;

// =====================================================================
// v1.5 诊断: hook 存活探针 + 文本/返回值采样（仅 early_diag=1 时输出）
//   动机: MS Store 与 Steam 两版日志里 format hit 恒为 0（3442 次调用零命中）,
//   而 Format 收到的串形如 MENU_BACK / PLT_MENU_WINDOWED —— 都是 le_string 的
//   **本地化 KEY**（见 resource/le_string/{en,zh}/platform_pc_us.txt、menu_us.txt）,
//   而词典键是英文原文。于是必须区分三种可能:
//     (1) 引擎在这条链路上传的是 KEY（解析发生在别处）-> 词典方向根本不对
//     (2) 挂错了函数（Format 落点是别的 swprintf 类函数）-> 输入全是内部/调试串
//     (3) 词典表本身查不中 -> 由 LoadDictDir 末尾的 dict-selftest 排除
//   手段: 采样 Format 的**调用者模块内偏移**(ra=+0x…, 可与 Steam 侧已知调用点常量
//   RA_FMT_* 或 MS dump 反汇编直接对上) + 采样 F/G 语言服务的**返回串**。
// =====================================================================
enum { HC_A = 0, HC_B, HC_C, HC_D, HC_E, HC_J, HC_N };
static volatile LONG g_hookCalls[HC_N];     // 各 hook 被调用次数（仅 SR4R_DIAG_RUNTIME=1 时写入）

// =====================================================================
// v1.7: 运行时探针总开关（默认 0 = 整块编译期关闭）
//   依据: v1.5 与 v1.6 均在日志最后一行 "alive: TEXOBJ (first call)" 之后立刻崩溃,
//         而 v1.4 在同一位置正常; 且两版相对 v1.4 的差异只有本块探针。
//   关闭后运行路径回到 v1.4 等价（hook 入口不再有 Log / 文件 IO / 互斥等待）。
//   需要诊断时把 0 改成 1 重新构建（届时才会生成那些采样日志行）。
// =====================================================================
#define SR4R_DIAG_RUNTIME 0

#if SR4R_DIAG_RUNTIME

static void DiagAlive(const char* name, volatile LONG* once, volatile LONG* counter)
{
    InterlockedIncrement(counter);
    if (InterlockedIncrement(once) == 1)
        Log("alive: %s  (first call)", name);
}

static void DiagStr(char* dst, size_t cap, const wchar_t* s)
{
    size_t w = 0;
    for (const wchar_t* p = s; *p && w + 6 < cap; ++p)
    {
        wchar_t c = *p;
        if      (c == L'\\')            { dst[w++] = '\\'; dst[w++] = '\\'; }
        else if (c == L'"')             { dst[w++] = '\\'; dst[w++] = '"';  }
        else if (c == L'\n')            { dst[w++] = '\\'; dst[w++] = 'n';  }
        else if (c == L'\r')            { dst[w++] = '\\'; dst[w++] = 'r';  }
        else if (c == L'\t')            { dst[w++] = '\\'; dst[w++] = 't';  }
        else if (c >= 0x20 && c < 0x7F) dst[w++] = (char)c;
        else if (c < 0x20)              dst[w++] = '.';
        else                            dst[w++] = '#';   // 非拉丁字符: 占位（避免日志编码歧义）
    }
    dst[w] = '\0';
}

// 形如 MENU_BACK / PLT_MENU_WINDOWED / HASH_A1360200 —— 判据: 仅 [A-Z0-9_]
static bool DiagKeyLike(const wchar_t* s)
{
    bool any = false;
    for (const wchar_t* p = s; *p; ++p)
    {
        any = true;
        if (!((*p >= L'A' && *p <= L'Z') || (*p >= L'0' && *p <= L'9') || *p == L'_'))
            return false;
    }
    return any;
}

static uint64_t DiagModBase()
{
    return reinterpret_cast<uint64_t>(GetModuleHandleW(nullptr));
}

#define DIAG_FMT_SAMPLE 64
static volatile LONG g_fmtSampleN = 0;
static void DiagFmtSample(uint64_t raCaller, const wchar_t* s)
{
    if (!g_cfg.earlyDiag || !s || !*s) return;
    LONG n = InterlockedIncrement(&g_fmtSampleN);
    if (n > DIAG_FMT_SAMPLE) return;
    char buf[256];
    DiagStr(buf, sizeof(buf), s);
    Log("fmt#%-3ld len=%-4zu ra=+0x%-8llX %s\"%s\"", n, wcslen(s),
        (unsigned long long)(raCaller - DiagModBase()),
        DiagKeyLike(s) ? "[KEYLIKE] " : "", buf);
}

#define DIAG_EARLY_SAMPLE 32
static volatile LONG g_earlySampleN = 0;

// v1.6 崩溃修复: 原版只校验"指针首字节所在页可读", 然后一路读最多 96 个 wchar_t(192 B)
//   —— 指针若落在已提交区尾部, 读会跨进下一页(可能未提交/ PAGE_GUARD) => 访问违例。
//   改为向内核问"从 p 起连续可读多少字节", 并把扫描严格限制在该区间内。
static size_t DiagReadableSpan(const void* p)
{
    if (!p) return 0;
    MEMORY_BASIC_INFORMATION mbi;
    if (VirtualQuery(p, &mbi, sizeof(mbi)) != sizeof(mbi)) return 0;
    if (mbi.State != MEM_COMMIT) return 0;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return 0;
    uint64_t base = reinterpret_cast<uint64_t>(mbi.BaseAddress);
    uint64_t end  = base + mbi.RegionSize;
    uint64_t cur  = reinterpret_cast<uint64_t>(p);
    if (cur >= end) return 0;
    return static_cast<size_t>(end - cur);
}

// 尝试把参数当 wchar_t* 读出可打印串。判据: 首字符可打印, 且在"连续可读区间"内、
//   96 字符以内见到 NUL。成功 => 该参数是"文本指针"; 失败 => 大概率是哈希/整数。
static bool DiagWideAt(uint64_t p, char* out, size_t cap)
{
    if (p < 0x10000) return false;
    size_t span = DiagReadableSpan(reinterpret_cast<const void*>(p));
    if (span < sizeof(wchar_t) * 2) return false;      // 连两个 wchar 都读不齐 -> 放弃
    const wchar_t* s = reinterpret_cast<const wchar_t*>(p);
    size_t limit = span / sizeof(wchar_t);
    if (limit > 96) limit = 96;
    if (s[0] < 0x20 || s[0] == 0xFFFF) return false;
    size_t n = 0;
    while (n < limit && s[n]) ++n;
    if (n == 0 || n >= limit) return false;            // 0 长 / 区间内未见 NUL -> 不当串读
    DiagStr(out, cap, s);
    return true;
}

static void DiagEarlySample(const char* which, const wchar_t* s,
                            uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4)
{
    if (!g_cfg.earlyDiag) return;
    LONG n = InterlockedIncrement(&g_earlySampleN);
    if (n > DIAG_EARLY_SAMPLE) return;

    char buf[256];
    if (s) DiagStr(buf, sizeof(buf), s); else buf[0] = '\0';
    Log("early#%-3ld %-8s a1=0x%-12llX a2=0x%llX a3=0x%llX a4=0x%llX ret=\"%s\"",
        n, which, (unsigned long long)a1, (unsigned long long)a2,
        (unsigned long long)a3, (unsigned long long)a4, buf);

    char k1[256], k2[256], k3[256];
    if (DiagWideAt(a1, k1, sizeof(k1))) Log("         %-8s a1(string) -> \"%s\"", which, k1);
    if (DiagWideAt(a2, k2, sizeof(k2))) Log("         %-8s a2(string) -> \"%s\"", which, k2);
    if (DiagWideAt(a3, k3, sizeof(k3))) Log("         %-8s a3(string) -> \"%s\"", which, k3);
}

#else   // SR4R_DIAG_RUNTIME == 0

// --- 关闭态: 空桩。让调用点保持原样, 但目标码里不产生任何字符串 / 文件 IO / 互斥等待 ---
static void DiagAlive(const char*, volatile LONG*, volatile LONG*) {}
static void DiagFmtSample(uint64_t, const wchar_t*) {}
static void DiagEarlySample(const char*, const wchar_t*,
                            uint64_t, uint64_t, uint64_t, uint64_t) {}

#endif  // SR4R_DIAG_RUNTIME

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

// slotHint: fontId 已知时直接给槽位号（免去反查, 也免去对 fontTab 的依赖）;
//           未知传 0xFFFFFFFF, 由 ResolveSlot 反查。
static void EnsureFontReady(void* off, uint32_t slotHint)
{
    LONG n = g_ptrCacheN; if (n > 16) n = 16;
    for (LONG i = 0; i < n; ++i)
    {
        if (g_ptrCache[i].off != off) continue;
        return;   // D3D 阶段已移至后台线程, 渲染线程只读 state 不推进（防卡顿）
    }
    uint32_t slot = (slotHint < FAKE_FONT_MAX) ? slotHint : ResolveSlot(off);
    if (slot >= FAKE_FONT_MAX) return;
    FakeFont* f = &g_fake[slot];
    LONG idx = InterlockedIncrement(&g_ptrCacheN) - 1;
    if (idx < 16) { g_ptrCache[idx].off = off; g_ptrCache[idx].f = f; }
    if (f->state == 0) RequestFont(slot);
}

static void* __fastcall HookFontLookup(int fontId)
{
    static volatile LONG once = 0;
    DiagAlive("FONT_LOOKUP", &once, &g_hookCalls[HC_C]);

    void* off = g_origFontLookup(fontId);
    if (!off) return off;

    // fontId 即槽位号（三版反汇编证实 FontLookup(id) == fontTab[id]）;
    // 顺手记进自学习表, 供 ResolveSlot 在无 fontTab 时反查
    uint32_t slotHint = (fontId >= 0 && (uint32_t)fontId < FAKE_FONT_MAX)
                        ? (uint32_t)fontId : 0xFFFFFFFFu;
    if (slotHint != 0xFFFFFFFFu)
    {
        g_slotMap[slotHint] = off;
        if (g_slotMapN <= (LONG)slotHint) InterlockedExchange(&g_slotMapN, (LONG)slotHint + 1);
    }

    // 触发/推进升级（菜单文本走 sub_14016E7D0 渲染器, 不经 DrawWide,
    // 故在此公共必经点触发; 英文渲染不受影响, 官方 cell 照抄）
    if (g_stbReady && g_dictReady) EnsureFontReady(off, slotHint);

    // 已 live 的伪对象替换（校验官方对象仍在槽位）
    uint32_t slot = (fontId >= 0 && (uint32_t)fontId < FAKE_FONT_MAX)
                    ? (uint32_t)fontId : ResolveSlot(off);
    if (slot < FAKE_FONT_MAX)
    {
        FakeFont* f = &g_fake[slot];
        if (f->state == 2)
        {
            // 官方对象仍在槽位时才替换。
            //   优先用 fontTab[slot]（最快）; fontTab 不可用（未知构建自解失败）时
            //   改用 FontLookup(slot) 复核 —— 二者语义等价（FontLookup 就是返回
            //   fontTab[id]）, 因此该路径完全不依赖版本 VA 表。
            void* cur = g_fontTabPtr ? (*g_fontTabPtr)[slot]
                                     : g_origFontLookup((int)slot);
            if (cur == f->official) return f->obj;
            f->state = 4;   // 引擎重建了字体, 回退官方
        }
    }
    return off;
}

// ---------- Hook D: 纹理对象查询 ----------
static void* __fastcall HookTexObj(int texId)
{
    static volatile LONG once = 0;
    DiagAlive("TEXOBJ", &once, &g_hookCalls[HC_D]);

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
    static volatile LONG once = 0;
    DiagAlive("SRV_RESOLVE", &once, &g_hookCalls[HC_E]);

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
    // 容量自适应收窄（仅当默认容量装不下当前字符集时）
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
        uint32_t offTexId = *(uint32_t*)((uint8_t*)off + 568);   // SR4: texId @ +568 (SR3R was +184)
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

    int      offCount = *(int*)(off + 8);
    uint32_t offTexId = *(uint32_t*)(off + 568);   // SR4: texId @ +568 (SR3R was +184)
    void*    offMet   = *(void**)(off + 560);       // SR4: metrics @ +560 (SR3R was +176)
    void*    offXtab  = *(void**)(off + 576);       // SR4: xtab @ +576 (SR3R was +192)
    void*    offYtab  = *(void**)(off + 584);       // SR4: ytab @ +584 (SR3R was +200)
    if (!offMet || !offXtab || !offYtab || offTexId == 0xFFFFFFFFu)
    { Log("font%u: official blob bad, abort", fontId); f->state = 4; return false; }

    // 官方 SRV -> 纹理 -> 尺寸/格式
    ID3D11ShaderResourceView* offSrv =
        static_cast<ID3D11ShaderResourceView*>(g_origSrvResolve(offTexId, 0));
    if (!offSrv) { Log("font%u: official SRV null (texId=%u), retry", fontId, offTexId); f->state = 3; return false; }

    // D3D 设备/上下文: 优先由 offSrv 反查（版本无关）, 版本 VA 表只作兜底
    ID3D11Device*        dev = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    EnsureD3D(offSrv, &dev, &ctx);
    if (!dev || !ctx) { Log("font%u: d3d not ready, retry later", fontId); f->state = 3; return false; }

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

    // 4) 伪字体对象 blob: 592B 头 + metrics(16B) + xtab(4B) + ytab(4B)
    //    SR4 头大小 592B (SR3R was 208B): kern +552, metrics +560, texId +568, xtab +576, ytab +584
    //    +39..+551 间字段（图集名等）照抄官方, 不修改
    size_t FONT_HDR_SIZE = 592;
    size_t blobSize = FONT_HDR_SIZE + (size_t)FAKE_GLYPHS * 16 + (size_t)FAKE_GLYPHS * 4 * 2;
    uint8_t* obj = static_cast<uint8_t*>(VirtualAlloc(nullptr, blobSize,
                                                      MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    if (!obj) { Log("font%u: blob alloc failed", fontId); f->srv->Release(); f->srv = nullptr; f->tex->Release(); f->tex = nullptr; f->state = 4; return false; }
    memset(obj, 0, blobSize);

    memcpy(obj, off, FONT_HDR_SIZE);                           // 头照抄（行高/cellH/偏移/图集名/kern 指针域+计数）
    *(int*)(obj + 8)   = (int)FAKE_GLYPHS;          // count: 覆盖 0x20..0xFFFF
    *(int*)(obj + 12)  = (int)FONT_BASECHAR_DEFAULT;// baseChar=0x20（照官方）
    *(uint32_t*)(obj + 568) = MAGIC_TEXID_BASE + fontId;  // SR4: texId @ +568

    uint8_t* met = obj + FONT_HDR_SIZE;
    uint8_t* xt  = met + (size_t)FAKE_GLYPHS * 16;
    uint8_t* yt  = xt  + (size_t)FAKE_GLYPHS * 4;
    *(void**)(obj + 560) = met;   // SR4: metrics @ +560
    *(void**)(obj + 576) = xt;    // SR4: xtab @ +576
    *(void**)(obj + 584) = yt;    // SR4: ytab @ +584
    // kern 表(+552 指针/+32 计数) 已随头照抄, 指官方 blob（官方槽码不变, kern 语义保持）

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
    if (!off) return 0xFFFFFFFFu;

    // 1) 自学习表：HookFontLookup 每次调用都带 (fontId, 官方对象) 进来,
    //    顺手记下; 反查即可。**这条路径完全不依赖 fontTab**, 未知构建(MS Store 版)
    //    在自解失败时仍能正常升级字形。
    LONG n = g_slotMapN;
    if (n > (LONG)FAKE_FONT_MAX) n = (LONG)FAKE_FONT_MAX;
    for (LONG i = 0; i < n; ++i)
        if (g_slotMap[i] == off) return (uint32_t)i;

    // 2) 版本 fontTab 表（仅指纹命中的已知构建可用）
    if (!g_fontTabPtr || !g_fontCountPtr) return 0xFFFFFFFFu;
    int m = *g_fontCountPtr;
    if (m < 0) return 0xFFFFFFFFu;
    if (m > (int)FAKE_FONT_MAX) m = (int)FAKE_FONT_MAX;
    for (int i = 0; i < m; ++i)
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
    static volatile LONG once = 0;
    DiagAlive("DRAW_WIDE", &once, &g_hookCalls[HC_A]);

    if (text && *text)
    {
#if SR4R_DIAG_RUNTIME
        if (g_cfg.earlyDiag && InterlockedCompareExchange(&once, 0, 0) <= 6)
        {
            char buf[256];
            DiagStr(buf, sizeof(buf), text);
            Log("drawin#%-3ld fontId=%-4u len=%-4zu \"%s\"",
                once, fontId, wcslen(text), buf);
        }
#endif
        const DictNode* r = LookupNode(text, &g_hitA, &g_missA);
        if (r) text = r->trans;
        // 文本含中文字符（无论替换来自 Hook A 还是 Hook B）-> 确保该字体已升级
        if (TextNeedsGlyphs(text)) EnsureFontFor(fontId);
    }
    return g_origDrawWide(a1, x, y, text, scale, flag, fontId, a8);
}

// =====================================================================
// v1.6 安全闸: 词典命中时译文是【整串替换】fmt, 而紧随其后的 g_origFormat 仍用调用方的
//   args/argc。若译文引入了原文没有的 % 转换符（典型: 裸 KEY "HUD_DROP_ITEM_MESSAGE"
//   -> 译文 "丢弃 %ls"），格式化器会去读一个并不存在的参数 -> 野指针崩溃。
//   规则: 译文按类别统计的 % 转换符数量必须 <= 原文。
//   这是第二道防线（第一道在 Tools/gen_keydict.py 的 S1 规则里，直接不生成这类条目）。
// =====================================================================
static bool FmtSpecChar(wchar_t c)
{
    return (c >= L'a' && c <= L'z') || (c >= L'A' && c <= L'Z') ||
           (c >= L'0' && c <= L'9') || c == L'.' || c == L'*' ||
           c == L'-' || c == L'+' || c == L'#' || c == L' ';
}

static void FmtSpecTally(const wchar_t* s, int* c)
{
    for (const wchar_t* p = s; *p; )
    {
        if (*p != L'%') { ++p; continue; }
        ++p;
        if (*p == L'%') { ++p; continue; }        // %% 是字面百分号, 不消耗参数
        wchar_t last = 0;
        while (*p && FmtSpecChar(*p)) { last = *p; ++p; }
        int k;
        switch (last)
        {
        case L'c': case L'C':                        k = 0; break;
        case L's': case L'S':                        k = 1; break;
        case L'd': case L'i': case L'u':             k = 2; break;
        case L'x': case L'X': case L'o':             k = 3; break;
        case L'f': case L'F': case L'e': case L'E':
        case L'g': case L'G': case L'a': case L'A':  k = 4; break;
        default:                                     k = 5; break;  // 未知/畸形/未终止(% 在串尾)
        }
        ++c[k];
    }
}

static bool FmtSpecOk(const wchar_t* orig, const wchar_t* trans)
{
    int co[6] = {0}, ct[6] = {0};
    FmtSpecTally(orig, co);
    FmtSpecTally(trans, ct);
    for (int i = 0; i < 6; ++i)
        if (ct[i] > co[i]) return false;
    return true;
}

using Format_t = __int64(__fastcall*)(wchar_t*, const wchar_t*, unsigned long long, void*, unsigned int);
static Format_t g_origFormat = nullptr;

static __int64 __fastcall HookFormat(wchar_t* dst, const wchar_t* fmt,
                                      unsigned long long cap, void* args, unsigned int argc)
{
    // 必须先捕获: _ReturnAddress() 在函数入口 = Format 真正调用者的返回地址
    // （任何后续 call 都会覆盖它 -> 归因必须用此刻的值）
    uint64_t raCaller = reinterpret_cast<uint64_t>(_ReturnAddress());
    static volatile LONG once = 0;
    DiagAlive("FORMAT", &once, &g_hookCalls[HC_B]);

    if (fmt && *fmt)
    {
        DiagFmtSample(raCaller, fmt);          // v1.5: 前 64 条输入 + 调用者偏移
        const DictNode* r = LookupNode(fmt, &g_hitB, &g_missB);
        if (r)
        {
            // v1.6: 不安全译文一律不替换 —— 宁可显示英文/KEY, 也不能让格式化器越界读参
            if (FmtSpecOk(fmt, r->trans))
            {
                fmt = r->trans;
            }
            else
            {
                static volatile LONG rejB = 0;
                if (g_cfg.earlyDiag && InterlockedIncrement(&rejB) <= 5)
                    Log("fmt: REJECT unsafe trans (%% spec mismatch) key=\"%.40ls\"", fmt);
            }
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
    // SR4: 调用点常量待确认, 暂仅计数 idx0
    InterlockedIncrement(&g_fmtFrom[0]);
}

// 返回层整句替换共享逻辑。约定:
//   - 词典未就绪(g_dictReady=0)或空串: 原样
//   - 含 % / \n: 模板或显式多行, 交给 Format hook / 引擎, 不提前拦
//   - 长度 <4(图标/占位) 或 >= WRAP_MAX_CHARS(超长): 原样
//   命中: 返回译文(中文整句, 引擎布局自行按 CJK 切行); miss: 返回原文。
// ---------- 裸 KEY 兜底 (direction A, 2026-09-18) ----------
// 引擎在 LANG_TXT 查表失败时会把 KEY 本身当文本返回(sub_1402A61C0 行为, SR4 / SR4-Re 一致)。
// MS Store 版因所选语言表的覆盖缺口远大于 Steam, 大量 KEY 回落成裸键上屏。
// 此处对 LANG_TXT 返回值做「裸键形态」识别, 命中内置 KEY->中文 表则直接返回中文,
// 使后续 Format / 绘制链路拿到已是中文的串, 彻底消除裸键(与 MS Store 选了哪种语言无关)。
// 注: 下表为按 KEY 命名语义推断的草稿, 翻译需 haojun 审阅; 不在表中的裸键仍原样放行以暴露缺口。
// 有意不收录: EMPTY(语义空, 不该翻)、F11 / WWWWWWWWWWWWWWWW / XXXX(调试/测试串)。
static const wchar_t* g_keyZh[][2] = {
    { L"OPTION_YES",                    L"是" },
    { L"CONTROLS_INVERT_AIRCRAFT_PITCH", L"反转飞行器俯仰" },
    { L"COOP_MENU_FRIENDLY_FIRE",       L"友军伤害" },
    { L"COOP_MENU_FULL_DAMAGE",         L"全额伤害" },
    { L"CUTSCENE_REMOTE_SKIPPING",      L"过场可远程跳过" },
    { L"DLT_DESC_CASUAL",               L"休闲" },
    { L"HUD_SUPER_LEVEL",               L"超能力等级" },
    { L"HUD_UNARMED",                   L"徒手" },
    { L"HUD_CANCEL_MISSION",            L"取消任务" },
    { L"HORDE_MODE_MENU_EXIT",          L"退出浩劫模式" },
    { L"INFO",                          L"信息" },
    { L"LABEL",                         L"标签" },
    { L"MENU_AUDIO_OVERALL",            L"总音量" },
    { L"MENU_CONTROLS_INVERT_Y",        L"反转 Y 轴" },
    { L"MENU_CONTROL_SCHEMES",          L"操控方案" },
    { L"MENU_DISPLAY_TITLE",            L"显示" },
    { L"MENU_RADIO",                    L"电台" },
    { L"MENU_VEHICLE_CAMERA_SNAP",      L"载具镜头吸附" },
    { L"METER_LABEL",                   L"计量条" },
    { L"NEWSTICKER_LOCAL_MOTD_04",      L"本地消息" },
    { L"RECHARGING_WEAPON",             L"武器充能中" },
    { L"SCANNING",                      L"扫描中" },
    { L"SHOP",                          L"商店" },
    { L"SYSTEM_PAUSED",                 L"系统已暂停" },
    { L"TEXT_LOCALIZED",                L"本地化文本" },
    { L"TIERS",                         L"层级" },
    { L"TITLE",                         L"标题" },
    { nullptr, nullptr }
};

static const wchar_t* KeyZhLookup(const wchar_t* s)
{
    if (!s) return nullptr;
    for (size_t i = 0; g_keyZh[i][0]; ++i)
        if (!wcscmp(s, g_keyZh[i][0])) return g_keyZh[i][1];
    return nullptr;
}

// 裸键形态: 全大写字母/数字/下划线, 长度 3..64 (如 OPTION_YES / HUD_SUPER_LEVEL)
static bool IsBareKey(const wchar_t* s)
{
    if (!s || !*s) return false;
    size_t n = wcslen(s);
    if (n < 3 || n > 64) return false;
    for (const wchar_t* p = s; *p; ++p)
    {
        wchar_t c = *p;
        if (!((c >= L'A' && c <= L'Z') || (c >= L'0' && c <= L'9') || c == L'_'))
            return false;
    }
    return true;
}

static const wchar_t* EarlySub(const char* which, LangGet_t orig,
                               uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4,
                               volatile LONG* hit, volatile LONG* miss)
{
    const wchar_t* s = orig(a1, a2, a3, a4);   // 完整透传调用者参数, 不破坏槽位语义
    DiagEarlySample(which, s, a1, a2, a3, a4);   // v1.5: 采样入参 + 语言服务返回串
    if (!s || !*s || !g_dictReady) return s;
    // direction A: 裸键兜底 —— 仅 LANG_TXT 路径(LANG_CUR 返回的是语言名, 不应拦截)。
    // 引擎查表失败会把 KEY 当文本返回; 命中内置 KEY->中文 表则直接给中文, 后续链路不再裸键。
    if (which && !strcmp(which, "LANG_TXT") && IsBareKey(s))
    {
        const wchar_t* zh = KeyZhLookup(s);
        if (zh) return zh;
    }
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
    return EarlySub("LANG_CUR", g_origLangCur, a1, a2, a3, a4, &g_earlyCurHit, &g_earlyCurMiss);
}

static const wchar_t* __fastcall HookLangTxt(uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4)
{
    return EarlySub("LANG_TXT", g_origLangTxt, a1, a2, a3, a4, &g_earlyTxtHit, &g_earlyTxtMiss);
}

// ---------- 字幕/HUD 绘制入口整串替换 (sub_140476D80) ----------
// SR4 签名变更（IDA 2026-09-07 实测, sr_hv.exe）:
//   __int64 sub_140476D80(__int64 a1, unsigned int a2, int a3, __int64 a4, __int64 a5, int a6)
//   - a1 = UTF-8 char* 文本（SR3R 为 wchar_t*, 完全不同）
//   - 返回值 double（与 SR3R 一致）
//   - 4 参数（与 SR3R 一致）
//   - 内部折行布局后逐行绘制
//   - 含 \n<毫秒> 尾码解析逻辑和 0x2711 计数器
// 与其他 hook 的关系:
//   - 字幕链不经 Format, F/G 语言服务层收不到语音字幕 —— 本 hook 是该链路唯一替换点
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
    static volatile LONG once = 0;
    DiagAlive("SUBTITLE", &once, &g_hookCalls[HC_J]);

    if (text && *text && g_dictReady && !wcschr(text, L'%'))
    {
        size_t len = wcslen(text);
        if (len < WRAP_MAX_CHARS)
        {
            size_t bodyLen = SubBodyLen(text, len);

            // 查词典: 有尾码 -> 按主体查（词典 KEY 实测零尾码, 整串查只会 miss）;
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

// ======================================================================
//  Hook K’(v1.9): 挂载表磁盘项插队 —— 直接调用注册器, 不再 hook
// ----------------------------------------------------------------------
//  目标: sub_140B7E7A0(int type, int param, char insertFront)
//  完整原理见上方 SIG_MOUNT_REG 处的大段注释; 这里只写行为契约:
//
//    引擎在 WinMain 早期注册 5 类资源槽位, 其中「磁盘 loose 探测」
//    项(type==0)的 insertFront 被硬编码为 0 —— 结果它排到了表尾,
//    而遍历是「首个成功即返回」, 所以 vpp 永远先命中, 根目录的 loose
//    资源文件(xtbl / le_strings / …)全都读不到。这就是"loose 无效"的根因。
//
//    v1.9 做法: 直接调 sub_140B7E7A0(0, 0, 1) 把磁盘项前移到表头 [0]。
//    不用 MinHook —— 因为注册期(DllMain 之前)早已结束, hook 上去也永不触发
//    (v1.8 实测 mrCalls=0)。而挂载表是运行时活表, 只要赶在第一次资源访问
//    之前把表头换掉即可, 这正是 DllMain 能胜任的。
//
//  安全性:
//    - 只调注册器移动 type==0 项, 其它 type 一律不碰, 不改变原有优先级结构;
//    - 磁盘上不存在该文件时探测返回 0, 遍历会自然走到下一项(vpp),
//      行为与未改表时完全一致 —— 纯增量;
//    - 幂等: 若磁盘项已在 [0], sub_140B7E7A0 内部 (v6==0) 不搬动;
//    - ini `loose_first = 0` 可完全关闭本功能。
// ======================================================================

// 前向声明: 本功能需要在 DllMain(ATTACH) 里执行, 而 AobScan/SigMatch
// 定义在本文件靠后位置（它们依赖 AobSig / 段扫描基础设施）
static uint64_t AobScan(const AobSig& s, int* hitsOut);
static bool     SigMatch(const uint8_t* target, const uint8_t* sig, const uint8_t* mask, size_t len);

// 本 DLL 自身模块句柄（DllMain 里记录; 早期读 ini 用）
static HMODULE  g_hSelfModule = nullptr;

using MountReg_t = char(__fastcall*)(int type, int param, char insertFront);
static MountReg_t g_fnMountRegCall  = nullptr;   // ★ 引擎注册器本体（用于直接调用）
static uint32_t*  g_pMountCount     = nullptr;   // dword_146998D80 表项数
static uint32_t*  g_pMountArray     = nullptr;   // dword_146998D90 表项数组（12B/项）
static bool       g_mountRegApplied = false;

// ----------------------------------------------------------------------
//  LocateMountReg / LooseFirstWatcherThread —— 定位(只读) + 独立守候线程改表
// ----------------------------------------------------------------------
//  时机: 挂载表注册在进程启动极早期完成, 而 CRT 静态构造(临界区初始化)更早。
//    ★ DllMain 里**不能**调用注册器: 那时临界区尚未初始化 -> 崩溃（实测）。
//    故拆两步: ① DllMain 只做只读定位  ② MainThread 里等表就绪后改表。
//
//  MainThread 需要: 读 ini → 建 arena/桶 → 载词典(几万条) → AOB 扫 8 条特征
//    (每条都遍历整个 .text, 实测数百 ms), 跑到改表那一步时引擎早已初始化完。
//
//  安全性:
//    - 不做 LoadLibrary / CreateThread 之外的危险操作, 无 loader lock 死锁面;
//    - 不使用 MinHook（v1.9 起完全不需要）;
//    - 任一步失败 -> 只写日志并返回, 主流程不受影响;
//    - 成功后置 g_mountRegApplied, 不再重复处理。
// ----------------------------------------------------------------------

// 只读 ini 里的 loose_first 一项（默认 true）。用 Win32 文件 API + 朴素 ASCII
// 扫描, 避免在 DllMain 里依赖 CRT 的 locale/文件全局状态。
static bool ReadLooseFirstFlag(HMODULE hSelf)
{
    wchar_t path[MAX_PATH];
    DWORD n = GetModuleFileNameW(hSelf, path, MAX_PATH);
    if (!n || n >= MAX_PATH) return true;
    wchar_t* slash = wcsrchr(path, L'\\');
    if (!slash) return true;
    const wchar_t* leaf = slash + 1;
    // 同目录 <dllname>.ini（dllmain 里的 asi 名是 SR4R_I18N.asi -> SR4R_I18N.ini）
    wcscpy_s(slash + 1, MAX_PATH - (slash + 1 - path), L"SR4R_I18N.ini");

    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return true;   // 无 ini -> 默认开

    char buf[4096];
    DWORD rd = 0;
    BOOL ok = ReadFile(f, buf, sizeof(buf) - 1, &rd, nullptr);
    CloseHandle(f);
    if (!ok) return true;
    buf[rd] = '\0';

    // 朴素查找 "loose_first"（大小写不敏感）, 取其后的 '=' 与数字
    for (DWORD i = 0; i + 11 < rd; ++i)
    {
        char k[12];
        for (int j = 0; j < 11; ++j) k[j] = (char)tolower((unsigned char)buf[i + j]);
        k[11] = '\0';
        if (strcmp(k, "loose_first") != 0) continue;
        DWORD p = i + 11;
        while (p < rd && (buf[p] == ' ' || buf[p] == '\t')) ++p;
        if (p >= rd || buf[p] != '=') continue;
        ++p;
        while (p < rd && (buf[p] == ' ' || buf[p] == '\t')) ++p;
        if (p >= rd) return true;
        return buf[p] != '0';       // 0/off 之外的任何值都当"开"
    }
    (void)leaf;
    return true;
}

// 把挂载表里的磁盘项 {type=0,param=0} 前移到表头。返回 true = 已完成。
//
//   ★ 调用前提（血的教训, 同 SR3R）: 注册器内部会 EnterCriticalSection,
//     该临界区由引擎 CRT 静态构造初始化 —— **DllMain 时期尚未初始化**。
//     在 DllMain 里调用 = 进未初始化临界区 = 进程崩溃。
//     故必须等「表非空」（说明引擎已完成静态构造与注册）才动手。
static bool ApplyLooseFirst()
{
    if (g_mountRegApplied) return true;
    if (!g_fnMountRegCall)
    {
        Log("mountreg: 注册器未定位 -> 无法插队");
        return false;
    }

    // ★ 安全门: 表为空说明引擎还没注册（CRT 静态构造也没跑），此时调用会崩。
    if (!g_pMountCount || *g_pMountCount == 0 || *g_pMountCount > 16)
    {
        Log("mountreg: mount table not ready (count=%u) -> 暂不改表",
            g_pMountCount ? *g_pMountCount : 0);
        return false;
    }

    // ★★ v1.9.2 新增「注册序列已结束」判定（与 SR3R v7.8.2 同构, 实测崩溃教训）:
    //   引擎连续调 3 次注册器（count 1→2→3）。若在 count 刚到 1/2 时抢先插队,
    //   会与引擎后续那次 MountReg(...,insertFront=1) 打架 → 后续线程空指针崩溃
    //   （c0000005, 崩在游戏自身链表遍历, 栈上无本 DLL 帧）。
    //   故要求「count 稳定在预期值」再动手。
    static const uint32_t MOUNTREG_EXPECT = 3;          // 原版固定注册 3 项
    static const int      MOUNTREG_STABLE_SAMPLES = 4;  // 连续 4 次(×50ms=200ms)不变
    {
        uint32_t c = *g_pMountCount;
        static uint32_t s_lastCount = 0;
        static int      s_sameCnt   = 0;
        if (c < MOUNTREG_EXPECT)
        {
            if (c == s_lastCount) ++s_sameCnt; else { s_lastCount = c; s_sameCnt = 1; }
            Log("mountreg: 注册序列进行中 (count=%u/%u) -> 继续等待", c, MOUNTREG_EXPECT);
            return false;
        }
        if (c == s_lastCount)
        {
            if (++s_sameCnt < MOUNTREG_STABLE_SAMPLES) return false;
        }
        else
        {
            s_lastCount = c; s_sameCnt = 1; return false;
        }
    }

    // 调用前后各扫一次表, 便于日志核对「是否真的搬动了」
    auto findDisk = [](uint32_t* arr, uint32_t cnt) -> int
    {
        if (!arr || cnt > 16) return -1;
        for (uint32_t i = 0; i < cnt; ++i)
            if (arr[3 * i] == 0 && arr[3 * i + 1] == 0) return (int)i;
        return -1;
    };
    uint32_t cnt0 = *g_pMountCount;
    int idx0 = findDisk(g_pMountArray, cnt0);

    char ok = g_fnMountRegCall(0, 0, 1);            // ★ insertFront=1 -> 顶到 [0]

    uint32_t cnt1 = *g_pMountCount;
    int idx1 = findDisk(g_pMountArray, cnt1);

    Log("mountreg: ApplyLooseFirst -> ret=%d, count %u->%u, disk idx %d -> %d",
        (int)ok, cnt0, cnt1, idx0, idx1);

    if (idx1 == 0)
    {
        g_mountRegApplied = true;
        Log("mountreg: ★ 磁盘项已置于表头 [0] —— loose 资源(含 le_strings)优先于 vpp_pc");
        return true;
    }
    Log("mountreg: WARN 磁盘项未在表头 (idx=%d) —— loose 可能不生效", idx1);
    return false;
}

// 定位注册器 + 解出挂载表全局地址（**纯只读**, 可在 DllMain 里安全执行）。
//   注意: 本函数**不调用**注册器 —— 调用必须等引擎 CRT 静态构造跑完
//   （见 ApplyLooseFirst 的「安全门」注释）。改表动作在 MainThread 里做。
static bool LocateMountReg()
{
    if (g_fnMountRegCall) return true;              // 已定位

    // ① 定位: L1 精确 -> L2 放宽 -> L3 换代构建精确
    //    L2 解决「同代编译器, 帧尺寸/rip disp/call rel32 漂移」;
    //    L3 解决「换了一代编译器, 前导指令形态不同」—— MS Store(2020-02) 属后者,
    //    其 17B 序言在 MS dump 命中 0 次, mask 再怎么放宽也救不了（详见 R3_MOUNTREG）。
    //    ★ 命中哪一层, 就必须用那一层的 sig/mask 做落点校验, 且用那一层的内部偏移。
    int hits = 0, h2 = 0, h3 = 0;
    uint64_t va  = AobScan(AOB_MOUNT_REG, &hits);
    bool     isMs = false;                            // L3(MS 构建) 命中?
    if (va)
    {
        g_mrOffMovzx = MOUNTREG_OFF_MOVZX_ESI_NEW;
        g_mrOffCount = MOUNTREG_OFF_COUNT_INSN_NEW;
        g_mrOffArray = MOUNTREG_OFF_ARRAY_INSN_NEW;
    }
    else
    {
        AobSig l2 = { "MOUNT_REG", R2_MOUNTREG, RM_MOUNTREG, 48, nullptr, nullptr, 0, nullptr, nullptr, 0 };
        va = AobScan(l2, &h2);
        if (va)
        {
            Log("mountreg: L1 落空(%d hits) -> L2 放宽命中 @0x%llX", hits, (unsigned long long)va);
            g_mrOffMovzx = MOUNTREG_OFF_MOVZX_ESI_NEW;
            g_mrOffCount = MOUNTREG_OFF_COUNT_INSN_NEW;
            g_mrOffArray = MOUNTREG_OFF_ARRAY_INSN_NEW;
        }
    }
    if (!va)
    {
        AobSig l3 = { "MOUNT_REG", R3_MOUNTREG, nullptr, 48, nullptr, nullptr, 0, nullptr, nullptr, 0 };
        va = AobScan(l3, &h3);
        if (va)
        {
            isMs = true;
            Log("mountreg: L1/L2 落空(%d/%d hits) -> L3 换代构建(MS Store)特征码命中 @0x%llX",
                hits, h2, (unsigned long long)va);
            g_mrOffMovzx = MOUNTREG_OFF_MOVZX_ESI_MS;
            g_mrOffCount = MOUNTREG_OFF_COUNT_INSN_MS;
            g_mrOffArray = MOUNTREG_OFF_ARRAY_INSN_MS;
        }
        else
        {
            Log("mountreg: AOB 未命中 (L1 %d / L2 %d / L3 %d hits) -> loose 不生效（引擎更新? 请上报日志）",
                hits, h2, h3);
            return false;
        }
    }
    if (!va) return false;

    uint8_t* fn = VA<uint8_t*>(va);
    // 落点校验: 必须用「命中该地址的那一套」sig/mask（L1 → L2 → L3 依次试）
    if (!SigMatch(fn, AOB_MOUNT_REG.sig, AOB_MOUNT_REG.mask, AOB_MOUNT_REG.len) &&
        !SigMatch(fn, R2_MOUNTREG, RM_MOUNTREG, 48) &&
        !(AOB_MOUNT_REG.sig3 && SigMatch(fn, AOB_MOUNT_REG.sig3, AOB_MOUNT_REG.mask3,
                                        AOB_MOUNT_REG.len3)))
    {
        Log("mountreg: @%p 落点特征校验失败, 放弃", (void*)fn);
        return false;
    }

    // ② 落点语义复核: 按本层偏移取 movzx esi, r8b（insertFront 参数通道）。
    //    这是「调用参数语义正确」的依据 —— 若此处不是 insertFront, 调用就传错了参数。
    {
        static const uint8_t OPC[4] = { 0x41, 0x0F, 0xB6, 0xF0 };
        bool ok = true;
        for (int i = 0; i < 4; ++i)
            if (fn[g_mrOffMovzx + i] != OPC[i])
            {
                ok = false;
                Log("mountreg: +0x%llX opcode mismatch (got %02X want %02X), 放弃",
                    (unsigned long long)g_mrOffMovzx, fn[g_mrOffMovzx + i], OPC[i]);
                break;
            }
        if (!ok) return false;
    }

    // ③ 从函数体解出挂载表两个全局地址（rip disp32）
    {
        uint8_t* insnCnt = fn + g_mrOffCount;   // mov r10d, cs:计数
        uint8_t* insnArr = fn + g_mrOffArray;   // lea rax,  cs:数组+4
        if (insnCnt[0] != 0x44 || insnCnt[1] != 0x8B || insnCnt[2] != 0x15)
        {
            Log("mountreg: count-insn mismatch at +0x%llX (%02X %02X %02X), 放弃",
                (unsigned long long)g_mrOffCount, insnCnt[0], insnCnt[1], insnCnt[2]);
            return false;
        }
        if (insnArr[0] != 0x48 || insnArr[1] != 0x8D || insnArr[2] != 0x05)
        {
            Log("mountreg: array-insn mismatch at +0x%llX (%02X %02X %02X), 放弃",
                (unsigned long long)g_mrOffArray, insnArr[0], insnArr[1], insnArr[2]);
            return false;
        }
        int32_t dCnt = *reinterpret_cast<int32_t*>(insnCnt + 3);
        int32_t dArr = *reinterpret_cast<int32_t*>(insnArr + 3);
        g_pMountCount   = reinterpret_cast<uint32_t*>(insnCnt + 7 + dCnt);
        g_pMountArray   = reinterpret_cast<uint32_t*>(insnArr + 7 + dArr - 4);  // lea 出「数组+4」
        g_fnMountRegCall = reinterpret_cast<MountReg_t>(fn);

        Log("mountreg: fn=0x%llX (off %llX/%llX/%llX%s), count=0x%llX (val=%u), array=0x%llX",
            (unsigned long long)va,
            (unsigned long long)g_mrOffMovzx, (unsigned long long)g_mrOffCount,
            (unsigned long long)g_mrOffArray, isMs ? ", MS代" : "",
            (unsigned long long)(uint64_t)g_pMountCount, *g_pMountCount,
            (unsigned long long)(uint64_t)g_pMountArray);
    }
    return true;                                        // 仅定位, 不改表
}

// 在 MainThread 里「等到挂载表就绪后」把磁盘项插队。
// 「守候线程」: 独立线程轮询挂载表, 一旦引擎注册完(count>0)就把磁盘项插队。
//
//   ★ v1.9.1 架构（与 SR3R v7.8.1 同构, 两次实测教训的最终形态）:
//     ① 不能在 DllMain 里调注册器 —— 那时临界区(CRT 静态构造)还没初始化, 进程崩。
//     ② 不能在 MainThread 里同步等待 —— 引擎注册发生在主初始化里, 由静态构造
//        分派器在 ASI 注入**之后**才跑, 实测可晚于注入 30s+;
//        同步等就会把后面的 hook 安装一起卡死。
//     → 最终: DllMain 只做只读定位; 本线程独立轮询, 与 hook 安装完全解耦。
//
//   ★ v1.9.2 追加「注册序列已结束」判定（实测崩溃教训, 同 SR3R v7.8.2）:
//     必须等 count 稳定在预期值再插队, 否则会与引擎自身的后续注册打架,
//     导致游戏后续线程空指针崩溃。本线程不再「表非空就退出」。
static DWORD WINAPI LooseFirstWatcherThread(LPVOID)
{
    if (!LocateMountReg())
    {
        Log("mountreg[watch]: 注册器未定位 -> loose 优先不可用");
        return 0;
    }

    const DWORD LOOSE_WAIT_MS = 180000;     // 上限 180s（表就绪即返回, 不会等满）
    const DWORD STEP_MS       = 50;
    DWORD waited   = 0;
    DWORD lastBeat = 0;

    while (waited < LOOSE_WAIT_MS)
    {
        if (ApplyLooseFirst())
        {
            Log("mountreg[watch]: 等待 %u ms 后表就绪并完成插队", waited);
            return 0;
        }
        // v1.9.2: 不再「表非空就退出」—— 注册序列未结束时 ApplyLooseFirst 会
        //   主动返回 false 继续等；只有它返回 true（已插队成功）才收工。
        Sleep(STEP_MS);
        waited += STEP_MS;
        if (waited - lastBeat >= 5000)      // 心跳 5s
        {
            lastBeat = waited;
            Log("mountreg[watch]: 仍在等待引擎注册挂载表... (%u ms)", waited);
        }
    }
    Log("mountreg[watch]: WARN 等待 %u ms 后表仍未就绪, 放弃", (unsigned)LOOSE_WAIT_MS);
    return 0;
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
            else if (*p == L't')  *w++ = L'\t';   // v1.6: 原表缺 \t, 未知转义会吃掉反斜杠 -> 变成字母 t
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
            else if (*p == L't')  *w++ = L'\t';   // v1.6: 原表缺 \t, 未知转义会吃掉反斜杠 -> 变成字母 t
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

// v1.5: 词典自检样本（加载完成后回查, 证明哈希表端到端可用）
//   目的: 把「查不中」的两种成因分开 —— 表坏了(自检失败) vs 输入不是键(自检通过)
static constexpr int  DICT_SELFTEST_N = 8;
static const wchar_t* g_selfKeys[DICT_SELFTEST_N];
static int            g_selfKeyN = 0;

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

    if (DictInsert(origW, (uint32_t)effLen, transW))
    {
        ++*loaded;
        if (g_selfKeyN < DICT_SELFTEST_N) g_selfKeys[g_selfKeyN++] = origW;   // v1.5 自检样本
    }

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

    // \n→空格 规范化副本键: 引擎把含 \n 的文本按换行拆段分别送入 hook,
    // WrapTryConcat 以空格拼接段1+段2, 词典原键含 \n 无法命中。
    // 补一份 \n 替换为空格 + 压连续空格 + trim 的副本键, 使空格拼接能命中。
    {
        wchar_t nlNorm[4097];
        size_t w = 0;
        bool hasNl = false;
        for (size_t i = 0; i < effLen; ++i)
        {
            wchar_t c = origW[i];
            if (c == L'\n') { c = L' '; hasNl = true; }
            if (c == L' ' && w > 0 && nlNorm[w - 1] == L' ') continue;   // 压连续空格
            if (w >= 4096) { w = 0; break; }
            nlNorm[w++] = c;
        }
        if (hasNl && w > 0)
        {
            size_t nb = 0, ne = w;
            while (nb < ne && nlNorm[nb] <= 0x20) ++nb;               // trim 左
            while (ne > nb && nlNorm[ne - 1] <= 0x20) --ne;            // trim 右
            if (nb < ne)
            {
                nlNorm[ne] = L'\0';
                if (DictInsert(nlNorm + nb, (uint32_t)(ne - nb), transW)) ++*loaded;
            }
        }
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
    // v1.6 自检闸门: 回查加载时留样的前 N 个键 —— 证明哈希表端到端可用。
    //   全 hit => 线上"format hit=0"不是表坏了, 而是输入本身不是词典里的英文原文;
    //   有 miss => 表/索引有问题, 先修表再谈其它。
    if (loaded > 0 && g_selfKeyN > 0)
    {
        int okN = 0;
        for (int i = 0; i < g_selfKeyN; ++i)
        {
            const DictNode* r = DictLookup(g_selfKeys[i], wcslen(g_selfKeys[i]));
            if (r) ++okN;
        }
        Log("dict-selftest: %d/%d hit -> hash table %s", okN, g_selfKeyN,
            okN == g_selfKeyN ? "OK" : "BROKEN");
    }
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
#if SR4R_DIAG_RUNTIME
        // v1.5: hook 存活/调用量 + 语言服务层计数 + wrap 事件 + Format 调用点分类
        //   calls: 每个 hook 的真实被调用次数（0 = 该 hook 在本构建从未触发 ->
        //          A=0 说明 DrawWide 落点不是本构建 UI 的绘制入口）
        //   early: F/G 语言服务的 hit/miss（双双为 0 且无 early# 采样 = 该层未参与）
        Log("stats2: calls A=%ld B=%ld C=%ld D=%ld E=%ld J=%ld | early cur=%ld/%ld txt=%ld/%ld | fmtFrom=[%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld] | wrapEv=[L%ld T%ld C%ld R%ld S%ld O%ld]",
            g_hookCalls[HC_A], g_hookCalls[HC_B], g_hookCalls[HC_C], g_hookCalls[HC_D],
            g_hookCalls[HC_E], g_hookCalls[HC_J],
            g_earlyCurHit, g_earlyCurMiss, g_earlyTxtHit, g_earlyTxtMiss,
            g_fmtFrom[0], g_fmtFrom[1], g_fmtFrom[2], g_fmtFrom[3],
            g_fmtFrom[4], g_fmtFrom[5], g_fmtFrom[6], g_fmtFrom[7],
            g_wrapCnt[WD_LEARN], g_wrapCnt[WD_TTLEXP], g_wrapCnt[WD_CONCAT],
            g_wrapCnt[WD_REPLACE], g_wrapCnt[WD_STABLE], g_wrapCnt[WD_TOKEOVER]);
#endif  // SR4R_DIAG_RUNTIME
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

// ---------- 安装 ----------
// ---------- AOB 扫描定位 ----------
// 在主模块所有可执行段内搜索特征码, 要求「恰好 1 命中」才认可。
// 返回值遵循本文件统一的「GAME_BASE 相对 VA」口径（与 VA() 配对使用）; 0 = 未定位到。
// 注意: 不能用内存镜像头里的 ImageBase 换算 —— ASLR 重定位后它已被加载器改写成
//       运行时基址, 会造成二次换算(详见下方 found 处注释)。
// 之所以要求唯一: 多命中说明特征码不够长（DrawWide 的 char/wchar 孪生函数就是典型）,
// 此时宁可回落到 VA 表并打日志, 也不能赌第一个命中。
static uint64_t AobScan(const AobSig& s, int* hitsOut)
{
    if (hitsOut) *hitsOut = 0;
    const uint8_t* base = reinterpret_cast<const uint8_t*>(GetModuleHandleW(nullptr));
    if (!base) return 0;

    const IMAGE_DOS_HEADER* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
    const IMAGE_NT_HEADERS64* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE ||
        nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC)
    {
        return 0;   // 内存镜像头异常 -> 直接放弃扫描
    }

    // 锚点 = 第一个参与匹配的字节, 用 memchr 快速跳进
    //   （.text 约 40MB, 朴素逐字节比较要上亿次; memchr 是 CRT SIMD 实现）
    size_t anchor = 0;
    while (anchor < s.len && s.mask && !s.mask[anchor]) ++anchor;
    if (anchor >= s.len) anchor = 0;

    constexpr DWORD EXEC_ANY = IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_CNT_CODE;
    const IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    const int      nsec       = nt->FileHeader.NumberOfSections;
    // !! 不要用 nt->OptionalHeader.ImageBase !!
    //   ASLR 重定位时, 加载器会把内存镜像头里的 ImageBase 改写成【运行时基址】,
    //   用它算出来的就是运行时地址; 而本文件所有 hook 地址/CFG 表都遵循
    //   「GAME_BASE(0x140000000) 相对 VA」口径, 由 VA() 做统一换算。
    //   用镜像头 ImageBase 会导致 VA() 二次换算 -> 地址错位 -> 安装 hook 时段错误。
    //   (2026-09-12 修复: 曾因此让 Steam/GOG/EPIC 三版在 AOB 命中后全部崩溃)
    uint64_t found = 0;
    int      hits  = 0;

    for (int i = 0; i < nsec && hits <= 1; ++i, ++sec)
    {
        if (!(sec->Characteristics & EXEC_ANY)) continue;
        size_t n = sec->Misc.VirtualSize ? sec->Misc.VirtualSize : sec->SizeOfRawData;
        if (n < s.len) continue;

        const uint8_t* p     = base + sec->VirtualAddress;
        const size_t   limit = n - s.len;

        for (size_t off = 0; off <= limit; )
        {
            const uint8_t* q = static_cast<const uint8_t*>(
                memchr(p + off, s.sig[anchor], (limit - off) + 1));
            if (!q) break;
            const size_t o = static_cast<size_t>(q - p);

            bool match = true;
            for (size_t k = 0; k < s.len; ++k)
                if ((!s.mask || s.mask[k]) && q[k] != s.sig[k]) { match = false; break; }
            if (match)
            {
                if (++hits > 1) break;
                found = GAME_BASE + sec->VirtualAddress + o;   // 统一 VA 口径(见上)
            }
            off = o + 1;
        }
    }

    if (hitsOut) *hitsOut = hits;
    if (hits == 1)
    {
#ifdef SR4R_DBG_VEH
        Log("dbg aob %s: modBase=%p modImageBase=0x%llX GAME_BASE=0x%llX found=0x%llX",
            s.name, (void*)base,
            (unsigned long long)nt->OptionalHeader.ImageBase,
            (unsigned long long)GAME_BASE, (unsigned long long)found);
#endif
        return found;
    }
    return 0;   // 命中数已由 hitsOut 带出, 日志由 LocateHook 统一打
}

// 一次定位的结果: 地址 + 命中该地址所用的那套 sig/mask（校验时必须用同一套）
struct LocResult
{
    uint64_t       va;
    const uint8_t* sig;
    const uint8_t* mask;
    size_t         len;
    const char*    via;     // "aob"(L1) / "aob-l2"(L2 放宽) / "va"(版本表回退)
};

// 带记忆的 LocateHook: 同一签名只做一次定位。
//   MainThread 会先定位 FontLookup 用于自解全局变量, 随后 InstallHook 还会再要
//   同一签名 —— 记忆化既省掉一次 20MB 扫描, 也避免失败时重复打日志。
//   （安装阶段单线程, 无需加锁）
static const AobSig* g_aobMemoKey[8] = {};
static LocResult      g_aobMemoVal[8] = {};
static int           g_aobMemoN      = 0;

// 定位顺序: L1 精确特征码 -> L2 放宽特征码 -> L3 换代构建精确特征码 -> 版本 VA 表。
//   L2 存在的意义: L1 里那几个「编译期布局字段」(栈帧 imm / cookie disp / call rel32)
//   在别的构建里会变。
//   L3 存在的意义: Microsoft Store 版 (TDS=5E58CEF8) 换了编译器, Format / Subtitle
//   的前导指令**形态**都不同(指令长度都变了) —— 字节级放宽救不了, 只能构建族各一套。
//   四构建(Steam/GOG/EPIC/MSStore)的唯一性均由 gen_aob_relaxed.py 离线断言。
static LocResult LocateHook(const AobSig& s)
{
    for (int i = 0; i < g_aobMemoN; ++i)
        if (g_aobMemoKey[i] == &s) return g_aobMemoVal[i];

    LocResult r = { 0, s.sig, s.mask, s.len, "va" };
    int h1 = 0, h2 = 0, h3 = 0;

    r.va = AobScan(s, &h1);
    if (r.va)
    {
        r.via = "aob";
    }
    else
    {
        if (s.sig2)
        {
            // L2 是一套独立数组, 需用 AobSig 的副本走 AobScan（mask/len 不同）
            AobSig l2 = s;
            l2.sig = s.sig2; l2.mask = s.mask2; l2.len = s.len2;
            r.va = AobScan(l2, &h2);
            if (r.va)
            {
                r.sig = s.sig2; r.mask = s.mask2; r.len = s.len2;
                r.via = "aob-l2";
                Log("locate %s: L1 落空(%d hits) -> L2 放宽命中 @0x%llX (帧尺寸/disp/call 已通配)",
                    s.name, h1, (unsigned long long)r.va);
            }
        }
        if (!r.va && s.sig3)
        {
            // L3: 换代构建(MS Store 2020-02)的纯精确特征码
            AobSig l3 = s;
            l3.sig = s.sig3; l3.mask = s.mask3; l3.len = s.len3;
            r.va = AobScan(l3, &h3);
            if (r.va)
            {
                r.sig = s.sig3; r.mask = s.mask3; r.len = s.len3;
                r.via = "aob-l3";
                Log("locate %s: L1/L2 落空(%d/%d hits) -> L3 换代构建特征码命中 @0x%llX",
                    s.name, h1, h2, (unsigned long long)r.va);
            }
        }
        if (!r.va)
        {
            char d2[24] = "", d3[24] = "";
            if (s.sig2) _snprintf_s(d2, sizeof(d2), _TRUNCATE, ", L2 %d hits", h2);
            if (s.sig3) _snprintf_s(d3, sizeof(d3), _TRUNCATE, ", L3 %d hits", h3);
            Log("locate %s: L1 %d hits%s%s -> 均落空, 回退版本 VA 表", s.name, h1, d2, d3);
        }
    }

    if (g_aobMemoN < 8)
    {
        g_aobMemoKey[g_aobMemoN] = &s;
        g_aobMemoVal[g_aobMemoN] = r;
        ++g_aobMemoN;
    }
    return r;
}

// 兼容旧调用点: 只关心地址（自解全局变量用）
static uint64_t AobScanMemo(const AobSig& s) { return LocateHook(s).va; }

// 带 mask 的签名比较: mask[i]=1 必须精确匹配, mask[i]=0 跳过 (RIP 相对地址通配)
static bool SigMatch(const uint8_t* target, const uint8_t* sig, const uint8_t* mask, size_t len)
{
    for (size_t i = 0; i < len; i++)
        if ((!mask || mask[i]) && target[i] != sig[i])
            return false;
    return true;
}

// 在回退 VA 上找出「真正成立」的那一层签名, 并把该层的 sig/mask/len 回填。
//   [!] 为什么必须逐层试: 落点校验用的是「命中该地址的那套签名」。走在 AOB 路径上时
//   LocResult 已经把对应层带出来了; 但走 **VA 回退路径** 时, v1.3 写死用 L1 校验 ——
//   而换代构建(MS Store)的 Format/Subtitle 在其正确地址上**只有 L3 成立**,
//   于是正确地址被判成 mismatch 并 ABORT。这正是 v1.3 日志里
//       `hook FORMAT @0000000140CF9A00 (via va): signature mismatch, ABORT`
//   的成因之一(另一个成因是当时 VA 表还是 Steam 的)。
static bool MatchAnyLayerAt(uint64_t va, const AobSig& s, LocResult* out)
{
    const uint8_t* p = VA<const uint8_t*>(va);
    if (!p) return false;
    if (SigMatch(p, s.sig, s.mask, s.len))
    {
        out->sig = s.sig;  out->mask = s.mask;  out->len = s.len;  return true;
    }
    if (s.sig2 && SigMatch(p, s.sig2, s.mask2, s.len2))
    {
        out->sig = s.sig2; out->mask = s.mask2; out->len = s.len2; return true;
    }
    if (s.sig3 && SigMatch(p, s.sig3, s.mask3, s.len3))
    {
        out->sig = s.sig3; out->mask = s.mask3; out->len = s.len3; return true;
    }
    return false;
}

// 定位顺序: L1/L2/L3 扫描为主 -> 失败回落到 VA 表; 无论哪条路径, 落点都必须通过特征码校验
static bool InstallHook(const AobSig& s, uint64_t fallbackVa, void* detour, void** orig)
{
    LocResult   loc = LocateHook(s);
    uint64_t    va  = loc.va;
    const char* via = loc.via;

    if (!va)
    {
        // 版本 VA 表回退: 逐层试探, 用「该地址上确实成立」的那套签名来校验
        LocResult vloc = { fallbackVa, s.sig, s.mask, s.len, "va" };
        va  = fallbackVa;
        via = "va";
        if (fallbackVa && MatchAnyLayerAt(fallbackVa, s, &vloc))
            loc = vloc;
    }

    uint8_t* target = VA<uint8_t*>(va);
#ifdef SR4R_DBG_VEH
    Log("dbg %s: via=%s va=0x%llX target=%p exeBase=%p",
        s.name, via, (unsigned long long)va, (void*)target,
        (void*)GetModuleHandleW(nullptr));
#endif
    if (!SigMatch(target, loc.sig, loc.mask, loc.len))
    {
        Log("hook %s @%p (via %s): signature mismatch, ABORT (game updated?)",
            s.name, (void*)target, via);
        return false;
    }
    if (MH_CreateHook(target, detour, orig) != MH_OK)
    {
        Log("hook %s: MH_CreateHook failed", s.name);
        return false;
    }
    if (MH_EnableHook(target) != MH_OK)
    {
        Log("hook %s: MH_EnableHook failed", s.name);
        return false;
    }
    Log("hook %s @%p installed (via %s)", s.name, (void*)target, via);
    return true;
}

// ======================================================================
//  可执行段落盘（ini: text_dump = 0 | 1 | auto）
//  ---------------------------------------------------------------------
//  为什么需要: Microsoft Store (MSIXVC) 版的 exe 是**密文** —— 非授权进程读到
//  的没有 MZ/PE 头, 全局熵 8.0000, 离线既取不到 PE 指纹也取不到 AOB。
//  但注入进游戏进程的 DLL 看到的是**已解密的内存映像**。把可执行段原样落盘,
//  离线侧就能为新构建生成精确特征码(也能用 IDA 以 raw binary 打开做正经分析)。
//
//  auto(默认) = 只有出现「hook 定位失败」时才落盘 —— 正常构建零额外开销,
//               出问题的构建自动留下证据。
//  产物: <dir>\SR4R_dump_text.bin (段原始字节) + SR4R_dump_text.map (段布局)
// ======================================================================

static constexpr DWORD DUMP_MAX_TOTAL = 128u << 20;   // 上限 128MB

static void DumpExecutableSections(const wchar_t* dir, const char* why)
{
    const uint8_t* base = reinterpret_cast<const uint8_t*>(GetModuleHandleW(nullptr));
    if (!base) { Log("dump: no module base"); return; }

    const IMAGE_DOS_HEADER* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;
    const IMAGE_NT_HEADERS64* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return;

    wchar_t dbgDir[MAX_PATH];
    wcscpy_s(dbgDir, dir); wcscat_s(dbgDir, L"\\SR4R_dump");
    CreateDirectoryW(dbgDir, nullptr);

    wchar_t binPath[MAX_PATH], mapPath[MAX_PATH];
    wcscpy_s(binPath, dbgDir); wcscat_s(binPath, L"\\SR4R_dump_text.bin");
    wcscpy_s(mapPath, dbgDir); wcscat_s(mapPath, L"\\SR4R_dump_text.map");

    HANDLE h = CreateFileW(binPath, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE)
    {
        Log("dump: 打开 %ls 失败 (GLE=%lu), 跳过可执行段落盘", binPath, GetLastError());
        return;
    }

    char  map[8192];
    int   mLen = 0;
    mLen += _snprintf_s(map + mLen, sizeof(map) - mLen, _TRUNCATE,
        "# SR4R_I18N executable-section dump\r\n"
        "# 用途: 为加密/未知构建的 exe 生成 AOB 特征码（离线看不到明文, 进程内看得到）\r\n"
        "# 说明: 落盘发生在 MinHook 安装【之前】, 因此入口字节是**未被 detour 跳板改写**的干净映像\r\n"
        "# 触发: %s\r\n"
        "# IDA 用法: 以 raw binary (x64) 打开 SR4R_dump_text.bin, 装载基址填第一段的 VA\r\n"
        "# 离线用法: Tools/gen_aob_relaxed.py --ms-dump <本 bin>（自动读同目录 .map 定位段与 VA）\r\n"
        "# va 列 = GAME_BASE(0x140000000) 相对 VA, 与 dllmain.cpp 的 CFG/VA 表同口径\r\n"
        "va_base=0x%llX\r\n"
        "timestamp=%08X ep=%08X sizeofimage=%08X\r\n"
        "bin=SR4R_dump_text.bin\r\n"
        "# section va vsize bin_off size\r\n",
        why,
        (unsigned long long)GAME_BASE,
        nt->FileHeader.TimeDateStamp,
        nt->OptionalHeader.AddressOfEntryPoint,
        nt->OptionalHeader.SizeOfImage);

    constexpr DWORD EXEC_ANY = IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_CNT_CODE;
    const IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    DWORD written = 0, total = 0, nsec = nt->FileHeader.NumberOfSections;

    for (DWORD i = 0; i < nsec; ++i, ++sec)
    {
        if (!(sec->Characteristics & EXEC_ANY)) continue;
        DWORD n = sec->Misc.VirtualSize ? sec->Misc.VirtualSize : sec->SizeOfRawData;
        if (!n) continue;
        if (total + n > DUMP_MAX_TOTAL)
        {
            Log("dump: 已达上限 %u MB, 停止", DUMP_MAX_TOTAL >> 20);
            break;
        }

        const uint8_t* p = base + sec->VirtualAddress;
        DWORD off = 0;
        bool  ok  = true;
        while (off < n)
        {
            DWORD chunk = n - off;
            if (chunk > (4u << 20)) chunk = 4u << 20;
            DWORD w = 0;
            if (!WriteFile(h, p + off, chunk, &w, nullptr) || w != chunk) { ok = false; break; }
            off += w;
        }

        char name[9] = {};
        memcpy(name, sec->Name, 8);
        mLen += _snprintf_s(map + mLen, sizeof(map) - mLen, _TRUNCATE,
            "%s va=0x%llX vsize=0x%X bin_off=0x%X size=0x%X%s\r\n",
            name, (unsigned long long)(GAME_BASE + sec->VirtualAddress),
            sec->Misc.VirtualSize, total, n, ok ? "" : "  (WRITE_FAILED)");
        total += n;
        if (!ok) { Log("dump: 段 %s 写入失败", name); break; }
    }

    CloseHandle(h);

    HANDLE hm = CreateFileW(mapPath, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                            FILE_ATTRIBUTE_NORMAL, nullptr);
    if (hm != INVALID_HANDLE_VALUE)
    {
        DWORD w = 0;
        WriteFile(hm, map, (DWORD)mLen, &w, nullptr);
        CloseHandle(hm);
    }

    Log("dump: 可执行段已落盘 %u 字节 -> %ls (+ .map)", total, binPath);
}


// ---------- 主线程 ----------
static DWORD WINAPI MainThread(LPVOID hSelf)
{
    Log("==== SR4R_I18N v1.7: SR3R v7.5.2 ported to SR4 (Steam/GOG/EPIC/MSStore + 未知构建自解, L1/L2/L3 三层特征码) ====");
    wchar_t dir[MAX_PATH], iniPath[MAX_PATH], dictDir[MAX_PATH], dtxt[MAX_PATH];
    GetModuleFileNameW((HMODULE)hSelf, dir, MAX_PATH);
    wchar_t* slash = wcsrchr(dir, L'\\');
    if (slash) *slash = L'\0'; else *dir = L'\0';
    s_exeBase = reinterpret_cast<uint64_t>(GetModuleHandleW(nullptr));   // v7.4 诊断 RA 换算用

    // 先加载 ini —— `exe=` 覆盖开关必须先于构建识别生效
    wcscpy_s(iniPath, dir); wcscat_s(iniPath, L"\\SR4R_I18N.ini");    LoadConfig(iniPath);

    // --- 识别当前构建 (Steam / GOG / EPIC) ---
    //   [!] 按【内容】识别, 绝不按文件名 ——
    //       EPIC 版官方安装的可执行文件同样叫 sr_hv.exe (与 Steam 同名!),
    //       过去按文件名判别的结果是 EPIC 被当成 Steam: hook 走 AOB 仍正确,
    //       但走配置表的 4 个全局变量全部指向 Steam 地址 → 字形表读成垃圾 →
    //       文字完全不显示。文件名仅作日志提示, 不参与判定。
    wchar_t exeName[MAX_PATH];
    GetModuleFileNameW(nullptr, exeName, MAX_PATH);
    wchar_t* exeSlash = wcsrchr(exeName, L'\\');
    const wchar_t* exeBase = exeSlash ? exeSlash + 1 : exeName;

    uint32_t fTs = 0, fEp = 0, fSoi = 0;
    int      fScore = 0;
    const BuildFp* fp = DetectBuildByFp(&fTs, &fEp, &fSoi, &fScore);

    const BuildFp* chosen = nullptr;
    const char*    how    = nullptr;
    int            score  = fScore;
    if (_wcsicmp(g_cfg.exeOverride, L"auto") != 0)
    {
        chosen = FindBuildByName(g_cfg.exeOverride);
        how    = "ini override";
        if (!chosen)
            Log("exe: ini exe=%ls 无效 (应为 auto/steam/gog/epic/msstore), 已忽略, 继续自动识别", g_cfg.exeOverride);
    }
    if (!chosen && fp) { chosen = fp; how = "PE fingerprint"; }

    // 版本 VA 表是否可信: 指纹命中, 或用户在 ini 里显式指定了构建。
    // 两者都不成立 = 未知构建（例如 Microsoft Store / MSIXVC 版）:
    //   该 exe 在授权进程外只能读到密文, 离线无法提取地址, 所以它的 VA 表
    //   我们根本没有 —— 此时全局变量只能靠运行时自解（见 §运行时自解）。
    const bool buildTrusted = (fp != nullptr) || (chosen != nullptr && chosen != fp);

    if (chosen)
    {
        g_exe = *chosen->cfg;
        Log("exe: %s (via %s, score=%d/3) | file=%ls TimeDateStamp=%08X EntryPoint=%08X SizeOfImage=%08X",
            chosen->name, how, score, exeBase, fTs, fEp, fSoi);
        if (chosen == fp && score < 3)
            Log("exe: 注意 EP/SizeOfImage 与已知值不符 (多为 DRM/loader 在内存中改写, 不影响判别)");
    }
    else
    {
        g_exe = CFG_STEAM;
        Log("exe: PE 指纹未命中 -> hook VA 回退表按 Steam 填 (仅供 AOB 失效时兜底)");
        Log("exe: !! NEWBUILD file=%ls TimeDateStamp=%08X EntryPoint=%08X SizeOfImage=%08X",
            exeBase, fTs, fEp, fSoi);
        Log("exe: 全局变量将走运行时自解 (fontTab/fontCount 反解 + SRV 反查 D3D 设备), "
            "不再依赖版本表; 若日志出现 'globals: dynamic resolve FAILED' 请上报上面那行原文");
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

    // 4. 引擎指针
    //    顺序: AOB 定位 FontLookup -> 从函数体内反解 fontTab/fontCount
    //          -> 解出即用（版本无关, MS Store / 加密版 / 未来新构建都适用）
    //          -> 解不出才回落版本 VA 表; 但「指纹未命中 + 自解失败」时回退表
    //             里的地址必然属于别的构建, 强用会在 HookFontLookup 里解引用
    //             垃圾指针而崩溃 —— 此时宁可不装字形层（文本替换照常）。
    uint64_t fontLookupVa = AobScanMemo(AOB_FONT_LOOKUP);
    if (!fontLookupVa) fontLookupVa = g_exe.vaFontLookup;
    DynGlobals dyn = ResolveFontGlobalsDyn(fontLookupVa);
    Log("globals: dynamic resolve %s | FontLookup=0x%llX fontTab=0x%llX fontCount=0x%llX",
        dyn.resolved ? "OK" : "FAILED",
        (unsigned long long)fontLookupVa,
        (unsigned long long)dyn.fontTab, (unsigned long long)dyn.fontCount);

    if (dyn.resolved)
    {
        g_fontTabPtr   = VA<void***>(dyn.fontTab);
        g_fontCountPtr = VA<volatile int*>(dyn.fontCount);
    }
    else if (buildTrusted && g_exe.vaFontTab)
    {
        g_fontTabPtr   = VA<void***>(g_exe.vaFontTab);
        g_fontCountPtr = VA<volatile int*>(g_exe.vaFontCount);
        Log("globals: fallback to version VA table (known build)");
    }
    else
    {
        g_fontTabPtr   = nullptr;
        g_fontCountPtr = nullptr;
        Log("globals: 自解失败且构建表无静态值(或在未知构建上) -> 禁用 fontTab"
            "（防读垃圾崩溃）; 字形层改走自学习槽位表 + SRV 反查设备");
    }

    // D3D 槽: 只在「已知构建且表里有值」时可信; 其余一律置空, 强制走 EnsureD3D 的
    //        「SRV 反查 device」版本无关路径。
    //        MSStore 表里这四个字段是 0（离线取不到）, 必须靠这里的分支挡住。
    if (buildTrusted && g_exe.vaD3dDevice)
    {
        g_devSlot = VA<ID3D11Device**>(g_exe.vaD3dDevice);
        g_ctxSlot = VA<ID3D11DeviceContext**>(g_exe.vaD3dContext);
    }
    else
    {
        g_devSlot = nullptr;
        g_ctxSlot = nullptr;
    }

    // 5. MinHook
    if (MH_Initialize() != MH_OK)
    {
        Log("MH_Initialize failed");
        return 0;
    }

    // 5a. 预定位（两阶段安装的第一阶段）
    //   [!] 必须「先把 8 条全部定位完 -> 需要时落盘可执行段 -> 最后才安装 hook」。
    //   原因: MinHook 往函数入口写 5 字节 `E9 rel32` detour 跳板, 一旦安装完再落盘,
    //   dump 里的入口字节就失真了 —— v1.3 的第一个 MS Store dump 正是如此: 已安装的
    //   6 条入口全变成 `E9 ..`, 离线侧不得不再写一步「还原跳板」才能用。
    //   LocateHook 自带记忆化, 这一遍不会重复付出扫描成本。
    static const AobSig* const kHookSigs[8] = {
        &AOB_DRAW_WIDE, &AOB_FORMAT,      &AOB_FONT_LOOKUP, &AOB_TEXOBJ,
        &AOB_SRV_RESOLVE, &AOB_LANG_CUR,  &AOB_LANG_TXT,    &AOB_SUBTITLE
    };
    int nLocateFail = 0;
    for (const AobSig* sp : kHookSigs)
        if (!LocateHook(*sp).va) ++nLocateFail;

    // 5b. 可执行段落盘（text_dump=auto 时只在「有 hook 定位失败」才落盘）
    //     加密的 MS Store 版只能在进程内看到明文映像 —— 落盘后即可离线生成
    //     精确特征码。正常构建(全命中)走 auto 不产生任何文件。
    //     此刻 hook 尚未安装, 落盘的是**干净**映像（入口未被 patch）。
    const int nFail = nLocateFail;
    char why[160];
    _snprintf_s(why, sizeof(why), _TRUNCATE,
                "text_dump=%d, %d/8 hooks 定位失败（入口未 patch, 干净映像）",
                g_cfg.textDump, nFail);
    if (g_cfg.textDump == 1 || (g_cfg.textDump == 2 && nFail > 0))
    {
        Log("dump: 触发可执行段落盘 (%s)", why);
        DumpExecutableSections(dir, why);
    }

    // 5c. 安装（第二阶段）
    //   挂载表磁盘项插队: 起一个「守候线程」异步完成。
    //   ★ 不能在 DllMain 调注册器（临界区未初始化 -> 崩溃, v1.9 实测）;
    //   ★ 也不能在此处同步等待（引擎注册由静态构造分派器在 ASI 注入后才跑,
    //     实测可晚 30s+, 同步等会把下面的 hook 安装一起卡死, v1.9.1 实测）。
    //   → 独立线程轮询, 与 hook 安装完全解耦; 结果以 [watch] 行日志为准。
    if (g_cfg.looseFirst)
    {
        HANDLE hWatcher = CreateThread(nullptr, 0, LooseFirstWatcherThread, nullptr, 0, nullptr);
        if (hWatcher) CloseHandle(hWatcher);
        else          Log("mountreg: WARN 守候线程创建失败 -> loose 优先不可用");
    }

    bool a = InstallHook(AOB_DRAW_WIDE,   g_exe.vaDrawWide,   (void*)HookDrawWide,   (void**)&g_origDrawWide);
    bool b = InstallHook(AOB_FORMAT,      g_exe.vaFormat,     (void*)HookFormat,     (void**)&g_origFormat);
    bool c = InstallHook(AOB_FONT_LOOKUP, g_exe.vaFontLookup, (void*)HookFontLookup, (void**)&g_origFontLookup);
    bool d = InstallHook(AOB_TEXOBJ,      g_exe.vaTexObj,     (void*)HookTexObj,     (void**)&g_origTexObj);
    bool e = InstallHook(AOB_SRV_RESOLVE, g_exe.vaSrvResolve, (void*)HookSrvResolve, (void**)&g_origSrvResolve);
    // v7.4 早期整句替换（语言服务返回层; ini lang_early 可关）
    bool f = g_cfg.langEarly && InstallHook(AOB_LANG_CUR, g_exe.vaLangCur,
                                            (void*)HookLangCur, (void**)&g_origLangCur);
    bool g = g_cfg.langEarly && InstallHook(AOB_LANG_TXT, g_exe.vaLangTxt,
                                            (void*)HookLangTxt, (void**)&g_origLangTxt);
    // 字幕/HUD 绘制入口整串替换（ini subtitle_early 可关）
    bool j = g_cfg.subtitleEarly && InstallHook(AOB_SUBTITLE, g_exe.vaSubtitleDraw,
                                                (void*)HookSubtitle, (void**)&g_origSubtitle);
    if (!a && !b && !c && !d && !e && !f && !g && !j)
    {
        Log("no hooks installed, idle");
        return 0;
    }

    InterlockedExchange(&g_dictReady, 1);

    // 6. 字体文件后台线程（读 TTF + InitFont）
    CloseHandle(CreateThread(nullptr, 0, FontFileThread, hSelf, 0, nullptr));

    CloseHandle(CreateThread(nullptr, 0, StatsThread, nullptr, 0, nullptr));
    Log("SR4R v1.9.4 active: dict=%u keys (%u files), hooks A=%d B=%d C=%d D=%d E=%d F=%d G=%d J=%d, loose_first=%d, applied=%d, idling",
        g_dictCount, files, (int)a, (int)b, (int)c, (int)d, (int)e, (int)f, (int)g, (int)j,
        (int)g_cfg.looseFirst, (int)g_mountRegApplied);
    // 注: 挂载表插队由独立「守候线程」异步完成（引擎注册晚于本线程）,
    //     此处 applied 可能仍为 0 —— 属正常, 不等同失败; 结果以 [watch] 行日志为准。
    return 0;
}

#ifdef SR4R_DBG_VEH
// ======================================================================
//  崩溃定位辅助（仅 -DSR4R_DBG_VEH 时编译）:
//  进程级异常处理器把异常码/出错 RIP/AV 目标地址写入 SR4R_crash.txt。
//  用于「AOB 定位重构」后的 hook 安装期崩溃定位, 正式发布不编译。
// ======================================================================
static LONG CALLBACK SR4R_DbgVeh(PEXCEPTION_POINTERS ep)
{
    // 取本 DLL 自身目录（用代码地址反查模块, 不能把函数指针当 HMODULE 用）
    HMODULE hSelf = nullptr;
    wchar_t path[MAX_PATH] = L"SR4R_crash.txt";
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                          GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                          reinterpret_cast<LPCWSTR>(SR4R_DbgVeh), &hSelf))
    {
        GetModuleFileNameW(hSelf, path, MAX_PATH);
        wchar_t* s = wcsrchr(path, L'\\');
        if (s) s[1] = L'\0';
    }
    wcscat_s(path, L"SR4R_crash.txt");
    FILE* f = nullptr;
    if (_wfopen_s(&f, path, L"ab") == 0 && f)
    {
        CONTEXT* c = ep->ContextRecord;
        uint64_t base = reinterpret_cast<uint64_t>(GetModuleHandleW(nullptr));
        HMODULE mod = nullptr;
        wchar_t modName[MAX_PATH] = L"?";
        if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                              GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                              reinterpret_cast<LPCWSTR>(c->Rip), &mod))
            GetModuleFileNameW(mod, modName, MAX_PATH);
        fprintf(f, "code=0x%08X addr=%p av=%llu/%p\n",
                ep->ExceptionRecord->ExceptionCode,
                ep->ExceptionRecord->ExceptionAddress,
                (unsigned long long)ep->ExceptionRecord->ExceptionInformation[0],
                (void*)ep->ExceptionRecord->ExceptionInformation[1]);
        fprintf(f, "  rip=%p  exeBase=%p  mod=%ls modBase=%p rva=0x%llX\n",
                (void*)c->Rip, (void*)base, modName, (void*)mod,
                (unsigned long long)(mod ? (c->Rip - reinterpret_cast<uint64_t>(mod)) : 0));
        fprintf(f, "  rax=%016llX rbx=%016llX rcx=%016llX rdx=%016llX\n",
                (unsigned long long)c->Rax, (unsigned long long)c->Rbx,
                (unsigned long long)c->Rcx, (unsigned long long)c->Rdx);
        fprintf(f, "  rsi=%016llX rdi=%016llX r8 =%016llX r9 =%016llX\n",
                (unsigned long long)c->Rsi, (unsigned long long)c->Rdi,
                (unsigned long long)c->R8, (unsigned long long)c->R9);
        fprintf(f, "  r10=%016llX r11=%016llX r14=%016llX r15=%016llX\n",
                (unsigned long long)c->R10, (unsigned long long)c->R11,
                (unsigned long long)c->R14, (unsigned long long)c->R15);
        uint64_t* sp = reinterpret_cast<uint64_t*>(c->Rsp);
        for (int i = 0; i < 16; ++i) fprintf(f, "  [rsp+%02X] %p\n", i * 8, (void*)sp[i]);
        fclose(f);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
#endif

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved)
{
    switch (ul_reason_for_call)
    {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hModule);
        g_hSelfModule = hModule;    // 早期读 ini 用（Hook K 抢装路径）
#ifdef SR4R_DBG_VEH
        AddVectoredExceptionHandler(1, SR4R_DbgVeh);
#endif
        InitializeCriticalSection(&g_logCS);
        LogOpen(hModule);
        if (g_log)
        {
			Log("[Info] SR4R Font Extend By HaoJun0823 https://www.haojun0823.xyz | https://github.com/HaoJun0823/SR4R_I18N");
	        Log("[DllMain] ATTACH SR4R v1.9.4");
            // v1.9: 这里**只做只读定位**, 绝不调用引擎注册器。
            //   原因（v1.8 实测崩溃）: 注册器内部 EnterCriticalSection(&挂载表锁),
            //   该临界区由引擎 CRT 静态构造初始化, 而 ASI 的 DllMain 跑在
            //   主 exe CRT 初始化之前 —— 此时临界区还是全 0, 进去就崩。
            //   所以: ① 只读 ini 里 loose_first（轻量解析, 不跑整套 LoadConfig）
            //         ② 只读定位注册器 + 解出挂载表全局地址（纯 AOB/内存读取）
            //   真正的改表动作在 MainThread 里起的「守候线程」（见 LooseFirstWatcherThread）。
            {
                bool iniLoose = ReadLooseFirstFlag(hModule);
                if (!iniLoose) g_cfg.looseFirst = false;
            }
            if (g_cfg.looseFirst)
                LocateMountReg();          // ★ 只读, 不改表
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
