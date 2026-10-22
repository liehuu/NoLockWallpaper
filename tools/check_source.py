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
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(_REPO, "Tweak.m")
BUILD = os.path.join(_REPO, "devkit", "build.sh")
MKDEB = os.path.join(_REPO, "devkit", "mkdeb.sh")
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

for gorig in ["gOrigAddGesture", "gOrigDidMove", "gOrigViewDidAppear"]:
    # ★ 实际写法是 ((void (*)(id, SEL, id))gOrigXxx)(self, _cmd, ...)
    #   正则必须容忍 (*)( 里面带参数列表。
    #   第一版写成 \(\*\s*\) 只匹配空参数，结果"调用 0 处"——典型的假通过：
    #   检查项没在跑却报 OK。这里改成允许参数表任意内容。
    calls = re.findall(r"\(\s*\(\s*void\s*\(\s*\*\s*\)\s*\([^)]*\)\s*\)\s*%s\s*\)"
                       % gorig, code)
    guarded = re.findall(r"if\s*\(\s*%s\s*\)" % gorig, code)
    named = re.findall(r"[^a-zA-Z_](%s)\s*\)\s*\(" % gorig, code)

    # ★ 必须真的找到调用点，否则"全判空"是假通过
    chk("%s 存在真实调用点（判据有效性自检）" % gorig,
        len(calls) >= 1,
        "找到 %d 处强转调用" % len(calls))
    chk("%s 每次调用前都判空" % gorig,
        len(calls) >= 1 and len(guarded) >= 1,
        "调用 %d 处 / 判空 %d 处" % (len(calls), len(guarded)))

# =====================================================================
print("\n[A4] hook 安装必须区分「自己实现」与「继承」（否则污染父类）")

chk("NLWInstallHook 比较 curImp 与 supImp", "curImp == supImp" in code,
    "" if "curImp == supImp" in code else "未区分继承，会改到父类方法表")
chk("继承时走 class_addMethod（不碰父类）", "class_addMethod" in code)
chk("安装失败返回 NO（干净跳过）", re.search(r"return NO;", code) is not None)

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
    # ★ 期望值要精确：定义 1 次 + 两处调用 = 3 次。
    #   （上一版写成 >=4 是预期值本身错，属于本项目踩过的「闸门自己错」。）
    code.count("NLWIsSuppressibleLockScreenArea") == 3,
    "引用 %d 次（应为 1 定义 + 2 调用）" % code.count("NLWIsSuppressibleLockScreenArea"))

# =====================================================================
print("\n[A6] 私有 selector 零依赖（只允许公开 API）")

# ★ 这是本项目最重要的架构约束：不猜私有方法名 = 不可能签名错位崩宿主
forbidden = [
    "SBLockScreenView ", "setWallpaperMode:", "wallpaperMode",
    "_setWallpaper", "deactivateWallpaper", "handleLongPress",
]
for f in forbidden:
    chk("未调用私有符号 %r" % f.strip(), f not in code)

# 只允许 hook 这三个公开 selector
hooked_sel = re.findall(r"@selector\(([^)]+)\)", code)
allowed = {"addGestureRecognizer:", "didMoveToWindow", "viewDidAppear:"}
extra = [s.strip() for s in hooked_sel if s.strip() not in allowed]
chk("只 hook 了公开 API", not extra, "多出的：%s" % extra if extra else
    "addGestureRecognizer: / didMoveToWindow / viewDidAppear:")

# =====================================================================
print("\n[A7] 热路径零 IO（watchdog 会杀进程）")

# 日志调用点必须少，且 NLWLog 内部不能在循环里
log_calls = [i for i, l in enumerate(code_lines, 1) if "NLWLog(" in l]
chk("NLWLog 调用点 <= 5", len(log_calls) <= 5, "实际 %d 处：%s" % (len(log_calls), log_calls))

# NLWProcessView 里不能直接写日志（只能通过 gLoggedInventory 门控）
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
    chk("NLWProcessView 内日志有 gLoggedInventory 门控",
        ("NLWLog(" not in body) or ("if (!gLoggedInventory)" in body),
        "" if ("NLWLog(" not in body or "if (!gLoggedInventory)" in body) else "无门控 = 每次都写盘")

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


_open_pos = _code_hits(mk, "tarfile.open(")
_calls = [_call_args(mk, p) for p, _ in _open_pos]
_no_fmt = [c for c in _calls if "format=" not in c]
chk("mkdeb.sh 的 tarfile.open 都指定了 format",
    bool(_calls) and not _no_fmt,
    ("%d 处未指定格式: %s" % (len(_no_fmt), _no_fmt[0][:60])) if _no_fmt
    else "%d 处全部显式指定" % len(_calls))
chk("mkdeb.sh 使用 tarfile.GNU_FORMAT", bool(_code_hits(mk, "tarfile.GNU_FORMAT")))
chk("mkdeb.sh 代码里未显式选 PAX_FORMAT", not _code_hits(mk, "PAX_FORMAT"))
chk("mkdeb.sh 里有 PAX 扩展头自检闸门", "_check_no_pax_headers" in mk)

# 闸门必须是「按 tar 结构遍历」而非盲扫（盲扫会在二进制载荷上误报，已实测）
m = re.search(r"def _check_no_pax_headers\(raw, label\):\n(.*?)\nimport lzma", mk, re.S)
if m:
    body = m.group(1)
    chk("PAX 闸门按 size 字段跳数据区（非盲扫）",
        "off += 512 + ((size + 511) // 512) * 512" in body or
        "off += 512 + ((size + 511) // 512) * 512" in body.replace(" ", ""),
        "" if "size + 511" in body else "盲扫会被 dylib 里的 'x' 字节误报")
    chk("PAX 闸门读 size 字段", "int(size_f, 8)" in body)

# =====================================================================
print("\n[A11] dylib 权限必须是 755（644 会被 ellekit 拒绝）")

chk("mkdeb.sh 对 dylib chmod 755",
    re.search(r"chmod 755 \S*\$NAME\.dylib", mk) is not None)
chk("_is_exec 对 .dylib 返回 True", "endswith('.dylib')" in mk)

# =====================================================================
print("\n[A12] 注入范围限定 SpringBoard")

pl = open(PLIST, encoding="utf-8").read()
chk("Filter 限定 com.apple.springboard", "com.apple.springboard" in pl)
chk("没有把 Filter 写成空（会注入所有进程）",
    re.search(r"Bundles\s*=\s*\(\s*\)", pl) is None)

# =====================================================================
print("\n[A13] 构建不安装 Theos（用户明确要求）")

bd = open(BUILD, encoding="utf-8").read()
chk("build.sh 未调用 install-theos", "install-theos" not in bd)
chk("build.sh 用 xcrun clang 直接编", "xcrun clang" in bd)
chk("build.sh 走 mkdeb.sh 打包", "mkdeb.sh" in bd)
chk("build.sh 覆盖 arm64e 与 arm64",
    "arm64e" in bd and "arm64)" in bd)
chk("build.sh 校验架构切片", "lipo -info" in bd)

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
print()
print("通过 %d 项，失败 %d 项" % (len(oks), len(fails)))
if fails:
    print("失败：")
    for n, d in fails:
        print("  -", n, d)
    sys.exit(1)
print("全部通过")