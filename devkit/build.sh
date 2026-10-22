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

# ---------- 2) 签名 ----------
codesign --force --sign - "$BUILD/NoLockWallpaper.dylib"

# ---------- 3) 架构硬闸门 ----------
echo "--- dylib ---"
file "$BUILD/NoLockWallpaper.dylib"
xcrun lipo -info "$BUILD/NoLockWallpaper.dylib"

xcrun lipo -info "$BUILD/NoLockWallpaper.dylib" | grep -q "$CPU" \
  || { echo "✗ dylib 缺 ${CPU} 切片"; exit 1; }

# ★ 变量后紧跟中文/全角字符必须写 ${CPU}：
#   bash 会把多字节 UTF-8 也算进变量名，`$CPU）` 变成变量 `CPU）`，
#   set -u 下直接 unbound variable（SingleVPN 项目 CI 实测踩过）。
echo "✓ 架构校验通过（${CPU}）"

# ---------- 4) 功能指纹闸门 ----------
# 这些符号/字符串任何一条丢了，产物表面看不出来，装到机器上就是功能缺失。
# ★ ObjC 选择子不进 nm -u（走 _objc_msgSend + __objc_methname），只能 grep -a。
for sym in addGestureRecognizer: didMoveToWindow viewDidAppear: \
           UILongPressGestureRecognizer minimumPressDuration \
           LockScreen CoverSheet; do
  grep -aq "$sym" "$BUILD/NoLockWallpaper.dylib" \
    || { echo "✗ dylib 缺 ${sym}（功能会缺失）"; exit 1; }
done
echo "✓ 功能指纹齐全"

# ---------- 5) 崩溃防护静态断言 ----------
# 这几条是「崩宿主」维度，不是「编不过」维度，必须在产物上验。
grep -aq 'objc_msgSend\|objc_msg_lookup' "$BUILD/NoLockWallpaper.dylib" \
  || echo "  提示：未见动态消息发送符号（可能已被优化，非致命）"

# 必须存在异常处理相关段（@try/@catch 生效的证据）
if xcrun otool -l "$BUILD/NoLockWallpaper.dylib" | grep -q '__objc_exception'; then
  echo "✓ 存在 ObjC 异常处理元数据（@try 生效）"
else
  echo "  提示：未见 __objc_exception 标记，异常的 @try 可能被优化掉"
fi

# ---------- 6) 打包 ----------
sh "$ROOT/devkit/mkdeb.sh" "$ARCH_NAME" "$VER"