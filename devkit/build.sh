#!/bin/sh
# 不用 Theos 的构建流程（零 Theos，全流程约 20 秒）
#
# 只用三样东西：
#   ① xcrun clang 直接编 dylib
#   ② codesign --sign - 自签（ellekit / RootHide 会跳过无 LC_CODE_SIGNATURE 的 dylib）
#   ③ devkit/mkdeb.sh 自己拼 deb（纯 Python，不依赖 dpkg-deb）
#
# 本项目**不需要 Logos**：没有 %hook 语法，全部是运行时 hook（objc runtime），
# 所以连 logos.pl 都不用下载，构建更快。
#
# 用法：sh devkit/build.sh <arm64|arm64e> <arch-name> <version>
#   例：sh devkit/build.sh arm64e iphoneos-arm64e 1.0-1
set -eu

CPU="${1:?用法: build.sh <arm64|arm64e> <arch-name> <version>}"
ARCH_NAME="${2:?}"
VER="${3:?}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/.build"
mkdir -p "$BUILD"

# iOS 部署目标：arm64e 从 14.0 起，arm64 从 13.0 起。
# 用户是 iOS 17.0.3，但保持较低部署目标不影响在任何更高版本运行，
# 反而让同一个产物能覆盖更多机型。
case "$CPU" in
  arm64)  TRIPLE="arm64-apple-ios13.0"  ;;
  arm64e) TRIPLE="arm64e-apple-ios14.0" ;;
  *) echo "未知架构: $CPU"; exit 1 ;;
esac

SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
echo "SDK   : $SDK"
echo "TRIPLE: $TRIPLE"

# ---------- 1) 编译 dylib ----------
# -undefined dynamic_lookup：ObjC runtime 的私有符号留到运行时由 SpringBoard 解析。
# -fobjc-arc：源码用 ARC。
# -Os：SpringBoard 里常驻，体积与速度平衡。
# ★ install_name 用 @loader_path/.jbroot/...：rootless/RootHide 下的标准写法，
#   与 SingleVPN 一致（那边已验证过实机可加载）。
xcrun clang -target "$TRIPLE" -isysroot "$SDK" \
  -dynamiclib -fobjc-arc -Os -Wall -Wextra \
  -Wno-unused-parameter \
  -undefined dynamic_lookup \
  -framework Foundation -framework UIKit -framework CoreFoundation \
  "$ROOT/Tweak.m" \
  -o "$BUILD/NoLockWallpaper.dylib" \
  -Xlinker -install_name -Xlinker \
    "@loader_path/.jbroot/Library/MobileSubstrate/DynamicLibraries/NoLockWallpaper.dylib"

# ---------- 2) 编译设置页 bundle ----------
# 设置页是个独立产物，跑在「设置」App 进程里（不是 SpringBoard）。
#
# ★ 为什么单独编一份 dylib 而不是把设置页代码塞进主 tweak：
#   主 tweak 注入 SpringBoard，设置页要跑在 Preferences 进程里，
#   两个进程、两套依赖，混在一起必然出事。
#
# ★ -F 指向自带的 Frameworks 目录：Preferences.framework 是私有框架，
#   iOS SDK 里没有它的头文件/存根，所以我们自带一份 .tbd。
#   .tbd 只提供「链接期符号声明」，不把库打进产物 —— 运行时由设置 App
#   进程里已经加载的 Preferences.framework 提供实现。
#
# ★ -framework Preferences 与 -F 缺一不可：
#   只 -F 不 -framework，链接器不会去解 PSListController 这些符号；
#   只 -framework 不 -F，找不到 tbd。
PREF_SRC="$ROOT/NoLockWallpaperPrefs"
PREF_OUT="$BUILD/NoLockWallpaperPrefs.bundle"
mkdir -p "$PREF_OUT"

xcrun clang -target "$TRIPLE" -isysroot "$SDK" \
  -dynamiclib -fobjc-arc -Os -Wall -Wextra \
  -Wno-unused-parameter \
  -undefined dynamic_lookup \
  -I"$PREF_SRC" \
  -F"$PREF_SRC/Frameworks" \
  -framework Foundation -framework UIKit -framework Preferences \
  "$PREF_SRC/NoLockWallpaperRootListController.m" \
  -o "$PREF_OUT/NoLockWallpaperPrefs" \
  -Xlinker -install_name -Xlinker \
    "@loader_path/NoLockWallpaperPrefs.bundle/NoLockWallpaperPrefs"

codesign --force --sign - "$PREF_OUT/NoLockWallpaperPrefs"

# ★ 设置页产物必须也过架构闸门。
#   主 dylib 的闸门管不到它 —— 这里漏检的后果是「插件能用但设置页白屏」，
#   或者更糟：架构不对导致写了 arm64e 设备也加载不上。
xcrun lipo -info "$PREF_OUT/NoLockWallpaperPrefs" | grep -q "$CPU" \
  || { echo "✗ 设置页 bundle 缺 ${CPU} 切片"; exit 1; }
echo "✓ 设置页 bundle 架构校验通过（${CPU}）"

# ---------- 3) 签名 ----------
codesign --force --sign - "$BUILD/NoLockWallpaper.dylib"

# ---------- 4) 架构硬闸门 ----------
echo "--- dylib ---"
file "$BUILD/NoLockWallpaper.dylib"
xcrun lipo -info "$BUILD/NoLockWallpaper.dylib"

