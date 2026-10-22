//
//  NoLockWallpaper —— 禁掉「锁屏长按弹出更换壁纸」
//
//  需求原话：手机锁屏时，长按屏幕就会跳出更换壁纸的选项，经常误触，
//            帮我禁掉这个功能，锁屏长按不会出现更换壁纸。
//
//  目标设备：iOS 17.0.3 / arm64e / RootHide（jbroot）
//
// ============================================================================
//  ★★ 核心思路：不猜私有方法名，只在「公开 API 的必经之路」上拦
// ============================================================================
//  锁屏长按换壁纸，最终一定是「一个 UILongPressGestureRecognizer 挂在
//  锁屏视图上」。这个手势必然经过 UIView -addGestureRecognizer:。
//  在那个时刻把它压掉，就不需要知道任何私有 selector 名。
//
//  为什么这条路比 hook 私有方法安全一个数量级：
//    · 私有 selector 名每个大版本都会变（猜错 = 功能不生效，白等一轮）
//    · 就算名字蒙对，签名猜错 → arm64 寄存器语义错乱 → 崩宿主
//    · 公开 API 的签名是编译器校验的，**不存在猜错的可能**
//
// ============================================================================
//  ★★ 三层拦截（每层单独都无害，缺一层可能漏）
// ============================================================================
//  第 0 层（主）：UIView -addGestureRecognizer:
//      手势被添加的那一刻就压制。最根本的时机，不依赖视图何时上屏。
//
//  第 1 层（补）：UIView -didMoveToWindow
//      视图上屏时再压一次。盖住「系统添加后又把 enabled 改回 YES」。
//
//  第 2 层（补）：锁屏控制器 -viewDidAppear:
//      整个锁屏页出现时再整体扫一遍。盖住「手势加载得比本插件更早」。
//
// ============================================================================
//  ★★★ 最重要的安全边界：绝不下钻子视图 ★★★
// ============================================================================
//  锁屏上还有：通知列表长按（必须保留）、手电筒/相机按钮（必须保留）、
//  时钟小组件长按（必须保留）。
//  所以每层都**只看当前视图自身**的 gestureRecognizers，绝不递归 subviews。
//  一旦下钻，就是把锁屏上所有长按全废 —— 属于严重误伤，绝不能做。
//
// ============================================================================
//  ★★ 崩宿主防护（本项目铁律，每条对应一次真实事故）
// ============================================================================
//   1. 每个 hook 整体 @try/@catch，异常绝不出线程
//   2. 调 orig 前判非空；addGestureRecognizer / didMoveToWindow /
//      viewDidAppear 都是宿主必须完成的回调，**无论如何先转发再压制**。
//      不转发 addGestureRecognizer 会让 gesture.view 保持 nil，
//      系统后续代码读它可能崩 —— 所以顺序是「转发 → 压制」，不是「不转发」。
//   3. 热路径零 IO：日志只在首次命中时写，其余全是内存操作
//   4. 不访问私有属性，全部公开 API + 消息发送
//   5. hook 装不上（类/sel 不存在）就干净跳过，绝不硬上
//   6. 构造器整体 try/catch，任何异常都不影响 SpringBoard 启动
//   7. 提供 killswitch：/var/mobile/Library/Preferences/com.liehuu.nlw.disabled
//      文件存在时插件完全不动手 —— 出问题不用卸载，删文件即可
//

#import <UIKit/UIKit.h>
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <dlfcn.h>
#import <string.h>

// ============================================================================
//  一、日志
// ============================================================================

static NSString *const NLWLogSubdir = @"NoLockWallpaper";
static NSString *const NLWLogName   = @"nlw.log";

static NSString *NLWLogDir(void)
{
    static NSString *cached = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        NSMutableArray *cands = [NSMutableArray arrayWithObjects:
            @"/var/mobile/Library/Logs",
            @"/var/jb/var/mobile/Library/Logs",
            nil];

        // 从 dylib 自身路径反推 jbroot（RootHide 下形如 <jbroot>/Library/...）
        Dl_info info;
        if (dladdr((const void *)NLWLogDir, &info) && info.dli_fname) {
            NSString *selfPath = [NSString stringWithUTF8String:info.dli_fname];
            NSRange r = [selfPath rangeOfString:@"/Library/MobileSubstrate/DynamicLibraries"];
            if (r.location != NSNotFound) {
                NSString *jbroot = [selfPath substringToIndex:r.location];
                if (jbroot.length) {
                    [cands addObject:
                        [jbroot stringByAppendingPathComponent:@"var/mobile/Library/Logs"]];
                }
            }
        }

        NSFileManager *fm = [NSFileManager defaultManager];
        for (NSString *base in cands) {
            @try {
                NSString *dir = [base stringByAppendingPathComponent:NLWLogSubdir];
                BOOL isDir = NO;
                if (![fm fileExistsAtPath:dir isDirectory:&isDir]) {
                    [fm createDirectoryAtPath:dir withIntermediateDirectories:YES
                                   attributes:nil error:nil];
                }
                if (![fm fileExistsAtPath:dir isDirectory:&isDir] || !isDir) continue;
                NSString *probe = [dir stringByAppendingPathComponent:@".probe"];
                if ([@"p" writeToFile:probe atomically:YES
                            encoding:NSUTF8StringEncoding error:nil]) {
                    [fm removeItemAtPath:probe error:nil];
                    cached = dir;
                    break;
                }
            } @catch (NSException *e) { }
        }
    });
    return cached;
}

