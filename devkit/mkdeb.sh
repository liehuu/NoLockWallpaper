#!/bin/sh
# 打 deb 包（不依赖 Theos / dpkg-deb）
#
# 为什么自己写：整套 Theos 只为跑一次 dpkg-deb 不值得 —— clone 主仓库
# 加全部子模块要 11 分钟，而这里 1 秒就能做完同样的事。
# deb 的 ar 归档格式非常简单，标准库即可生成。
#
# 用法：sh devkit/mkdeb.sh <arch> <version>
#   arch    : iphoneos-arm64 | iphoneos-arm64e
#   version : 1.0-1
set -eu

ARCH="${1:?用法: mkdeb.sh <arch> <version>}"
VER="${2:?用法: mkdeb.sh <arch> <version>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/.build/stage"
OUT="$ROOT/.build/out"
PKG="com.liehuu.nolockwallpaper"
NAME="NoLockWallpaper"
# ★ 设置页的基础名单独给个变量，不要用 "$NAME"Prefs 这种拼接：
#   在 shell 里相邻的字符串会被拼一起，读起来极容易看错（以为少了引号），
#   后面改路径时也容易改漏。
PREFNAME="NoLockWallpaperPrefs"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/Library/MobileSubstrate/DynamicLibraries" \
         "$STAGE/Library/PreferenceBundles/$PREFNAME.bundle" \
         "$STAGE/Library/PreferenceLoader/Preferences/$PREFNAME" \
         "$OUT"

# ---- 载荷：主 tweak ----
cp "$ROOT/.build/$NAME.dylib" \
   "$STAGE/Library/MobileSubstrate/DynamicLibraries/$NAME.dylib"
cp "$ROOT/$NAME.plist" \
   "$STAGE/Library/MobileSubstrate/DynamicLibraries/$NAME.plist"

# ---- 载荷：设置页 bundle ----
# ★ 设置页需要**两个入口**，缺一个就进不去：
#   ① Library/PreferenceBundles/<X>.bundle/   ← 控制器 + 资源
#   ② Library/PreferenceLoader/Preferences/<X>/Root.plist ← 设置列表里的那一项
#   只放 ① 不会出现在设置里；只放 ② 点进去会白屏（找不到 bundle）。
PREFSRC="$ROOT/$PREFNAME"
PREFBUNDLE="$STAGE/Library/PreferenceBundles/$PREFNAME.bundle"
PREFENTRY="$STAGE/Library/PreferenceLoader/Preferences/$PREFNAME"

cp "$ROOT/.build/$PREFNAME.bundle/$PREFNAME" "$PREFBUNDLE/"
cp "$PREFSRC/Resources/Info.plist"           "$PREFBUNDLE/"
cp "$PREFSRC/Resources/Root.plist"           "$PREFBUNDLE/"
# ★ 三个尺寸都要拷：入口 plist 里写的是基础名 `icon.png`，
#   iOS 会按屏幕 scale 去找 icon@2x/@3x；只给 @2x/@3x 而缺基础名，
#   在部分版本（以及设置页缩略图缓存命中时）会显示成空白方块。
for _ic in icon.png icon@2x.png icon@3x.png; do
  if [ -f "$PREFSRC/Resources/$_ic" ]; then
    cp "$PREFSRC/Resources/$_ic" "$PREFBUNDLE/"
  fi
done

# 入口 plist 必须是**二进制** plist：
# PreferenceLoader 实际读的是二进制格式，XML 文本在部分版本上不认。
# 用 python 转一下（构建机一定有 python3，本脚本后面本来就要用它）。
python3 - "$PREFSRC/layout/Library/PreferenceLoader/Preferences/$PREFNAME/Root.plist" \
          "$PREFENTRY/Root.plist" <<'PYPLIST'
import plistlib, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src, 'rb') as f:
    obj = plistlib.load(f)
with open(dst, 'wb') as f:
    plistlib.dump(obj, f, fmt=plistlib.FMT_BINARY)
print('  ✓ 入口 plist 已转为二进制格式')
PYPLIST

