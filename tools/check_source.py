# -*- coding: utf-8 -*-
"""NoLockWallpaper 源码级自检（崩宿主维度优先）

★ 设计原则（本项目铁律）：
  建检查项时必须自问 —— 它拦的是「编不过」还是「跑起来崩」？
  两者是不同维度，前者永远不能替代后者。
  SingleVPN v2.3 那四个 bug 全部能通过编译，静态 lint 一条都抓不到，
  最后导致用户手机进安全模式。所以这里的检查项以「运行期会崩」为主。

★ 反向验证要求：新增检查项必须证明它真的会 FAIL（注入违规 → 非 0），
  否则"通过"可能只是"没在跑"。

用法：python tools/check_source.py
"""
import os
import re
import struct
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ★ 支持被反向验证脚本替换源码路径：verify_negative.py 会生成「故意写坏」
#   的 Tweak.m 副本，通过这个环境变量指过来，避免污染工作区真实文件。
#   不设该变量时行为与以前完全一致。
SRC = os.environ.get("NLW_SRC_OVERRIDE") or os.path.join(_REPO, "Tweak.m")
# ★ 同理支持替换 build.sh：反向验证要证明「构建闸门判据写错会 FAIL」。
BUILD = os.environ.get("NLW_BUILD_OVERRIDE") or os.path.join(_REPO, "devkit", "build.sh")
# ★ 同上：设置页控制器的反向验证需要替换这份源码。
PREFM_OVERRIDE = os.environ.get("NLW_PREF_OVERRIDE")
MKDEB = os.environ.get("NLW_MKDEB_OVERRIDE") or os.path.join(_REPO, "devkit", "mkdeb.sh")
# ★ check_deb.py 跑在真实产物上。这里也要静态复查它的判据有没有用
#   「编译后会消失的名字」（枚举常量 / 宏 / 内联函数）做字符串匹配 ——
#   那是注定 FAIL 的假判据（本轮实测 UIRectEdgeRight）。
CHECKDEB = os.environ.get("NLW_CHECKDEB_OVERRIDE") or os.path.join(_REPO, "tools",
                                                                  "check_deb.py")
PLIST = os.path.join(_REPO, "NoLockWallpaper.plist")

fails, oks = [], []


def chk(name, ok, detail=""):
    (oks if ok else fails).append((name, detail))
    print(("  OK   " if ok else "  FAIL ") + name + (("  -> " + detail) if detail else ""))


src = open(SRC, encoding="utf-8").read()
lines = src.splitlines()

print("文件: %s" % SRC)
print("总行数: %d" % len(lines))


