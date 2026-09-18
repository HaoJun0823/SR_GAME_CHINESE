# 《黑道圣徒3：重制版》3DM 汉化补丁逆向技术规格

> 基础：3dm64.dll (1,150,976 字节, x64, 基址 0x7000000000) 全量逆向
> 目的：在新版 SRTTR.exe (2026/1/29 更新) 上重新实现等效 hook（ASI 插件路线）
> 日期：2026-09-04

---

## 1. 总体架构

```
binkw64.dll 代理 (PE头TLS目录劫持, 0x110)
  └─ LoadLibraryA("3dm64.dll")
       └─ DllMain → sub_7000005240 初始化:
            1. GetCurrentThreadId 记录
            2. 加载 text.btxt 词典 → CHelper 单例 (0x1D0 字节)
            3. 7 个 CRC 魔数白名单 → BaseD3d11HookInstaller+664（值不变：
               201172246 / 211829199 / 2111270088 / -1276609774 /
               -1041037332 / -375342829 / -284672888）
            4. 设置图集纹理尺寸 4096×4096 (+576/+580)
            5. +928 = sub_70000051B0 回调
       └─ 首个窗口消息触发 51B0:
            F320 → F010 读 INI 配置 + 构建 INI 路径
            4790 → CodeHookInstaller 单例 (0xA8 字节, RTTI: CodeHookInstaller)
            13380 → 遍历补丁表, 特征码扫描 + 安装 5 个 hook
       └─ SetWindowsHookExW(WH_GETMESSAGE) → fn 热键处理
```

**用户环境适配**：游戏根目录 binkw64.dll 为 Ultimate ASI Loader，重写时应编译为 `.asi` 放入 `scripts/`（或 `plugins/`）目录，无需代理 DLL。

### 涉及的 C++ 类（RTTI 泄露）

| 类名 | 单例指针 | 大小 | 职责 |
|---|---|---|---|
| `CHelper` | qword_700010EA60 | 0x1D0 | 词典哈希表(+272)、字形映射表(+400)、繁简归一化 |
| `CodeHookInstaller` | qword_700010E5F0 | 0xA8 | hook 安装器（trampoline 引擎） |
| `BaseD3d11HookInstaller` | qword_700010E5F8 | 0x4C0 | D3D11 hook 上下文、字形度量缓存(+528)、图集尺寸(+576/+580)、TextureFont 指针(+592) |
| `TextureFont` ←复合 `PixelFont` | (BaseD3d11+592) | — | D3D11TextureFont：FreeType 字形栅格化 + 纹理图集 |

---

## 2. 词典系统

### 2.1 text.btxt 文件格式

**完整路径**：`<3dm64.dll 所在目录>\3DMGAME\text.btxt`。加载链 CBA0：先查 6C70（按文件名 CRC 的已加载缓存）→ 未命中则 BE30→BC70 构造目录名 L"3DMGAME"（栈字符串逐字符 +1 混淆，非明文常量）→ 7BB0 拼路径 → wfopen("rb") 全读。

**本地状态**：`汉化\3DM\3DMGAME\` 只有 3dm.ttf + 3dm.dds，**text.btxt 缺失**，需从完整 3DM 补丁包重新获取（Launcher.exe 是 x86 注入器，不含词典）。
```
偏移 0x00: 魔数 "BEXT" (0x54584542, 4字节)
偏移 0x04: 保留 (4字节)
偏移 0x08: u32 条目数 N
偏移 0x0C: N 条记录, 每条:
    [u32 keyLen ] [key  UTF-8, NUL 结尾, keyLen 含 NUL]   ← 信息性字段, 不参与查找
    [u32 origLen] [orig UTF-8, NUL 结尾]                  ← 查找键 (英文原文)
    [u32 transLen] [trans UTF-8, NUL 结尾]                ← 译文
```

加载器 `sub_700000CDD0`：读文件 → 校验魔数 → 逐条 `MultiByteToWideChar(CP_UTF8=0xFDE9)` 转 UTF-16 → `CRC32(orig)` 作键插入 CHelper+272 哈希表。

### 2.2 查找算法（sub_700000D310）

```
输入: 游戏 wchar_t* 文本
1. crc = CRC32(text)            // sub_700000C1C0: 标准CRC-32, 多项式 0x04C10DB7 (MSB-first 查表)
                                // 注意: 计算范围含 L'\0' 终止符 (2*len+2 字节)
