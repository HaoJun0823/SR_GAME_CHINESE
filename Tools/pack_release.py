# -*- coding: utf-8 -*-
"""把 release/{target}/ 打成可直接分发的中文名 zip。

用法：
    python Tools/pack_release.py <out_root> [--date YYYYMMDD]

输出（写到 <out_root>/ 下，与包目录同级）：
    《黑道圣徒III》_简体中文_通用_补丁_<date>.zip
    《黑道圣徒IV》_简体中文_通用_补丁_<date>.zip
    《黑道圣徒IV》_简体中文_微软商店_补丁_<date>.zip

★ 为什么用 Python zipfile 而不是 7z / Compress-Archive：
  1. **UTF-8 文件名标志位**：`zipfile` 对非 ASCII 条目名会**自动置 bit 11**
     （通用位标志的 UTF-8 位），Windows 自带解压器才能正确显示中文条目名。
     7-Zip 需要额外参数，Compress-Archive 则**根本不设置**该标志（会乱码）。
  2. **跨平台**：同一个脚本在 windows-2022 与 ubuntu-latest 上行为一致，
     打包逻辑只有一份，不会出现「两个 job 各压一次、结果不一样」。
  3. **可本地测试**：不需要装 7-Zip。

★ 压缩级别 9 + deflate：与原先 `zip -r -9 -UN=UTF8` 的效果对齐。
"""
import argparse
import datetime
import io
import os
import sys
import zipfile

# target 名 -> 分发用中文名（不含日期与扩展名）
ZIP_NAMES = {
    'sr3r_common':    '《黑道圣徒III》_简体中文_通用_补丁',
    'sr4r_common':    '《黑道圣徒IV》_简体中文_通用_补丁',
    'sr4r_microsoft': '《黑道圣徒IV》_简体中文_微软商店_补丁',
}


def sanitize_arcname(name):
    """统一 zip 内的路径分隔符为 '/'（zip 规范要求，Windows 的 '\\' 会让部分解压器出错）。"""
    return name.replace('\\', '/')


def pack_one(src_dir, out_zip, compresslevel=9):
    """把 src_dir 的内容（不含 src_dir 本身）打包成 out_zip。返回 (文件数, 字节数)。

    ★ 用「先写条目的 data 再 close」的默认方式即可 —— zipfile 会自动处理
      UTF-8 标志位；不要手动构造 ZipInfo 并自己设 flag_bits（容易漏掉其它位）。
    """
    n = 0
    total = 0
    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED,
                         compresslevel=compresslevel) as z:
        # 排序保证产物可复现（同一输入 → 字节级相同的 zip 结构）
        for root, dirs, files in os.walk(src_dir):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, src_dir)
                z.write(full, sanitize_arcname(rel))
                n += 1
                total += os.path.getsize(full)
    return n, total


def main():
    ap = argparse.ArgumentParser(description='把 release 包目录压成中文名 zip')
    ap.add_argument('out_root', help='release 根目录（含 sr3r_common/ 等子目录）')
    ap.add_argument('--date', default=None,
                    help='构建日期 YYYYMMDD（默认取当前 UTC+8 日期）')
    ap.add_argument('--targets', default=','.join(ZIP_NAMES),
                    help='限定目标（逗号分隔）')
    ap.add_argument('--require-all', action='store_true', default=True,
                    help='（默认开启）缺少任一期望目录即失败')
    args = ap.parse_args()

    out_root = os.path.abspath(args.out_root)
    if not os.path.isdir(out_root):
        print(f'!! 输出根目录不存在: {out_root}')
        return 1

    if args.date:
        date = args.date
    else:
        # ★ 显式用 UTC+8 —— runner 默认 UTC，直接用会把日期算错一天
        date = (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(hours=8)).strftime('%Y%m%d')

    targets = [t.strip() for t in args.targets.split(',') if t.strip()]
    print(f'打包 {len(targets)} 个目标  date={date}  out={out_root}')

    # ★★ 先做**全量存在性检查**，再开始压。
    #   否则缺了一个目录时会「先压出前几个 zip 再报错」—— 留下半套产物，
    #   若调用方只判断「有没有 zip」而忽略退出码，就会误发不完整的包。
    #   宁可一个都不产出，也不要产出半套。
    unknown = [t for t in targets if t not in ZIP_NAMES]
    if unknown:
        print(f'!! 未知目标（不在白名单内，拒绝打包）: {", ".join(unknown)}',
              file=sys.stderr)
        return 1

    missing = [t for t in targets if not os.path.isdir(os.path.join(out_root, t))]
    if missing:
        print(f'!! 缺少期望的包目录: {", ".join(missing)}（不产出任何 zip）',
              file=sys.stderr)
        return 1

    made = []
    for name in targets:
        base = ZIP_NAMES[name]
        src = os.path.join(out_root, name)
        out_zip = os.path.join(out_root, f'{base}_{date}.zip')
        n, total = pack_one(src, out_zip)
        zsize = os.path.getsize(out_zip)
        print(f'  {name:16s} -> {os.path.basename(out_zip)}')
        print(f'      {n} 文件, {total/1048576:.1f} MB -> zip {zsize/1048576:.1f} MB')
        made.append(out_zip)

    # 报告未打包的目录（不失败，只提示 —— 便于发现构建污染）
    for d in sorted(os.listdir(out_root)):
        p = os.path.join(out_root, d)
        if os.path.isdir(p) and d not in targets:
            print(f'  提示：目录 {d} 不在发布白名单内，未打包')

    print(f'共打包 {len(made)} 个 zip')
    return 0


if __name__ == '__main__':
    sys.exit(main())
