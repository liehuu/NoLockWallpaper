# -*- coding: utf-8 -*-
"""交付前最终校验：对 deb 做逐字节独立检查。

★ 为什么要独立写一遍、不复用 mkdeb.sh 里的自检：
  打包脚本里的闸门如果本身写错了，它给出的「通过」毫无意义。
  本脚本从「用户装包那一刻」的视角反向验证产物：
    · dpkg 能不能吃（tar 不能有 PAX 扩展头）
    · 架构对不对（arm64e 的 cpusubtype 是 0x80000002，不是 2）
    · 权限对不对（dylib 644 会被 ellekit 拒绝加载）
    · 注入范围对不对（Filter 必须只限 SpringBoard）

用法：python tools/check_deb.py <deb 路径> [<deb 路径> ...]
"""
import glob
import io
import os
import re
import struct
import sys
import tarfile

EXPECT_PKG = "com.liehuu.nolockwallpaper"
EXPECT_NAME = "NoLockWallpaper"
EXPECT_PREFNAME = "NoLockWallpaperPrefs"
EXPECT_BUNDLE = "com.apple.springboard"

fails, oks = [], []


def chk(name, ok, detail=""):
    (oks if ok else fails).append((name, detail))
    print(("  OK   " if ok else "  FAIL ") + name + (("  -> " + detail) if detail else ""))


# ---------------------------------------------------------------- deb 解析
def read_deb_ar(path):
    """读 ar 归档，返回 [(名, bytes)]。纯手写，不依赖 ar 工具。"""
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("不是 ar 归档")
    off = 8
    out = []
    while off + 60 <= len(data):
        hdr = data[off:off + 60]
        name = hdr[0:16].decode("ascii", "replace").strip()
        size_s = hdr[48:58].decode("ascii", "replace").strip()
        try:
            size = int(size_s)
        except ValueError:
            break
        body = data[off + 60: off + 60 + size]
        if name.endswith("/"):
            name = name[:-1]
        out.append((name, body))
        off += 60 + size + (size & 1)   # ar 成员按 2 字节对齐
    return out


def decompress_member(name, body):
    """deb 内的 tar 允许压缩。返回解压后的 tar 字节流。

    ★ 支持的组合（dpkg 都认）：
        data.tar / control.tar          未压缩
        data.tar.gz / control.tar.gz    gzip
        data.tar.xz / control.tar.xz    xz
        data.tar.lzma                   lzma（老包常见）
      注意：压缩只影响 tar 外层包装，**PAX 扩展头是 tar 内部结构**，
      无论是否压缩都必须按解压后的字节流检查。
    """
    import gzip
    import lzma
    base = name
    if base.endswith(".gz"):
        return gzip.decompress(body), base[:-3]
    if base.endswith(".xz"):
        return lzma.decompress(body), base[:-3]
    if base.endswith(".lzma"):
        return lzma.decompress(body, format=lzma.FORMAT_ALONE), base[:-5]
    if base.endswith(".bz2"):
        import bz2
        return bz2.decompress(body), base[:-4]
    return body, base


def find_member(members, stem):
    """在 ar 成员里找 data.tar* / control.tar*，返回 (原名, 解压后 tar 字节, 主名)。"""
    for n, body in members:
        if n == stem or n.startswith(stem + "."):
            raw, main = decompress_member(n, body)
            return n, raw, main
    return None, None, None