2. bucket = FNV1a64(crc) & mask // 魔数 0xCBF29CE484222325 / 0x100000001B3, 开链法
3. 链表比较 node+16 == crc → 命中, 返回 node+24 处 std::wstring 的数据指针
4. 未命中 → 复制文本 → B960 trim (去首尾 ≤0x20 且 ∈{\t,\n,\v,\f,\r,空格})
   → 若 trim 后长度变化 → 重查一次
```

**替换语义（sub_700000D5B0）**：命中后 `*a2 = 译文指针` —— 指针替换而非就地写。译文 wstring 常驻 CHelper 堆内存，游戏侧只持有指针。

### 2.3 DumpText.dtxt（未匹配文本导出）

- `sub_7000003F00` 创建后台线程（上下文 0x191A0 字节）+ mutex + 条件变量
- D310 未命中的文本进入环形队列，线程持续写 `DumpText.dtxt`
- 文件格式：8 字节魔数 + `u32 count`（fseek 到 +8 原子递增）+ 每条 `[u32 len][orig UTF-8][u32 len][trans UTF-8]`
- **翻译工作流**：跑游戏 → DumpText 收集原文 → 人工/机翻 → 转成 text.btxt → 游戏加载即生效

---

## 3. Hook 引擎

### 3.1 补丁表（.data 0x700010A660 起, stride 0x238）

字段布局（相对表项基址）：

| 偏移 | 类型 | 含义 |
|---|---|---|
| +0x000 | char* | 模块名。值为 `1`（特殊哨兵）→ 全进程扫描（13B0）；否则 GetModuleHandleA 后模块内扫描（11500） |
| +0x008 | BYTE[256] | 特征码（NUL 结尾字节串，无通配符） |
| +0x108 | u32 | 特征码长度（=strlen+1；-1 表示首次调用时动态 strlen 并回填） |
| +0x10C | i32 | delta：安装地址 = 命中地址 + delta |
| +0x110 | u32 | nth：跳过的命中数（nth=0 → 首个命中即安装；nth=1 → 跳过首个，第 2 个安装）。扫描从上次命中+pattern长度继续 |
| +0x114 | u32 | flag：1=只安装一个；0=同 pattern 所有命中都安装 |
| +0x118 | u32 | op（安装方式，见 3.2） |
| +0x120 | u64 | callback 地址 |
| +0x128 | u32 | arg/数据长度（op=4/5 用） |
| +0x12C | BYTE | op=5 的模式标志 |
| +0x12D | BYTE[] | data（op=4 补丁数据 / op=5 读取目标） |

### 3.2 op 语义

| op | 安装器 | 机制 |
|---|---|---|
| 0 | sub_7000018220 | 标准 detour：原函数入口覆写 14 字节跳转 → trampoline（push 全部 GPR+rflags → 对齐栈 → call hook → 恢复 → 复制被覆盖原指令 → push 返回地址+ret 跳回原函数+14） |
| 1 | sub_70000184C0 | vtable/函数指针替换 |
| 2 | sub_70000189B0 | fastcall 签名保持型：保存 rcx/rdx/r8/r9 到栈后调 hook（hook 收到原始参数） |
| 3 | sub_70000189B0 | 同 2，带第二指针参数 |
| 4 | — | VirtualProtect(PAGE_READWRITE) + memcpy 补字节 + 还原保护 |
| 5 | — | 读命中处数据到表项 data 字段 |

### 3.3 扫描算法

- 核心 `sub_700000B5C0`：**Boyer-Moore Horspool**（256 项失配跳转表，纯字节比较无 mask）
- 全进程版 `sub_70000113B0`：VirtualQuery 逐区域遍历（State==MEM_COMMIT 且 Protect ∈ 可读范围），`a1+288` 处函数指针做区域级预检（读 64 字节探针）；区域读取失败会 MessageBoxW 弹窗
- 模块版 `sub_7000011500`：GetModuleHandleA → ImageLoad(imagehlp) 取模块范围 → 从高往低 4096 步进找可读段 → B5C0

---

## 4. 五个 Hook 明细

| # | 特征码 | len | delta | nth | op | callback | 新版命中位置 | hook 点 rcx 语义（新 exe 实测） |
|---|---|---|---|---|---|---|---|---|
| 1 | `45 33 C9 45 33 C0 BA 00 20 00 00` | 11 | -14 | 0 | 0 | 0x70000050F0 | 2 处：0x14081CA7B / 0x14081CB68（均在 sub_14081C7A0 = `vint_insert_values_in_string` Lua 函数）→ 装 0x14081CA6D | `mov rcx,cs:unk_14294BE78` → 全局管理对象指针（合法，可解引用） |
| 2 | `4C 8B 8B 38 01 00 00 41` | 8 | 0 | 0 | 0 | 0x7000005150 | 2 处：sub_14082DD10（Lua 脚本文本）/ sub_140C4B2F8（Wwise 音频字符串管理，无关）→ 装第 1 处 0x14082DD97 | 栈缓冲区指针（rbp-0x2000），rdx=文本源指针，r8=0x1000 容量 |
| 3 | `0F B7 E9 8B CF 49 8B D9` | 8 | -31 | 0 | 2 | 0x7000004ED0 | 唯一命中 sub_140858C10 序言 → 装 0x140858C10 | **u16 curChar（字符码，非指针！）** 新签名 `(u16 cur, u16 next, int* outW, int* outH, int fontId)` |
| 4 | `0F 5B C9 0F 5B C0 41 0F` | 8 | 0 | 0 | 0 | 0x7000004FC0 | 唯一命中 sub_14016E7D0（宽字符→三角网格生成器）@0x14016E9BD | **u32 fontId**（0x14016E99F `mov ecx,edi`） |
| 5 | `0F 5B C0 44 0F 28 C6 F3` | 8 | -14 | 1 | 0 | 0x7000005070 | 2 处：0x1408B5CED（窄字符绘制 sub_1408B5B20）/ 0x1408B61BE（宽字符绘制 sub_1408B5FF0）；nth=1 取第 2 | **u32 fontId**（0x1408B61AE `mov ecx,edi`） |

> 两命中处签名模式相同：`call sub_14085D930`（图集宽高查询）→ `mov ecx,edi` → `movd xmm0,[rsp+..]` → `cvtdq2ps`（即特征码 `0F 5B C0 44 0F 28 C6 F3`），off=-14 使 hook 落在 `movd xmm0` 处。

### 4.1 回调语义（旧版 3DM 硬编码 → 新版必须重写）

**#1/#2 (50F0/5150) 文本指针替换**（rcx 为合法指针，语义尚存）：
```
50F0: 取 a1+272 处 wchar** → D5B0 查词典 → 命中则替换指针
      新版上下文：hook 点前 `mov rcx, cs:unk_14294BE78` + `call qword ptr [rax+88h]`
      分配 0x2000 缓冲 → 回调实际拦截的是缓冲分配返回后的翻译时机