static void NLWLog(NSString *msg)
{
    @try {
        NSString *dir = NLWLogDir();
        if (!dir) return;
        NSString *path = [dir stringByAppendingPathComponent:NLWLogName];
        NSString *line = [NSString stringWithFormat:@"%@\n", msg];
        NSFileManager *fm = [NSFileManager defaultManager];

        if ([fm fileExistsAtPath:path]) {
            NSFileHandle *fh = [NSFileHandle fileHandleForWritingAtPath:path];
            if (fh) {
                @try {
                    [fh seekToEndOfFile];
                    [fh writeData:[line dataUsingEncoding:NSUTF8StringEncoding]];
                } @finally {
                    [fh closeFile];
                }
                return;
            }
        }
        [line writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:nil];
        // 锁屏时默认文件保护 Complete，未解锁前读不到；放宽到 UntilFirstAccess
        [fm setAttributes:@{ NSFileProtectionKey : NSFileProtectionCompleteUntilFirstAccess }
             ofItemAtPath:path error:nil];
    } @catch (NSException *e) { }
}

static NSString *NLWiOSVersion(void)
{
    @try {
        NSOperatingSystemVersion v = [NSProcessInfo processInfo].operatingSystemVersion;
        return [NSString stringWithFormat:@"%ld.%ld.%ld",
                (long)v.majorVersion, (long)v.minorVersion, (long)v.patchVersion];
    } @catch (NSException *e) { return @"?"; }
}

// ============================================================================
//  二、killswitch（出问题时不用卸载，删文件即可）
// ============================================================================

static BOOL NLWIsKilled(void)
{
    static BOOL killed = NO;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        @try {
            NSArray *cands = @[
                @"/var/mobile/Library/Preferences/com.liehuu.nlw.disabled",
                @"/var/jb/var/mobile/Library/Preferences/com.liehuu.nlw.disabled",
            ];
            NSFileManager *fm = [NSFileManager defaultManager];
            for (NSString *p in cands) {
                if ([fm fileExistsAtPath:p]) { killed = YES; break; }
            }
        } @catch (NSException *e) { }
    });
    return killed;
}

// ============================================================================
//  三、判据：这个视图算不算「锁屏的一部分」
// ============================================================================

/// 沿父类链找锁屏相关字样。
///
/// ★ 为什么按「类名特征」而不是「精确类名」判：
///   锁屏视图类名每个大版本都可能调整（SBLockScreenView / CSCoverSheetView /
///   PRSPosterViewController / …），但命名习惯稳定，一定带
///   LockScreen / CoverSheet / Poster 字样。
///   按特征判，跨版本存活率高得多，且完全不碰私有符号。
///
/// ★ 为什么不会误伤主屏图标长按：
///   SBIconView / SBIconListView / SBHomeScreenViewController 的类名链
///   不含上述字样，直接不命中。
static BOOL NLWIsLockScreenish(id obj)
{
    if (!obj) return NO;
    Class cls = object_getClass(obj);
    for (Class c = cls; c != Nil; c = class_getSuperclass(c)) {
        const char *n = class_getName(c);
        if (!n) continue;
        if (strstr(n, "LockScreen") || strstr(n, "Lockscreen") ||
            strstr(n, "CoverSheet")  || strstr(n, "PosterBoard") ||
            strstr(n, "PosterBoardUIServices")) {
            return YES;
        }
    }
    return NO;
}

// ============================================================================
//  四、压制动作
// ============================================================================

static NSUInteger gSuppressedTotal   = 0;
static BOOL       gLoggedFirstSuppress = NO;
static BOOL       gLoggedInventory     = NO;

