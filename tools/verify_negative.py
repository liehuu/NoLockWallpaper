# -*- coding: utf-8 -*-
"""反向验证：往 Tweak.m 注入「已知会造成崩溃 / 回退」的写法，
确认 check_source.py 必须报失败。

★ 为什么必须做这件事（本项目铁律）：
  「检查项通过」有两种可能 —— 真的没问题，或者检查项压根没在跑。
  只有「注入违规 → 检查项非 0」才能证明它确实在工作。
  本项目已经踩过三次「闸门自己坏掉还报绿」的坑。

用法：
  python tools/verify_negative.py
全部拦截成功 = 退出码 0。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "Tweak.m")
BUILD_SH = os.path.join(ROOT, "devkit", "build.sh")
MKDEB_SH = os.path.join(ROOT, "devkit", "mkdeb.sh")
PREF_M = os.path.join(ROOT, "NoLockWallpaperPrefs",
                      "NoLockWallpaperRootListController.m")
CHECK_DEB = os.path.join(HERE, "check_deb.py")
CHK = os.path.join(HERE, "check_source.py")

PY = sys.executable


def run_check(src_path):
    """用指定 Tweak.m 跑一遍自检，返回 (通过?, 输出)。"""
    # check_source.py 里的 SRC 是常量，这里通过环境变量覆盖
    env = dict(os.environ)
    env["NLW_SRC_OVERRIDE"] = src_path
    p = subprocess.run([PY, CHK], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    return (p.returncode == 0), out


def run_check_build_sh(mutated_text):
    """用替换过的 build.sh 内容跑自检（验证「构建闸门判据写错」类检查项）。

    ★ 做法：把替换版 build.sh 写到临时目录，让 check_source.py 通过
      NLW_BUILD_OVERRIDE 读到它。这样才能证明那几条判据真的会 FAIL ——
      否则又是「检查项没在跑还报绿」。
    """
    return _run_with_override("NLW_BUILD_OVERRIDE", "build.sh", mutated_text)


def run_check_pref_m(mutated_text):
    """用替换过的设置页控制器源码跑自检。

    ★ 设置页跑在「设置」App 进程里，一旦它崩，用户连开关都进不去 ——
      严重性不低于崩 SpringBoard。所以「Preferences 七坑」那几条判据
      同样必须做反向验证，证明它们真的会 FAIL。
    """
    return _run_with_override("NLW_PREF_OVERRIDE",
                              "NoLockWallpaperRootListController.m", mutated_text)


def _write(tmpdir, filename, text):
    """把文本写到临时文件，返回路径（供需要传路径的检查器使用）。"""
    path = os.path.join(tmpdir, filename)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def run_check_mkdeb(mutated_text):
    """用替换过的 mkdeb.sh 跑自检（设置页载荷类判据）。"""
    return _run_with_override("NLW_MKDEB_OVERRIDE", "mkdeb.sh", mutated_text)


def run_check_checkdeb(mutated_text):
    """用替换过的 check_deb.py 跑自检（产物指纹维度类判据）。

    ★ 这一组防的是「产物校验脚本自己写错」—— 它跑在真实 deb 上，
      写错时要么永远 FAIL（误杀正确产物），要么永远 PASS（假绿）。
      两种都只能靠反向验证发现。
    """
    return _run_with_override("NLW_CHECKDEB_OVERRIDE", "check_deb.py", mutated_text)


def _run_with_override(env_key, filename, mutated_text):
    tmpdir = tempfile.mkdtemp(prefix="nlw_neg_ov_")
    try:
        target = os.path.join(tmpdir, filename)
        with io.open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(mutated_text)
        env = dict(os.environ)
        env[env_key] = target
        p = subprocess.run([PY, CHK], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        out = (p.stdout or "") + (p.stderr or "")
        return (p.returncode == 0), out
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


CASES = [
    # (用例名, 原文片段, 替换成, 期望失败的检查项关键字)
    (
        "去掉本类 IMP 的转发桩过滤（orig 可能是 objc_msgForward → 崩宿主）",
        "IMP ownImp = method_getImplementation(own);\n        if (NLWIsForwardingIMP(ownImp)) return NO;   // 转发桩 → 干净跳过",
        "IMP ownImp = method_getImplementation(own);",
        "对 ownImp 做转发桩过滤",
    ),
    (
        "回退路径也去掉转发桩过滤",
        "IMP cur = method_getImplementation(m);\n            if (NLWIsForwardingIMP(cur)) return NO;",
        "IMP cur = method_getImplementation(m);",
        "对回退路径的 cur 做转发桩过滤",
    ),
    (
        "直接引用 arm64 上不存在的 _objc_msgForward_stret（dylib 加载失败）",
        "fwdStret = dlsym(RTLD_DEFAULT, \"_objc_msgForward_stret\");",
        "fwdStret = (void *)&_objc_msgForward_stret;",
        "未在代码中直接写 &_objc_msgForward_stret",
    ),
    (
        "viewDidAppear 改回共用单一全局 orig（截断其他控制器生命周期）",
        "IMP o = NLWOrigViewDidAppearFor(self);",
        "IMP o = gOrigViewDidAppear;",
        "不再使用单一全局 gOrigViewDidAppear",
    ),
    (
        "取 gestureRecognizers 提前到判据之前（热路径无谓分配）",
        "if (!NLWIsSuppressibleLockScreenArea(v)) return 0;\n\n        NSArray *gs = v.gestureRecognizers;",
        "NSArray *gs = v.gestureRecognizers;\n\n        if (!NLWIsSuppressibleLockScreenArea(v)) return 0;",
        "判据在取 gestureRecognizers 之前",
    ),
    (
        "去掉 UIControl 排除（手电筒/相机长按被误伤）",
        "if ([v isKindOfClass:[UIControl class]]) return NO;",
        "",
        "判据排除 UIControl",
    ),
    (
        "去掉滚动容器排除（通知长按被误伤）",
        "if ([p isKindOfClass:[UIScrollView class]]) return NO;",
        "",
        "判据排除滚动容器",
    ),
    (
        "把 viewDidAppear 转发整个删掉（锁屏控制器初始化被砍）",
        "if (o && !NLWIsForwardingIMP(o)) {\n        ((void (*)(id, SEL, BOOL))o)(self, _cmd, animated);\n    }",
        "",
        "NLWViewDidAppear 存在真实转发调用",
    ),
    (
        "orig 转发前去掉判空（调 NULL 必崩）",
        "if (gOrigAddGesture) {\n        ((void (*)(id, SEL, id))gOrigAddGesture)(self, _cmd, g);\n    }",
        "((void (*)(id, SEL, id))gOrigAddGesture)(self, _cmd, g);",
        "gOrigAddGesture 每次调用前都判空",
    ),

    # ---------------- 1.1 新增：开关与相机滑动 ----------------
    (
        "开关改成把「键不存在」当 false（装完直接不生效）",
        "if (present) on = (v != 0);",
        "on = (v != 0);",
        "键缺失时按开启处理",
    ),
    (
        "删掉恢复已压制手势的逻辑（关掉开关等于没关）",
        "static void NLWRestoreAllSuppressed(void)\n{",
        "static void NLWRestoreAllSuppressed_UNUSED(void)\n{",
        "有恢复函数 NLWRestoreAllSuppressed 的定义",
    ),
    (
        "恢复时无条件设 enabled=YES（擅自打开系统本已关闭的手势）",
        "g.enabled = [en boolValue];",
        "g.enabled = YES;",
        "恢复时还原**原始** enabled",
    ),
    (
        "允许反复覆盖「原状」（关掉后长按仍是 90 秒）",
        "if ([gSuppressed objectForKey:g] == nil) {",
        "if (YES) {",
        "记录原状时只在首次记录",
    ),
    (
        "去掉右边缘限定（上滑解锁会被一起废掉）",
        "if (e & UIRectEdgeRight) return YES;",
        "if (e) return YES;",
        "判据要求 UIRectEdgeRight",
    ),
    (
        "相机滑动不看子开关（无法单独关闭）",
        "gBlockCameraSwipe && NLWIsCameraSwipeish(g, (UIView *)self)",
        "NLWIsCameraSwipeish(g, (UIView *)self)",
        "相机滑动受子开关控制",
    ),
    (
        "通知回调不切主线程（改 UI 相关对象有风险）",
        "dispatch_async(dispatch_get_main_queue(), ^{",
        "if (1) {",
        "通知回调切回主线程",
    ),
    (
        "把日志加回去（热路径写盘，watchdog 会杀进程）",
        "__attribute__((constructor)) static void NLWInit(void)",
        "static void NLWLog(NSString *s) { }\n\n__attribute__((constructor)) static void NLWInit(void)",
        "源码里已无日志落盘函数",
    ),
]

# ★ B 组：构建闸门本身的判据写错（真实误报过一次，潜伏三个 run 才暴露）
BUILD_CASES = [
    (
        "指纹列表退回 getter 名 minimumPressDuration（永远匹配不到 setter 字符串）",
        # ★ 替换点必须在 for sym 列表里。上一版替换的是 build.sh 里的文件名
        #   第一处出现（那句在注释里），等于没改到真正的指纹项 ——
        #   反向验证自己报了「漏放」，正好证明这个测试是有效的。
        "           UILongPressGestureRecognizer setMinimumPressDuration \\",
        "           UILongPressGestureRecognizer minimumPressDuration \\",
        "指纹列表含 setMinimumPressDuration",
    ),
    (
        "指纹列表漏掉 viewDidAppear:（第二层补丁失效也不会被发现）",
        "addGestureRecognizer: didMoveToWindow viewDidAppear: \\",
        "addGestureRecognizer: didMoveToWindow \\",
        "指纹列表覆盖三个 hook 选择子",
    ),
    (
        "指纹列表漏掉 CoverSheet（类名判据退化也不会被发现）",
        "LockScreen CoverSheet; do",
        "LockScreen; do",
        "指纹列表覆盖锁屏类名判据关键串",
    ),
    (
        "指纹列表漏掉 edge pan 判据（禁相机失效不会被发现）",
        "for sym in UIScreenEdgePanGestureRecognizer edges Camera; do",
        "for sym in Camera; do",
        "指纹列表覆盖 edge pan 判据",
    ),
    (
        "指纹列表漏掉 BlockCameraSwipe（相机子开关失效不会被发现）",
        "for sym in com.liehuu.nolockwallpaper Enabled BlockCameraSwipe \\",
        "for sym in com.liehuu.nolockwallpaper Enabled \\",
        "指纹列表覆盖配置域名与两个开关键",
    ),
    (
        # 枚举常量编译后是立即数，产物里没有字符串 → 闸门必然误报
        "指纹列表用枚举常量 UIRectEdgeRight（产物里根本没有此字符串）",
        "for sym in UIScreenEdgePanGestureRecognizer edges Camera; do",
        "for sym in UIScreenEdgePanGestureRecognizer edges Camera UIRectEdgeRight; do",
        "指纹列表不含编译后会消失的枚举常量",
    ),
    (
        "设置页产物不做架构校验（装了设置页白屏也发现不了）",
        'xcrun lipo -info "$PREF_OUT/NoLockWallpaperPrefs" | grep -q "$CPU"',
        'echo skip',
        "设置页产物也过架构闸门",
    ),
    (
        # ★ 本轮 CI 真实翻车原样复现（run 39713919672）：
        #   设置页代码压根不碰配置域名，闸门却要求产物里有 → 必然 exit 1。
        "设置页指纹误加配置域名（闸门必然误报，CI 直接失败）",
        "for sym in NoLockWallpaperRootListController loadSpecifiersFromPlistName \\\n"
        "           object_getIvar SpringBoard; do",
        "for sym in NoLockWallpaperRootListController com.liehuu.nolockwallpaper \\\n"
        "           object_getIvar SpringBoard; do",
        "设置页指纹不含配置域名",
    ),
    (
        "设置页指纹漏掉 object_getIvar（退回裸指针存取也不会被发现）",
        "           object_getIvar SpringBoard; do",
        "           SpringBoard; do",
        "设置页指纹含 object_getIvar",
    ),
    (
        "退回不存在的段名 __objc_exception（判据永远不生效）",
        'grep -q \'__gcc_except_tab\'',
        'grep -q \'__objc_exception\'',
        "build.sh 用 __gcc_except_tab 判 @try 生效",
    ),
    (
        # ★ 本轮 CI 真实翻车原样复现（run 39714345362）：
        #   __objc_personality 是未定义符号，段表里根本没有 → 必然 exit 1。
        "用 otool -l 查 __objc_personality（把符号当段查，闸门必然误报）",
        'if xcrun nm -u "$BUILD/NoLockWallpaper.dylib" 2>/dev/null \\\n'
        "     | grep -q '__objc_personality'; then",
        'if xcrun otool -l "$BUILD/NoLockWallpaper.dylib" \\\n'
        "     | grep -q '__objc_personality'; then",
        "build.sh 不用 otool -l 查 __objc_personality",
    ),
]

# ★ C 组：设置页控制器源码（跑在「设置」App 进程里，崩了用户连开关都进不去）
PREF_CASES = [
    (
        "自己声明 _specifiers ivar（偏移与真实布局错位 → 崩设置 App）",
        "@implementation NoLockWallpaperRootListController\n",
        "@implementation NoLockWallpaperRootListController\n"
        "{\n    NSArray *_specifiers;\n}\n",
        "设置页未自己声明 _specifiers ivar",
    ),
    (
        "引用 UIColorWell（iOS 13 设置 App 直接打不开）",
        "- (void)viewDidLoad",
        "- (Class)nlwX { return [UIColorWell class]; }\n\n- (void)viewDidLoad",
        "设置页未引用 UIColorWell",
    ),
    (
        "用 reloadSpecifier:nil（会崩）",
        "        self.title = @\"NoLockWallpaper\";",
        "        [self reloadSpecifier:nil animated:NO];\n        self.title = @\"NoLockWallpaper\";",
        "设置页未调用 reloadSpecifier:nil",
    ),
    (
        # ★ 本轮 CI 真实失败原样复现：ARC 下裸指针强转
        "用 ARC 禁止的裸指针强转读 ivar（本轮 CI 真实失败原因）",
        "    return nil;\n}\n\nstatic void NLWSetSpecifiers",
        "    return *(NSArray **)((char *)(__bridge void *)self + 8);\n"
        "}\n\nstatic void NLWSetSpecifiers",
        "设置页未用 ARC 禁止的裸指针强转读 ivar",
    ),
    (
        "退回 ivar_getOffset 手工指针算术（接着必然裸指针强转，ARC 编不过）",
        "            iv = class_getInstanceVariable([PSListController class],\n"
        "                                           \"_specifiers\");",
        "            iv = class_getInstanceVariable([PSListController class],\n"
        "                                           \"_specifiers\");\n"
        "            (void)ivar_getOffset(iv);",
        "设置页未用 ivar_getOffset 手工算指针",
    ),
    (
        "不实现 specifiers（用户看到整块空白）",
        "- (NSArray *)specifiers\n{",
        "- (NSArray *)specifiersDisabled\n{",
        "设置页自己实现了 specifiers",
    ),
    (
        "用没证据的 notify_post 通知名（点了可能没反应）",
        "        // 软杀：让 SpringBoard 有机会正常退出，比 SIGKILL 干净",
        "        notify_post(\"com.apple.springboard.respring\");\n"
        "        // 软杀：让 SpringBoard 有机会正常退出，比 SIGKILL 干净",
        "设置页未用没证据的 notify_post 通知名",
    ),
]

# ★ D 组：mkdeb.sh（设置页载荷类）
MKDEB_CASES = [
    (
        "漏拷设置页可执行文件（设置页白屏）",
        'cp "$ROOT/.build/$PREFNAME.bundle/$PREFNAME" "$PREFBUNDLE/"',
        'echo skip-pref-exec',
        "真的把设置页可执行文件拷进 PreferenceBundles",
    ),
    (
        "漏写 PreferenceLoader 入口 plist（设置里根本不显示本插件）",
        '          "$PREFENTRY/Root.plist" <<\'PYPLIST\'',
        '          "/tmp/nlw_no_entry.plist" <<\'PYPLIST\'',
        "真的写了 PreferenceLoader 入口 plist",
    ),
    (
        "PREFBUNDLE 指错目录（资源全落到别处，设置页打不开）",
        'PREFBUNDLE="$STAGE/Library/PreferenceBundles/$PREFNAME.bundle"',
        'PREFBUNDLE="$STAGE/Library/PreferenceBundlesX/$PREFNAME.bundle"',
        "PREFBUNDLE 指向 PreferenceBundles",
    ),
    (
        "入口 plist 不转二进制（部分版本 PreferenceLoader 不认）",
        "    plistlib.dump(obj, f, fmt=plistlib.FMT_BINARY)",
        "    plistlib.dump(obj, f)",
        "入口 plist 转二进制",
    ),
    (
        "control 丢掉 preferenceloader 依赖（纯净设备看不到设置页）",
        "Depends: mobilesubstrate, preferenceloader",
        "Depends: mobilesubstrate",
        "control 依赖 preferenceloader",
    ),
    (
        "图标只打包 @2x/@3x 漏掉基础名（部分版本显示空白方块）",
        "for _ic in icon.png icon@2x.png icon@3x.png; do",
        "for _ic in icon@2x.png icon@3x.png; do",
        "三个尺寸图标一起打包",
    ),
    (
        # ★ 真实事故原样复现（1.1-1）：postinst 里 sbreload 打断 dpkg 事务
        #   → 锁残留 + 半装 → Sileo 报「dpkg 被中断，无法安装 deb」
        # 锚点用 printf 行，不用含反斜杠转义的整行，避免用例因转义写法差异而 SKIP
        "postinst 里重启桌面（打断 dpkg 事务 → 锁残留，之后所有 deb 装不上）",
        "printf '再重启桌面，插件将完全不再动手。删掉该文件即恢复。",
        "if [ -x /usr/bin/sbreload ]; then /usr/bin/sbreload >/dev/null 2>&1 || true; fi\n"
        "printf '再重启桌面，插件将完全不再动手。删掉该文件即恢复。",
        "postinst 里不重启桌面",
    ),
]


CHECKDEB_CASES = [
    (
        # ★ 本轮真实翻车原样复现：UIRectEdgeRight 是枚举值 8，
        #   产物里一个字节都没有 → 判据必然 FAIL，而产物完全正确。
        "用枚举常量做产物指纹（该名字编译后不存在，判据必然误杀）",
        'b"UIScreenEdgePanGestureRecognizer", b"edges"):',
        'b"UIScreenEdgePanGestureRecognizer", b"edges",\n'
        '                       b"UIRectEdgeRight"):',
        "check_deb.py 不用枚举常量做产物指纹",
    ),
    (
        # ★ 1.1-1 事故的第二道防线：产物侧对维保脚本的硬判据。
        #   删掉 _MNT_BAD 后，check_deb.py 就再也发现不了 postinst 里的 sbreload
        #   → 坏包会一路绿灯进交付目录。
        "删掉产物侧「维保脚本不重启桌面」判据（坏包能过生产校验）",
        "_MNT_BAD = (",
        "_MNT_BAD_UNUSED = (",
        "check_deb.py 校验维保脚本不重启桌面",
    ),
]


def _run_group(label, cases, orig_text, runner, tmpdir, fails):
    """跑一组「原地替换源码 → 期望自检 FAIL」的用例。返回本组用例数。"""
    n = 0
    for idx, (name, old, new, expect) in enumerate(cases, 1):
        n += 1
        if old not in orig_text:
            print("[%s%d] SKIP  %s" % (label, idx, name))
            print("      原因：源码里找不到要替换的片段（用例本身过期了，需更新）")
            fails.append("%s（用例片段过期）" % name)
            continue

        mutated = orig_text.replace(old, new, 1)
        if mutated == orig_text:
            print("[%s%d] SKIP  %s（替换无效）" % (label, idx, name))
            fails.append("%s（替换无效）" % name)
            continue

        ok2, out2 = runner(mutated)
        blocked = (not ok2) and (expect in out2)
        status = "OK   已拦截" if blocked else "!! 漏放"
        print("[%s%d] %s  %s" % (label, idx, status, name))
        if not blocked:
            fails.append(name)
            # 打印诊断：是没失败，还是失败在别的检查项上
            if ok2:
                print("      -> 自检居然全过：这条判据没有生效")
            else:
                tail = [l for l in out2.splitlines() if l.strip().startswith("- ")]
                print("      -> 失败了但不是期望的那项，实际失败：%s" % tail)
    return n


def main():
    original = io.open(SRC, encoding="utf-8").read()
    build_sh_orig = io.open(BUILD_SH, encoding="utf-8").read()
    pref_m_orig = io.open(PREF_M, encoding="utf-8").read()
    mkdeb_orig = io.open(MKDEB_SH, encoding="utf-8").read()
    checkdeb_orig = io.open(CHECK_DEB, encoding="utf-8").read()

    ok, out = run_check(SRC)
    print("=" * 70)
    print("基线：原始源码必须全部通过")
    print("=" * 70)
    if not ok:
        print("!! 基线就不通过，先修源码，不要做反向验证")
        print(out[-3000:])
        return 2

    baseline_ok = "全部通过" in out or "失败 0 项" in out
    print("基线 %s\n" % ("通过" if baseline_ok else "异常（输出里找不到『全部通过』）"))
    if not baseline_ok:
        print(out[-2000:])
        return 2

    fails = []
    total = 0
    tmpdir = tempfile.mkdtemp(prefix="nlw_neg_")
    try:
        # ---------- A 组：改 Tweak.m ----------
        total += _run_group("A", CASES, original,
                            lambda t: run_check(_write(tmpdir, "Tweak_neg.m", t)),
                            tmpdir, fails)
        # ---------- B 组：改 build.sh ----------
        total += _run_group("B", BUILD_CASES, build_sh_orig,
                            run_check_build_sh, tmpdir, fails)
        # ---------- C 组：改设置页控制器 ----------
        total += _run_group("C", PREF_CASES, pref_m_orig,
                            run_check_pref_m, tmpdir, fails)
        # ---------- D 组：改 mkdeb.sh ----------
        total += _run_group("D", MKDEB_CASES, mkdeb_orig,
                            run_check_mkdeb, tmpdir, fails)
        # ---------- E 组：改 check_deb.py（产物校验脚本自己写错） ----------
        total += _run_group("E", CHECKDEB_CASES, checkdeb_orig,
                            run_check_checkdeb, tmpdir, fails)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 70)
    if fails:
        print("反向验证未通过 %d 项：" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("反向验证全部通过：%d 个注入违规均被拦下" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())