5150: 同 50F0, 字段为 a1+376
      新版上下文：sub_14082DD10 内 format 调用前夜，rdx=文本源，r8=0x1000
```

**#3 (4ED0) 字体度量 hook**（渲染核心）——**新版已证实签名漂移，旧回调必崩**：
```c
// 旧版假设: a1 = 游戏字体/文本对象 (fastcall rcx)
// 新版事实: sub_140858C10(u16 curChar, u16 nextChar, int* outW, int* outH, int fontId)
//           序言 movzx ebp,cx —— rcx 是字符码！
//           字体对象由内部 sub_140859B10(fontId) 从全局字体表获取
line_height = *(u16*)(font+22) * 0.7;   // 旧回调读 a1[48](+384)/a1[38](+304) 并解引用
...                                       // → [字符码+384] 解引用 = 必然访问违例
```
新版 sub_140858C10 语义（重写时的等价物）：
```
font = sub_140859B10(fontId)            // fontId<0 → scale 逻辑 (qword_142998180+16*(fontId+0x7FFFFFFF))
字符索引 v11 = cur - *(font+12), 范围 *(font+8)
字形度量: font+176 起 16 字节/项 (+0 宽, +4 高, +12 i16 advance)
kerning:  font+168 起 6 字节/项 (与 next 字符配对)
缺字 → *outW=0, *outH=(*(font+16)+*(font+28))*scale, 返回 -1
调用者 sub_1408590D0 = 文本测量循环 (kerning 累加、\n 换行)
```

**#4 (4FC0) 文本替换+长度修正**：D5B0 替换指针后，`*(int*)(a1+280) = wcslen(新文本)`。新版 hook 点 rcx=fontId → 旧回调写 [fontId+280] 必崩。

**#5 (5070) 宽字符文本替换**：a1+352 指针替换 + 写 `*(*(a1+384)+52/+56)` 图集宽高。新版 hook 点 rcx=fontId → 同样必崩。

### 4.2 字形度量缓存（1EFA0）

```
键 = (字号 v9, 字符 a3) → LCMapStringW 简繁归一化 (locale 由 Big5 配置决定: 2052 简中 / 1028 繁中)
值 = 80 字节度量记录 (含 asc/desc/advance/width/baseline)
存储 = BaseD3d11HookInstaller+528 哈希表 (FNV-1a-64(键) & mask, 开链)
miss → FE10 栅格化 → 写入缓存 + 挂到 LRU list (a1+504/+512/+520)
```

### 4.3 字形栅格化（FE10，FreeType 静态链接）

```c
if (PixelFont+25 /*降采样标志*/) size = size * 0.9;
FT_Set_Pixel_Sizes(face, 0, size);       // 26.6 定点: size<<6
if (FT_Load_Glyph(face, char, 4 /*FT_LOAD_RENDER*/)) return 0;
提取 bitmap → y 翻转 (GDI 顺序→D3D 顺序)
→ 位深转换: 目标 32bpp (a1+36==32) 或 16bpp
```

- FreeType 错误码/常量确认：35=FT_Err_Invalid_Argument、1970170211=FT_ENCODING_UNICODE charmap
- 字体文件加载（F630）：`waccess(路径)` → 失败则 `GetWindowsDirectoryW` 拼接重试 → `FT_Init_FreeType` → `FT_New_Face` → `FT_Select_Charmap(UNICODE)`
- 字体来源：`3DMGAME\3dm.ttf`（8.6 MB）

### 4.4 D3D11TextureFont 图集（D3E0 构造）

```
+8   : 图集像素缓冲 (texSize² × bpp, bpp 由配置 4 或 2)
+24  : PixelFont 子对象 (vtable 0x70000E97F0)
+80  : texSize>>8, +84: texSize>>8   (16×16 = 256 格索引)
+96~116: 渲染参数 (含纹理尺寸 4096)
+256 : 格子索引数组 ((texSize>>8)² × 4 字节)
+264/+312: Locker (CriticalSection, spin 0xFA0)
+368~400: 度量缓存 LRU 链表
+504~544: 字形哈希表
+568~608: 第二字形表
```
D3D11 动态加载：`LoadLibraryA("\D3D11.DLL")`、`D3DX11_%d.dll`、`D3DX11CreateTextureFromMemory`（GetProcAddress 运行时解析，故导入表无 D3D11）。

---

## 5. 配置与热键

### 5.1 INI（GetPrivateProfileIntW，文件不存在时用默认值）

```ini
[Font]
size_adjust = 0        ; 字号微调, 范围 -50..50 (dword_700010E5C4)
yoffset_adjust = 0     ; Y偏移, 范围 -100..100 (dword_700010E5D8)
[Common]
Charset = 1            ; 0=GBK? 1=默认 2=扩展 (byte_700010B3E0)
Big5 = 0               ; 繁体输出 (byte_700010E5C2, 影响 LCMapStringW locale)
```

### 5.2 运行时热键（WH_GETMESSAGE 回调 fn，需按住 Ctrl）

| 键 | 功能 |
|---|---|
| `.` | 翻译总开关翻转 |
| `p` | Charset 循环 0→1→2→0 |
| `q` | Big5 简繁切换 |
| `z` / `{` | size_adjust -1 / +1 |
| `x` / `y` | yoffset_adjust -1 / +1 |

所有调整实时 `WritePrivateProfileStringW` 写回 INI。

---

## 6. 崩溃原因分析（已证实，静态逆向闭环）

**根因：游戏 2026/1/29 更新后，渲染侧 hook 点的 rcx 语义从「对象指针」变为「整数」，旧回调按指针解引用全部必崩。**

| hook | 旧版假设 | 新版实测 | 后果 |
|---|---|---|---|
| #3 4ED0 (度量) | rcx = 字体对象 | rcx = u16 字符码（`movzx ebp,cx`） | 读 [字符码+384] → 访问违例 |
| #4 4FC0 (网格生成) | rcx = 文本对象 | rcx = u32 fontId | 写 [fontId+280] → 访问违例 |
| #5 5070 (宽字符绘制) | rcx = 文本对象 | rcx = u32 fontId | 读 [fontId+384] 再二次解引用 → 访问违例 |
| #1 50F0 (Lua 文本) | rcx = 对象 | rcx = 全局对象指针（未变） | 偏移 +272 是否漂移未逐字段核，风险较低 |
| #2 5150 (Lua 文本) | rcx = 对象 | rcx = 栈缓冲指针（未变） | 同上 |

三个渲染 hook 只要游戏一画文字就触发 → 启动即崩，与现象完全吻合。次生风险：#3 的 trampoline（189B0 生成）虽正确保存/恢复寄存器，但回调返回后已无需跳回——崩溃发生在回调体内。

其余已排除项：
- Trampoline 指令边界：189B0 保存全寄存器 + 按 RtlAddFunctionTable 语义复制指令，且 #3 安装在函数入口（off=-31），边界风险低——非主因
- #1/#2/#5 双命中取址：已逐一核对语义（#1 两处同函数、#2 第 2 处为 Wwise 音频无关代码、#5 第 2 处为宽字符绘制）→ nth 取址正确
- dumps 目录崩溃转储与汉化无关（用户确认系未从 Steam 启动导致）

> 结论：重写版不能复用 3DM 任何按对象偏移解引用的回调逻辑，需按新版语义全新设计（见第 7 节）。

---

## 7. 重写实现路线（ASI 插件）

**技术栈**：VS2017+ x64 / MinHook（替代自写 trampoline，规避指令边界问题）/ FreeType 2.x / C++17

### 阶段一：文本替换（可独立验证）
1. `.asi` DllMain → `DisableThreadLibraryCalls` + 初始化线程
2. 轮询 `GetModuleHandleA(NULL)` 就绪 → 解析 text.btxt（沿用 BEXT 格式，或简化为 key=orig 的纯文本格式）
3. CRC32(UTF-16 含 NUL) + FNV1a64 双层哈希表（或直接 std::unordered_map<u32, wstring>，性能足够）
4. MinHook 只装文本类 hook：
   - #1（0x14081CA6D，op=0 detour / MinHook 均可）→ 回调读 unk_14294BE78 分配的 0x2000 缓冲，翻译后写回（注意：新版对象布局下 50F0 的 a1+272 偏移需实测修正，可直接在回调里扫 wstring）
   - #2（0x14082DD97）→ 拦截 format 调用，rdx 为文本源，可在此翻译 rdx 指向文本或事后处理 rcx 缓冲
   - ⚠️ #4/#5 在旧版是文本替换 hook，但新版 hook 点 rcx=fontId，**指针替换路线失效**；这两处在新架构中改作渲染对接点（阶段二）
5. 验证：文本替换后英文 → 中文占位/空白（无字形渲染时正常），无崩溃即通过

### 阶段二：中文字形渲染
1. FreeType 加载 TTF → 按需栅格化缓存（std::map<(char,size), metrics> + LRU）
2. 4ED0 等效：行高 = 字号×0.42（0.7×0.6），度量取自字形缓存
3. 图集：4096×4096 R8 纹理 + stb_rect_pack 打包（替代 3DM 的 256 格固定布局，更省空间）
4. D3D11 对接：逆向确认 3DM 的图集注入点（BaseD3d11HookInstaller 的 vtable hook 链，候选：游戏字体纹理创建/更新时替换，或 Present 自绘）——**此为最后一块未完全逆向的部分**，实现前建议补逆 1C670/E8F0/DCB0/F5B0

### 阶段三：DumpText 工作流
- 未命中文本入队 → 后台线程写 DumpText.dtxt（追加式，u32 count 原子递增）
- 配套转换脚本：dtxt → 翻译表 → text.btxt

### 风险清单
- [x] 新版 exe 5 个安装点指令边界 → 已核对，全部落点安全（详见第 4 节表）
- [x] 回调 rcx 语义 → 已锁定（3 个渲染 hook rcx=整数，为崩溃根因）
- [x] 崩溃根因 → rcx 语义崩坏（第 6 节）
- [ ] D3D11 图集注入点逆向补全（3DM 候选：1C670/E8F0/DCB0/F5B0）
- [ ] text.btxt 从完整 3DM 补丁包重新获取
- [ ] DumpText 环形队列线程安全性（原版 mutex 保护，重写沿用）
- [ ] #1/#2 回调内对象偏移（+272/+376）在新 exe 上是否漂移（阶段一实测即知）

---

## 附录 A：关键函数地址速查（3dm64.dll）

| 地址 | 功能 |
|---|---|
| 0x7000004690 | DllMain |
| 0x7000005240 | 初始化（词典加载+魔数注册+尺寸设置） |
| 0x70000051B0 | 延迟初始化触发器（F320→4790→13380） |
| 0x700000F320 | SetWindowsHookExW(WH_GETMESSAGE) 安装 |
| 0x700000F010 | INI 读取 + INI 路径构建 |
| 0x700000ED80 | fn: 键盘热键回调 |
| 0x7000013380 | 补丁表主循环（扫描+分发安装） |
| 0x7000011500 | 模块内扫描器 |
| 0x70000113B0 | 全进程扫描器 |
| 0x700000B5C0 | Boyer-Moore Horspool 核心 |
| 0x7000018220 | op=0 detour 安装器 |
| 0x70000184C0 | op=1 vtable 替换安装器 |
| 0x70000189B0 | op=2/3 fastcall 保持型安装器 |
| 0x7000004ED0 | #3 字体度量回调 |
| 0x7000004FC0 | #4 文本替换+长度修正回调 |
| 0x7000005070 | #5 宽字符替换回调 |
| 0x70000050F0 | #1 文本指针替换回调 |
| 0x7000005150 | #2 文本指针替换回调 |
| 0x700000D5B0 | 替换核心（wstring→查词典→换指针） |
| 0x700000D310 | 词典两级查找（CRC32→FNV1a64→trim 重查） |
| 0x700000C1C0 | CRC-32 计算（含 L'\0'） |
| 0x700000C050 | CRC-32 查表生成（多项式 0x04C10DB7） |
| 0x700000B040 | CHelper 构造（含 6 对繁→简字形 fallback 映射） |
| 0x700000CDD0 | text.btxt 加载器 |
| 0x700000DC80 | std::wstring 拷贝构造 |
| 0x700000B960 | wstring trim（首尾空白） |
| 0x7000005730 | std::wstring assign |
| 0x700001EFA0 | 字形度量缓存查询（LCMapStringW 归一化+哈希） |
| 0x7000010050 | std::map 字形缓存查询 |
| 0x700000FE10 | FreeType 字形栅格化（+y 翻转+位深转换） |
| 0x7000032310 | FT_Set_Pixel_Sizes 包装（26.6 定点） |
| 0x700002FEC0 | FT_Load_Glyph 包装 |
| 0x700000F630 | 字体文件加载（waccess→WinDir 回退→FT_New_Face） |
| 0x700001D3E0 | D3D11TextureFont 构造（图集+格子索引） |
| 0x7000003F00 | DumpText 线程创建（0x191A0 上下文） |
| 0x7000009640 | DumpText 线程主循环（StartAddress） |
| 0x7000004C90 | BaseD3d11HookInstaller 单例 getter |
| 0x7000004790 | CodeHookInstaller 单例 getter |
| 0x7000004750 | CHelper 单例 getter |

## 附录 C：新版 SRTTR.exe 函数映射（2026/1/29 版，MD5 A4B0E11C...）

> RVA→文件偏移 = RVA − 0xC00。IDA 数据库：`汉化\.temp\SRTTR.exe.i64`

| 新版地址 | 功能 | 对应旧回调 |
|---|---|---|
| sub_140858C10 | 字符度量 `(u16 cur, u16 next, int* outW, int* outH, int fontId)`，序言 `movzx ebp,cx` | #3 (4ED0) |
| sub_140859B10 | fontId → 字体对象（全局字体表） | #3 内部 |
| sub_140859B00 | 单字符步进 `(cur, next, int* w, int* adv, fontId)` → 字形索引 | 度量辅助 |
| sub_1408590D0 | 文本测量循环（kerning 累加、\n 换行） | — |
| sub_14085D930 | 图集纹理宽高查询 `(texId, int* w, int* h)` | 渲染侧共用 |
| sub_1408B5B20 | 窄字符(UTF-8)文本绘制（逐字节扫描→quad） | #5 第 1 命中所在 |
| sub_1408B5FF0 | 宽字符文本绘制（0xFDD0-0xFDD3 PUA→`%[\]{` 映射、UV 从 font+192/+200、4 顶点/字） | #5 (5070) |
| sub_14016E7D0 | 宽字符→三角网格生成（每 64 字符一批、6 顶点/字、28 字节/顶点） | #4 (4FC0) |
| sub_14081C7A0 | `vint_insert_values_in_string` Lua 函数（模板串插值，0x2000 缓冲 ×2） | #1 (50F0) |
| sub_14081CEF0 | Lua 绑定注册（紧邻 `aVintInsertValu` 字符串） | #1 定位佐证 |
| sub_14082DD10 | Lua 脚本文本函数（format 栈缓冲 0x2000） | #2 (5150) |
| sub_140C4B2F8 | Wwise 音频字符串管理（AK::MemoryMgr::Malign 附近）| #2 第 2 命中（排除） |
| unk_14294BE78 | 全局管理对象（vtable+0x88 = 0x2000 缓冲分配） | #1 hook 点 rcx |
| unk_141537A30 | 顶点缓冲管理器（单例，9=类型，4=顶点数/字） | 渲染输出 |
| qword_142998180 | 字号缩放表（fontId<0 时 scale = +16*(fontId+0x7FFFFFFF)） | — |
| dword_14115F4C0 | 文本快照全局区（矩阵/fontId/顶点数，16E7D0 批尾写入） | — |

### hook 点寄存器速查（新 exe 实测）

| hook 点 VA | 关键指令 | rcx | 其他 |
|---|---|---|---|
| 0x14081CA6D | `mov rcx,cs:unk_14294BE78` | 全局对象 | 0x2000 缓冲分配（vtable+0x88） |
| 0x14082DD97 | （rbp-0x2000） | 栈缓冲 | rdx=文本源, r8=0x1000, r9=[rbx+0x138] |
| 0x140858C10 | `movzx ebp,cx` | u16 curChar | rdx=next, r8=outW, r9=outH, [rsp+0x50]=fontId |
| 0x14016E9BD | `mov ecx,edi` @-0x1E | u32 fontId | 图集查询后 UV 比例计算 |
| 0x1408B61B0 | `mov ecx,edi` @-0x2 | u32 fontId | 同上（宽字符版） |

## 附录 B：全局变量速查

| 地址 | 含义 |
|---|---|
| qword_700010EA60 | CHelper 单例 |
| qword_700010E5F0 | CodeHookInstaller 单例 |
| qword_700010E5F8 | BaseD3d11HookInstaller 单例 |
| qword_700010EA80 | 扫描器上下文单例 (0x130) |
| qword_700010EA58 | DumpText 线程上下文 |
| dword_700010E5C4 | size_adjust |
| dword_700010E5D8 | yoffset_adjust |
| byte_700010B3E0 | Charset |
| byte_700010E5C2 | Big5 |
| byte_700010E5C1 | 翻译开关 |
| lpFileName (0x700010B428) | INI 路径 (wstring) |
| 0x700010A660 | 补丁表（5 条 × 0x238） |

> AI生成