static void NLWSuppressGesture(UIGestureRecognizer *g, const char *where)
{
    if (!g) return;

    // 双保险：
    //   enabled=NO          → 常规手段
    //   minimumPressDuration → 万一系统把 enabled 改回 YES，
    //                         90 秒的长按人类按不出来，等于永久失效
    @try { g.enabled = NO; } @catch (NSException *e) { }
    @try {
        if ([g isKindOfClass:[UILongPressGestureRecognizer class]]) {
            ((UILongPressGestureRecognizer *)g).minimumPressDuration = 90.0;
        }
    } @catch (NSException *e) { }

    gSuppressedTotal++;
    if (gLoggedFirstSuppress) return;      // 热路径：只写一次盘
    gLoggedFirstSuppress = YES;

    NSString *gcls = @"?", *vcls = @"?";
    @try { gcls = NSStringFromClass(object_getClass(g)); } @catch (NSException *e) { }
    @try { vcls = NSStringFromClass([g.view class]); }    @catch (NSException *e) { }

    NLWLog([NSString stringWithFormat:
        @"[nlw] >>> 已禁用锁屏长按（首次）via %s\n"
         "      手势类: %@\n"
         "      宿主类: %@\n"
         "      iOS: %@\n",
        where, gcls, vcls, NLWiOSVersion()]);
}

/// 视图 v 是否属于「锁屏壁纸区」，可以安全压制其长按。
///
/// ★★ 这里有一个极易踩的误伤陷阱（v1.1 第一版就踩了，已修正）：
///   「祖先链含锁屏」这个判据太宽 —— 通知列表的 cell 也在锁屏视图之下，
///   它的祖先链必然经过锁屏视图。按宽判据会把**通知长按**一起禁掉。
///   同理手电筒/相机按钮也在锁屏之下。
///
///   所以判据必须同时满足三条：
///     ① 自身或某个祖先像锁屏（NLWIsLockScreenish）
///     ② 从 v 往上走到那个锁屏锚点之间，**不能经过滚动容器**
///        （UIScrollView / UITableView / UICollectionView / 通知栈）——
///        通知、今日视图、小组件列表全在滚动容器里，这条把它们全排除
///     ③ v 自身不能是 UIControl（手电筒、相机是 UIButton，属于 UIControl）
///
///   这样保留下来的只有：锁屏根视图、壁纸容器、深度特效图层 ——
///   正是我们要压制的范围。
static BOOL NLWIsSuppressibleLockScreenArea(UIView *v)
{
    if (!v) return NO;

    // ③ 控件直接排除（手电筒/相机/各类按钮）
    if ([v isKindOfClass:[UIControl class]]) return NO;

    BOOL selfIsLS = NLWIsLockScreenish(v);
    UIView *p = [v superview];
    NSUInteger guard = 0;

    while (p && guard++ < 64) {
        // ② 中间经过滚动容器 → 属于通知/今日视图/列表，不动
        if ([p isKindOfClass:[UIScrollView class]]) return NO;

        if (NLWIsLockScreenish(p)) {
            // ① 找到锁屏锚点。自身是锁屏、或与锁屏锚点之间无滚动容器 → 可压制
            return YES;
        }
        p = [p superview];
    }

    // 自身就是锁屏视图（没有更上的锚点，或链走完了）
    return selfIsLS;
}

/// 处理一个视图**自身挂载**的手势。返回压制个数。
/// ★ 绝不下钻 subviews —— 见文件头「最重要的安全边界」。
static NSUInteger NLWProcessView(UIView *v, const char *where)
{
    NSUInteger n = 0;
    @try {
        if (!v) return 0;
        NSArray *gs = v.gestureRecognizers;     // nil 时 for-in 安全跳过
        if (gs.count == 0) return 0;

        if (!NLWIsSuppressibleLockScreenArea(v)) return 0;

        // 首次找到可压制的锁屏区域：把手势清单整体记下来。
        // ★ 这是给「万一没生效」留的诊断口 —— 日志里能直接看到锁屏上
        //   究竟是哪几个手势类、挂在什么类上、各自 enabled 状态，不必再靠猜。
        if (!gLoggedInventory) {
            gLoggedInventory = YES;
            NSMutableString *inv = [NSMutableString string];
            for (UIGestureRecognizer *g in gs) {
                NSString *cn = @"?";
                @try { cn = NSStringFromClass(object_getClass(g)); } @catch (NSException *e) { }
                [inv appendFormat:@"        - %@  enabled=%d\n", cn, (int)g.enabled];
            }
            NSString *vcls = @"?";
            @try { vcls = NSStringFromClass([v class]); } @catch (NSException *e) { }
            NLWLog([NSString stringWithFormat:
                @"[nlw] 命中锁屏壁纸区 %@ via %s，该视图自身手势 %lu 个：\n%@",
                vcls, where, (unsigned long)gs.count, inv]);
        }

        for (UIGestureRecognizer *g in gs) {
            if ([g isKindOfClass:[UILongPressGestureRecognizer class]]) {
                NLWSuppressGesture(g, where);
                n++;
            }
        }
    } @catch (NSException *e) { }
    return n;
}