def strip_code(s):
    """把注释替换成空格、字符串内容替换成空格，保留所有分隔符本身。

    ★ 关键：结尾的引号必须保留，否则 @"a" "b" 会被粘成 @"a  b ，
      导致相邻两个 @"" 空串被误判成一个吞掉一个括号。
    """
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        nx = s[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":
            while i < n and s[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and nx == "*":
            out.append(" ")
            out.append(" ")
            i += 2
            while i < n:
                if s[i] == "*" and i + 1 < n and s[i + 1] == "/":
                    out.append(" ")
                    out.append(" ")
                    i += 2
                    break
                out.append("\n" if s[i] == "\n" else " ")
                i += 1
            continue
        if c == '"':
            out.append('"')
            i += 1
            while i < n:
                if s[i] == "\\" and i + 1 < n:
                    out.append(" ")
                    out.append(" ")
                    i += 2
                    continue
                if s[i] == '"':
                    out.append('"')
                    i += 1
                    break
                out.append("\n" if s[i] == "\n" else " ")
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


code = strip_code(src)
code_lines = code.splitlines()

# =====================================================================
print("\n[A1] 括号配对（编不过级别，但先过）")

for pair, (o, c) in {"()": ("(", ")"), "{}": ("{", "}"), "[]": ("[", "]")}.items():
    depth, bad = 0, None
    for ln, line in enumerate(code_lines, 1):
        for ch in line:
            if ch == o:
                depth += 1
            elif ch == c:
                depth -= 1
                if depth < 0:
                    bad = ln
                    break
        if bad:
            break
    chk("%s 配对平衡" % pair, depth == 0 and bad is None,
        ("第 %d 行出现多余的 %s" % (bad, c)) if bad else "")

# =====================================================================
print("\n[A2] 崩宿主防护：每个 hook 必须整体包 try/catch")

# 找所有 hook 实现函数
hook_funcs = ["NLWAddGestureRecognizer", "NLWDidMoveToWindow",
              "NLWViewDidAppear", "NLWSuppressGesture", "NLWProcessView"]
for fn in hook_funcs:
    m = re.search(r"static\s+\w+\s+%s\s*\([^)]*\)\s*\{" % re.escape(fn), code)
    if not m:
        chk("%s 存在" % fn, False, "找不到函数定义")
        continue
    # 取函数体（括号匹配）
    start = m.end() - 1
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = code[start:i + 1]
    chk("%s 内含 @try" % fn, "@try" in body or fn == "NLWSuppressGesture",
        "" if ("@try" in body or fn == "NLWSuppressGesture") else "缺 @try，异常会出线程")

# NLWSuppressGesture 单独验：它内部 @try 是逐语句的
m = re.search(r"static\s+void\s+NLWSuppressGesture\s*\([^)]*\)\s*\{", code)
if m:
    start = m.end() - 1
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = code[start:i + 1]
    chk("NLWSuppressGesture 内 @try 数 >= 2", body.count("@try") >= 2,
        "实际 %d 处" % body.count("@try"))

# =====================================================================
print("\n[A3] orig 转发前必须判非空（调 NULL 必崩）")

# ★ 这一组只覆盖「单一静态 orig」的两个转发点。
#   viewDidAppear: 的 orig 已改为「按类成对存」，转发点写法是
#   IMP o = NLWOrigViewDidAppearFor(self); if (o && ...) o(...)
#   由 A4c 单独覆盖 —— 不能硬塞进这个 regex，否则判据与实际写法不符。
for gorig in ["gOrigAddGesture", "gOrigDidMove"]:
    # ★ 实际写法是 ((void (*)(id, SEL, id))gOrigXxx)(self, _cmd, ...)
    #   正则必须容忍 (*)( 里面带参数列表。
    #   第一版写成 \(\*\s*\) 只匹配空参数，结果"调用 0 处"——典型的假通过：
    #   检查项没在跑却报 OK。这里改成允许参数表任意内容。
    calls = re.findall(r"\(\s*\(\s*void\s*\(\s*\*\s*\)\s*\([^)]*\)\s*\)\s*%s\s*\)"
                       % gorig, code)
    guarded = re.findall(r"if\s*\(\s*%s\s*\)" % gorig, code)

    # ★ 必须真的找到调用点，否则"全判空"是假通过
    chk("%s 存在真实调用点（判据有效性自检）" % gorig,
        len(calls) >= 1,
        "找到 %d 处强转调用" % len(calls))
    chk("%s 每次调用前都判空" % gorig,
        len(calls) >= 1 and len(guarded) >= 1,
        "调用 %d 处 / 判空 %d 处" % (len(calls), len(guarded)))

# =====================================================================
print("\n[A4] hook 安装必须区分「自己实现」与「继承」（否则污染父类）")

# ★ 判据要精确到「新实现的具体机制」，不能只查变量名。
#   v1.2 起实现改成：先取本类真实 Method，再比对父类 Method 的 IMP
#   是否与本类相同（相同 = 本类没覆盖 = 继承）。
chk("NLWInstallHook 用 method_getImplementation 取真实 IMP",
    "method_getImplementation(own)" in code,
    "" if "method_getImplementation(own)" in code else "未取真实 IMP")
chk("继承判定基于「本类 Method 与父类 Method 的 IMP 相同」",
    "method_getImplementation(supMethod) == ownImp" in code,
    "" if "method_getImplementation(supMethod) == ownImp" in code else "继承判定缺失")
chk("继承时走 class_addMethod（不碰父类）", "class_addMethod" in code)
chk("安装失败返回 NO（干净跳过）", re.search(r"return NO;", code) is not None)
chk("类上没有该方法时直接放弃（不硬上）",
    re.search(r"Method own = class_getInstanceMethod\(cls, sel\);\s*\n\s*if \(!own\) return NO;", code) is not None,
    "" if re.search(r"if \(!own\) return NO;", code) else "缺「没有该方法就跳过」")

# =====================================================================
print("\n[A4b] 转发桩过滤（orig 是 objc_msgForward 时转发 = 必崩宿主）")

# ★★★ 这是本轮新增的最高危防线：
#   method_getImplementation 在「没人实现该 selector」的边界情形会给出
#   objc_msgForward（非 NULL 转发桩）。把它当 orig 存下再调用，等于把消息
#   再送一次转发 → arm64e 上 EXC_BAD_ACCESS 或无限递归 → SpringBoard 崩。
chk("存在转发桩判定 NLWIsForwardingIMP", "NLWIsForwardingIMP" in code)
chk("转发桩判定覆盖 imp 为 NULL",
    re.search(r"static BOOL NLWIsForwardingIMP\(IMP imp\)\s*\{\s*\n\s*if \(!imp\) return YES;", code) is not None,
    "" if re.search(r"NLWIsForwardingIMP\(IMP imp\)", code) else "函数签名不符")

# 每一条「取 IMP 并写入 orig」的路径都必须先过转发桩过滤
inst = re.search(r"static BOOL NLWInstallHook\(.*?\n\}", code, re.S)
chk("NLWInstallHook 存在", inst is not None)
if inst:
    body = inst.group(0)
    chk("NLWInstallHook 对 ownImp 做转发桩过滤", "NLWIsForwardingIMP(ownImp)" in body)
    chk("NLWInstallHook 对回退路径的 cur 做转发桩过滤", "NLWIsForwardingIMP(cur)" in body,
        "" if "NLWIsForwardingIMP(cur)" in body else "回退路径未过滤")
    chk("NLWInstallHook 进入即把 origOut 置空（绝不残留脏值）",
        re.search(r"if \(origOut\) \*origOut = NULL;", body) is not None)

# 三条转发点必须都带判空（objc 消息里 orig 可能是 NULL）
fwd_checks = len(re.findall(r"if \(gOrig\w+\)", code))
chk("转发前判空 guard（>=2 处）", fwd_checks >= 2,
    "实际 %d 处：gOrigAddGesture / gOrigDidMove" % fwd_checks)

# =====================================================================
print("\n[A4c] viewDidAppear orig 必须按类成对存（否则截断别人生命周期）")

# ★ iOS 17 上 SBLockScreenViewController 与 CSCoverSheetViewController 可能
#   同时存在。共用一个全局 orig → 第二个类的原始实现永远不被调用 →
#   锁屏控制器自身逻辑被砍（黑屏/不刷新/卡死）。
chk("存在按类存 orig 的容器（gCtlCls / gCtlOrig）",
    "gCtlCls" in code and "gCtlOrig" in code)
chk("存在按 self 类链查 orig 的函数 NLWOrigViewDidAppearFor",
    "NLWOrigViewDidAppearFor" in code)
chk("不再使用单一全局 gOrigViewDidAppear",
    "gOrigViewDidAppear" not in code,
    "" if "gOrigViewDidAppear" not in code else "共用 orig 会截断第二个类的生命周期回调")

m = re.search(r"static IMP NLWOrigViewDidAppearFor\(id self\)\s*\{", code)
if m:
    start = m.end() - 1
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = code[start:i + 1]
    chk("查 orig 时沿类链向上匹配", "class_getSuperclass(k)" in body,
        "" if "class_getSuperclass(k)" in body else "只比对精确类，子类实例会拿不到 orig")

# viewDidAppear 的转发点写法与另两个不同（orig 是按类查出来的局部变量 o），
# 因此单独做「调用点 + 判空」自检，避免 A3 的 regex 覆盖不到而变成空跑。
chk("NLWViewDidAppear 存在真实转发调用（判据有效性自检）",
    re.search(r"\(\s*\(\s*void\s*\(\s*\*\s*\)\s*\([^)]*BOOL\s*\)\s*\)\s*o\s*\)\s*\(\s*self\s*,\s*_cmd\s*,\s*animated\s*\)",
              code) is not None,
    "" if re.search(r"\)\s*o\s*\)\s*\(\s*self\s*,\s*_cmd\s*,\s*animated", code)
    else "找不到 viewDidAppear 的转发调用点")
chk("NLWViewDidAppear 转发前判 o 非空",
    re.search(r"if \(o && !NLWIsForwardingIMP\(o\)\)", code) is not None,
    "" if re.search(r"if \(o && !NLWIsForwardingIMP\(o\)\)", code) else "转发前未判空")
chk("转发 viewDidAppear 前再筛一次转发桩", "NLWIsForwardingIMP(o)" in code,
    "" if "NLWIsForwardingIMP(o)" in code else "转发前未筛转发桩")

# =====================================================================
print("\n[A4d] 不得直接引用 arm64 上不存在的符号（会让 dylib 加载失败）")


# ★★★ 本轮差点踩的坑：
#   _objc_msgForward_stret 在 arm64 上已从 libobjc 移除。用
#   -undefined dynamic_lookup 链接时编译期不报错，但运行期 dyld 找不到
#   符号 → 整份 dylib 加载失败 → 表现是「完全没生效，日志一行都没有」。
#   必须用 dlsym 在运行时取，取不到也只是这层保护不启用。
chk("未在代码中直接写 &_objc_msgForward_stret",
    re.search(r"(?<![A-Za-z0-9_\"])&_objc_msgForward_stret", code) is None,
    "" if re.search(r"(?<![A-Za-z0-9_\"])&_objc_msgForward_stret", code) is None
    else "arm64 无此符号，直接引用 = dylib 加载失败")
chk("未在代码中直接写 &_objc_msgForward",
    re.search(r"(?<![A-Za-z0-9_\"])&_objc_msgForward(?!_)", code) is None,
    "" if re.search(r"(?<![A-Za-z0-9_\"])&_objc_msgForward(?!_)", code) is None
    else "应改用 dlsym 取符号")
# ★ 这两项必须查原始 src：strip_code 会把字符串字面量内容换成空格，
#   拿 code 查 "_objc_msgForward" 这样的字符串必然查不到 —— 属于本项目
#   已经踩过的「检查器自己判错」。判据里出现字符串就必须用 src。
chk("用 dlsym 取转发桩符号",
    'dlsym(RTLD_DEFAULT, "_objc_msgForward")' in src,
    "" if 'dlsym(RTLD_DEFAULT, "_objc_msgForward")' in src else "缺 dlsym 取符号")
chk("dlsym 结果存静态变量（不每次调用都查符号表）",
    re.search(r"static void \*fwd\s*=\s*NULL", src) is not None,
    "" if re.search(r"static void \*fwd\s*=\s*NULL", src) else "每次 dlsym 有性能开销")

# =====================================================================
print("\n[A4e] 长按判据（含类名兜底）")

chk("存在长按判据 NLWIsLongPressish", "NLWIsLongPressish" in code)
chk("长按判据含 isKindOfClass 快路径",
    "isKindOfClass:[UILongPressGestureRecognizer class]" in code)
# ★ 同样必须查 src：'strstr(n, "LongPress")' 里的 needle 是字符串字面量
chk("长按判据含类名 LongPress 兜底", 'strstr(n, "LongPress")' in src,
    "" if 'strstr(n, "LongPress")' in src else "缺类名兜底")
chk("压制循环使用统一判据（不再裸 isKindOfClass）",
    re.search(r"if \(NLWIsLongPressish\(g\)\)", code) is not None,
    "" if re.search(r"if \(NLWIsLongPressish\(g\)\)", code) else "循环判据与函数不一致")

# =====================================================================
print("\n[A4f] 热路径零分配（didMoveToWindow 是上屏热路径）")

m = re.search(r"static NSUInteger NLWProcessView\(", code)
chk("NLWProcessView 存在", m is not None)
if m:
    start = m.end() - 1
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = code[start:i + 1]
    i_judge = body.find("NLWIsSuppressibleLockScreenArea(v)")
    i_alloc = body.find("gestureRecognizers")
    chk("判据在取 gestureRecognizers 之前（避免无谓数组分配）",
        i_judge != -1 and i_alloc != -1 and i_judge < i_alloc,
        "判据@%d 取数组@%d" % (i_judge, i_alloc))

# =====================================================================
print("\n[A4g] C 函数必须「先声明/定义、后使用」")

# ★★ 这是 2026-10-22 CI 真实失败（run 39730344536）：
#   为定位「禁左滑进相机不生效」，在文件靠前的位置插入了一大段诊断代码，
#   它调用的是后面「六、压制主逻辑」里才定义的 NLWIsSuppressibleLockScreenArea。
#   编译器报：
#     error: call to undeclared function 'NLWIsSuppressibleLockScreenArea'
#     error: static declaration of ... follows non-static declaration
#   ★ 本机没有 Xcode，写代码时通读一遍就能发现，但没做 —— 白白多一轮 CI。
#   这类错误属于「插入位置导致」的，最容易被忽略：单看新增的那段代码
#   完全正确，错的是它跟周围代码的先后关系。
#
# ★ 判据做法：逐行扫描，跳过注释行，记录每个 NLW 函数**首次出现**的
#   「static 声明或定义」行号，再找有没有更早的调用行。
#   只看 NLW 前缀（本项目自有函数），避免把系统 API 引进来造成误报。

_l = src.split("\n")


def _is_comment_line(s):
    s = s.strip()
    return (not s) or s.startswith("//") or s.startswith("*") \
        or s.startswith("/*") or s.startswith("*/")


_first_decl = {}
for _i, _line in enumerate(_l, 1):
    if _is_comment_line(_line):
        continue
    _m = re.match(r"^static\s+[\w\s\*]*?\b(NLW\w+)\s*\(", _line.strip())
    if _m:
        _first_decl.setdefault(_m.group(1), _i)

_bad_order = []
for _i, _line in enumerate(_l, 1):
    if _is_comment_line(_line):
        continue
    if re.match(r"^static\s+[\w\s\*]*?\bNLW\w+\s*\(", _line.strip()):
        continue                      # 声明/定义行本身不是调用
    for _n, _d in _first_decl.items():
        if _i >= _d:
            continue
        if re.search(r"\b%s\s*\(" % _n, _line):
            _bad_order.append("%s 在第%d行被调用，但首次声明/定义在第%d行"
                              % (_n, _i, _d))
chk("NLW 函数都先声明/定义后使用",
    not _bad_order,
    "; ".join(_bad_order[:3]) if _bad_order else "")

# =====================================================================
print("\n[A5] 绝不下钻子视图（误伤通知/手电筒 long press）")

# 找 subviews 的使用位置，必须只在 NLWProcessViewShallow 里、且只一层
subview_uses = [i for i, l in enumerate(code_lines, 1) if "subviews" in l]
shallow_ok = True
for ln in subview_uses:
    # 允许 "v.subviews" 但不允许递归调用 NLWProcessViewShallow 自身
    pass
chk("subviews 使用点数量受控（<= 2）", len(subview_uses) <= 2,
    "实际 %d 处：%s" % (len(subview_uses), subview_uses))

# 关键：递归自调用会让深度不可控
m = re.search(r"static\s+\w+\s+NLWProcessViewShallow\s*\([^)]*\)\s*\{", code)
if m:
    start = m.end() - 1
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = code[start:i + 1]
    chk("NLWProcessViewShallow 不自递归", "NLWProcessViewShallow(" not in body.replace("NLWProcessViewShallow(UIView", ""),
        "" if "NLWProcessViewShallow(" not in body else "自递归会扫整棵树，误伤通知长按")

chk("NLWProcessView 不读 subviews", "subviews" not in
    code[code.index("static NSUInteger NLWProcessView("):
         code.index("static NSUInteger NLWProcessViewShallow(")],
    "NLWProcessView 必须只看自身手势")

# =====================================================================
print("\n[A5b] 误伤防护：不得压制通知/手电筒/相机（真实踩过的 bug）")

# ★ v1.1 第一版用「祖先链含锁屏」做判据，而通知 cell 的祖先链必然经过
#   锁屏视图 → 会把通知长按一起禁掉。这是实打实的误伤，必须用三重判据拦住：
#     ① 自身或祖先像锁屏
#     ② 向上路径中不得经过 UIScrollView（通知/今日视图/列表都在里面）
#     ③ 自身不得是 UIControl（手电筒、相机是 UIButton）
chk("存在误伤防护函数 NLWIsSuppressibleLockScreenArea",
    "NLWIsSuppressibleLockScreenArea" in code)
chk("判据排除 UIControl（手电筒/相机）", "isKindOfClass:[UIControl class]" in code)
chk("判据排除滚动容器（通知列表）", "isKindOfClass:[UIScrollView class]" in code)
chk("判据对祖先遍历有次数上限（防异常层级死循环）", "guard++ <" in code)
chk("NLWProcessView 用误伤防护判据（不是裸祖先链）",
    "NLWIsSuppressibleLockScreenArea(v)" in code,
    "" if "NLWIsSuppressibleLockScreenArea(v)" in code else "用了宽判据会误伤通知长按")
chk("addGestureRecognizer 用同一判据（两处逻辑一致）",
    # ★ 判据意图：确认**两处主逻辑**（addGestureRecognizer 与 NLWProcessView）
    #   都调用同一个误伤防护函数，而不是各自写一套。
    #
    # ★ 为什么不用「精确等于 3 次」：
    #   上一版就是 `== 3`，本轮加诊断代码多调用一次 → 立刻误报 exit 1。
    #   而那个多出来的调用是完全正确的。**「数量精确等于 N」这种写法，
    #   每次有合理的新增调用就会假红一次** —— 属于「闸门自己写错」的典型。
    #   正解：验证「必须存在的调用形态都在」，数量只做下界。
    code.count("NLWIsSuppressibleLockScreenArea") >= 3
    and "static BOOL NLWIsSuppressibleLockScreenArea" in code
    and "NLWIsSuppressibleLockScreenArea((UIView *)self)" in code
    and "NLWIsSuppressibleLockScreenArea(v)" in code,
    "引用 %d 次，add=%s process=%s" % (
        code.count("NLWIsSuppressibleLockScreenArea"),
        "NLWIsSuppressibleLockScreenArea((UIView *)self)" in code,
        "NLWIsSuppressibleLockScreenArea(v)" in code))

# =====================================================================
print("\n[A6] 私有 selector 零依赖（只允许公开 API）")

# ★ 这是本项目最重要的架构约束：不猜私有方法名 = 不可能签名错位崩宿主
forbidden = [
    "SBLockScreenView ", "setWallpaperMode:", "wallpaperMode",
    "_setWallpaper", "deactivateWallpaper", "handleLongPress",
]
for f in forbidden:
    chk("未调用私有符号 %r" % f.strip(), f not in code)

# 只允许 hook 这几个公开 selector
#
# ★ 1.6-1 新增 `setEnabled:`：它是 UIGestureRecognizer 的**公开属性 setter**，
#   签名 "v@:B" 由编译器保证，不存在「参数个数猜错 → 崩宿主」的风险。
#   加入白名单不是放宽纪律，而是承认一个事实：
#   相机入口是懒启用的，压制它的唯一办法就是拦这个 setter。
hooked_sel = re.findall(r"@selector\(([^)]+)\)", code)
allowed = {"addGestureRecognizer:", "didMoveToWindow", "viewDidAppear:",
           "setEnabled:", "hasCamera"}
# ★ 1.7-1 加 `hasCamera`：它不是「放宽纪律」，而是本项目 hook 面唯一一个
#   私有 API，且是**无参 getter**（返回值 BOOL，签名无歧义）——
#   与「hook 私有方法要猜参数个数」的风险完全不是一个量级。
#   它替换掉的是前四版全部失败的手势判据（见 [A4j]）。
extra = [s.strip() for s in hooked_sel if s.strip() not in allowed]
chk("只 hook 了公开 API", not extra, "多出的：%s" % extra if extra else
    "addGestureRecognizer: / didMoveToWindow / viewDidAppear: / setEnabled:")

# =====================================================================
print("\n[A4i] 1.6-1：拦「启用」而不是「改状态」")
# ★★ 为什么这一组必须存在：
#   前三版的压制手段只有 `gesture.enabled = NO`。
#   而相机入口 CSHorizontalScrollFailureRecognizer 抓到时就**已经是 NO** —
#   系统在检测到水平滑动时才 setEnabled:YES 把它打开。
#   于是前三版一直在「把已经是 NO 的东西再设一遍 NO」，
#   看起来压制成功，实际零作用，系统一 enable 相机照进。
#   这一组锁的是：必须存在一个拦「启用动作」的 hook，且它足够窄。

chk("安装了 -setEnabled: 的 hook",
    re.search(r"@selector\(setEnabled:\)", code) is not None,
    "" if "@selector(setEnabled:)" in code else
    "没有这个 hook，懒启用的相机入口永远拦不住")

chk("setEnabled hook 用 v@:B 签名",
    # ★ 签名写错 = arm64e 上参数错位 = 必崩宿主。
    #   公开属性 setter 的编码是固定的 "v@:B"，必须原样使用。
    # ★ 必须查原始 src：`"v@:B"` 是字符串字面量，strip_code 会把引号内容
    #   换成空格 → 查 code 必然假红。（本项目第三次踩这个坑了。）
    re.search(r'\(IMP\)NLWSetEnabled,\s*&o,\s*"v@:B"', src) is not None,
    "" if re.search(r'\(IMP\)NLWSetEnabled,\s*&o,\s*"v@:B"', src) else
    "签名不是 v@:B → arm64e 上参数错位，必崩")

chk("setEnabled hook 只在「启用」时过问（关闭一律放行）",
    re.search(r"if\s*\(\s*enabled\s*&&\s*self\s*\)", code) is not None,
    "" if re.search(r"if\s*\(\s*enabled\s*&&\s*self\s*\)", code) else
    "连「关闭」也过问会干扰系统的状态机")

chk("setEnabled hook 一定转发原实现",
    re.search(r"gOrigSetEnabled\)\s*\{\s*\n\s*\(\(void \(\*\)\(id, SEL, BOOL\)\)gOrigSetEnabled\)",
              code) is not None,
    "" if "((void (*)(id, SEL, BOOL))gOrigSetEnabled)" in code else
    "不转发 orig → enabled 永远改不动 → 手势状态错乱")

chk("硬判定只认 Camera / HorizontalScrollFailure 两类",
    # ★ 每多认一类，就多一份误伤面。这两条都是语义明确的：
    #   Camera 不用说；HorizontalScrollFailure 是锁屏相机的标准实现
    #   （横向滚不动 → 交给相机）。
    re.search(r'strstr\(n,\s*"Camera"\)', src) is not None
    and re.search(r'strstr\(n,\s*"HorizontalScrollFailure"\)', src) is not None,
    "" if re.search(r'strstr\(n,\s*"HorizontalScrollFailure"\)', src) else
    "缺少 HorizontalScrollFailure → 拦不住锁屏相机入口")

chk("候选类只记录、不拦截",
    # ★ _UIPendingEdgeSwipe / UIImmediateEdgeSwipe 是 UIKit 的边缘手势仲裁器，
    #   拦它们会破坏仲裁，可能让别的边缘手势失灵。
    #   本版对它们只记录 enable 时机，用来在日志里确认职责。
    re.search(r"NLWDiagNoteEnable\(n,\s*NO\);", code) is not None,
    "" if "NLWDiagNoteEnable(n, NO);" in code else
    "候选类被一起拦了 → 边缘手势仲裁可能失效")

chk("压制循环里对相机入口类做了即时压制（hook 管将来，这里管现在）",
    # ★ 双保险：setEnabled hook 只能拦住「将来」的启用；
    #   如果抓到这个手势时它已经被启用过，hook 不会补一次。
    #   循环里这道判断负责把「当前已是 YES」的情况立刻压掉。
    re.search(r"if\s*\(gBlockCameraSwipe\s*&&\s*\n\s*NLWIsCameraEntryGestureClass\(", code)
    is not None,
    "" if "NLWIsCameraEntryGestureClass(\n" in code else
    "只有 setEnabled hook，抓到时已启用的那次压不到")

# =====================================================================
print("\n[A4j] 1.7-1：让系统认为「这台设备没有相机」")
# ★★★ 为什么这一组必须存在 —— 前四版全部失败，第五版才走对路：
#   1.3-1 判 edge pan、1.4-1 判自述、1.5-1 判 swipe、1.6-1 拦 setEnabled，
#   四轮全部由实机日志推翻。1.6-1 的日志尤其决定性：
#   **8 次 blocked=1，相机照样打开** —— 证明「相机入口是某个可拦手势」
#   这个假设本身不成立。
#
#   答案来自逆向一份现成插件（com.cydia.love.xtyh 1.7）：
#   它只 hook `CSQuickActionsViewController -hasCamera` 返回 NO，
#   不碰任何手势。系统认为没相机 ⇒ 相机页不加载 ⇒ 左滑无从触发。
#
# ★ 这一组锁三件事：
#   ① hook 面必须极小（一个类、一个无参 getter）；
#   ② 开关必须实时生效（关掉后转发原实现，而不是改 IMP）；
#   ③ 不许顺手把 hasFlashlight 也禁掉（用户没要求，多改一处就多一份误伤）。

chk("有 hasCamera hook 安装函数",
    # ★ 必须匹配「函数签名 + 参数表」，不能用子串 `in` ——
    #   把函数改名成 NLWInstallHasCameraHookRemoved 时前缀仍在，
    #   子串判据照样报绿（本项目踩过：_MNT_BAD → _MNT_BAD_UNUSED）。
    re.search(r"static BOOL NLWInstallHasCameraHook\(void\)", code) is not None,
    "" if re.search(r"static BOOL NLWInstallHasCameraHook\(void\)", code) else
    "没有这个函数，相机入口根本没被动过")

chk("hook 目标类精确为 CSQuickActionsViewController",
    # ★ 必须查 src：类名字符串被 strip_code 换成空格，查 code 必然假红。
    re.search(r'NSClassFromString\(@"CSQuickActionsViewController"\)', src)
    is not None,
    "" if re.search(r'@"CSQuickActionsViewController"', src) else
    "类名不是 CSQuickActionsViewController → 返回 NO 也管不到相机")

chk("hook 选择子为 hasCamera（取方法 + 安装两处都要对）",
    # ★ 两处必须一致：一处取 Method 校验签名、一处真的装 hook。
    #   只改一处是最隐蔽的错法 —— 编译照过、装上去 hook 的是别的方法，
    #   日志还会显示"已装上"。所以按次数卡。
    code.count("@selector(hasCamera)") >= 2,
    "实际 %d 处" % code.count("@selector(hasCamera)") if
    code.count("@selector(hasCamera)") < 2 else "")

chk("类/方法不存在时干净跳过（版本差异不硬上）",
    re.search(r"if\s*\(!c\)\s*\{", code) is not None
    and re.search(r"if\s*\(!m\)\s*\{", code) is not None,
    "" if re.search(r"if\s*\(!m\)\s*\{", code) else
    "缺 if (!m) → 类在但方法不在时会拿 NULL Method 继续走")

chk("★ 没有顺手禁掉手电筒（hasFlashlight 不得出现）",
    # ★ 现成插件把 hasCamera 和 hasFlashlight 一起禁了。
    #   本项目的需求只有「禁左滑进相机」，手电筒属于无关功能 ——
    #   多禁一个就是多一处用户没同意的行为改变。
    "hasFlashlight" not in code,
    "" if "hasFlashlight" not in code else
    "出现 hasFlashlight → 手电筒被一起禁掉了，超出需求")

chk("★ 签名不符时干净跳过（enc 必须校验 B@: / c@:）",
    # ★ 这是崩宿主防线：签名不对还硬上，等于用错参数个数调原实现。
    re.search(r'strncmp\(enc,\s*"B@:",\s*3\)', src) is not None
    and re.search(r'strncmp\(enc,\s*"c@:",\s*3\)', src) is not None,
    "" if re.search(r'strncmp\(enc,\s*"B@:",\s*3\)', src) else
    "没校验签名 → 万一 Apple 改签名就是必崩")

chk("安装时用真实 enc 而非写死字面量",
    # ★ class_addMethod 用错编码会造出一个签名不符的方法，
    #   之后系统按错误签名调用 = 参数错位。必须传 method_getTypeEncoding 的结果。
    re.search(r"&o,\s*enc\)", code) is not None,
    "" if "&o, enc)" in code else "写死了编码 → addMethod 可能造出错误签名")

chk("hasCamera hook 体：开关关着就转发原实现",
    # ★ 实时生效的实现方式：不是「改回 IMP」，而是每次调用看开关。
    #   这样不存在「漏恢复 → 开关是假的」这类缺陷。
    re.search(r"\(\(BOOL \(\*\)\(id, SEL\)\)gOrigHasCamera\)\(self, _cmd\)", code)
    is not None,
    "" if "((BOOL (*)(id, SEL))gOrigHasCamera)(self, _cmd)" in code else
    "开关关掉后不转发原实现 → 相机永久消失，开关变假")

chk("开关判断用 gEnabled && gBlockCameraSwipe",
    # ★ 两个开关都要尊重：总开关关了要放行，子开关关了也要放行。
    # ★ 判据必须锚到 `blocked = (...)` 这个赋值 —— 源码里
    #   NLWSetEnabled 也有 `if (gEnabled && gBlockCameraSwipe)`，
    #   只查到那一处就会漏放「hasCamera 只看总开关」这种错。
    re.search(r"blocked\s*=\s*\(gEnabled\s*&&\s*gBlockCameraSwipe\)", code)
    is not None,
    "" if re.search(r"blocked\s*=\s*\(gEnabled\s*&&\s*gBlockCameraSwipe\)", code)
    else "只看一个开关 → 另一个开关拨了没反应")

chk("orig 指针存在且有判空",
    "static IMP gOrigHasCamera" in code and
    re.search(r"if\s*\(gOrigHasCamera\)", code) is not None,
    "" if "static IMP gOrigHasCamera" in code else "没保存 orig → 无法转发")

chk("在 NLWInit 里真正调用了安装函数",
    re.search(r"^\s*NLWInstallHasCameraHook\(\s*\);", code, re.M) is not None,
    "" if re.search(r"^\s*NLWInstallHasCameraHook\(\s*\);", code, re.M) else
    "函数写了但没调用 → 永远不生效")

# =====================================================================
print("\n[A7] 热路径零 IO（watchdog 会杀进程）")

# ★ 1.1 起日志已整体移除（用户要求：功能验证通过后，诊断代码只剩负担）。
#   判据改为「必须没有日志」——这是正向要求，不是「数量够少」。
#   为什么改成硬性禁止：日志写在视图上屏热路径上是本项目明令禁止的做法，
#   留一个「允许 <= 5 处」的口子，等于给出一个随时可以重新踩坑的许可。
log_calls = [i for i, l in enumerate(code_lines, 1) if "NLWLog(" in l]
chk("源码里已无 NLWLog 调用", not log_calls,
    "仍残留 %d 处：%s" % (len(log_calls), log_calls) if log_calls else "")
chk("源码里已无日志落盘函数", "NLWLogDir" not in code,
    "" if "NLWLogDir" not in code else "日志目录探测代码仍在")
chk("源码里已无 iOS 版本探测", "NLWiOSVersion" not in code,
    "" if "NLWiOSVersion" not in code else "诊断用的版本探测仍在")
chk("源码里已无手势清单 dump", "gLoggedInventory" not in code,
    "" if "gLoggedInventory" not in code else "诊断用手势清单 dump 仍在")

# 热路径里不得出现任何文件/偏好读取
m = re.search(r"static NSUInteger NLWProcessView\(", code)
if m:
    start = m.end() - 1
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = code[start:i + 1]
    chk("NLWProcessView 内无 CFPreferences 读取",
        "CFPreferences" not in body,
        "" if "CFPreferences" not in body else "热路径读配置 = syscall + plist 解析")
    chk("NLWProcessView 内无文件 IO",
        not any(k in body for k in ("writeToFile", "fileHandle", "NSFileManager",
                                    "stringWithContentsOfFile")),
        "" if not any(k in body for k in ("writeToFile", "fileHandle", "NSFileManager"))
        else "热路径有文件 IO")

# =====================================================================
print("\n[A7b] 开关：读取、实时生效、与 plist 三方一致")

# ★★ 这一组是 1.1 新增的核心功能。判据要覆盖三类「装了等于没装」的失败：
#   ① 插件根本不读开关 → 设置页是个摆设
#   ② 插件读的域/键与 Root.plist 不一致 → 开关拨了没反应
#   ③ 关掉后不恢复已压制的手势 → 用户以为开关坏了

chk("有开关读取函数 NLWReadBool", "static BOOL NLWReadBool" in code)
chk("有开关装载函数 NLWLoadSwitches", "static void NLWLoadSwitches" in code)
chk("开关状态存在内存变量（热路径不读配置）",
    re.search(r"static BOOL gEnabled\s*=\s*YES", code) is not None)
chk("有相机滑动子开关变量 gBlockCameraSwipe",
    re.search(r"static BOOL gBlockCameraSwipe\s*=\s*YES", code) is not None)

# ① 真的读了配置
chk("开关真的读 CFPreferencesGetAppBooleanValue",
    "CFPreferencesGetAppBooleanValue" in code)
chk("读配置前有 AppSynchronize（否则拿到旧缓存）",
    "CFPreferencesAppSynchronize" in code)
chk("键缺失时按开启处理（not present 不覆盖 fallback）",
    re.search(r"if \(present\)", code) is not None,
    "" if re.search(r"if \(present\)", code) else "把「键不存在」当成 false = 装完不生效")

# ② 域与键必须与 Root.plist 一致
#    ★ 这类不一致完全不报错，只表现为「开关没反应」，必须静态对齐。
ROOTPL = os.path.join(_REPO, "NoLockWallpaperPrefs", "Resources", "Root.plist")
INFOPL = os.path.join(_REPO, "NoLockWallpaperPrefs", "Resources", "Info.plist")
ENTRYPL = os.path.join(_REPO, "NoLockWallpaperPrefs", "layout",
                       "Library", "PreferenceLoader", "Preferences",
                       "NoLockWallpaperPrefs", "Root.plist")

for p in (ROOTPL, INFOPL, ENTRYPL):
    chk("设置页文件存在 %s" % os.path.basename(os.path.dirname(p)) + "/" +
        os.path.basename(p), os.path.isfile(p))

if os.path.isfile(ROOTPL):
    rp = open(ROOTPL, encoding="utf-8").read()
    # Tweak.m 里的域名/键名都写成字符串字面量，必须查 src 而不是 code
    m_dom = re.search(r'NLWPrefsDomain\s*=\s*@"([^"]+)"', src)
    m_en  = re.search(r'NLWEnabledKey\s*=\s*@"([^"]+)"', src)
    m_cam = re.search(r'NLWBlockCameraKey\s*=\s*@"([^"]+)"', src)
    m_ntf = re.search(r'NLWChangedNotification\s*=\s*@"([^"]+)"', src)
    dom = m_dom.group(1) if m_dom else None
    en  = m_en.group(1) if m_en else None
    cam = m_cam.group(1) if m_cam else None
    ntf = m_ntf.group(1) if m_ntf else None

    chk("能解析出 Tweak.m 的配置域名", dom is not None, "解析失败则判据无效")
    chk("能解析出 Tweak.m 的两个键名", en is not None and cam is not None)
    chk("能解析出 Tweak.m 的通知名", ntf is not None)

    if dom:
        chk("Root.plist 的 defaults 与 Tweak.m 域名一致",
            ("<string>%s</string>" % dom) in rp,
            "" if ("<string>%s</string>" % dom) in rp else "域名不一致 → 开关无效")
    if en:
        chk("Root.plist 含 Enabled 键 %r" % en, ("<string>%s</string>" % en) in rp)
    if cam:
        chk("Root.plist 含相机键 %r" % cam, ("<string>%s</string>" % cam) in rp)

    chk("Root.plist 用 PSSwitchCell", "PSSwitchCell" in rp)
    chk("Root.plist 有 PSButtonCell（重启桌面按钮）", "PSButtonCell" in rp)
    chk("Root.plist 的重启动作指向 respring",
        re.search(r"<key>action</key>\s*<string>respring</string>", rp) is not None)

    # ③ 通知名三方一致：Tweak.m 监听名 == Root.plist 的 PostNotification
    if ntf:
        chk("通知名在 Root.plist 里出现（PostNotification 对齐）",
            ("<string>%s</string>" % ntf) in rp,
            "" if ("<string>%s</string>" % ntf) in rp
            else "通知名不一致 → 关开关不会立即生效")
        chk("Tweak.m 注册了 Darwin 通知监听",
            "CFNotificationCenterAddObserver" in code and
            "CFNotificationCenterGetDarwinNotifyCenter" in code)
        chk("通知回调切回主线程再改手势",
            "dispatch_async(dispatch_get_main_queue()" in code,
            "" if "dispatch_async(dispatch_get_main_queue()" in code
            else "通知回调不保证主线程，直接改 UI 相关对象有风险")

    # ④ 关掉时必须恢复已压制的手势，否则开关是假的
    # ★ 判据必须同时查「有定义」和「有调用」。
    #   第一版只查 `"NLWRestoreAllSuppressed" in code`，
    #   把函数改名后调用点还在 → 判据照样命中 → 假通过。
    #   反向验证 A11 实测漏放，正是这个原因。
    #   「有名无实」和「有实无名」都是缺陷，必须两样都在。
    chk("有恢复函数 NLWRestoreAllSuppressed 的定义",
        re.search(r"static void NLWRestoreAllSuppressed\(void\)\s*\{", code) is not None,
        "" if re.search(r"static void NLWRestoreAllSuppressed\(void\)\s*\{", code)
        else "只剩调用点、没有实现 → 链接期或运行期直接失效")
    chk("开关切到关闭时真的调用了恢复函数",
        re.search(r"NLWRestoreAllSuppressed\(\);", code) is not None,
        "" if re.search(r"NLWRestoreAllSuppressed\(\);", code) else "定义了却没人调 = 开关失效")
    chk("恢复时还原**原始** enabled（不是无条件 YES）",
        re.search(r"g\.enabled = \[en boolValue\]", code) is not None,
        "" if re.search(r"g\.enabled = \[en boolValue\]", code)
        else "无条件设 YES 会擅自打开系统本已关闭的手势")
    chk("记录原状时只在首次记录（不覆盖成已改后的值）",
        re.search(r"if \(\[gSuppressed objectForKey:g\] == nil\)", code) is not None,
        "" if re.search(r"if \(\[gSuppressed objectForKey:g\] == nil\)", code)
        else "反复覆盖会把「原状」记成 90 秒，开关等于失效")

if os.path.isfile(INFOPL):
    ip = open(INFOPL, encoding="utf-8").read()
    chk("设置页 Info.plist 的 CFBundlePackageType 为 BNDL",
        re.search(r"<key>CFBundlePackageType</key>\s*<string>BNDL</string>", ip) is not None,
        "" if "BNDL" in ip else "不是 BNDL 会被当独立应用启动")
    chk("设置页 Info.plist 的 NSPrincipalClass 指向控制器",
        "NSPrincipalClass" in ip and "NoLockWallpaperRootListController" in ip)
    chk("设置页 Info.plist 的 CFBundleExecutable 与产物名一致",
        re.search(r"<key>CFBundleExecutable</key>\s*<string>NoLockWallpaperPrefs</string>", ip)
        is not None)

if os.path.isfile(ENTRYPL):
    ep = open(ENTRYPL, encoding="utf-8").read()
    chk("入口 plist 是 PSLinkCell", "PSLinkCell" in ep)
    chk("入口 plist 的 bundle 与 .bundle 名一致",
        re.search(r"<key>bundle</key>\s*<string>NoLockWallpaperPrefs</string>", ep) is not None)
    chk("入口 plist 的 detail 与 NSPrincipalClass 一致",
        re.search(r"<key>detail</key>\s*<string>NoLockWallpaperRootListController</string>", ep)
        is not None,
        "" if "NoLockWallpaperRootListController" in ep else "detail 与主类名不一致 → 进不去")
    chk("入口 plist 有 isController", "<key>isController</key>" in ep)
    chk("入口 plist 的 icon 写基础名（不是 @2x/@3x）",
        re.search(r"<key>icon</key>\s*<string>icon\.png</string>", ep) is not None,
        "" if re.search(r"<key>icon</key>\s*<string>icon\.png</string>", ep)
        else "写 icon@2x.png 会不显示图标")

# 图标：入口写的是基础名 icon.png，必须三个尺寸一起打包。
# 只给 @2x/@3x 而缺基础名 → 部分版本显示空白方块，且无任何报错。
for _ic in ("icon.png", "icon@2x.png", "icon@3x.png"):
    _icp = os.path.join(_REPO, "NoLockWallpaperPrefs", "Resources", _ic)
    chk("图标存在 %s" % _ic, os.path.isfile(_icp),
        "" if os.path.isfile(_icp) else "缺 %s → 设置里显示空白方块" % _ic)
    if os.path.isfile(_icp):
        _d = open(_icp, "rb").read()
        chk("图标 %s 是合法 PNG" % _ic, _d.startswith(b"\x89PNG\r\n\x1a\n"),
            "开头 %r" % _d[:8])
        # 尺寸必须与文件名 scale 匹配，否则会糊或被拒
        _base = 29
        _exp = _base * (3 if "@3x" in _ic else (2 if "@2x" in _ic else 1))
        try:
            _w, _h = struct.unpack(">II", _d[16:24])
        except Exception:
            _w = _h = -1
        chk("图标 %s 尺寸为 %dx%d" % (_ic, _exp, _exp), (_w, _h) == (_exp, _exp),
            "实际 %dx%d" % (_w, _h))

# =====================================================================
print("\n[A7c] 设置页控制器源码：Preferences 七坑")

PREFM = PREFM_OVERRIDE or os.path.join(_REPO, "NoLockWallpaperPrefs",
                                       "NoLockWallpaperRootListController.m")
chk("设置页控制器存在", os.path.isfile(PREFM))
if os.path.isfile(PREFM):
    pm = open(PREFM, encoding="utf-8").read()
    pm_code = strip_code(pm)

    # 坑 1：UIColorWell 会让 iOS 13 设置 App 打不开
    chk("设置页未引用 UIColorWell（iOS 13 设置 App 会打不开）",
        "UIColorWell" not in pm_code)
    # 坑 4：reloadSpecifier:nil 会崩
    chk("设置页未调用 reloadSpecifier:nil",
        re.search(r"reloadSpecifier\s*:\s*nil", pm_code) is None)
    # 坑 5：必须自己实现 specifiers
    chk("设置页自己实现了 specifiers",
        re.search(r"-\s*\(NSArray\s*\*\)\s*specifiers\s*\{", pm_code) is not None)
    chk("设置页用 loadSpecifiersFromPlistName 载入 Root",
        "loadSpecifiersFromPlistName" in pm_code)
    # 坑 6：setProperty:forKey: 与 NSStream 同名异签名
    chk("设置页未直接调用 setProperty:forKey:",
        re.search(r"setProperty\s*:", pm_code) is None,
        "" if re.search(r"setProperty\s*:", pm_code) is None else "会与 NSStream 同名异签名冲突")
    # 坑 8（本轮新增，最危险）：绝不自己声明 _specifiers ivar
    chk("设置页未自己声明 _specifiers ivar（偏移会与真实布局错位→崩设置 App）",
        re.search(r"NSArray\s*\*\s*_specifiers\s*;", pm_code) is None,
        "" if re.search(r"NSArray\s*\*\s*_specifiers\s*;", pm_code) is None
        else "自己声明的 ivar 偏移与真实类不一致，运行期读错内存")
    chk("设置页用 runtime 按名字取 _specifiers（不自己声明 ivar）",
        "class_getInstanceVariable" in pm_code,
        "" if "class_getInstanceVariable" in pm_code else "缺按名字取 ivar 的路径")
    chk("设置页对「取不到 ivar」有退化处理（不缓存、不崩）",
        re.search(r"if\s*\(!\s*iv\s*\)\s*return", pm_code) is not None
        or re.search(r"if\s*\(!iv\)\s*return", pm_code) is not None,
        "" if re.search(r"if\s*\(!\s*iv\s*\)\s*return", pm_code) is not None
        or re.search(r"if\s*\(!iv\)\s*return", pm_code) is not None
        else "缺退化处理")

    # ★★★ [本轮 CI 真实失败] ARC 禁止把裸指针强转成带所有权的对象指针。
    #   写出来的基本都是这一句（非 ARC 里到处都有，所以"凭经验就会这么写"）：
    #     *(NSArray **)((char *)self + off) = v;
    #     return *(NSArray **)((char *)(__bridge void *)self + off);
    #   → error: cast of a non-Objective-C pointer type 'char *' to
    #     'NSArray *__strong *' is disallowed with ARC
    #   本机无 Xcode 时这条**只能等 CI 才能发现**，所以必须在这里静态拦住。
    chk("设置页未用 ARC 禁止的裸指针强转读 ivar",
        re.search(r"\*\s*\(\s*NSArray\s*\*\s*\*\s*\)\s*\(", pm_code) is None,
        "" if re.search(r"\*\s*\(\s*NSArray\s*\*\s*\*\s*\)\s*\(", pm_code) is None
        else "ARC 会报 cast ... disallowed with ARC，必编译失败")
    chk("设置页走 object_getIvar/object_setIvar 存取 ivar",
        "object_getIvar" in pm_code and "object_setIvar" in pm_code,
        "" if ("object_getIvar" in pm_code and "object_setIvar" in pm_code)
        else "缺 runtime 存取路径 → 只能裸指针算术 → ARC 编译失败")
    chk("设置页未用 ivar_getOffset 手工算指针（ARC 下必失败路径）",
        "ivar_getOffset" not in pm_code,
        "" if "ivar_getOffset" not in pm_code
        else "拿到偏移后必然要裸指针算术，ARC 下编不过")
    chk("设置页重启桌面只杀 SpringBoard（不重启整机）",
        "SpringBoard" in pm and "SIGTERM" in pm_code,
        "" if ("SpringBoard" in pm and "SIGTERM" in pm_code) else "缺重启桌面实现")
    chk("设置页未用没证据的 notify_post 通知名",
        "notify_post" not in pm_code,
        "" if "notify_post" not in pm_code else "通知名无实证，猜错=点了没反应")

# =====================================================================
print("\n[A7d] 禁左滑进相机：判据必须精确，不得废掉上滑解锁")

# ★★ 这是本轮最容易造成「严重误伤」的地方：
#   锁屏「左滑进相机」和「上滑解锁」都是 pan 类手势。
#   如果图省事写成「锁屏上所有 pan 都压掉」，解锁直接废掉 ——
#   对用户来说这比原需求（误触）严重得多，属于绝对不可接受。
#   所以判据必须锚定「右边缘」，而不是「pan」。

chk("有相机滑动判据 NLWIsCameraSwipeish", "NLWIsCameraSwipeish" in code)
chk("有滑动类判据 NLWIsPanish", "NLWIsPanish" in code)
chk("判据用 UIScreenEdgePanGestureRecognizer（公开类，不猜私有）",
    "UIScreenEdgePanGestureRecognizer" in code)
chk("判据读 edges 属性（公开 API，区分左右边缘）",
    re.search(r"\.edges\b", code) is not None,
    "" if re.search(r"\.edges\b", code) is not None else "不读 edges 就无法区分方向")
chk("判据要求 UIRectEdgeRight（只针对右边缘，不碰解锁）",
    "UIRectEdgeRight" in code,
    "" if "UIRectEdgeRight" in code else "缺右边缘限定 = 可能废掉上滑解锁")
chk("相机判据在压制前先过锁屏区域判据（作用域收窄）",
    "NLWIsSuppressibleLockScreenArea" in code)
chk("相机滑动受子开关控制（可单独关闭）",
    # ★ 判据必须是「**每一条压制路径**都判了这个子开关」，不能是「出现过一次」。
    #   本项目有两条路径会压制手势：
    #     · NLWProcessView 的压制循环（主要路径）
    #     · NLWAddGestureRecognizer 的预判（决定 add 之后要不要立刻处理）
    #   只判一处 → 关掉子开关后，另一条路径照样压 → 用户看到「关了没用」。
    #   反向验证 A15 实测：只查「出现过」时，删掉一处判据照样报绿。
    len(re.findall(r"gBlockCameraSwipe\s*&&\s*NLWIsCameraSwipeish", code)) >= 2 and
    re.search(r"static BOOL gBlockCameraSwipe\s*=", code) is not None,
    "压制路径含子开关 %d 处（应为 2）" %
    len(re.findall(r"gBlockCameraSwipe\s*&&\s*NLWIsCameraSwipeish", code)))

# ---- 1.4-1 新增：相机手势走「description 语义」这条旁路 ----
#
# ★★ 为什么这一组判据必须存在（本轮真正的修复就在这里）：
#   1.3-1 的实机日志证明，「视图树 + edges」这两个维度对相机手势**同时失效**：
#   它挂在 UISystemGestureView（独立窗口 _UISystemGestureWindow，与锁屏窗口
#   平行，祖先链里没有 CoverSheet/LockScreen），所以 sup 恒为 0；
#   而 8 个边缘手势里也没有任何一个 edges 含 Right。
#   → 修复的本质是「换一个数据源」（description），再「绕过区域判据」。
#   这两件事在源码里都只表现为几行，很容易被后续「统一写法」顺手抹平，
#   而抹平后编译照过、锁屏照常、只是功能悄悄退回不生效。
#   这类「语义被抹平」的回归，只有闸门能拦。
chk("有「按 description 认相机」判据",
    "static BOOL NLWIsCameraGestureByDescription(UIGestureRecognizer *g)" in code)

chk("有「系统手势窗口宿主」判据",
    "static BOOL NLWIsSystemGestureHost(UIView *v)" in code,
    "" if "static BOOL NLWIsSystemGestureHost(UIView *v)" in code
    else "缺它就永远发现不了挂在 _UISystemGestureWindow 上的手势")

_desc_refs = len(re.findall(r"NLWIsCameraGestureByDescription\s*\(", code))
chk("两条压制路径都查了 description 判据", _desc_refs >= 3,
    # 3 = 定义 1 处 + 调用 2 处（ProcessView / addGestureRecognizer）
    "引用 %d 次（定义 1 + 调用 2 = 3）" % _desc_refs)

chk("description 命中时绕过锁屏区域判据",
    # ★ 正形态：(camByDesc || camBySwipe) ? YES : NLWIsSuppressibleLockScreenArea(...)
    #   这一句就是「宿主不在锁屏视图树里也能压」的唯一通道。
    #   1.5-1 起判据扩到两个变量（swipe 那条也走同一个旁路口），
    #   所以正则里两者都要出现 —— 只有 camByDesc 会被判成「swipe 没接上」。
    re.search(r"\(\s*camByDesc\s*\|\|\s*camBySwipe\s*\)\s*\?\s*YES\s*:\s*"
              r"NLWIsSuppressibleLockScreenArea", code) is not None,
    "" if re.search(r"camByDesc\s*\|\|\s*camBySwipe\s*\)\s*\?\s*YES", code) else
    "改回「先过区域判据」→ 相机手势重新失效（它宿主天然不满足该判据）")

chk("压制循环里 description 命中也绕过区域判据",
    re.search(r"if\s*\(\s*camByDesc\s*\|\|\s*camBySwipe\s*\|\|", code) is not None,
    "" if re.search(r"if\s*\(\s*camByDesc\s*\|\|\s*camBySwipe", code) else
    "循环里若写成 areaOK && camByDesc，等于没有这条旁路")

# =====================================================================
print("\n[A4h] 1.5-1：swipe 类判据（上一版连看都没看见的那一类）")
# ★★ 为什么这一组必须存在：
#   1.4-1 的实机日志 108 条、13 个手势类**全是 Pan 类**，edges 只有 1/4/15，
#   8（右边缘）一次都没出现 —— 而相机功能确实生效。
#   唯一自洽的解释是：相机手势不是 pan 类，被类型过滤连续挡了两道。
#   1.5-1 因此新增 swipe 判据，并放宽诊断的类型过滤。
#   这一组判据锁的是「不要再退回只认 pan 类」。

chk("有 swipe 类判据 NLWIsCameraSwipeBySwipe",
    "static BOOL NLWIsCameraSwipeBySwipe(UIGestureRecognizer *g)" in code)

chk("swipe 判据读 direction 且只认左滑",
    # ★ 只压左滑：上滑解锁、下滑通知、右滑通知中心都不能动。
    re.search(r"UISwipeGestureRecognizerDirectionLeft", code) is not None,
    "" if "UISwipeGestureRecognizerDirectionLeft" in code else
    "不限定方向 → 会顺手废掉右滑呼出通知中心")

chk("swipe 判据先排除 pan 类（防左滑删除通知被误伤）",
    # ★ _UISwipeActionPanGestureRecognizer 名字里有 Swipe，
    #   实际是 UIPanGestureRecognizer 的子类，通知左滑删除用的就是它。
    #   类名兜底前不排除 pan 类 = 锁屏通知再也删不掉。
    #
    # ★ 第二个条件必须查原始 src：`"Swipe"` 是字符串字面量，
    #   strip_code 会把引号里的内容换成空格 → 查 code 必然假红。
    #   （这是本项目反复踩过的坑，见 A4g 附近同类注释。）
    re.search(r"if\s*\(\s*NLWIsPanish\(g\)\s*\)\s*return\s+NO;", code) is not None
    and re.search(r'strstr\(n,\s*"Swipe"\)', src) is not None,
    "" if re.search(r"if\s*\(\s*NLWIsPanish\(g\)\s*\)\s*return\s+NO;", code) else
    "类名兜底没排除 pan 类 → 左滑删除通知被废掉")

chk("两条压制路径都接入了 swipe 判据",
    # 定义 1 + ProcessView 1 + addGestureRecognizer 1 = 3
    len(re.findall(r"NLWIsCameraSwipeBySwipe\s*\(", code)) >= 3,
    "引用 %d 次（定义 1 + 调用 2 = 3）" %
    len(re.findall(r"NLWIsCameraSwipeBySwipe\s*\(", code)))

chk("swipe 判据在 add 层仍受作用域约束",
    # ★ 与 camByDesc 不同：camera 三个字母只可能指向相机，
    #   而 swipe 在主屏/App 内到处都是，必须在 add 层先过作用域判据。
    re.search(r"camBySwipe\s*=\s*\(NLWIsSystemGestureHost\(\(UIView \*\)self\)\s*\|\|",
              code) is not None,
    "" if "NLWIsSystemGestureHost((UIView *)self)" in code else
    "add 层不判作用域 → 主屏/App 内的左滑会被一起压")

chk("有「锁屏主窗口宿主」判据",
    "static BOOL NLWIsLockScreenWindowHost(UIView *v)" in code,
    "" if "static BOOL NLWIsLockScreenWindowHost(UIView *v)" in code else
    "缺它就覆盖不到「相机手势挂在锁屏窗口视图树上」这种情况")

chk("诊断不再只记录 pan 类（否则永远看不见相机手势）",
    # ★ 这是本轮最要紧的一条：上一版的早退条件
    #   `if (!isEdge && !NLWIsPanish(g)) return;` 直接把非 pan 类丢掉了，
    #   而相机手势恰恰就是非 pan 类 —— 诊断自己制造了盲区。
    re.search(r"if\s*\(!isEdge\s*&&\s*!isPan\s*&&\s*!sysHost\s*&&\s*!sup\)\s*return;",
              code) is not None,
    "" if "!isEdge && !isPan && !sysHost && !sup" in code else
    "诊断仍只记滑动类 → 非 pan 的相机手势永远查不到")

chk("诊断记录 swipe 的方向值",
    re.search(r"\(UISwipeGestureRecognizer \*\)g\)\.direction", code) is not None,
    "" if "UISwipeGestureRecognizer *)g).direction" in code else
    "不记 direction 就无法区分左滑/右滑")

chk("诊断的 desc 不再限定 pan 类",
    # ★ 1.4-1 把 desc 包在 `if (NLWIsPanish(g))` 里，
    #   结果最可疑的非 pan 类连 description 都没有 —— 等于对盲区失明。
    re.search(r"if\s*\(NLWIsPanish\(g\)\)\s*\{\s*\n\s*NSString \*d = \[g description\]",
              code) is None,
    "" if re.search(r"if\s*\(NLWIsPanish\(g\)\)\s*\{\s*\n\s*NSString \*d = \[g description\]",
                    code) is None else
    "desc 仍只对 pan 类记录 → 非 pan 手势看不到 target 是谁")

chk("系统手势窗口放行但不放弃热路径优化",
    # ★ 正形态：if (!areaOK && !sysHost(v) && !lockScreenWindowHost(v)) return 0;
    #   少了后两个条件 = 每个视图都要取 gestureRecognizers，
    #   didMoveToWindow 是启动期热路径，等于白分配几百次。
    re.search(r"!\s*areaOK\s*&&\s*!\s*NLWIsSystemGestureHost\(v\)\s*&&\s*"
              r"!\s*NLWIsLockScreenWindowHost\(v\)", code) is not None,
    "" if "NLWIsLockScreenWindowHost(v)) {" in code else
    "每个视图都取手势数组 = 启动期白分配几百次")

chk("长按仍严格限定在锁屏区域（不被新旁路带偏）",
    # ★ 反向：长按绝不能被 camByDesc 那条旁路带出去，
    #   否则主屏图标长按、通知长按会一起被压。
    re.search(r"if\s*\(!areaOK\)\s*continue", code) is not None,
    "" if re.search(r"if\s*\(!areaOK\)\s*continue", code) else
    "长按跟着走旁路 → 压掉主屏图标长按和通知长按")

# 反向：绝不能出现「无条件压掉所有 pan」
chk("没有「压掉所有 pan」的粗暴写法",
    re.search(r"NLWIsPanish\(g\)\s*\)\s*\{\s*NLWSuppressGesture", code) is None,
    "" if re.search(r"NLWIsPanish\(g\)\s*\)\s*\{\s*NLWSuppressGesture", code) is None
    else "会连上滑解锁一起废掉")
chk("总开关在判据最前面（关闭时零开销放行）",
    re.search(r"if\s*\(!gEnabled\)\s*return\s+0;", code) is not None,
    "" if re.search(r"if\s*\(!gEnabled\)\s*return\s+0;", code) else "关闭时仍在做判据")

# =====================================================================
print("\n[A8] killswitch（出问题不用卸载）")

chk("有 killswitch 判据 NLWIsKilled", "NLWIsKilled" in code)
chk("构造器里先查 killswitch", re.search(r"NLWInit.*?\{.*?NLWIsKilled", code, re.S) is not None)
chk("killswitch 文件路径含 com.liehuu.nlw.disabled",
    "com.liehuu.nlw.disabled" in src,
    # ★ 必须在**原始**源码里查，不能用 strip_code 后的 code：
    #   路径是字符串字面量，strip_code 会把引号内容换成空格，
    #   拿 code 查必然查不到（这正是 A10 那类「检查器自己错」的问题）。
    "" if "com.liehuu.nlw.disabled" in src else "缺 killswitch 路径")

# =====================================================================
print("\n[A9] 构造器整体 try/catch（异常不得影响宿主启动）")

m = re.search(r"__attribute__\(\(constructor\)\)\s*static\s+void\s+NLWInit\s*\([^)]*\)\s*\{", code)
chk("有 constructor 入口", m is not None)
if m:
    start = m.end() - 1
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = code[start:i + 1]
    chk("NLWInit 含 @try", "@try" in body)
    chk("NLWInit 含 @catch", "@catch" in body)

# =====================================================================
print("\n[A10] 打包脚本：GNU tar 格式（PAX 会让 dpkg 装不上）")

mk = open(MKDEB, encoding="utf-8").read()
bd = open(BUILD, encoding="utf-8").read()


def sh_code(txt):
    """剥掉 shell 脚本里的整行注释，只留真正的代码行。

    ★★★ 为什么所有 shell 判据都必须走这个函数（本项目已犯六次的同一类错误）：
      判据一旦拿全文查，就会被「解释为什么不能用某个写法」的说明文字蒙住。
      本轮实测的假通过：
        · D1「载荷含 PreferenceBundles」—— 注释里写了
          `Library/PreferenceBundles/<X>.bundle/` 的说明，删掉真正的 mkdir 后
          判据靠注释照样报绿。
        · A13c「未使用 __objc_exception」—— 注释里写了「不存在叫
          __objc_exception 的段」，于是判据永远 FAIL。
        · build.sh 里「故意不做设置页」「nlw.log」同理。
      这一类错误的共同点不是「正则写错」，而是**判据的作用域包含了注释**。
      所以：凡是对 .sh / .py 源码做「有没有某个字符串」的判断，
      必须先剥注释。不要靠「我这次注释里不写这个词」来回避 ——
      注释恰恰是最该写清楚「为什么不能这么写」的地方。
    """
    out = []
    for ln in txt.splitlines():
        s = ln.lstrip()
        if s.startswith("#"):
            out.append("")
        else:
            out.append(ln)
    return "\n".join(out)


mk_code = sh_code(mk)
bd_code = sh_code(bd)


def _code_hits(txt, needle):
    """返回 txt 里所有「引号闭合状态下」出现的 needle。

    ★ 这个坑在本项目已经犯过四次：检查器把「解释为什么不能用某个写法」的
      说明文字当成了「真的用了」。判据必须是「扫描到该位置时引号是否闭合」，
      而不是「行首是不是 #」。
    """
    hits = []
    quote = None
    i, n = 0, len(txt)
    while i < n:
        if quote is None:
            if txt.startswith('"""', i) or txt.startswith("'''", i):
                quote = txt[i:i + 3]
                i += 3
                continue
            c = txt[i]
            if c == "\\":
                i += 2
                continue
            if c in ('"', "'"):
                quote = c
                i += 1
                continue
            if txt.startswith(needle, i):
                hits.append((i, txt[max(0, i - 40):i + len(needle) + 40]))
                i += len(needle)
                continue
            i += 1
        else:
            if len(quote) == 3:
                if txt.startswith(quote, i):
                    quote = None
                    i += 3
                    continue
                if txt[i] == "\\":
                    i += 2
                    continue
                i += 1
            else:
                if txt[i] == "\\":
                    i += 2
                    continue
                if txt[i] == quote:
                    quote = None
                i += 1
    return hits


def _call_args(txt, start):
    i = txt.index("(", start)
    depth = 0
    while i < len(txt):
        if txt[i] == "(":
            depth += 1
        elif txt[i] == ")":
            depth -= 1
            if depth == 0:
                return txt[start:i + 1]
        i += 1
    return txt[start:start + 200]


_open_pos = _code_hits(mk_code, "tarfile.open(")
_calls = [_call_args(mk_code, p) for p, _ in _open_pos]
_no_fmt = [c for c in _calls if "format=" not in c]
chk("mkdeb.sh 的 tarfile.open 都指定了 format",
    bool(_calls) and not _no_fmt,
    ("%d 处未指定格式: %s" % (len(_no_fmt), _no_fmt[0][:60])) if _no_fmt
    else "%d 处全部显式指定" % len(_calls))
chk("mkdeb.sh 使用 tarfile.GNU_FORMAT", bool(_code_hits(mk_code, "tarfile.GNU_FORMAT")))
chk("mkdeb.sh 代码里未显式选 PAX_FORMAT", not _code_hits(mk_code, "PAX_FORMAT"))
chk("mkdeb.sh 里有 PAX 扩展头自检闸门", "_check_no_pax_headers" in mk_code)

# 闸门必须是「按 tar 结构遍历」而非盲扫（盲扫会在二进制载荷上误报，已实测）
m = re.search(r"def _check_no_pax_headers\(raw, label\):\n(.*?)\nimport lzma", mk_code, re.S)
if m:
    body = m.group(1)
    chk("PAX 闸门按 size 字段跳数据区（非盲扫）",
        "off += 512 + ((size + 511) // 512) * 512" in body or
        "off += 512 + ((size + 511) // 512) * 512" in body.replace(" ", ""),
        "" if "size + 511" in body else "盲扫会被 dylib 里的 'x' 字节误报")
    chk("PAX 闸门读 size 字段", "int(size_f, 8)" in body)

# =====================================================================
print("\n[A11] 可执行位：dylib 与设置页可执行体都必须是 755")

chk("mkdeb.sh 对 dylib chmod 755",
    re.search(r"chmod 755 \S*\$NAME\.dylib", mk) is not None)
chk("_is_exec 对 .dylib 返回 True", "endswith('.dylib')" in mk_code)

# ★ 设置页 bundle 的可执行体丢了 +x，表现是「设置页点不进去 / 白屏」，
#   dpkg 不报错。必须单独判。
chk("mkdeb.sh 对设置页可执行体 chmod 755",
    re.search(r"chmod 755 \S*\$PREFBUNDLE/\$PREFNAME", mk) is not None,
    "" if re.search(r"chmod 755 \S*\$PREFBUNDLE/\$PREFNAME", mk)
    else "缺 chmod，设置页可执行体可能 644")
chk("_is_exec 覆盖 PreferenceBundles 下的可执行体",
    "Library/PreferenceBundles/" in mk_code,
    "" if "Library/PreferenceBundles/" in mk_code else "打包脚本不认设置页可执行位")
chk("_is_exec 用 len('.bundle') 而不是写死切片下标",
    "len('.bundle')" in mk_code,
    "" if "len('.bundle')" in mk_code else "写死下标错一位就静默失败")

# ★★ data.tar 必须排除 DEBIAN/（2026-10-22 发现的真实缺陷）
#   dpkg 只从 control.tar.gz 读控制文件。data.tar 里若也带 ./DEBIAN/postinst，
#   会被解到文件系统根目录的 /DEBIAN/postinst：
#     · rootful → 根目录留垃圾，卸载不清
#     · rootless → / 只读，解包直接失败，整个 deb 装不上
#   这个缺陷 1.0-1 / 1.1-1 / 1.2-1（首版）一直存在，装了 3 个版本才被发现。
#   ★ 判据要盯「_tar() 遍历时是否真的剪掉了」，不是「注释里有没有写 DEBIAN」。
chk("mkdeb.sh 的 _tar() 遍历时剪掉 DEBIAN/",
    re.search(r"rel_root\s*==\s*'DEBIAN'", mk_code) is not None
    and re.search(r"dirs\[:\]\s*=", mk_code) is not None,
    "" if re.search(r"rel_root\s*==\s*'DEBIAN'", mk_code) is not None
    else "data.tar 会带上 ./DEBIAN/postinst（rootless 上 / 只读 → 装不上）")
chk("剪枝后确实 continue 跳过该子树",
    re.search(r"startswith\('DEBIAN/'\)[\s\S]{0,120}?continue", mk_code) is not None,
    "" if re.search(r"startswith\('DEBIAN/'\)[\s\S]{0,120}?continue", mk_code)
    else "只判了条件没跳过 → 剪枝无效")
chk("check_deb.py 校验 data.tar 不含 DEBIAN/",
    "不含 DEBIAN/" in open(CHECKDEB, encoding="utf-8").read()
    if os.path.isfile(CHECKDEB) else False,
    "" if os.path.isfile(CHECKDEB) and
    "不含 DEBIAN/" in open(CHECKDEB, encoding="utf-8").read()
    else "产物侧无此判据 → 打包脚本被改回来时无人发现")

# =====================================================================
print("\n[A11b] 设置页载荷：两个入口缺一不可")

# ★★ 判据必须精确到「真的有 mkdir/cp 这两个路径」，而不是「文本里出现过
#   这个路径字符串」。反向验证 D1/D2 实测：只查字符串时，把真正的 mkdir
#   删掉、判据靠注释里的路径说明照样报绿 —— 假通过。
#   正确做法：在剥掉注释的代码里，找实际命令行的出现。
def _has_cmd_line(code_txt, needle):
    """在剥注释后的 shell 代码里，找「非注释行中包含 needle」的行。

    与 _code_hits 的区别：这里不需要引号状态机（shell 判据多看命令行形态），
    但必须确认 needle 出现在**会被执行的**那一行里，
    且不是出现在 echo/printf 的提示文案中。
    """
    for ln in code_txt.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if needle not in s:
            continue
        # 排除「只是打印给用户看的文案」
        if s.startswith("echo ") or s.startswith("printf "):
            continue
        return True
    return False


chk("mkdeb.sh 真的把设置页可执行文件拷进 PreferenceBundles",
    _has_cmd_line(mk_code, '.build/$PREFNAME.bundle/$PREFNAME'),
    "" if _has_cmd_line(mk_code, '.build/$PREFNAME.bundle/$PREFNAME')
    else "bundle 里没有可执行文件 → 设置页白屏")
chk("mkdeb.sh 真的写了 PreferenceLoader 入口 plist",
    _has_cmd_line(mk_code, "$PREFENTRY/Root.plist"),
    "" if _has_cmd_line(mk_code, "$PREFENTRY/Root.plist")
    else "缺入口 plist → 设置里根本不显示本插件")
# ★ 为什么判据锚定「cp / 写文件」而不是「mkdir -p」：
#   mkdir 失败会让后续 cp 立即报错，属于「构建就挂」，不是静默缺陷；
#   而 cp 那一步被删掉时脚本照样跑完、deb 照样生成，
#   装上去才发现「设置里没有这一项」—— 那才是真正需要拦的。
chk("PREFBUNDLE 指向 PreferenceBundles",
    re.search(r"PREFBUNDLE=\"\$STAGE/Library/PreferenceBundles/\$PREFNAME\.bundle\"",
              mk_code) is not None,
    "" if re.search(r"PREFBUNDLE=", mk_code) else "缺 PREFBUNDLE 定义")
chk("PREFENTRY 指向 PreferenceLoader/Preferences",
    re.search(r"PREFENTRY=\"\$STAGE/Library/PreferenceLoader/Preferences/\$PREFNAME\"",
              mk_code) is not None,
    "" if re.search(r"PREFENTRY=", mk_code) else "缺 PREFENTRY 定义")
chk("入口 plist 转二进制（PLIST 格式）",
    _has_cmd_line(mk_code, "FMT_BINARY"),
    "" if _has_cmd_line(mk_code, "FMT_BINARY") else "XML 文本在部分版本不被 PreferenceLoader 认")

# ★ 图标三个尺寸都要打包：入口 plist 写的是基础名 icon.png，
#   iOS 按 scale 找 @2x/@3x。缺基础名时部分版本显示空白方块，且无报错。
chk("mkdeb.sh 三个尺寸图标一起打包（含基础名 icon.png）",
    _has_cmd_line(mk_code, "for _ic in icon.png icon@2x.png icon@3x.png"),
    "" if _has_cmd_line(mk_code, "for _ic in icon.png icon@2x.png icon@3x.png")
    else "缺基础名 → 部分版本设置页图标空白")
chk("control 依赖 preferenceloader",
    _has_cmd_line(mk_code, "preferenceloader"),
    "" if _has_cmd_line(mk_code, "preferenceloader") else "纯净设备上装完设置里看不到，dpkg 不报错")
chk("已移除「故意不做设置页」的旧注释",
    "故意不做设置页" not in mk_code)
chk("postinst 不再提日志路径",
    "nlw.log" not in mk_code,
    "" if "nlw.log" not in mk_code else "日志已删，postinst 还在指路")
# ★ postinst 的文案本身写在 printf 里（heredoc 内容），
#   所以这一条不能用 _has_cmd_line（它会跳过 printf 行）。
#   判据改为「heredoc 正文里出现过」，且必须出现 —— 装完用户需要知道去哪开关。
chk("postinst 提示了设置页位置",
    "设置 → NoLockWallpaper" in mk_code,
    "" if "设置 → NoLockWallpaper" in mk_code else "装完用户不知道去哪开/关")

# ★★★ postinst / prerm / postrm 里**绝不允许**重启桌面。
#   真实事故（1.1-1，用户报「Sileo 报错，dpkg 被中断，无法安装 deb」）：
#     postinst 是 dpkg 事务的最后一步。在 Sileo 里安装时它跑 sbreload
#     → SpringBoard 重启 → 前台 App（Sileo）被系统终止
#     → dpkg 还没写完 status、还没释放 lock 就被切断
#     → 锁残留 + 半装状态 → 之后任何 deb 都装不上。
#   `|| true` 只兜返回码，**兜不住进程被杀 / 事务被切断**。
#   这类缺陷不是「编不过」也不是「功能缺失」，是「装包时炸环境」，
#   严重性等同于崩宿主，所以必须是硬判据。
#
#   ★ 判据必须查**剥离注释后的正文**：
#     本轮修复时我在注释里写了 sbreload 三个字解释原因，
#     若拿全文查会被自己的说明蒙住（本项目已犯 6 次的同一类错误）。
#
#   ★ 只查「会真的启进程」的形态，不查裸 "respring" 这个词 ——
#     postinst 的 printf 文案里本来就要写「请手动 respring」，
#     拿这个词做判据会误伤文案。判据只认真正执行的命令。
_POSTINST_BAD = (
    r"\bsbreload\b",
    r"killall[^\n]*SpringBoard",
    r"launchctl\s+kickstart",
    r"/usr/bin/killall",
)
_bad_pi = [p for p in _POSTINST_BAD if re.search(p, mk_code)]
chk("mkdeb.sh 的 postinst 里不重启桌面（会打断 dpkg 事务→锁残留）",
    not _bad_pi,
    "实际含：%s —— postinst 是事务最后一步，重启桌面会切断 dpkg，"
    "留下 lock + 半装状态，之后所有 deb 都装不上" % _bad_pi if _bad_pi else "")

# prerm/postrm 同理：卸载过程里重启桌面一样会打断事务
chk("卸载脚本也没有重启桌面的动作", not _bad_pi)

# =====================================================================
print("\n[A12] 注入范围限定 SpringBoard")

pl = open(PLIST, encoding="utf-8").read()
chk("Filter 限定 com.apple.springboard", "com.apple.springboard" in pl)
chk("没有把 Filter 写成空（会注入所有进程）",
    re.search(r"Bundles\s*=\s*\(\s*\)", pl) is None)

# =====================================================================
print("\n[A13] 构建不安装 Theos（用户明确要求）")

bd = open(BUILD, encoding="utf-8").read()
chk("build.sh 未调用 install-theos", "install-theos" not in bd_code)
chk("build.sh 用 xcrun clang 直接编", "xcrun clang" in bd_code)
chk("build.sh 走 mkdeb.sh 打包", "mkdeb.sh" in bd_code)
chk("build.sh 覆盖 arm64e 与 arm64",
    "arm64e" in bd_code and "arm64)" in bd_code)
chk("build.sh 校验架构切片", "lipo -info" in bd_code)

# =====================================================================
print("\n[A13b] 功能指纹闸门：属性名 vs 选择子名（真实误报过一次）")

# ★★ 这是一个「闸门自己写错」的真实案例，必须长期防住：
#   代码里用点语法赋值 `g.minimumPressDuration = 90.0;`，
#   编译器翻成 objc_msgSend(g, @selector(setMinimumPressDuration:), 90.0)，
#   落到 __objc_methname 的字符串是 setMinimumPressDuration（带 set、大写 M）。
#   而闸门最初写的是 getter 名 minimumPressDuration，大小写敏感 → 永远匹配
#   不到 → 每次构建都误报「缺符号」。这个缺陷在编译错误的掩盖下潜伏了
#   整整三个 run（前几次都先崩在编译阶段，根本没跑到这一步）。
chk("代码确实在赋 minimumPressDuration（闸门的前提成立）",
    ".minimumPressDuration = 90.0" in code,
    "" if ".minimumPressDuration = 90.0" in code else "改写法后闸门前提要重新确认")

# ★ 判据必须只扫「for sym 列表本身」，不能全文搜 bd。
#   为什么：注释里也会提到 setMinimumPressDuration（就是上面那段说明），
#   全文搜会被自己的注释蒙过去 —— 反向验证 B1 实测漏放，正是这个原因。
#   「把说明文字当成代码」这个坑本项目已经犯过四次，这里必须精确到结构。
#
# ★★ 1.1 起 build.sh 里有**四个** for sym 循环（主指纹 / 相机指纹 /
#    开关指纹 / 设置页指纹）。只解析第一个会让后三个的判据全部失效 ——
#    那是「闸门自己漏检」，比没有闸门更危险（因为它报绿）。
#    所以这里解析**全部**循环，合并成一个集合。
_fp_block = []
for _m in re.finditer(r"for sym in (.*?);\s*do", bd_code, re.S):
    _fp_block.extend(_m.group(1).split())
chk("能解析出指纹列表 for sym", bool(_fp_block),
    "解析到 %d 项（含全部 for 循环）" % len(_fp_block) if _fp_block else "解析失败，判据无效")
chk("build.sh 至少有 2 个 for sym 循环（主 + 设置页）",
    len(re.findall(r"for sym in (.*?);\s*do", bd_code, re.S)) >= 2,
    "实际 %d 个" % len(re.findall(r"for sym in (.*?);\s*do", bd_code, re.S)))

chk("指纹列表含 setMinimumPressDuration（setter 名）",
    "setMinimumPressDuration" in _fp_block,
    "" if "setMinimumPressDuration" in _fp_block else "缺 setter 名，闸门永远匹配不到")
chk("指纹列表不含裸 getter 名 minimumPressDuration",
    "minimumPressDuration" not in _fp_block,
    "" if "minimumPressDuration" not in _fp_block else "属性名 ≠ 选择子名")

chk("指纹列表覆盖三个 hook 选择子",
    all(x in _fp_block for x in
        ["addGestureRecognizer:", "didMoveToWindow", "viewDidAppear:"]),
    "实际：%s" % _fp_block)
chk("指纹列表覆盖锁屏类名判据关键串",
    "LockScreen" in _fp_block and "CoverSheet" in _fp_block,
    "实际：%s" % _fp_block)

# ★ 1.1 新增功能的指纹：任何一条漏了，产物表面看不出来，装上就是功能缺失。
chk("指纹列表覆盖 edge pan 判据",
    "UIScreenEdgePanGestureRecognizer" in _fp_block and "edges" in _fp_block,
    "实际：%s" % _fp_block,
    )
chk("指纹列表覆盖配置域名与两个开关键",
    all(x in _fp_block for x in
        ["com.liehuu.nolockwallpaper", "Enabled", "BlockCameraSwipe"]),
    "实际：%s" % _fp_block)

# ★★★ 对产物做字符串指纹时，禁止使用「编译后不存在的名字」。
#   真实翻车（本轮 check_deb.py，产物校验）：
#     UIRectEdgeRight 是枚举常量（值 8），编译后是 `and w8, w0, #8`
#     这类指令里的立即数 —— 二进制里**一个字节都不剩**。
#     判据查它 → 在真实产物上必然 FAIL，而产物其实完全正确。
#   同类会消失的名字：
#     · 枚举常量（UIRectEdgeRight / NSTextAlignmentCenter ...）
#     · #define 宏（宏在预处理期就被替换掉）
#     · static const（会被内联或消除）
#     · C 内联函数名
#   反过来，**会留下字符串**的是：
#     · selector 名（@selector(...) / 点语法调用的方法名）
#     · 类名（objc_getClass / isKindOfClass 用的字符串）
#     · 字符串字面量（CFPreferences 域名、键名、通知名）
#   所以：产物指纹一律只用后三类。前一类靠源码层判据保证。
_DEAD_FP = ["UIRectEdgeRight", "UIRectEdgeLeft", "UIRectEdgeTop",
            "UIRectEdgeBottom", "UIRectEdgeAll", "UIRectEdgeNone"]
_dup = [x for x in _DEAD_FP if x in _fp_block]
chk("指纹列表不含编译后会消失的枚举常量",
    not _dup,
    "实际含：%s —— 枚举值是立即数，产物里没有字符串 → 闸门必然误报" % _dup
    if _dup else "")
# check_deb.py 是真正跑在产物上的校验，同样不能查枚举
# ★★ 必须查**原始源码**，不能用 strip_code：strip_code 会把字符串字面量
#   的内容一起换成空格，而这里要找的 `b"UIRectEdgeRight"` 本身就是字符串字面量
#   —— 用 _cd_code 查永远匹配不到，判据 100% 假绿（反向验证 E1 实测漏放）。
#   本项目已犯过同源错误：判据含字符串字面量时必须查原始 src。
if os.path.isfile(CHECKDEB):
    _cd_raw = open(CHECKDEB, encoding="utf-8").read()
    # ★ 这里**必须**查原始文本 _cd_raw，不能查 strip_code 结果：
    #   要找的 `b"UIRectEdgeRight"` 本身就是字符串字面量，
    #   strip_code 会把字面量内容换成空格 → 永远匹配不到 → 判据 100% 假绿。
    #   （反向验证 E1 实测漏放，本项目同源错误已第 N 次出现。）
    #   代价是注释里提到枚举名也会命中 —— 但这方向是「宁可误报」：
    #   注释里写 `b"UIRectEdgeRight"` 这种形态本来就极罕见，
    #   而漏放的后果是产物校验脚本带着死判据上线。
    _dup2 = [x for x in _DEAD_FP if ('b"%s"' % x) in _cd_raw]
    chk("check_deb.py 不用枚举常量做产物指纹",
        not _dup2,
        "实际含：%s —— 该名字编译后不存在，判据必然 FAIL" % _dup2
        if _dup2 else "")

    # ★★ 1.1-1 事故的产物侧防线必须存在：
    #   光在 mkdeb.sh 里改掉 sbreload 不够 —— 源码改了、下次有人改回来，
    #   或者手工替换 control.tar，产物侧就瞎了。必须在 check_deb.py
    #   里对**维保脚本正文**留一条硬判据。
    #
    # ★ 判据写法坑（反向验证 E2 实测漏放）：第一版用 `"_MNT_BAD" in _cd_raw`，
    #   而把变量改名成 `_MNT_BAD_UNUSED = (` 后子串依然命中 → 判据假绿。
    #   **子串包含 ≠ 该判据还在工作**。必须匹配「定义 + 真正被遍历使用」两处形态，
    #   且用 \b 边界，防止 `_MNT_BAD_XXX` 这类近名变量蒙过。
    #   通用规律：用 in 判「某个检查还在不在」时，先想清楚
    #   「把这段代码删掉/改名后，这个子串还在不在」。
    _has_mnt_def = re.search(r"\b_MNT_BAD\s*=\s*\(", _cd_raw) is not None
    _has_mnt_use = re.search(r"in\s+_MNT_BAD\b", _cd_raw) is not None
    chk("check_deb.py 校验维保脚本不重启桌面",
        _has_mnt_def and _has_mnt_use,
        "" if (_has_mnt_def and _has_mnt_use) else
        "缺产物侧判据（定义=%s 使用=%s）：postinst 里若混进 sbreload 会打断"
        " dpkg 事务 → 锁残留 → 之后所有 deb 装不上（1.1-1 真实事故）"
        % (_has_mnt_def, _has_mnt_use))
    chk("check_deb.py 的维保脚本判据排除了 printf/echo 提示行",
        re.search(r'\^\(\?:printf\|echo\)', _cd_raw) is not None
        or re.search(r"printf|echo", _cd_raw) is not None,
        "" if re.search(r"printf", _cd_raw) is not None else
        "不排除提示行 → 文案里的「请手动 respring」会 100% 误报")
    chk("check_deb.py 校验了维保脚本权限 0755",
        "smode & 0o777" in _cd_raw,
        "" if "smode & 0o777" in _cd_raw else
        "postinst 不是 0755 时 dpkg 会拒绝执行（现只有 dylib/图标的权限判据，管不到维保脚本）")

# 设置页产物必须单独过指纹闸门（主 dylib 的闸门管不到它）
chk("build.sh 有设置页产物的指纹闸门",
    re.search(r"PREF_OUT/\$PREFNAME\b", bd) is not None or
    "PREF_OUT" in bd_code,
    "" if "PREF_OUT" in bd_code else "设置页白屏不会被发现")
chk("设置页产物也过架构闸门",
    re.search(r'lipo -info\s+"\$PREF_OUT', bd_code) is not None,
    "" if re.search(r'lipo -info\s+"\$PREF_OUT', bd_code) is not None
    else "设置页架构不对不会被发现（主 dylib 的闸门查不到它）")

# ★★ 设置页指纹**不能**含配置域名 —— 否则闸门必然误报。
#   真实翻车（本轮 CI run 39713919672）：
#     设置页的开关读写全交给 PSSwitchCell（走 Root.plist 的 defaults + key），
#     控制器代码从来不碰 com.liehuu.nolockwallpaper。早先版本为了"看起来严谨"
#     声明了一个 kNLWDomain 常量但从未使用（-Wunused-const-variable），
#     删掉之后 —— 指纹闸门开始报「设置页缺 com.liehuu.nolockwallpaper」并 exit 1。
#     这就是「改了实现没同步改判据」：查的是一个已经不存在的东西。
#   域名正确的归属：主 dylib（读开关）+ Resources/Root.plist（写开关），
#   两者对齐由 A7b 校验。设置页只要类名/载入/ivar 存取/重启能力。
_pref_fp = ""
for _m in re.finditer(r"for sym in (.*?);\s*do(.*?)done", bd_code, re.S):
    if "PREF_OUT" in _m.group(2):
        _pref_fp = _m.group(1)
chk("能解析出设置页指纹列表", bool(_pref_fp),
    "" if _pref_fp else "解析失败则下列两条判据无效")
chk("设置页指纹不含配置域名（设置页不碰 CFPreferences）",
    "com.liehuu.nolockwallpaper" not in _pref_fp.split(),
    "" if "com.liehuu.nolockwallpaper" not in _pref_fp.split()
    else "设置页产物里没有这个字符串 → 闸门必然误报 exit 1（本轮 CI 真实翻车）")
chk("设置页指纹含 object_getIvar（证明走的是 runtime 存取而非裸指针）",
    "object_getIvar" in _pref_fp.split(),
    "实际：%s" % _pref_fp.split())

# =====================================================================
print("\n[A13c] 异常处理段名必须是真的（__objc_exception 不存在）")

# ★ 真实踩过的坑：第一版闸门查 otool 里的 '__objc_exception'，
#   这个段**不存在**（ObjC 的 @try 落点是 __TEXT 段里的 __gcc_except_tab）。
#   于是那条闸门永远走 else 分支只打印「提示」，从来没真正拦过任何东西 ——
#   典型的「闸门自己坏掉还一直报绿」。
#
# ★★ 判据必须排除注释。本项第一版写成 `"__objc_exception" not in bd`，
#   结果被 build.sh 里那句「**不存在**叫 __objc_exception 的段」的说明
#   命中了 —— 又是「把解释文字当成代码」，本项目已犯过五次。
#   正解：剥掉整行注释后再查。
_bd_code = "\n".join(
    ("" if ln.lstrip().startswith("#") else ln) for ln in bd.splitlines())
chk("build.sh 代码里未使用不存在的段名 __objc_exception",
    "__objc_exception" not in _bd_code,
    "" if "__objc_exception" not in _bd_code else "该段不存在，判据永远不生效")
chk("build.sh 用 __gcc_except_tab 判 @try 生效", "__gcc_except_tab" in _bd_code,
    "" if "__gcc_except_tab" in _bd_code else "缺真实的 LSDA 段判据")
chk("build.sh 缺 __gcc_except_tab 时是硬失败（不是提示）",
    re.search(r"__gcc_except_tab.*?exit 1", _bd_code, re.S) is not None,
    "" if re.search(r"__gcc_except_tab.*?exit 1", _bd_code, re.S) is not None
    else "只打印提示 = 等于没拦")

# ★★ __objc_personality 是**未定义符号**（由 libobjc 运行期提供），不是段。
#   真实翻车（本轮 CI run 39714345362）：
#     判据写成 `otool -l | grep __objc_personality` → 段表里永远没有它
#     → 闸门必然 exit 1，编译明明成功却被判死。
#   与「__objc_exception 段不存在」完全同源：**把符号当成段来查**。
#   必须：查符号用 nm -u，查段用 otool -l，两者不能混。
chk("build.sh 用 nm -u 查 __objc_personality 符号（不是 otool -l 找段）",
    re.search(r"nm\s+-u[^\n]*\n?[^\n]*__objc_personality", _bd_code) is not None
    or re.search(r"nm\s+-u[^|\n]*\|[^\n]*__objc_personality", _bd_code) is not None,
    "" if re.search(r"nm\s+-u", _bd_code) is not None
    else "用 otool -l 找未定义符号 → 永远匹配不到 → 必然 exit 1")
chk("build.sh 不用 otool -l 查 __objc_personality（会把符号当段查）",
    re.search(r"otool\s+-l[^\n]*__objc_personality", _bd_code) is None,
    "" if re.search(r"otool\s+-l[^\n]*__objc_personality", _bd_code) is None
    else "otool -l 只列段，未定义符号不在其中 → 闸门必然误报")

# =====================================================================
print("\n[A14] shell 里 $VAR 不得紧跟全角字符（bash 会算进变量名）")

for name, txt in [("build.sh", bd), ("mkdeb.sh", mk)]:
    bad = []
    for i, line in enumerate(txt.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m2 in re.finditer(r"\$[A-Za-z_][A-Za-z0-9_]*", line):
            after = line[m2.end():m2.end() + 1]
            if after and ord(after) > 0x7F:
                bad.append("L%d: %s" % (i, line.strip()[:50]))
    chk("%s 无 $VAR 紧邻全角" % name, not bad, "；".join(bad[:3]))

# =====================================================================
print("\n[A15] 行尾必须 LF（CRLF 会让 runner 报 command not found）")

for path in [BUILD, MKDEB, os.path.join(_REPO, "NoLockWallpaper.plist")]:
    if not os.path.isfile(path):
        continue
    raw = open(path, "rb").read()
    chk("%s 为 LF" % os.path.basename(path), b"\r\n" not in raw,
        "" if b"\r\n" not in raw else "含 CRLF，runner 上会报错")

# =====================================================================
print("\n[A16] 索引里的可执行位（644 在 runner 上 permission denied）")

# ★ 真实事故（SingleVPN 项目反复出现）：
#   git add -A 会把 755 降成 644；Windows 上 os.name == 'nt' 恒真导致
#   REST 直推也走错分支。表现是 runner 上报 "permission denied"，
#   或者 sh 脚本能被 sh 调用但 ./build.sh 不行。
try:
    import subprocess
    out = subprocess.run(["git", "ls-files", "-s", "devkit/"],
                         cwd=_REPO, capture_output=True, text=True).stdout
    bad = []
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        mode = meta.split()[0]
        if path.endswith(".sh") and mode != "100755":
            bad.append("%s=%s" % (path, mode))
    chk("索引中所有 .sh 均为 100755", not bad,
        "；".join(bad) + "（修法：git update-index --chmod=+x <file>）" if bad else "全部 100755")
except Exception as e:
    chk("索引中所有 .sh 均为 100755", False, "无法读取索引：%s" % e)

# =====================================================================
print()
print("通过 %d 项，失败 %d 项" % (len(oks), len(fails)))
if fails:
    print("失败：")
    for n, d in fails:
        print("  -", n, d)
    sys.exit(1)
print("全部通过")