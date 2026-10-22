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
        # ★ 1.4-1 更新锚点：判据已从 `if (!NLWIsSuppressibleLockScreenArea(v))`
        #   改为 `BOOL areaOK = ...`（因为要复用到 camByDesc 旁路）。
        #   负例相应改成「判据之前先取一次数组」—— 语义与原来完全一致。
        "        BOOL areaOK = NLWIsSuppressibleLockScreenArea(v);",
        "        NSArray *gs0 = v.gestureRecognizers;\n"
        "        BOOL areaOK = NLWIsSuppressibleLockScreenArea(v);",
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
    # ---- 1.4-1 新增：相机手势「description 语义」旁路的四条回归 ----
    #
    # 这四条覆盖的是本轮真正的修复。它们的共同点是：
    # **改坏之后编译照过、锁屏照常、功能悄悄退回不生效** ——
    # 属于最难靠人工发现的一类回归，必须靠闸门拦。
    (
        # 语义被抹平：改回「先过区域判据」
        # → 相机手势宿主在 _UISystemGestureWindow，天然不满足该判据
        # → 依然不生效，而且源码看起来更「整齐」
        "description 命中后仍要求过区域判据（相机手势重新失效）",
        "hit = (camByDesc || camBySwipe) ? YES",
        "hit = NLWIsSuppressibleLockScreenArea((UIView *)self);",
        "description 命中时绕过锁屏区域判据",
    ),
    (
        "压制循环里把 camByDesc 也门在 areaOK 后面（等于没有这条旁路）",
        "if (camByDesc || camBySwipe ||\n",
        "if ((areaOK && camByDesc) || camBySwipe ||\n",
        "压制循环里 description 命中也绕过区域判据",
    ),
    (
        # ★ 这是最危险的误伤方向：长按跟着旁路走
        # → 主屏图标长按、锁屏通知长按会一起被压
        "长按跟着新旁路走（主屏图标长按/通知长按被误伤）",
        "                    if (!areaOK) continue;\n",
        "",
        "长按仍严格限定在锁屏区域",
    ),
    (
        # 功能上等价，但热路径回退：每个视图都要取一次 gestureRecognizers
        "系统手势窗口放行时放弃热路径优化（启动期白分配几百次）",
        "if (!areaOK && !NLWIsSystemGestureHost(v) && !NLWIsLockScreenWindowHost(v)) {",
        "if (!areaOK) {",
        "系统手势窗口放行但不放弃热路径优化",
    ),
    (
        # 少了锁屏主窗口这一路 → 「相机手势挂在锁屏视图树上」那种情况覆盖不到
        "入口只认系统手势窗口（锁屏窗口上的相机手势扫不到）",
        "!NLWIsSystemGestureHost(v) && !NLWIsLockScreenWindowHost(v)",
        "!NLWIsSystemGestureHost(v)",
        "系统手势窗口放行但不放弃热路径优化",
    ),
    # ---- 1.5-1 新增：swipe 类判据的六条回归 ----
    #
    # 背景：1.4-1 的日志 108 条全是 Pan 类，edges 里没有 8 ——
    # 相机手势不是 pan 类，被类型过滤连续挡了两道。
    # 以下六条锁的是「不要再退回只认 pan 类 / 不要放宽到误伤通知」。
    (
        # 类名兜底不排除 pan 类 → _UISwipeActionPanGestureRecognizer 被当成
        # 相机手势压掉 → 锁屏通知再也删不掉
        "swipe 判据不排除 pan 类（左滑删除通知被废掉）",
        "        if (NLWIsPanish(g)) return NO;\n",
        "",
        "swipe 判据先排除 pan 类",
    ),
    (
        # 方向写错：左滑变右滑 → 相机不受影响，反而废掉右滑呼出通知中心
        "swipe 判据方向写成右滑（相机没压到，反废通知中心）",
        "UISwipeGestureRecognizerDirectionLeft",
        "UISwipeGestureRecognizerDirectionRight",
        "swipe 判据读 direction 且只认左滑",
    ),
    (
        # 只在 add 层接入、压制循环没接 → 已挂好的手势扫不到
        "压制循环没接 swipe 判据（已存在的手势扫不到）",
        "                BOOL camBySwipe = (gBlockCameraSwipe && NLWIsCameraSwipeBySwipe(g));",
        "                BOOL camBySwipe = NO;",
        "两条压制路径都接入了 swipe 判据",
    ),
    (
        # add 层不判作用域 → 主屏/App 内所有左滑被一起压
        "add 层不给 swipe 判据加作用域（主屏左滑被误伤）",
        "                camBySwipe = (NLWIsSystemGestureHost((UIView *)self) ||",
        "                camBySwipe = (YES ||",
        "swipe 判据在 add 层仍受作用域约束",
    ),
    (
        # ★ 本轮最要紧的一条：诊断自己制造盲区
        "诊断退回只记滑动类（非 pan 的相机手势永远查不到）",
        "if (!isEdge && !isPan && !sysHost && !sup) return;",
        "if (!isEdge && !isPan) return;",
        "诊断不再只记录 pan 类",
    ),
    (
        "诊断的 desc 恢复成只对 pan 类记录（盲区失明）",
        "            NSString *d = [g description];",
        "            if (NLWIsPanish(g)) {\n            NSString *d = [g description];",
        "诊断的 desc 不再限定 pan 类",
    ),
    # ---- 1.6-1 新增：拦「启用」而非「改状态」的六条回归 ----
    #
    # 前三版的压制手段只有 enabled=NO，而相机入口抓到时就**已经是 NO** ——
    # 等于一直在压制一个本来就关闭的东西，看起来成功、实际零作用。
    # 这六条锁的是「必须拦启用动作、且拦得足够窄、且一定转发原实现」。
    (
        "不装 setEnabled hook（懒启用的相机入口再次拦不住）",
        "@selector(setEnabled:)",
        "@selector(setEnabledLegacy:)",
        "安装了 -setEnabled: 的 hook",
    ),
    (
        # ★ 最严重的一条：签名错 = arm64e 上参数错位 = 必崩宿主
        "setEnabled hook 签名写错（arm64e 参数错位，必崩）",
        '(IMP)NLWSetEnabled, &o, "v@:B"',
        '(IMP)NLWSetEnabled, &o, "v@:@"',
        "setEnabled hook 用 v@:B 签名",
    ),
    (
        "setEnabled hook 连「关闭」也过问（干扰系统状态机）",
        "        if (enabled && self) {",
        "        if (self) {",
        "setEnabled hook 只在「启用」时过问",
    ),
    (
        # 不转发 = enabled 永远改不动 = 所有手势状态错乱，比不生效严重得多
        "setEnabled hook 不转发原实现（手势状态错乱）",
        "        ((void (*)(id, SEL, BOOL))gOrigSetEnabled)(self, _cmd, block ? NO : enabled);",
        "        ;",
        "setEnabled hook 一定转发原实现",
    ),
    (
        # ★ 误伤方向：把边缘手势仲裁器也拦了
        "候选类被一起拦截（边缘手势仲裁失效）",
        "                NLWDiagNoteEnable(n, NO);",
        "                NLWDiagNoteEnable(n, YES); block = YES;",
        "候选类只记录、不拦截",
    ),
    (
        "硬判定漏掉 HorizontalScrollFailure（拦不住锁屏相机入口）",
        '    if (strstr(n, "HorizontalScrollFailure")) return YES;\n',
        "",
        "硬判定只认 Camera / HorizontalScrollFailure 两类",
    ),
    (
        # 只有 setEnabled hook，抓到时已经是 YES 的那次补不上
        "去掉压制循环里的即时压制（已启用的那次漏掉）",
        "                    if (gBlockCameraSwipe &&\n"
        "                        NLWIsCameraEntryGestureClass(",
        "                    if (NO &&\n"
        "                        NLWIsCameraEntryGestureClass(",
        "压制循环里对相机入口类做了即时压制",
    ),
    # ------------------------------------------------------------------
    # 1.7-1：hasCamera 方案（10 条）
    # ★ 这一组守的是「四轮失败后终于走对的那条路」，每条都对应一个
    #   真实可能犯的错，而不是凑数。
    # ------------------------------------------------------------------
    (
        "去掉 -hasCamera 签名校验（Apple 改签名后必崩）",
        '        BOOL encOK = (enc && (strncmp(enc, "B@:", 3) == 0 ||\n'
        '                              strncmp(enc, "c@:", 3) == 0));',
        "        BOOL encOK = YES;",
        "签名不符时干净跳过",
    ),
    (
        "安装时写死签名编码（addMethod 可能造出错误签名）",
        "                            &o, enc)) {",
        '                            &o, "B@:")) {',
        "安装时用真实 enc 而非写死字面量",
    ),
    (
        "开关关闭时不转发原实现（相机永久消失，开关变假）",
        "            return ((BOOL (*)(id, SEL))gOrigHasCamera)(self, _cmd);",
        "            return NO;",
        "开关关着就转发原实现",
    ),
    (
        "只看总开关、忽略相机子开关（子开关拨了没反应）",
        "        blocked = (gEnabled && gBlockCameraSwipe);",
        "        blocked = gEnabled;",
        "开关判断用 gEnabled && gBlockCameraSwipe",
    ),
    (
        "★ 顺手把手电筒也禁了（超出需求，多一份误伤）",
        "        Method m = class_getInstanceMethod(c, @selector(hasCamera));",
        '        NLWInstallHook(c, @selector(hasFlashlight), (IMP)NLWHasCamera, NULL, "B@:");\n'
        "        Method m = class_getInstanceMethod(c, @selector(hasCamera));",
        "没有顺手禁掉手电筒",
    ),
    (
        "hook 目标类写错（返回 NO 也管不到相机）",
        'NSClassFromString(@"CSQuickActionsViewController")',
        'NSClassFromString(@"CSLockScreenSettings")',
        "hook 目标类精确为 CSQuickActionsViewController",
    ),
    (
        "安装函数写了但 NLWInit 里没调用（永远不生效）",
        "        NLWInstallHasCameraHook();\n",
        "",
        "在 NLWInit 里真正调用了安装函数",
    ),
    (
        "orig 判空被删（orig 为 NULL 时调空指针 = 必崩）",
        "        if (gOrigHasCamera) {\n"
        "            return ((BOOL (*)(id, SEL))gOrigHasCamera)(self, _cmd);\n"
        "        }\n"
        "        return YES;",
        "        return ((BOOL (*)(id, SEL))gOrigHasCamera)(self, _cmd);",
        "orig 指针存在且有判空",
    ),
    (
        "★ 只改一处选择子（另一处仍 hook 别的方法，日志还会显示已装上）",
        "@selector(hasCamera), (IMP)NLWHasCamera,",
        "@selector(hasFlashlight), (IMP)NLWHasCamera,",
        "hook 选择子为 hasCamera",
    ),
    (
        "去掉 if (!m) 判空（类在但方法不在时拿 NULL Method 继续走）",
        "        if (!m) {\n            NLWDiagAdd(@\"[hook] -hasCamera 不存在 → 跳过\");\n            return NO;\n        }",
        "        if (NO) {\n            NLWDiagAdd(@\"[hook] -hasCamera 不存在 → 跳过\");\n            return NO;\n        }",
        "类/方法不存在时干净跳过",
    ),
    (
        "整个安装函数被删（相机入口完全没被动过）",
        "static BOOL NLWInstallHasCameraHook(void)",
        "static BOOL NLWInstallHasCameraHookRemoved(void)",
        "有 hasCamera hook 安装函数",
    ),

    # ------------------------------------------------------------------
    # 1.8-1：lockScreenCameraSupported（真正管住左滑的那一层）
    # ★ 这一组每条都对应一个「静默失效」的真实形态：
    #   装上去了、日志还显示成功，但功能没生效 —— 最难排查的那一类。
    # ------------------------------------------------------------------
    (
        "★ 只探测实例方法（若它其实是类方法，装上等于没装，且日志显示成功）",
        "        Method mc = class_getClassMethod(c, sel);",
        "        Method mc = NULL;",
        "同时探测实例方法与类方法",
    ),
    (
        "★ 类方法装到类本身而不是元类（等于没装，日志照样写已装上）",
        "            Class meta = object_getClass(c);\n"
        "            const char *enc = method_getTypeEncoding(mc);",
        "            Class meta = c;\n"
        "            const char *enc = method_getTypeEncoding(mc);",
        "类方法走 object_getClass 取元类再装",
    ),
    (
        "★ 实例与类方法共用同一个 orig（转发到另一张方法表的原实现）",
        "static IMP gOrigLSCamMeta = NULL;    // 类方法原实现",
        "static IMP gOrigLSCamDummy = NULL;   // 类方法原实现",
        "实例与类方法各有自己的 orig 变量",
    ),
    (
        "★ 类方法 hook 体错用了实例方法的 orig",
        '    return NLWLSCamBody(self, _cmd, gOrigLSCamMeta, "meta");',
        '    return NLWLSCamBody(self, _cmd, gOrigLSCamInst, "meta");',
        "两个 hook 体分别引用各自的 orig",
    ),
    (
        "★ 其中一条路径只收 B@:（真实签名是 c@: 时被误跳过）",
        "            const char *enc = method_getTypeEncoding(mi);\n"
        "            if (enc && (strncmp(enc, \"B@:\", 3) == 0 ||\n"
        "                        strncmp(enc, \"c@:\", 3) == 0)) {",
        "            const char *enc = method_getTypeEncoding(mi);\n"
        "            if (enc && (strncmp(enc, \"B@:\", 3) == 0)) {",
        "签名校验接受 B@: 与 c@:",
    ),
    (
        "★ 安装时写死编码（真实签名不同则造出签名不符的方法）",
        "                if (NLWInstallHook(c, sel, (IMP)NLWLSCamInst, &o, enc)) {",
        '                if (NLWInstallHook(c, sel, (IMP)NLWLSCamInst, &o, "B@:")) {',
        "用真实 enc 安装（不写死 B@:）",
    ),
    (
        "hook 体只看总开关（相机子开关拨了没反应）",
        "static BOOL NLWLSCamBody(id self, SEL _cmd, IMP orig, const char *kind)\n"
        "{\n"
        "    BOOL blocked = NO;\n"
        "    @try {\n"
        "        blocked = (gEnabled && gBlockCameraSwipe);\n",
        "static BOOL NLWLSCamBody(id self, SEL _cmd, IMP orig, const char *kind)\n"
        "{\n"
        "    BOOL blocked = NO;\n"
        "    @try {\n"
        "        blocked = gEnabled;\n",
        "hook 体：两个开关都要尊重",
    ),
    (
        "★ 开关关闭时不转发原实现（锁屏相机永久消失，开关变假）",
        "    if (!blocked) {\n"
        "        if (orig) {\n"
        "            return ((BOOL (*)(id, SEL))orig)(self, _cmd);",
        "    if (!blocked) {\n"
        "        if (orig) {\n"
        "            return YES;",
        "hook 体：开关关着就转发原实现",
    ),
    (
        "★ orig 判空被删（orig 为 NULL 时调空指针 = 必崩）",
        "        if (orig) {\n"
        "            return ((BOOL (*)(id, SEL))orig)(self, _cmd);",
        "        if (YES) {\n"
        "            return ((BOOL (*)(id, SEL))orig)(self, _cmd);",
        "hook 体：orig 为空时保守放行",
    ),
    (
        "hook 体参数个数写错（与 B@: 不符即崩）",
        "static BOOL NLWLSCamInst(id self, SEL _cmd)",
        "static BOOL NLWLSCamInst(id self, SEL _cmd, id extra)",
        "hook 体是两参无参 getter 原型",
    ),
    (
        "★ 全类扫描改成无条件执行（每次开机白烧几十毫秒，且含 IO）",
        "        if (!gotInst && !gotMeta) {\n"
        '            NLWDiagAdd(@"[hook] -lockScreenCameraSupported 未装成 → 开始找真正宿主");\n'
        '            NLWScanForMethod("lockScreenCameraSupported");\n'
        "            return NO;\n"
        "        }",
        '        NLWScanForMethod("lockScreenCameraSupported");\n'
        "        if (!gotInst && !gotMeta) {\n"
        '            NLWDiagAdd(@"[hook] -lockScreenCameraSupported 未装成 → 开始找真正宿主");\n'
        "            return NO;\n"
        "        }",
        "全类扫描只在「没装成」时触发",
    ),
    (
        "★ 方法不存在也硬上（本项目最高等级缺陷：崩宿主）",
        "        if (!gotInst && !gotMeta) {",
        "        if (NO) {",
        "方法/类不存在时干净跳过",
    ),
    (
        "★ 目标类写成别的类（hook 不到真正宿主，功能全废）",
        'NSClassFromString(@"SpringBoard")',
        'NSClassFromString(@"SBHomeScreenController")',
        "目标类精确为 SpringBoard",
    ),
    (
        "★ 选择子名写错（hook 到不存在的方法，静默失败）",
        "        SEL sel = @selector(lockScreenCameraSupported);",
        "        SEL sel = @selector(lockScreenCameraAvailable);",
        "选择子确实是 lockScreenCameraSupported",
    ),
    (
        "安装函数改名（等于整段被删）",
        "static BOOL NLWInstallLSCamHook(void)",
        "static BOOL NLWInstallLSCamHookRemoved(void)",
        "有 lockScreenCameraSupported 安装函数",
    ),
    (
        "★ 装了但 NLWInit 里没调用（1.8-1 等于没改）",
        "        NLWInstallLSCamHook();\n",
        "",
        "在 NLWInit 里真正调用了 LSCam 安装函数",
    ),
    (
        "★ 为了「省一层」把 hasCamera 删掉（按钮会回来）",
        "        NLWInstallHasCameraHook();\n",
        "",
        "hasCamera 与 lockScreenCameraSupported 两层都在",
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
    (
        # ★ 真实缺陷原样复现（1.0-1 ~ 1.2-1 首版一直存在，装了 3 个版本才发现）：
        #   data.tar 里带着 ./DEBIAN/postinst → 解包落到根目录 /DEBIAN/postinst。
        #   rootless 上 / 只读 → 整个 deb 装不上。
        "data.tar 里带上 DEBIAN/（rootless 上 / 只读 → 整个包装不上）",
        "            rel_root = os.path.relpath(root, stage).replace('\\\\', '/')\n"
        "            if rel_root == 'DEBIAN' or rel_root.startswith('DEBIAN/'):\n"
        "                dirs[:] = []\n"
        "                continue",
        "            rel_root = os.path.relpath(root, stage).replace('\\\\', '/')",
        "遍历时剪掉 DEBIAN/",
    ),
]


CHECKDEB_CASES = [
    (
        # ★ 本轮真实翻车原样复现：UIRectEdgeRight 是枚举值 8，
        #   产物里一个字节都没有 → 判据必然 FAIL，而产物完全正确。
        "用枚举常量做产物指纹（该名字编译后不存在，判据必然误杀）",
        'b"UIScreenEdgePanGestureRecognizer", b"edges",',
        'b"UIScreenEdgePanGestureRecognizer", b"edges",\n'
        '                       b"UIRectEdgeRight",',
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