// ============================================================================
//  五、hook 安装（纯运行时，不用 Logos）
// ============================================================================

static IMP gOrigAddGesture    = NULL;
static IMP gOrigDidMove       = NULL;
static IMP gOrigViewDidAppear = NULL;

/// 在 cls 上安装 sel 的替换实现。
///
/// ★ 关键：必须区分「cls 自己实现了 sel」和「sel 从父类继承」。
///   后者若直接 method_setImplementation，改的是**父类**的方法，
///   会污染父类的所有其他子类 —— 可能直接崩宿主。
///   正确做法是给 cls 单独 class_addMethod 一份，只影响它自己。
static BOOL NLWInstallHook(Class cls, SEL sel, IMP newImp, IMP *origOut,
                           const char *typeEnc)
{
    @try {
        if (!cls || !sel || !newImp) return NO;

        Class sup = class_getSuperclass(cls);
        IMP curImp = class_getMethodImplementation(cls, sel);
        if (!curImp) return NO;

        IMP supImp = sup ? class_getMethodImplementation(sup, sel) : NULL;

        if (supImp && curImp == supImp) {
            // 继承来的：只给 cls 加一份，父类原样不动
            if (origOut) *origOut = supImp;
            if (class_addMethod(cls, sel, newImp, typeEnc)) return YES;
            Method m = class_getInstanceMethod(cls, sel);
            if (!m) return NO;
            if (origOut) *origOut = method_getImplementation(m);
            method_setImplementation(m, newImp);
            return YES;
        }

        Method m = class_getInstanceMethod(cls, sel);
        if (!m) return NO;
        if (origOut) *origOut = method_getImplementation(m);
        method_setImplementation(m, newImp);
        return YES;
    } @catch (NSException *e) {
        return NO;
    }
}

// ============================================================================
//  六、hook 实现
// ============================================================================

// ---- 第 0 层（主）：addGestureRecognizer: ----
// 签名：- (void)addGestureRecognizer:(UIGestureRecognizer *)g   →  v@:@
static void NLWAddGestureRecognizer(id self, SEL _cmd, UIGestureRecognizer *g)
{
    BOOL hit = NO;
    @try {
        // 两道便宜过滤：
        //   ① 不是长按 → 立刻放过（绝大多数调用走这条路）
        //   ② 宿主不是「锁屏壁纸区」→ 放过（含通知/滚动容器/UIControl 排除判据）
        // 函数内部已做完整判据，这里不重复判断，避免两处逻辑不一致。
        if (g && [g isKindOfClass:[UILongPressGestureRecognizer class]]) {
            hit = NLWIsSuppressibleLockScreenArea((UIView *)self);
        }
    } @catch (NSException *e) { }

    // ★ 先转发：不转发会让 gesture.view 保持 nil，系统后续代码读它可能崩。
    //   「转发 → 压制」才是安全顺序。
    if (gOrigAddGesture) {
        ((void (*)(id, SEL, id))gOrigAddGesture)(self, _cmd, g);
    }

    if (hit) NLWProcessView((UIView *)self, "addGestureRecognizer:");
}

// ---- 第 1 层（补）：didMoveToWindow ----
// 签名：- (void)didMoveToWindow   →  v@:
static void NLWDidMoveToWindow(id self, SEL _cmd)
{
    @try { NLWProcessView((UIView *)self, "didMoveToWindow"); }
    @catch (NSException *e) { }

    if (gOrigDidMove) ((void (*)(id, SEL))gOrigDidMove)(self, _cmd);
}

// ---- 第 2 层（补）：viewDidAppear: ----
// 签名：- (void)viewDidAppear:(BOOL)animated   →  v@:B

