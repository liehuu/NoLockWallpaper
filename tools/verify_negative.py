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
    tmpdir = tempfile.mkdtemp(prefix="nlw_neg_bd_")
    try:
        target = os.path.join(tmpdir, "build.sh")
        with io.open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(mutated_text)
        env = dict(os.environ)
        env["NLW_BUILD_OVERRIDE"] = target
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
]


def main():
    original = io.open(SRC, encoding="utf-8").read()
    build_sh_orig = io.open(BUILD_SH, encoding="utf-8").read()

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
        for idx, (name, old, new, expect) in enumerate(CASES, 1):
            total += 1
            if old not in original:
                print("[A%d] SKIP  %s" % (idx, name))
                print("      原因：源码里找不到要替换的片段（用例本身过期了，需更新）")
                fails.append("%s（用例片段过期）" % name)
                continue

            mutated = original.replace(old, new, 1)
            if mutated == original:
                print("[A%d] SKIP  %s（替换无效）" % (idx, name))
                fails.append("%s（替换无效）" % name)
                continue

            path = os.path.join(tmpdir, "Tweak_neg_%02d.m" % idx)
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(mutated)

            ok2, out2 = run_check(path)
            blocked = (not ok2) and (expect in out2)
            status = "OK   已拦截" if blocked else "!! 漏放"
            print("[A%d] %s  %s" % (idx, status, name))
            if not blocked:
                fails.append(name)
                # 打印诊断：是没失败，还是失败在别的检查项上
                if ok2:
                    print("      -> 自检居然全过：这条判据没有生效")
                else:
                    tail = [l for l in out2.splitlines() if l.strip().startswith("- ")]
                    print("      -> 失败了但不是期望的那项，实际失败：%s" % tail)

        # ---------- B 组：改 build.sh（构建闸门类判据） ----------
        for idx, (name, old, new, expect) in enumerate(BUILD_CASES, 1):
            total += 1
            if old not in build_sh_orig:
                print("[B%d] SKIP  %s" % (idx, name))
                print("      原因：build.sh 里找不到要替换的片段（用例过期）")
                fails.append("%s（构建用例片段过期）" % name)
                continue

            mutated = build_sh_orig.replace(old, new, 1)
            ok2, out2 = run_check_build_sh(mutated)
            blocked = (not ok2) and (expect in out2)
            status = "OK   已拦截" if blocked else "!! 漏放"
            print("[B%d] %s  %s" % (idx, status, name))
            if not blocked:
                fails.append(name)
                if ok2:
                    print("      -> 自检居然全过：这条判据没有生效")
                else:
                    tail = [l for l in out2.splitlines() if l.strip().startswith("- ")]
                    print("      -> 失败了但不是期望的那项，实际失败：%s" % tail)
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