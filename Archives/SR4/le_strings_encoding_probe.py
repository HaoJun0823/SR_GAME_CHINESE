"""独立取证: 直接读原始字节, 不经过任何自定义解析器。
逐个 le_strings 文件, 定位第一个字符串条目, 打印原始 hex, 并同时按
UTF-16LE 与 UTF-32LE 两种方式解码, 让人眼直接判断哪种合理。
"""
import os, struct, glob

FILES = [
    (r'G:\Projects\SR_GAME_CHINESE\data\SR4\microsoft\misc\activity_us.le_strings', 'MS activity'),
    (r'G:\Projects\SR_GAME_CHINESE\data\SR4\microsoft\misc\menu_us.le_strings',     'MS menu'),
    (r'G:\Projects\SR_GAME_CHINESE\data\SR4\microsoft\misc\platform_pc_us.le_strings', 'MS platform_pc'),
    (r'G:\Projects\SR_GAME_CHINESE\data\SR4\common\misc\activity_us.le_strings',    'PC activity'),
    (r'G:\Projects\SR_GAME_CHINESE\data\SR4\common\misc\menu_us.le_strings',        'PC menu'),
    (r'G:\Projects\SR_GAME_CHINESE\data\SR4\common\misc\platform_ggp_us.le_strings','PC platform_ggp'),
    (r'G:\Projects\SR_GAME_CHINESE\data\SR4\common\misc\platform_pc_us.le_strings', 'PC platform_pc'),
]

MAGIC = 0xA84C7F73

for path, tag in FILES:
    print('=' * 78)
    print(f'{tag}  {os.path.basename(path)}')
    if not os.path.isfile(path):
        print('  MISSING'); continue
    buf = open(path, 'rb').read()
    print(f'  size={len(buf)}')
    hdr = struct.unpack_from('<IHHI', buf, 0)
    print(f'  magic=0x{hdr[0]:08X}  ver={hdr[1]}  buckets={hdr[2]}  stringCount={hdr[3]}')
    if hdr[0] != MAGIC:
        print('  !! 非 le_strings 魔数'); continue
    nb = hdr[2]
    # 取第 0 个 bucket 的 offset 表, 找第一个非零条目绝对偏移
    c0, _, off0, _ = struct.unpack_from('<IIII', buf, 12)
    print(f'  bucket0: count={c0} offsetTableAbs={off0}')
    first_abs = None
    for i in range(c0):
        e = off0 + i * 8          # ★ 步长 8
        o = struct.unpack_from('<I', buf, e)[0]
        if o:
            first_abs = o
            break
    if first_abs is None:
        print('  !! 未找到非零条目'); continue
    print(f'  第一条目绝对偏移 = 0x{first_abs:06X}')
    h = struct.unpack_from('<I', buf, first_abs)[0]
    print(f'  条目 hash = 0x{h:08X}')
    # 从 first_abs+4 起, 截 48 字节原始载荷
    pay = buf[first_abs + 4: first_abs + 4 + 48]
    print(f'  原始载荷 48B:')
    print('    ' + ' '.join(f'{b:02X}' for b in pay[:16]))
    print('    ' + ' '.join(f'{b:02X}' for b in pay[16:32]))
    print('    ' + ' '.join(f'{b:02X}' for b in pay[32:48]))
    # UTF-16LE 解
    u16 = []
    for k in range(0, len(pay) - 1, 2):
        v = pay[k] | (pay[k + 1] << 8)
        if v == 0:
            break
        u16.append(v)
    s16 = ''.join(chr(c) for c in u16)
    # UTF-32LE 解 (容错: 非法码位用 U+FFFD 代替)
    u32 = []
    for k in range(0, len(pay) - 3, 4):
        v = struct.unpack_from('<I', pay, k)[0]
        if v == 0:
            break
        u32.append(v)
    s32 = ''.join(chr(c) if c < 0x110000 else '\ufffd' for c in u32)
    print(f'  按 UTF-16LE 解 -> {s16!r}   (字符数 {len(s16)})')
    print(f'  按 UTF-32LE 解 -> {s32!r}   (字符数 {len(s32)})')
    # 自动判据: 高半字非零比例
    if len(pay) >= 16:
        hi_nz = sum(1 for k in range(2, 16, 4) if pay[k]) + sum(1 for k in range(3, 16, 4) if pay[k])
        print(f'  判据A: 前 16B 中 (4k+2,4k+3) 位非零字节数 = {hi_nz} '
              f'-> {"UTF-16" if hi_nz else "UTF-32"}')