xcrun lipo -info "$BUILD/NoLockWallpaper.dylib" | grep -q "$CPU" \
  || { echo "✗ dylib 缺 ${CPU} 切片"; exit 1; }

# ★ 变量后紧跟中文/全角字符必须写 ${CPU}：
#   bash 会把多字节 UTF-8 也算进变量名，`$CPU）` 变成变量 `CPU）`，
#   set -u 下直接 unbound variable（SingleVPN 项目 CI 实测踩过）。
echo "✓ 架构校验通过（${CPU}）"

# ---------- 5) 功能指纹闸门 ----------
# 这些符号/字符串任何一条丢了，产物表面看不出来，装到机器上就是功能缺失。
# ★ ObjC 选择子不进 nm -u（走 _objc_msgSend + __objc_methname），只能 grep -a。
#
# ★★ 这里踩过一个「闸门自己写错」的坑，记录清楚免得再犯：
#   代码里写的是点语法赋值 `g.minimumPressDuration = 90.0;`，
#   编译器把它翻成 objc_msgSend(g, @selector(setMinimumPressDuration:), 90.0)
#   —— 落在 __objc_methname 里的字符串是 **setMinimumPressDuration:**
#   （带 set 前缀、大写 M）。
#   最初的判据写的是 `minimumPressDuration`（getter 名），
#   大小写敏感 → 永远匹配不到 → 每次构建都在这里误报「缺符号」。
#   注意：属性名 ≠ 选择子名。凡是通过「赋值」使用的属性，指纹必须写 setter 名。
for sym in addGestureRecognizer: didMoveToWindow viewDidAppear: \
           UILongPressGestureRecognizer setMinimumPressDuration \
           LockScreen CoverSheet; do
  grep -aq "$sym" "$BUILD/NoLockWallpaper.dylib" \
    || { echo "✗ dylib 缺 ${sym}（功能会缺失）"; exit 1; }
done

# 相机滑动相关指纹：edge pan 判据与右边缘枚举值
for sym in UIScreenEdgePanGestureRecognizer edges Camera; do
  grep -aq "$sym" "$BUILD/NoLockWallpaper.dylib" \
    || { echo "✗ dylib 缺 ${sym}（禁左滑进相机会失效）"; exit 1; }
done

# 设置页开关读取指纹：配置域名与两个键名
for sym in com.liehuu.nolockwallpaper Enabled BlockCameraSwipe \
           com.liehuu.nolockwallpaper/changed; do
  grep -aq "$sym" "$BUILD/NoLockWallpaper.dylib" \
    || { echo "✗ dylib 缺 ${sym}（开关会失效）"; exit 1; }
done
echo "✓ 功能指纹齐全"

# 设置页产物指纹：控制器类名 + 载入入口 + ivar 存取方式 + 重启能力
# ★ 这里**不查** com.liehuu.nolockwallpaper：
#   设置页的开关读写全交给 PSSwitchCell（按 Resources/Root.plist 里的
#   defaults + key 走 Preferences 框架），控制器代码本身完全不碰这个域名。
#   早先版本声明过一个 kNLWDomain 常量但从未使用（-Wunused-const-variable），
#   删掉后这条闸门就开始误报「设置页缺域名」—— 属于「改了实现没同步改判据」。
#   域名的正确归属：dylib（读开关）+ Resources/Root.plist（写开关），
#   两边的对齐由 tools/check_source.py 的 A7b 校验。
for sym in NoLockWallpaperRootListController loadSpecifiersFromPlistName \
           object_getIvar SpringBoard; do
  grep -aq "$sym" "$PREF_OUT/NoLockWallpaperPrefs" \
    || { echo "✗ 设置页缺 ${sym}（进设置页会白屏 / 开关不生效）"; exit 1; }
done
echo "✓ 设置页指纹齐全"

# ---------- 6) 崩溃防护静态断言 ----------
# 这几条是「崩宿主」维度，不是「编不过」维度，必须在产物上验。
grep -aq 'objc_msgSend\|objc_msg_lookup' "$BUILD/NoLockWallpaper.dylib" \
  || echo "  提示：未见动态消息发送符号（可能已被优化，非致命）"

# 必须存在异常处理相关段（@try/@catch 生效的证据）
# ★ 段名别记错：ObjC 的 @try 落点是 __TEXT 段里的 __gcc_except_tab（LSDA），
#   配合 __objc_personality_v0 符号。**不存在**叫 __objc_exception 的段 ——
#   第一版判据查的就是这个名字，永远匹配不到，等于这条闸门从来没生效过。
if xcrun otool -l "$BUILD/NoLockWallpaper.dylib" | grep -q '__gcc_except_tab'; then
  echo "✓ 存在 __gcc_except_tab 段（@try 的 LSDA 落点）"
else
  echo "✗ 缺 __gcc_except_tab：@try/@catch 可能被优化掉，异常会漏出线程"
  exit 1
fi
if xcrun otool -l "$BUILD/NoLockWallpaper.dylib" | grep -q '__objc_personality'; then
  echo "✓ 存在 __objc_personality（ObjC 异常运行时挂钩）"
else
  echo "✗ 缺 __objc_personality：异常可能漏出线程"
  exit 1
fi

# ---------- 7) 打包 ----------
sh "$ROOT/devkit/mkdeb.sh" "$ARCH_NAME" "$VER"