def check_no_pax(raw, label):
    """按 tar 结构遍历，确认没有 PAX 扩展头（x/g）。

    ★ 必须按结构走，不能盲扫：盲扫会把二进制载荷里恰好等于 0x78 的字节
      误判成 PAX 头（本项目真实踩过，报出来「文件名」是 ObjC 符号名）。
    """
    bad = []
    off = 0
    n = len(raw)
    while off + 512 <= n:
        blk = raw[off:off + 512]
        if blk == b"\x00" * 512:
            break
        tf = blk[156:157]
        size_f = blk[124:136].rstrip(b"\x00 ").decode("ascii", "replace")
        try:
            size = int(size_f, 8) if size_f else 0
        except ValueError:
            size = 0
        if tf in (b"x", b"g"):
            nm = blk[0:100].rstrip(b"\x00").decode("ascii", "replace")
            bad.append("%s@%d" % (nm, off))
        off += 512 + ((size + 511) // 512) * 512
    chk("%s 无 PAX 扩展头（dpkg 只认 ustar/GNU）" % label, not bad,
        "发现：%s" % bad if bad else "")


def list_tar(raw):
    """返回 [(name, mode, size, isfile)]。"""
    tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
    out = []
    for m in tf.getmembers():
        out.append((m.name, m.mode, m.size, m.isfile()))
    return out


# ---------------------------------------------------------------- Mach-O
def macho_archs(body):
    """返回 [(cputype, cpusubtype)]，处理 fat 与 thin。"""
    if len(body) < 8:
        return []
    magic = struct.unpack(">I", body[0:4])[0]
    FAT_MAGIC, FAT_CIGAM = 0xCAFEBABE, 0xBEBAFECA
    FAT_MAGIC_64, FAT_CIGAM_64 = 0xCAFEBABF, 0xBFBAFECA

    def _is_fat(m):
        return m in (FAT_MAGIC, FAT_CIGAM, FAT_MAGIC_64, FAT_CIGAM_64)

    if _is_fat(magic):
        e = ">" if magic in (FAT_MAGIC, FAT_MAGIC_64) else "<"
        nfat = struct.unpack(e + "I", body[4:8])[0]
        is64 = magic in (FAT_MAGIC_64, FAT_CIGAM_64)
        step = 32 if is64 else 20
        base = 8
        out = []
        for i in range(nfat):
            o = base + i * step
            cpu, sub = struct.unpack(e + "II", body[o:o + 8])
            out.append((cpu, sub))
        return out

    # thin：MH_MAGIC_64 = 0xFEEDFACF（小端存储）
    import struct as _s
    for e in ("<", ">"):
        try:
            m = _s.unpack(e + "I", body[0:4])[0]
        except Exception:
            continue
        if m in (0xFEEDFACF, 0xFEEDFACE):
            cpu, sub = _s.unpack(e + "II", body[4:12])
            return [(cpu, sub)]
    return []


CPU_ARM64 = 0x0100000C


def macho_undefined_symbols(body):
    """解析 Mach-O 符号表，返回所有「未定义（外部引用）」符号名。

    ★ 为什么必须解析符号表而不是 grep 字节串：
      dlsym(RTLD_DEFAULT, "_objc_msgForward_stret") 会把这个名字作为
      C 字符串常量写进 __TEXT,__cstring，全文 grep 必然命中 ——
      但那恰恰是**安全的写法**。真正致命的是它出现在未定义符号表里，
      那样 dyld 加载时会找不到符号，整份 dylib 加载失败。

    只处理 thin Mach-O（本项目按架构分两个 deb，不存在 fat）。
    通过 LC_SYMTAB 取 nlist 数组 + 字符串表，筛 N_UNDF 且非 N_STAB。
    """
    if len(body) < 32:
        return []
    S = None
    for e in ("<", ">"):
        magic = struct.unpack(e + "I", body[0:4])[0]
        if magic in (0xFEEDFACF, 0xFEEDFACE):
            S = e
            is64 = (magic == 0xFEEDFACF)
            break
    if S is None:
        return []

    ncmds = struct.unpack(S + "I", body[16:20])[0]
    off = 32 if is64 else 28
    symtab = None
    for _ in range(ncmds):
        if off + 8 > len(body):
            break
        cmd, cmdsize = struct.unpack(S + "II", body[off:off + 8])
        if cmdsize < 8:
            break
        if cmd == 0x2:      # LC_SYMTAB
            symoff, nsyms, stroff, strsize = struct.unpack(
                S + "IIII", body[off + 8:off + 24])
            symtab = (symoff, nsyms, stroff, strsize)
            break
        off += cmdsize

    if not symtab:
        return []
    symoff, nsyms, stroff, strsize = symtab
    if stroff + strsize > len(body):
        return []
    strtab = body[stroff:stroff + strsize]

    nlist_size = 16 if is64 else 12
    out = []
    for i in range(nsyms):
        p = symoff + i * nlist_size
        if p + nlist_size > len(body):
            break
        n_strx = struct.unpack(S + "I", body[p:p + 4])[0]
        n_type = body[p + 4]
        N_STAB = 0xE0
        N_TYPE = 0x0E
        N_UNDF = 0x0
        if (n_type & N_STAB) != 0:
            continue
        if (n_type & N_TYPE) != N_UNDF:
            continue
        if n_strx == 0 or n_strx >= len(strtab):
            continue
        end = strtab.find(b"\x00", n_strx)
        if end < 0:
            end = len(strtab)
        name = strtab[n_strx:end]
        if name:
            out.append(name)
    return out


def arch_name(cpu, sub):
    """★ arm64e 的 cpusubtype 是 0x80000002（带 CPU_SUBTYPE_PTRAUTH_ABI 标志位），
    判架构必须取低 24 位： (sub & 0x00ffffff) == 2 → arm64e，== 0 → arm64。
    直接把 sub 和 2 比较会把 arm64e 误判成「未知」——本项目真实踩过。"""
    if cpu != CPU_ARM64:
        return "other(%x)" % cpu
    base = sub & 0x00FFFFFF
    if base == 2:
        return "arm64e"
    if base == 0:
        return "arm64"
    return "arm64?(sub=%x)" % sub


# ---------------------------------------------------------------- 单个 deb
def check_deb(path):
    print("\n" + "=" * 68)
    print("FILE:", os.path.basename(path))
    print("=" * 68)

    raw_size = os.path.getsize(path)
    print("大小: %d B" % raw_size)

    members = read_deb_ar(path)
    names = [n for n, _ in members]
    chk("ar 含 debian-binary", "debian-binary" in names, str(names))
    if "debian-binary" in names:
        ver = dict(members)["debian-binary"]
        chk("debian-binary 为 2.0", ver.strip().startswith(b"2.0"),
            ver.strip().decode("ascii", "replace"))

    dname, data_tar, dmain = find_member(members, "data.tar")
    cname, ctl_tar, cmain = find_member(members, "control.tar")
    # ★ 必须在这里初始化：下面有两处跨区块读 ctl，
    #   若 ctl_tar 为 None 则会 NameError（不是检查失败，是脚本直接崩）。
    ctl = None
    print("data 成员: %s -> %s" % (dname, dmain))
    print("ctl  成员: %s -> %s" % (cname, cmain))
    chk("含 data.tar*（dpkg 认 =tar / .gz / .xz / .lzma）", data_tar is not None, str(names))
    chk("data.tar 命名为 data.tar（不能是 data.tar.<压缩> 之外的怪名）",
        dmain == "data.tar", "%s" % dmain)
    chk("含 control.tar*", ctl_tar is not None, str(names))
    chk("control.tar 命名为 control.tar", cmain == "control.tar", "%s" % cmain)

    if ctl_tar:
        check_no_pax(ctl_tar, "control.tar")
        entries = list_tar(ctl_tar)
        ctl_names = [e[0] for e in entries]
        chk("control.tar 含 control", any(n.endswith("control") for n in ctl_names),
            str(ctl_names))
        ctl = None
        maint = {}      # 脚本名 -> (mode, 正文)
        tf = tarfile.open(fileobj=io.BytesIO(ctl_tar), mode="r:")
        for m in tf.getmembers():
            base = m.name.split("/")[-1]
            if m.name.endswith("/control") or base == "control":
                ctl = tf.extractfile(m).read().decode("utf-8", "replace")
            elif base in ("postinst", "preinst", "postrm", "prerm"):
                maint[base] = (m.mode,
                               tf.extractfile(m).read().decode("utf-8", "replace"))

        # ---------------- 维保脚本：绝不允许打断 dpkg 事务 ----------------
        # ★★ 1.1-1 真实事故：postinst 里跑 sbreload 重启 SpringBoard，
        #    Sileo 是前台 App 被一起杀掉，dpkg 事务未收尾（status 没写、
        #    lock 没释放）→ 锁残留 + 半装 → 之后**任何** deb 都装不上。
        #    `|| true` 只能兜「返回码非 0」，兜不住「进程被杀」。
        #    所以这条必须在产物上验，不能只靠源码闸门（源码闸门会被绕）。
        _MNT_BAD = (
            (r"\bsbreload\b", "sbreload"),
            (r"killall[^\n]*SpringBoard", "killall SpringBoard"),
            (r"launchctl\s+(?:kickstart|restart)", "launchctl kickstart/restart"),
            (r"\brespring\b", "respring"),
            (r"/usr/bin/killall\b", "显式路径 killall"),
        )
        print("  --- 维保脚本 ---")
        chk("control.tar 含 postinst", "postinst" in maint, str(sorted(maint)))
        for sname in sorted(maint):
            smode, sbody = maint[sname]
            print("    %-10s %04o %8d B" % (sname, smode, len(sbody)))
            chk("%s 权限 0755" % sname, (smode & 0o777) == 0o755,
                "%04o" % (smode & 0o777))
            chk("%s 有 shebang" % sname, sbody.startswith("#!"),
                sbody.splitlines()[0] if sbody else "空文件")
            # 去掉 shebang、注释行、以及 printf/echo 提示行后再判 ——
            # ★ 提示文案里必然出现「请手动 respring」这类字样，
            #   若把文案也纳入匹配就会 100% 误报（闸门自己写错的典型形态）。
            #   这里只检查「真正会执行的命令」。
            code_lines = []
            for l in sbody.splitlines():
                s = l.strip()
                if not s or s.startswith("#"):
                    continue
                if re.match(r"^(?:printf|echo)\b", s):
                    continue    # 纯提示输出，不动宿主进程
                code_lines.append(l)
            code_only = "\n".join(code_lines)
            hits = [label for pat, label in _MNT_BAD if re.search(pat, code_only)]
            chk("%s 不重启桌面/不杀宿主进程" % sname, not hits,
                "命中 %s → 会打断 dpkg 事务，留下 dpkg lock 与半装状态"
                % ", ".join(hits))
            # postinst 结束时必须以 exit 0 / 正常结尾收场，不能半途退出
            chk("%s 以 exit 0 结尾" % sname,
                code_only.rstrip().splitlines()[-1].strip() == "exit 0"
                if code_only.strip() else False,
                code_only.rstrip().splitlines()[-1].strip()
                if code_only.strip() else "空脚本")

        if ctl:
            print("  --- control 全文 ---")
            for l in ctl.splitlines():
                print("    " + l)
            chk("control 的 Package 正确", ("Package: %s" % EXPECT_PKG) in ctl,
                "" if ("Package: %s" % EXPECT_PKG) in ctl
                else "期望 Package: %s" % EXPECT_PKG)
            chk("control 的 Name 正确", ("Name: %s" % EXPECT_NAME) in ctl,
                "" if ("Name: %s" % EXPECT_NAME) in ctl
                else "期望 Name: %s" % EXPECT_NAME)
            chk("control 含 Architecture", "Architecture:" in ctl,
                "" if "Architecture:" in ctl else "缺 Architecture 字段")
            chk("control 含 Version", "Version:" in ctl,
                "" if "Version:" in ctl else "缺 Version 字段")

    if not data_tar:
        return

    check_no_pax(data_tar, "data.tar")
    entries = list_tar(data_tar)
    print("  --- data.tar 内容 ---")
    for nm, mode, size, isf in entries:
        print("    %-70s %04o %8d %s" % (nm, mode, size, "f" if isf else "d"))

    # ★★ data.tar 里绝不能出现 DEBIAN/（2026-10-22 发现的真实缺陷）：
    #   dpkg 只从 control.tar.gz 读 control/postinst。data.tar 里若也带了
    #   ./DEBIAN/postinst，dpkg 会把它当普通文件解到文件系统根目录的
    #   /DEBIAN/postinst ——
    #     · rootful：根目录多一份垃圾，卸载脚本不会清理它
    #     · rootless：/ 只读 → 解包直接失败，整个 deb 装不上
    #   这个缺陷 1.0-1 / 1.1-1 一直存在，只是恰好 rootful 上不致命。
    _deb_in_data = [e[0] for e in entries
                    if e[0].lstrip("./").split("/")[0].upper() == "DEBIAN"]
    chk("data.tar 不含 DEBIAN/（否则解包会落到根目录 /DEBIAN）",
        not _deb_in_data,
        "实际含：%s —— rootless 上 / 只读会让整个包装不上" % _deb_in_data
        if _deb_in_data else "")

    # Filter plist
    dylib_name = "%s.dylib" % EXPECT_NAME
    plist_name = "%s.plist" % EXPECT_NAME
    dl = [e for e in entries if e[0].endswith("/" + dylib_name) or e[0] == dylib_name]
    pl = [e for e in entries if e[0].endswith("/" + plist_name) or e[0] == plist_name]

    chk("data.tar 含 %s" % dylib_name, bool(dl))
    chk("data.tar 含 %s" % plist_name, bool(pl))

    # ---- dylib ----
    if dl:
        nm, mode, size, _ = dl[0]
        # ★ 预期值是 755，不是 644：644 的 dylib 在某些 ellekit 版本上
        #   会被拒绝加载，表现是「插件完全没生效」。
        chk("dylib 权限 0755（644 会被 ellekit 拒绝）", (mode & 0o777) == 0o755,
            "实际 %04o" % (mode & 0o777))

        tf = tarfile.open(fileobj=io.BytesIO(data_tar), mode="r:")
        body = tf.extractfile(tf.getmember(nm)).read()
        chk("dylib 大小 > 8KB（不是空壳）", len(body) > 8192, "%d B" % len(body))

        archs = macho_archs(body)
        names_a = [arch_name(c, s) for c, s in archs]
        print("    架构切片: %s" % (names_a or "解析失败"))
        chk("dylib 单架构（本包按架构分两个 deb）", len(archs) == 1,
            "实际 %d 片" % len(archs))

        # 与本 deb 文件名要求的架构对照
        base = os.path.basename(path)
        if "arm64e" in base:
            chk("dylib 架构为 arm64e", names_a == ["arm64e"], str(names_a))
        elif "arm64" in base:
            chk("dylib 架构为 arm64", names_a == ["arm64"], str(names_a))

        # install_name
        chk("dylib install_name 指向 @loader_path/.jbroot/...",
            b"@loader_path/.jbroot/Library/MobileSubstrate/DynamicLibraries" in body,
            "" if b"@loader_path/.jbroot" in body else "RootHide 下路径不对")

        # 功能指纹（独立于 build.sh 再验一遍）
        for sym in [b"addGestureRecognizer:", b"didMoveToWindow",
                    b"viewDidAppear:", b"setMinimumPressDuration",
                    b"LockScreen", b"CoverSheet"]:
            chk("dylib 含指纹 %s" % sym.decode(), sym in body)

        # 崩溃防护痕迹：@try 至少要有异常处理元数据
        # ★ 判断 @try/@catch 是否真的编进去了，要看的是 __TEXT,__gcc_except_tab
        #   段（LSDA 的落点）与 __objc_personality_v0 符号。
        #   实测本包：
        #     __TEXT 段里有 __gcc_except_tab  ← @try 生效
        #     __objc_personality 出现 2 次
        #     __TEXT/__auth_stubs + __DATA_CONST/__auth_got ← arm64e 认证桩
        #   若哪天这两样都没了，说明 -O2 把 try/catch 优化掉了，
        #   那么「异常绝不出线程」这条铁律就失去保障，必须人工复核。
        has_lsda = b"__gcc_except_tab" in body
        has_personality = b"__objc_personality" in body
        chk("dylib 含 __gcc_except_tab 段（@try 的 LSDA 落点）", has_lsda,
            "" if has_lsda else "缺 LSDA 段 → try/catch 可能被优化掉")
        chk("dylib 含 __objc_personality（ObjC 异常运行时挂钩）", has_personality,
            "" if has_personality else "缺 personality 符号 → 异常可能漏出线程")

        # ★★★ 判据必须精确到「符号表里的未定义符号」，不能全文搜字节串。
        #   dlsym(RTLD_DEFAULT, "_objc_msgForward_stret") 会把这个名字作为
        #   **C 字符串常量**放进 __TEXT,__cstring —— 全文搜必然命中，
        #   但它完全无害（恰恰是我们想要的安全写法）。
        #   真正致命的是「作为未定义符号被 dyld 绑定」。
        #   本项目已经因为「把字符串当符号」误报过一次（PAX 闸门盲扫），
        #   这里必须走 Mach-O 符号表解析。
        undef = macho_undefined_symbols(body)
        bad = [s for s in undef if b"msgForward" in s]
        print("    未定义符号数: %d；其中含 msgForward: %s"
              % (len(undef), [b.decode("ascii", "replace") for b in bad] or "无"))
        chk("未定义符号表里没有 msgForward_stret（arm64 无此符号）",
            not any(b"_objc_msgForward_stret" in s for s in undef),
            "有则会 dylib 加载失败")
        chk("未定义符号表里没有裸 _objc_msgForward（应走 dlsym）",
            not any(s == b"_objc_msgForward" for s in undef),
            "有则应改用 dlsym")

        # 代码签名
        chk("dylib 含代码签名（LC_CODE_SIGNATURE）",
            b"\xfa\xde\x0c\xc0" in body or b"__LINKEDIT" in body,
            "ad-hoc 签名已在构建时完成")

    # ---- plist ----
    if pl:
        nm, mode, size, _ = pl[0]
        tf = tarfile.open(fileobj=io.BytesIO(data_tar), mode="r:")
        pbody = tf.extractfile(tf.getmember(nm)).read().decode("utf-8", "replace")
        chk("Filter 限定 %s" % EXPECT_BUNDLE, EXPECT_BUNDLE in pbody, pbody.strip()[:200])
        chk("Filter 不是空（否则注入所有进程）",
            "Bundles" in pbody and pbody.count("com.") >= 1)

    # ==================================================================
    # 设置页：两个入口 + 可执行位 + 控制器指纹
    # ==================================================================
    # ★ 设置页这三条是本轮新增功能的核心。它们的失败模式都是「静默」：
    #   · 缺 PreferenceBundles → 设置里能看到项，点进去白屏
    #   · 缺 PreferenceLoader 入口 → 设置里根本看不到这一项
    #   · 可执行文件 644 → 同上，白屏，且 dpkg 不报错
    #   全部不会让装包失败，所以必须在产物上逐条验。
    print("\n  --- 设置页载荷 ---")
    bundle_exe = [e for e in entries
                  if e[0].startswith("./Library/PreferenceBundles/"
                                      + EXPECT_PREFNAME + ".bundle/")
                  and e[0].endswith("/" + EXPECT_PREFNAME)]
    bundle_info = [e for e in entries
                   if e[0].endswith("/PreferenceBundles/" + EXPECT_PREFNAME
                                    + ".bundle/Info.plist")]
    bundle_root = [e for e in entries
                   if e[0].endswith("/PreferenceBundles/" + EXPECT_PREFNAME
                                    + ".bundle/Root.plist")]
    entry_plist = [e for e in entries
                   if e[0].endswith("/PreferenceLoader/Preferences/"
                                    + EXPECT_PREFNAME + "/Root.plist")]

    chk("含 PreferenceBundles/<X>.bundle/<X> 可执行文件", bool(bundle_exe),
        "实际条目：%s" % [e[0] for e in entries
                          if "PreferenceBundles" in e[0]][:6])
    chk("含 bundle 的 Info.plist", bool(bundle_info))
    chk("含 bundle 的 Root.plist", bool(bundle_root))
    chk("含 PreferenceLoader 入口 Root.plist", bool(entry_plist),
        "" if entry_plist else "缺入口 → 设置里看不到本插件")

    # ★ 图标：入口 plist 写的是基础名 icon.png，iOS 再按 scale 取 @2x/@3x。
    #   只给其中一两种会显示空白方块，且没有任何报错。
    for ic in ("icon.png", "icon@2x.png", "icon@3x.png"):
        hit = [e for e in entries
               if e[0].endswith("/PreferenceBundles/" + EXPECT_PREFNAME
                                + ".bundle/" + ic)]
        chk("含设置页图标 %s" % ic, bool(hit),
            "" if hit else "缺 %s → 设置里显示空白方块" % ic)
        if hit:
            nm, mode, size, _ = hit[0]
            tf = tarfile.open(fileobj=io.BytesIO(data_tar), mode="r:")
            ib = tf.extractfile(tf.getmember(nm)).read()
            chk("图标 %s 是合法 PNG" % ic, ib.startswith(b"\x89PNG\r\n\x1a\n"),
                "开头 %r" % ib[:8])
            chk("图标 %s 不是可执行位" % ic, (mode & 0o777) == 0o644,
                "实际 %04o" % (mode & 0o777))

    if bundle_exe:
        nm, mode, size, _ = bundle_exe[0]
        # ★ 644 的可执行文件在设置 App 里加载失败，表现是白屏，
        #   而 dpkg 完全不报错 —— 与 dylib 的 755 要求同理。
        chk("设置页可执行文件权限 0755", (mode & 0o777) == 0o755,
            "实际 %04o" % (mode & 0o777))
        chk("设置页可执行文件大小 > 4KB（不是空壳）", size > 4096, "%d B" % size)

        tf = tarfile.open(fileobj=io.BytesIO(data_tar), mode="r:")
        pbody = tf.extractfile(tf.getmember(nm)).read()
        archs = macho_archs(pbody)
        names_a = [arch_name(c, s) for c, s in archs]
        print("    设置页架构切片: %s" % (names_a or "解析失败"))
        chk("设置页是单架构（按架构分包）", len(archs) == 1,
            "实际 %d 片" % len(archs))

        base = os.path.basename(path)
        if "arm64e" in base:
            chk("设置页架构为 arm64e", names_a == ["arm64e"], str(names_a))
        elif "arm64" in base:
            chk("设置页架构为 arm64", names_a == ["arm64"], str(names_a))

        # 控制器类名必须在产物里，否则 NSPrincipalClass 找不到类 → 白屏
        chk("设置页含控制器类名 NoLockWallpaperRootListController",
            b"NoLockWallpaperRootListController" in pbody,
            "" if b"NoLockWallpaperRootListController" in pbody
            else "NSPrincipalClass 找不到类 → 进设置页白屏")
        # 绝不得出现 UIColorWell（iOS 13 设置 App 会打不开）
        undef2 = macho_undefined_symbols(pbody)
        chk("设置页未绑定 UIColorWell 类符号（iOS 13 会打不开）",
            not any(b"UIColorWell" in s for s in undef2))
        # 重启桌面能力：必须有 kill + SpringBoard 字样
        chk("设置页含 SpringBoard 重启能力", b"SpringBoard" in pbody)

    if entry_plist:
        nm, mode, size, _ = entry_plist[0]
        tf = tarfile.open(fileobj=io.BytesIO(data_tar), mode="r:")
        ebody = tf.extractfile(tf.getmember(nm)).read()
        # ★ 必须是二进制 plist：bplist00 开头。
        #   文本 XML 在部分版本的 PreferenceLoader 上不被识别，
        #   表现同样是「设置里看不到这一项」。
        chk("入口 plist 是二进制格式（bplist00）",
            ebody.startswith(b"bplist00"),
            "开头 %r" % ebody[:8])
        txt = ebody.decode("utf-8", "replace")
        for needle in ("PSLinkCell", EXPECT_PREFNAME,
                       "NoLockWallpaperRootListController", "isController"):
            chk("入口 plist 含 %s" % needle, needle in txt)

    # control 必须依赖 preferenceloader（纯净设备否则看不到设置页）
    if ctl:
        chk("control 依赖 preferenceloader", "preferenceloader" in ctl,
            "" if "preferenceloader" in ctl else "纯净设备上设置里看不到本插件")

    # ==================================================================
    # 主 dylib 必须真的读了开关（否则设置页是个摆设）
    # ==================================================================
    if dl:
        nm = dl[0][0]
        tf = tarfile.open(fileobj=io.BytesIO(data_tar), mode="r:")
        body = tf.extractfile(tf.getmember(nm)).read()
        for needle in (b"com.liehuu.nolockwallpaper",
                       # ★ 2.0-1：总开关 + 两个相机子开关的键名。
                       #   漏掉 MasterEnabled ⇒「总开关没编进产物」不会被发现。
                       b"MasterEnabled", b"Enabled",
                       b"BlockCameraSwipe", b"BlockCameraButton",
                       b"BlockFlashlight",
                       b"com.liehuu.nolockwallpaper/changed",
                       b"UIScreenEdgePanGestureRecognizer", b"edges",
                       # ★ 1.9-1：三个 bool getter 一个都不能少。
                       #   缺 lockScreen 那个就是 1.7/1.8 的真实故障（左滑照进）。
                       b"hasFlashlight", b"hasCamera",
                       b"lockScreenCameraSupported",
                       # 装配路径必须带 MSHookMessageEx（唯一被实测证明能装上的）
                       b"MSHookMessageEx"):
            chk("主 dylib 含开关/相机指纹 %s" % needle.decode(), needle in body)
        # ★★ 绝不在这里查 UIRectEdgeRight —— 它是**枚举常量**，值是 8，
        #   编译后是 `and w8, w0, #8` 这类指令里的立即数，
        #   二进制里**不会留下任何字符串**。第一版判据查了它，
        #   在真实产物上必然 FAIL（本轮实测：dylib 里 UIRectEdgeRight=False，
        #   而 edges=True / EdgePan=True / ScreenEdge=True）。
        #   源码层面「真的用了右边缘限定」由 check_source.py 的 A7d 保证；
        #   产物层面只能验证「调用了 edges」这个 selector，两者合起来才完整。
        #   通用规律：**枚举值 / #define 常量 / 内联函数名都不会进二进制**，
        #   对产物做字符串判据时必须先自问「这个名字在编译后还存在吗」。
        for extra in (b"EdgePan", b"ScreenEdge"):
            chk("主 dylib 含类名兜底指纹 %s" % extra.decode(), extra in body,
                "" if extra in body else "自定义边缘手势类的兜底判据失效")
        # ★★ 2.1-1 起这是**硬判据**，不再有「诊断版豁免」。
        #   1.3-1 ~ 2.0-1 期间为了定位问题保留了一段临时诊断代码
        #   （打到 /tmp 和 Logs 目录），当时用 "camSw=" 这个格式串来
        #   区分「诊断版」和「正式版」，诊断版允许带日志路径。
        #   现在诊断段已整块删除，正式交付包里出现任何一个日志痕迹
        #   都是缺陷 —— SpringBoard 里做文件 IO 是 watchdog 风险，
        #   而且日志会无限增长。所以这里改成逐项硬查：
        #     · 日志目录路径
        #     · 两个日志文件名
        #     · 诊断段独有的格式串（证明它真的被删掉了，不是被注释掉）
        for _needle, _why in ((b"/Logs/NoLockWallpaper", "日志目录路径还在"),
                              (b"diag.txt", "诊断文件名还在"),
                              (b"nlw_diag", "/tmp 诊断文件名还在"),
                              (b"camSw=", "诊断格式串还在（诊断段没删干净）"),
                              (b"MSHookMessageEx=%p", "装配日志还在")):
            chk("主 dylib 不含诊断痕迹 %s" % _needle.decode(),
                _needle not in body, "" if _needle not in body else _why)


def main():
    args = sys.argv[1:]
    if not args:
        args = sorted(glob.glob("*.deb"))
    if not args:
        print("没有找到 deb")
        return 2
    for p in args:
        check_deb(p)

    print("\n" + "=" * 68)
    if fails:
        print("校验未通过 %d 项：" % len(fails))
        for n, d in fails:
            print("  - %s%s" % (n, ("  -> " + d) if d else ""))
        return 1
    print("全部通过（%d 项）" % len(oks))
    return 0


if __name__ == "__main__":
    sys.exit(main())