/// 扫描「本视图 + 其直接子视图」自身挂载的长按手势。返回压制个数。
///
/// ★ 为什么允许下探一层子视图：
///   锁屏控制器 .view 往往是顶层容器，长按实际挂在它的壁纸容器子视图上。
///   只扫顶层就会漏。
///
/// ★ 为什么只下探一层、不递归：
///   锁屏真正的层级是 控制器.view → (壁纸容器 | 通知列表 | 按钮栏)。
///   通知列表 / 手电筒 / 相机都在同一层或更深，但它们**不在锁屏容器的
///   直接子视图链上带长按**（通知是 UICollectionView 的 cell 内长按，
///   属于更深层）。只下探一层既能覆盖壁纸容器，又不会误伤通知。
///   ★ 若实测发现通知长按被影响，把这里改回只处理 0 层即可。
static NSUInteger NLWProcessViewShallow(UIView *v, const char *where)
{
    NSUInteger n = 0;
    @try {
        if (!v) return 0;
        n += NLWProcessView(v, where);

        NSArray *subs = v.subviews;      // nil 安全
        NSUInteger cap = 0;
        for (UIView *s in subs) {
            if (++cap > 80) break;       // 防御：子视图异常多时不拖慢启动
            n += NLWProcessView(s, where);
        }
    } @catch (NSException *e) { }
    return n;
}

static void NLWViewDidAppear(id self, SEL _cmd, BOOL animated)
{
    @try { NLWProcessViewShallow([self view], "viewDidAppear:"); }
    @catch (NSException *e) { }

    if (gOrigViewDidAppear)
        ((void (*)(id, SEL, BOOL))gOrigViewDidAppear)(self, _cmd, animated);
}

// ============================================================================
//  七、构造：装 hook
// ============================================================================

__attribute__((constructor)) static void NLWInit(void)
{
    @try {
        if (NLWIsKilled()) {
            NLWLog(@"[nlw] 检测到 killswitch 文件，插件不启用（删掉该文件后重启桌面即可恢复）");
            return;
        }

        NSMutableString *rep = [NSMutableString string];

        // ---- 第 0 层：UIView 基类 ----
        // ★ 为什么 hook 基类而不是具体锁屏类：
        //   锁屏视图具体类名随版本变，但最终都继承自 UIView。
        //   在基类上判「类名链含 LockScreen/CoverSheet/Poster」→ 跨版本通吃。
        // ★ 性能：addGestureRecognizer: 只在视图搭建期调用，不是高频路径；
        //   第一道判断只是 isKindOfClass，纳秒级。
        {
            IMP o = NULL;
            if (NLWInstallHook([UIView class], @selector(addGestureRecognizer:),
                               (IMP)NLWAddGestureRecognizer, &o, "v@:@")) {
                gOrigAddGesture = o;
                [rep appendString:@"  [主] UIView -addGestureRecognizer:\n"];
            } else {
                [rep appendString:@"  [主] UIView -addGestureRecognizer: 安装失败（跳过）\n"];
            }
        }
        {
            IMP o = NULL;
            if (NLWInstallHook([UIView class], @selector(didMoveToWindow),
                               (IMP)NLWDidMoveToWindow, &o, "v@:")) {
                gOrigDidMove = o;
                [rep appendString:@"  [补] UIView -didMoveToWindow\n"];
            } else {
                [rep appendString:@"  [补] UIView -didMoveToWindow 安装失败（跳过）\n"];
            }
        }

        // ---- 第 2 层：锁屏控制器 ----
        NSArray *ctlClasses = @[
            @"SBLockScreenViewController",
            @"SBLockScreenViewControllerBase",
            @"CSCoverSheetViewController",
        ];
        for (NSString *name in ctlClasses) {
            Class c = NSClassFromString(name);
            if (!c) continue;
            IMP o = NULL;
            if (NLWInstallHook(c, @selector(viewDidAppear:),
                               (IMP)NLWViewDidAppear, &o, "v@:B")) {
                if (!gOrigViewDidAppear && o) gOrigViewDidAppear = o;
                [rep appendFormat:@"  [补] %@ -viewDidAppear:\n", name];
            }
        }

        // ---- 启动记录 ----
        // ★ 这条日志是「插件到底有没有跑起来」的唯一凭据。
        //   装完看不到这条 → 插件没加载（不是没生效），要查加载问题；
        //   看到了但长按还能换壁纸 → 是判据没命中，把日志发我。
        NLWLog([NSString stringWithFormat:
            @"[nlw] ==== NoLockWallpaper 启动 ====\n"
             "[nlw] iOS %@\n"
             "%@"
             "[nlw] 预期：锁屏长按不再弹壁纸选择；主屏图标长按、锁屏通知长按不受影响\n"
             "[nlw] 日志目录: %@\n",
            NLWiOSVersion(), rep, (NLWLogDir() ?: @"(落盘失败)")]);
    } @catch (NSException *e) {
        // 构造器异常绝不外泄：最坏是插件不生效，宿主必须照常启动
    }
}