# 权限：目录 755，文件 644
# ★ dylib 必须 755：644 会被 ellekit / RootHide 的加载器拒绝，
#   表现是「装了完全没反应」，dpkg 却不报任何错。
# ★ 设置页的可执行文件同理必须 755。
find "$STAGE" -type d -exec chmod 755 {} \;
find "$STAGE" -type f -exec chmod 644 {} \;
chmod 755 "$STAGE/Library/MobileSubstrate/DynamicLibraries/$NAME.dylib"
chmod 755 "$PREFBUNDLE/$PREFNAME"

# ---- control ----
# 版本号必须由参数派生，不能硬编码（SingleVPN v2.8 踩过：包名 v2.7、内容 v2.8）
# ★ Depends 加 preferenceloader：设置页入口靠它加载。
#   不带这个依赖的话，纯净设备上装完「设置里看不到」，
#   而 dpkg 不会提示任何问题 —— 排查成本极高。
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Name: NoLockWallpaper
Version: $VER
Architecture: $ARCH
Description: 锁屏防误触：禁长按换壁纸、禁左滑进相机
Maintainer: liehuu
Author: liehuu
Section: Tweaks
Depends: mobilesubstrate, preferenceloader
EOF
chmod 644 "$STAGE/DEBIAN/control"

# ---- postinst ----
# 装完只打印提示，**绝不 respring / sbreload / killall SpringBoard**。
#
# ★★ 这里踩过一次真实事故（1.1-1，用户 Sileo 报「dpkg 被中断，无法安装 deb」）：
#   postinst 是 dpkg 事务的**最后一步**。在 Sileo/Zebra 里安装时，
#   postinst 里跑 sbreload 会重启 SpringBoard，而 Sileo 是前台 App ——
#   一起被系统终止。此时 dpkg 尚未完成收尾（写 /var/lib/dpkg/status、
#   释放 /var/lib/dpkg/lock），事务被打断 → 锁残留 + 半装状态
#   → 之后**任何** deb 都装不上。
#   `|| true` 只能兜住「返回码非 0」，**兜不住「进程被杀 / 事务被切断」**。
#   这不属于「编不过」，属于「装包时炸环境」，比功能缺失严重得多。
#
# 正确做法：只打印，让用户自己决定何时重启桌面。
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
# 只输出提示，不做任何会影响宿主进程的动作。
set -e
printf '\n'
printf 'NoLockWallpaper 已安装。\n'
printf '生效方式：请手动重启桌面（respring）。\n'
printf '  · Sileo/Zebra 装完后点「Restart SpringBoard」\n'
printf '  · 或直接锁屏一次再解锁（多数情况即可生效）\n'
printf '开关位置：设置 → NoLockWallpaper\n'
printf '\n'
printf '应急停用（设置打不开时）：创建空文件\n'
printf '  /var/mobile/Library/Preferences/com.liehuu.nlw.disabled\n'
printf '再重启桌面，插件将完全不再动手。删掉该文件即恢复。\n'
printf '\n'
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

# ---- 打包 ----
DEB="$OUT/${PKG}_${VER}_${ARCH}.deb"
export STAGE OUT DEB
python3 - <<'PY'
import io, os, lzma, tarfile, hashlib, time

stage = os.environ['STAGE']
deb   = os.environ['DEB']

# 固定 mtime（整数）。用 SOURCE_DATE_EPOCH 便于可复现构建；
# ★ 必须是 int：float 秒会让 PAX 格式写扩展头。
_MTIME = int(os.environ.get('SOURCE_DATE_EPOCH') or 1700000000)


def _is_exec(rel):
    """哪些文件在 deb 里必须是可执行位。

    SpringBoard 注入的 dylib 如果丢了 +x，装上去表现是「插件完全没加载」，
    而 dpkg 不报任何错 —— 这类问题排查成本极高，必须精确。

    ★ 设置页 bundle 里的可执行文件同理：丢了 +x 就是「设置页白屏 / 点不进去」，
      而且完全没有报错。它的路径特征是
      Library/PreferenceBundles/<名>.bundle/<名>
      —— 目录里那个「没有扩展名的文件」才是 Mach-O 可执行体，
      同目录下的 Info.plist / Root.plist / icon@2x.png 都不能给 +x。
    """
    if rel.endswith('.dylib'):
        return True
    if rel.startswith('DEBIAN/') and rel.split('/')[-1] in \
            ('postinst', 'postrm', 'preinst', 'prerm'):
        return True
    # ---- 设置页 bundle 的可执行体 ----
    if rel.startswith('Library/PreferenceBundles/'):
        parts = rel.split('/')
        # 形如 Library/PreferenceBundles/X.bundle/X → 4 段，最后一段无扩展名
        if len(parts) == 4 and parts[-1] and '.' not in parts[-1]:
            bundle = parts[2]
            # ★ 不要把 '.bundle' 的长度写死成切片下标（[:-7]）：
            #   一旦哪天写成 [:-8] 就静默错一位，导致可执行文件拿不到 +x，
            #   而这里出错的表现是「设置页点不进去」，极难反查。
            if bundle.endswith('.bundle'):
                if bundle[:len(bundle) - len('.bundle')] == parts[3]:
                    return True
    return False


