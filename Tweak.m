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
        // 锁屏时默认文件保护是 NSFileProtectionComplete，未解锁前读不到；
        // 放宽到「首次解锁后即可读」，用户解锁后就能用 Filza 打开。
        // ★ 常量名别记错：iOS 只有这四个
        //     NSFileProtectionComplete
        //     NSFileProtectionCompleteUnlessOpen
        //     NSFileProtectionCompleteUntilFirstUserAuthentication  ← 本处用的
        //     NSFileProtectionNone
        //   「...UntilFirstAccess」是**不存在**的名字，写错会直接编译失败
        //   （CI 实测：error: use of undeclared identifier）。
        [fm setAttributes:@{ NSFileProtectionKey :
                                 NSFileProtectionCompleteUntilFirstUserAuthentication }
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

/// 是否「长按类」手势。
///
/// ★ 为什么不只判 isKindOfClass:[UILongPressGestureRecognizer class]：
///   iOS 17 锁屏进入自定义模式用的手势，可能是系统自定义子类
///   （继承 UILongPressGestureRecognizer 时 isKindOfClass 已经覆盖），
///   但也存在「不继承、自己从 UIGestureRecognizer 派生」的实现可能。
///   类名带 LongPress 的按名判一次，属于零成本兜底。
///   ★ 为什么这个放宽不危险：本函数只在「已确认是锁屏壁纸区」之后才被调用，
///     作用域已经被三重判据收窄；且只要有 LongPress 字样，语义上就是要压的。
static BOOL NLWIsLongPressish(UIGestureRecognizer *g)
{
    if (!g) return NO;
    if ([g isKindOfClass:[UILongPressGestureRecognizer class]]) return YES;
    @try {
        const char *n = class_getName(object_getClass(g));
        if (n && strstr(n, "LongPress")) return YES;
    } @catch (NSException *e) { }
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
    // ★ 顺序不能反：先设 duration 再关 enabled。
    //   minimumPressDuration 是 UILongPressGestureRecognizer 的属性，
    //   必须在手势还「活着」的时候写；先 disable 再写有极小概率被
    //   系统在 disable 时清理内部状态而忽略。
    @try {
        if ([g isKindOfClass:[UILongPressGestureRecognizer class]]) {
            ((UILongPressGestureRecognizer *)g).minimumPressDuration = 90.0;
        }
    } @catch (NSException *e) { }
    @try { g.enabled = NO; } @catch (NSException *e) { }

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

        // ★ 顺序很重要：先做判据、后取 gestureRecognizers。
        //   v.gestureRecognizers 每次调用都会新建一个数组（视图无手势时返回
        //   空数组而非 nil，仍会分配）。didMoveToWindow 是所有视图上屏的
        //   热路径，SpringBoard 启动期几百个视图各走一遍 —— 先取数组等于
        //   白分配几百次。判据只是类名比对，纳秒级且零分配，所以提到前面。
        if (!NLWIsSuppressibleLockScreenArea(v)) return 0;

        NSArray *gs = v.gestureRecognizers;
        if (gs.count == 0) return 0;

        // 首次找到可压制的锁屏区域：把手势清单整体记下来。
        // ★ 这是给「万一没生效」留的诊断口 —— 日志里能直接看到锁屏上
        //   究竟是哪几个手势类、挂在什么类上、各自 enabled 状态，不必再靠猜。
        if (!gLoggedInventory) {
            gLoggedInventory = YES;
            NSMutableString *inv = [NSMutableString string];
            for (UIGestureRecognizer *g in gs) {
                NSString *cn = @"?";
                @try { cn = NSStringFromClass(object_getClass(g)); } @catch (NSException *e) { }
                BOOL en = NO;
                @try { en = g.enabled; } @catch (NSException *e) { }
                [inv appendFormat:@"        - %@  enabled=%d\n", cn, (int)en];
            }
            NSString *vcls = @"?";
            @try { vcls = NSStringFromClass([v class]); } @catch (NSException *e) { }
            NLWLog([NSString stringWithFormat:
                @"[nlw] 命中锁屏壁纸区 %@ via %s，该视图自身手势 %lu 个：\n%@",
                vcls, where, (unsigned long)gs.count, inv]);
        }

        for (UIGestureRecognizer *g in gs) {
            if (NLWIsLongPressish(g)) {
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

/// 是否「消息转发桩」而非真实实现。
///
/// ★★ 这是崩宿主的高危点，必须显式排除：
///   class_getMethodImplementation / method_getImplementation 在
///   「没人实现这个 selector」时可能给出 objc_msgForward（非 NULL 的转发桩）。
///   若把它当成 orig 存下来再转发调用，等于把消息再送一次转发 ——
///   arm64e 上会直接 EXC_BAD_ACCESS 或无限递归，SpringBoard 崩。
///   因此 orig 必须「要么是真实 IMP，要么视为没拿到」。
///
/// ★★★ 为什么用 dlsym 取符号，而不是直接写 &_objc_msgForward：
///   直接引用会在 dylib 的符号表里留下未定义符号绑定。本包用
///   -undefined dynamic_lookup 链接，编译期不报错，但**运行期若 dyld
///   找不到该符号，整份 dylib 加载失败** —— 表现就是「插件完全没生效，
///   连一行日志都没有」，这正是我们反复踩过的坑。
///   · _objc_msgForward_stret 在 arm64 上已从 libobjc 移除，
///     绝不能直接引用（arm64 是唯一目标架构，引用=必崩加载）
///   · 用 dlsym 拿到的是地址或 NULL，取不到也只是「这层保护不启用」，
///     绝不影响 dylib 加载。防御代码本身不能成为加载失败的原因。
static BOOL NLWIsForwardingIMP(IMP imp)
{
    if (!imp) return YES;

    static void *fwd = NULL;
    static void *fwdStret = NULL;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        fwd      = dlsym(RTLD_DEFAULT, "_objc_msgForward");
        fwdStret = dlsym(RTLD_DEFAULT, "_objc_msgForward_stret");
    });

    if (fwd && (void *)imp == fwd) return YES;
    if (fwdStret && (void *)imp == fwdStret) return YES;
    return NO;
}

/// 在 cls 上安装 sel 的替换实现。
///
/// ★ 关键：必须区分「cls 自己实现了 sel」和「sel 从父类继承」。
///   后者若直接 method_setImplementation，改的是**父类**的方法，
///   会污染父类的所有其他子类 —— 可能直接崩宿主。
///   正确做法是给 cls 单独 class_addMethod 一份，只影响它自己。
///
/// ★ 返回值语义：YES 表示已装好；NO 表示干净跳过（调用方必须不记 orig）。
///   origOut 只会被写成「真实可调用的 IMP」，绝不含转发桩 —— 调用方因此
///   可以放心用 if (orig) orig(...) 转发。
static BOOL NLWInstallHook(Class cls, SEL sel, IMP newImp, IMP *origOut,
                           const char *typeEnc)
{
    @try {
        if (!cls || !sel || !newImp) return NO;
        if (origOut) *origOut = NULL;

        Class sup = class_getSuperclass(cls);

        // ★ 优先走 method_getImplementation 取「本类真实存在的方法」。
        //   只有确实没找到时，才看 class_getMethodImplementation 是否
        //   落在父类上（= 继承关系）。两条路径都要过转发桩过滤。
        Method own = class_getInstanceMethod(cls, sel);
        if (!own) return NO;                    // 类根本没有这个方法 → 干净跳过

        IMP ownImp = method_getImplementation(own);
        if (NLWIsForwardingIMP(ownImp)) return NO;   // 转发桩 → 干净跳过

        Method supMethod = sup ? class_getInstanceMethod(sup, sel) : NULL;
        BOOL inherited = NO;

        if (supMethod && method_getImplementation(supMethod) == ownImp) {
            // 本类方法表里这一项和父类指向同一个 IMP → 没被本类覆盖
            inherited = YES;
        }

        if (inherited) {
            // 继承来的：只给 cls 加一份，父类原样不动
            if (class_addMethod(cls, sel, newImp, typeEnc)) {
                if (origOut) *origOut = ownImp;
                return YES;
            }
            // addMethod 失败（极少见：可能并发已有同名）→ 回退到改写本类 method
            Method m = class_getInstanceMethod(cls, sel);
            if (!m) return NO;
            IMP cur = method_getImplementation(m);
            if (NLWIsForwardingIMP(cur)) return NO;
            if (origOut) *origOut = cur;
            method_setImplementation(m, newImp);
            return YES;
        }

        // 本类自己实现（或父类根本没有）→ 直接改写本类方法表
        if (origOut) *origOut = ownImp;
        method_setImplementation(own, newImp);
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
        // ★ 长按判断统一走 NLWIsLongPressish，与后面的压制循环用同一判据。
        //   两处各写一套 isKindOfClass 迟早会不一致（一处放宽一处没放宽
        //   → 判为命中却一个都没压，或者反过来漏压）。
        if (NLWIsLongPressish(g)) {
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
//
// ★★ 为什么 orig 必须「一个类存一份」，不能共用一个全局变量：
//   iOS 17 上 SBLockScreenViewController / CSCoverSheetViewController 里
//   可能有两个甚至更多都真实存在。若只存第一个的 orig，第二个类的 orig
//   就永远拿不到 → 它的原始 viewDidAppear: 再也不会被执行 →
//   锁屏控制器自身逻辑被截断（黑屏、不刷新、卡死都可能）。
//   所以按「类 → orig」成对存，转发时按 self 的实际类链找最精确的那份。

#define NLW_MAX_CTL 4
static Class gCtlCls[NLW_MAX_CTL];
static IMP   gCtlOrig[NLW_MAX_CTL];
static int   gCtlCount = 0;

/// 按 self 的实际类链，找出对应的原始 viewDidAppear: 实现。
static IMP NLWOrigViewDidAppearFor(id self)
{
    if (!self) return NULL;
    for (Class k = object_getClass(self); k != Nil; k = class_getSuperclass(k)) {
        for (int i = 0; i < gCtlCount; i++) {
            if (gCtlCls[i] == k) return gCtlOrig[i];
        }
    }
    return NULL;
}

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
///   而且 NLWProcessView 内部还有「路径经过 UIScrollView 即排除」这道
///   独立防线，即使层级判断有偏差也不会误伤通知。
///   ★ 若实测发现通知长按被影响，把这里改成只处理 0 层即可。
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

    // ★ 无论如何都要转发原实现 —— 这是锁屏控制器自己的生命周期回调，
    //   不转发等于把锁屏的初始化流程砍掉，比「功能不生效」严重得多。
    IMP o = NLWOrigViewDidAppearFor(self);
    if (o && !NLWIsForwardingIMP(o)) {
        ((void (*)(id, SEL, BOOL))o)(self, _cmd, animated);
    }
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
        // ★ 这三个类名是「都可能存在但不一定都存在」的关系：
        //   iOS 13~15 用 SBLockScreenViewController，
        //   iOS 16+ 逐步迁到 CSCoverSheetViewController。
        //   哪个在就装哪个，都不在也不报错（干净跳过）。
        NSArray *ctlClasses = @[
            @"SBLockScreenViewController",
            @"SBLockScreenViewControllerBase",
            @"CSCoverSheetViewController",
        ];
        for (NSString *name in ctlClasses) {
            Class c = NSClassFromString(name);
            if (!c) {
                [rep appendFormat:@"  [补] %@ 不存在（跳过）\n", name];
                continue;
            }
            IMP o = NULL;
            if (NLWInstallHook(c, @selector(viewDidAppear:),
                               (IMP)NLWViewDidAppear, &o, "v@:B")) {
                if (gCtlCount < NLW_MAX_CTL) {
                    // ★ orig 按类成对存，绝不共用 —— 见 NLWOrigViewDidAppearFor
                    gCtlCls[gCtlCount]  = c;
                    gCtlOrig[gCtlCount] = o;
                    gCtlCount++;
                    [rep appendFormat:@"  [补] %@ -viewDidAppear:\n", name];
                } else {
                    // 槽位满：orig 存不下来就意味着该类的生命周期回调会被截断，
                    // 风险高于「这层补丁少装一个」。宁可撤销 hook 也不能丢 orig。
                    Method m = class_getInstanceMethod(c, @selector(viewDidAppear:));
                    if (m && o && !NLWIsForwardingIMP(o)) {
                        method_setImplementation(m, o);
                    }
                    [rep appendFormat:@"  [补] %@ 槽位已满，本层跳过（不影响主层）\n", name];
                }
            } else {
                [rep appendFormat:@"  [补] %@ -viewDidAppear: 安装失败（跳过）\n", name];
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