def _tar():
    """把 stage 目录打成 tar 字节流。

    ★ 不能遍历 os.walk 后对 dirs+files 逐个 tf.add(full, arcname=...)：
      tf.add 对一个目录会**递归**把整棵子树也加进去，而 os.walk 又会
      单独走到那些子目录 → 同一批文件被重复打包（实测出现
      './DEBIAN/control' 出现两次），deb 体积成倍膨胀。
    ★ 也不能只写 tf.add(stage, arcname='.')：那样文件权限取自本机文件系统，
      打包机上的 umask 会污染产物。
    正解：自己按 os.walk 的顺序逐个加入，并显式设置 mode。

    ★★ 必须显式 format=GNU_FORMAT（真实事故，2026-10-21 实机安装失败）：
      Python 3.8+ 的 tarfile.open(mode='w') 默认用 PAX_FORMAT。
      PAX 遇到「mtime 带小数秒 / uid 太大 / 路径非 ASCII」时，会在**每个**成员
      前插一个 typeflag='x' 的扩展头（名字 ././@PaxHeader）。
      dpkg 的 tar 实现不认 PAX 扩展头，安装时直接报：
        corrupted filesystem tarfile in package archive:
        unsupported PAX tar header type 'x'
        W: Unknown TAR header type 120
        E: Sub-process /usr/bin/dpkg returned an error code (1)
      实测那个 deb 里被插了 26 个 @PaxHeader（每个文件一个）。
      → 一律 GNU_FORMAT（支持长文件名，dpkg 完全认），并把 mtime 固定成整数。
      USTAR_FORMAT 也能用，但它对路径 >100 字符要求 prefix 字段，不如 GNU 稳。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w', format=tarfile.GNU_FORMAT) as tf:
        for root, dirs, files in os.walk(stage):
            dirs.sort()
            files.sort()
            for name in dirs:
                full = os.path.join(root, name)
                ti = tf.gettarinfo(full, arcname='./' + os.path.relpath(full, stage))
                ti.mode = 0o755
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = 'root'
                ti.mtime = _MTIME          # 整数，杜绝小数秒触发扩展头
                tf.addfile(ti)
            for name in files:
                full = os.path.join(root, name)
                ti = tf.gettarinfo(full, arcname='./' + os.path.relpath(full, stage))
                # ★ 不能读 os.stat().st_mode：脚本前面虽然 chmod 了，
                #   但 tarfile 在 Windows / 某些挂载上拿到的仍是 666。
                #   deb 里权限必须确定：dylib / postinst 755，其余 644。
                rel = os.path.relpath(full, stage).replace('\\', '/')
                ti.mode = 0o755 if _is_exec(rel) else 0o644
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = 'root'
                ti.mtime = _MTIME
                with open(full, 'rb') as fh:
                    tf.addfile(ti, fh)
    return buf.getvalue()


# data.tar 用 xz（与 Theos 产物一致）
data = lzma.compress(_tar(), format=lzma.FORMAT_XZ)

# control.tar 用 gz
# ★ 同样必须 GNU_FORMAT：PAX 会给 control/postinst 也插 @PaxHeader，
#   dpkg 读 control 时同样报 unsupported PAX tar header type 'x'。
import gzip
ctl = io.BytesIO()
with tarfile.open(fileobj=ctl, mode='w', format=tarfile.GNU_FORMAT) as tf:
    for name in ('control', 'postinst'):
        p = os.path.join(stage, 'DEBIAN', name)
        if os.path.exists(p):
            ti = tf.gettarinfo(p, arcname='./' + name)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            ti.mode = 0o755 if name in ('postinst', 'prerm', 'preinst', 'postrm') else 0o644
            ti.mtime = _MTIME
            with open(p, 'rb') as fh:
                tf.addfile(ti, fh)
control = gzip.compress(ctl.getvalue(), mtime=0)

members = [
    ('debian-binary', b'2.0\n'),
    ('control.tar.gz', control),
    ('data.tar.xz', data),
]

out = io.BytesIO()
out.write(b'!<arch>\n')
for name, blob in members:
    hdr = ('%-16s' % name).encode()
    hdr += ('%-12s' % str(int(time.time()))).encode()
    hdr += ('%-6s' % '0').encode()
    hdr += ('%-6s' % '0').encode()
    hdr += ('%-8s' % '100644').encode()
    hdr += ('%-10s' % str(len(blob))).encode()
    hdr += b'`\n'
    out.write(hdr)
    out.write(blob)
    if len(blob) % 2:
        out.write(b'\n')

blob = out.getvalue()

# ---------- 闸门：tar 里不得有任何扩展头（真实事故，必查）----------
# dpkg 的 tar 实现**不认** PAX 扩展头（typeflag 'x' = ASCII 120），
# 一旦出现，装包直接失败：
#   corrupted filesystem tarfile in package archive:
#   unsupported PAX tar header type 'x'
#   W: Unknown TAR header type 120
#   E: Sub-process /usr/bin/dpkg returned an error code (1)
#
# ★ 必须按 tar 的真实结构走：读一个头(512B) → 按 size 字段跳过数据区(按 512 对齐)
#   → 再读下一个头。不能每 512 字节盲扫！
#   第一版实现就是盲扫，结果在 CI 上报了**假警报**：dylib 是二进制，
#   其中某 512 字节块的偏移 156 处恰好是 'x'（ASCII 120），被当成 PAX 头。
#   报出来的"文件名"是 `_logos_superclass$SingleVPN_17$STUIStatusBar...`
#   —— 一看就是符号名，不是 tar 路径，即假阳性。
#   盲扫的误报率与「载荷里二进制字节的分布」相关，等于不可用。
#
# ★ 只拦 PAX 系的 'x'（per-file 扩展头）与 'g'（global 扩展头）：
#   dpkg 报的正是 "unsupported PAX tar header type 'x'"。
#   不拦 GNU 的 'L'（@LongLink）：那是规范内的长文件名机制，dpkg 认。
def _check_no_pax_headers(raw, label):
    bad = []
    off = 0
    n = len(raw)
    while off + 512 <= n:
        blk = raw[off:off + 512]
        if blk == b'\x00' * 512:      # 结尾的零块
            break
        tf = blk[156:157]
        size_f = blk[124:136].rstrip(b'\x00 ').decode('ascii', 'replace')
        try:
            size = int(size_f, 8) if size_f else 0
        except ValueError:
            size = 0
        if tf in (b'x', b'g'):
            nm = blk[0:100].rstrip(b'\x00').decode('ascii', 'replace')
            bad.append('%s@%d' % (nm, off))
        # 跳到下一个头：头 512 + 数据区向上取整到 512
        off += 512 + ((size + 511) // 512) * 512
    if bad:
        print('  ✗ %s 里有 %d 个 PAX 扩展头（dpkg 不认，装包必失败）:' % (label, len(bad)))
        for b in bad[:6]:
            print('      ', b)
        print('   修法：tarfile.open(..., format=tarfile.GNU_FORMAT) 且 mtime 取整')
        raise SystemExit(1)

import lzma as _lzma, gzip as _gz
_check_no_pax_headers(_lzma.decompress(data), 'data.tar')
_check_no_pax_headers(_gz.decompress(control), 'control.tar')
print('  ✓ tar 无 PAX 扩展头（GNU_FORMAT，dpkg 兼容）')

open(deb, 'wb').write(blob)
print('  %s  %d B  sha256=%s' % (os.path.basename(deb), len(blob),
                                 hashlib.sha256(blob).hexdigest()[:16]))
PY

echo "  -> $DEB"