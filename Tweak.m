//
//  NoLockWallpaper —— 锁屏防误触（禁长按换壁纸 / 禁左滑进相机）
//
//  需求原话：
//    ① 手机锁屏时，长按屏幕就会跳出更换壁纸的选项，经常误触，
//       帮我禁掉这个功能，锁屏长按不会出现更换壁纸。
//    ② 帮我增加一个禁掉左滑进相机的功能（锁屏左滑不再打开相机）。
//    ③ 在设置里增加个开关吧，我想关闭这个功能时能去关掉。
//
//  目标设备：iOS 17.0.3 / arm64e / RootHide（jbroot）
//
// ============================================================================
//  1.1 主要变更
// ============================================================================
//  · 新增：设置页开关（设置 → NoLockWallpaper），改完**立即生效**，不用 respring。
//    配置域 com.liehuu.nolockwallpaper，键 Enabled / BlockCameraSwipe。
//  · 新增：禁锁屏左滑进相机。
//  · 移除：日志落盘、手势清单 dump、iOS 版本探测。
//    它们当初的唯一用途是「区分没生效和崩了」，功能已实机验证通过后
//    只剩 IO 负担 —— 而 didMoveToWindow 是上屏热路径，写盘是被 watchdog
//    杀进程的经典成因。删掉是净收益。
//  · 保留 killswitch 文件：那不是诊断，是应急阀 —— 万一设置 App 都进不去，
//    还能靠文件系统把插件停掉。
//
// ============================================================================
//  ★★ 核心思路：不猜私有方法名，只在「公开 API 的必经之路」上拦
// ============================================================================
//  锁屏长按换壁纸、左滑进相机，最终都表现为「一个 UIGestureRecognizer
//  挂在锁屏视图上」。这些手势必然经过 UIView -addGestureRecognizer:。
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
//      视图上屏时再压一次。盖住两种真实情况：
//        · 系统添加后又把 enabled 改回 YES
//        · 手势参数（如 edge pan 的 edges）在 add 之后才设置，
//          add 那一刻还判不出来 —— 上屏时参数已定，这层能补上
//
//  第 2 层（补）：锁屏控制器 -viewDidAppear:
//      整个锁屏页出现时再整体扫一遍。盖住「手势加载得比本插件更早」。
//
// ============================================================================
//  ★★★ 最重要的安全边界：绝不下钻子视图 ★★★
// ============================================================================
//  锁屏上还有：通知列表长按（必须保留）、手电筒/相机按钮（必须保留）、
//  时钟小组件长按（必须保留）、**上滑解锁/下拉通知（必须保留）**。
//  所以每层都**只看当前视图自身**的 gestureRecognizers，绝不递归 subviews。
//  一旦下钻，就是把锁屏上所有手势全废 —— 属于严重误伤，绝不能做。
//
// ============================================================================
//  ★★ 两个功能各自的精确判据（克制是关键）
// ============================================================================
//  ① 禁长按换壁纸：命中「锁屏壁纸区」+ 长按类手势 → 压制。
//  ② 禁左滑进相机：命中「锁屏壁纸区」+ 且满足下面任一条 → 压制。
//       a) UIScreenEdgePanGestureRecognizer 且 edges 含 UIRectEdgeRight
//          —— 锁屏从右边缘往左滑就是相机，这是公开属性，不猜任何东西
//       b) 宿主视图类名链含 "Camera" 且手势是 pan 类
//     ★ 为什么绝不用「压掉锁屏上所有 pan」这种粗暴做法：
//       上滑解锁、下拉开通知都是垂直 pan。一刀切会把解锁废掉 ——
//       那比原需求破坏性大得多，属于绝对不可接受的误伤。
//       edges == UIRectEdgeRight 只覆盖「右边缘横向滑动」，与解锁无交集。
//
// ============================================================================
//  ★★ 崩宿主防护（本项目铁律，每条对应一次真实事故）
// ============================================================================
//   1. 每个 hook 整体 @try/@catch，异常绝不出线程
//   2. 调 orig 前判非空；addGestureRecognizer / didMoveToWindow /
//      viewDidAppear 都是宿主必须完成的回调，**无论如何先转发再压制**。
//      不转发 addGestureRecognizer 会让 gesture.view 保持 nil，
//      系统后续代码读它可能崩 —— 所以顺序是「转发 → 压制」，不是「不转发」。
//   3. 热路径零 IO：开关状态只存在内存里，热路径只读一个 BOOL
//   4. 不访问私有属性，全部公开 API + 消息发送
//   5. hook 装不上（类/sel 不存在）就干净跳过，绝不硬上
//   6. 构造器整体 try/catch，任何异常都不影响 SpringBoard 启动
//   7. 应急阀：/var/mobile/Library/Preferences/com.liehuu.nlw.disabled
//      文件存在时插件完全不动手 —— 设置 App 都进不去时还能靠它停用
//

#import <UIKit/UIKit.h>
#import <Foundation/Foundation.h>
#import <CoreFoundation/CFNotificationCenter.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <dlfcn.h>
#import <string.h>

// ============================================================================
//  一、设置页开关（读一次，之后只读内存）
// ============================================================================
//  ★ 为什么开关状态存在内存里，不在 hook 里读 CFPreferences：
//    UIView -didMoveToWindow 是**所有视图上屏的必经路径**。SpringBoard 启动
//    期几百个视图各走一遍，每次读 CFPreferences 都是 syscall + plist 解析。
//    这属于「hook 热路径里做 IO」，是 watchdog 杀进程的经典成因。
//    所以：构造器读一次 + 设置页改动时通过 Darwin 通知推一次，其余全是 BOOL。

static NSString *const NLWPrefsDomain        = @"com.liehuu.nolockwallpaper";
static NSString *const NLWEnabledKey         = @"Enabled";
static NSString *const NLWBlockCameraKey     = @"BlockCameraSwipe";
/// 与 Root.plist 里 PSSwitchCell 的 PostNotification 必须**完全一致**。
/// 不一致 = 改了开关没反应，且看不出原因（这类问题排查成本极高）。
static NSString *const NLWChangedNotification = @"com.liehuu.nolockwallpaper/changed";

/// 功能总开关。关闭时 hook 仍然装着，但一进判据就放行 ——
/// 这样「关掉」是零开销的（一次 BOOL 判断），而恢复已压制的手势也能做。
static BOOL gEnabled          = YES;
/// 禁左滑进相机的子开关（用户可能只想禁长按、保留相机）。
static BOOL gBlockCameraSwipe = YES;

static BOOL NLWReadBool(NSString *key, BOOL fallback)
{
    BOOL on = fallback;
    @try {
        CFStringRef domain = (__bridge CFStringRef)NLWPrefsDomain;
        // Synchronize 会「刷出挂起的写 + 从磁盘重读」，
        // 因此这里拿到的一定是设置 App 刚写下的最新值，不是旧缓存。
        CFPreferencesAppSynchronize(domain);
        Boolean present = false;
        Boolean v = CFPreferencesGetAppBooleanValue((__bridge CFStringRef)key,
                                                    domain, &present);
        if (present) on = (v != 0);
    } @catch (NSException *e) {
        // 读失败按默认值处理：最坏是功能照常生效，而不是静默失效
        on = fallback;
    }
    return on;
}

/// 键缺失时一律按「开启」：装完就该生效。
/// 不能因为用户还没进过设置页、配置文件还没落盘，就让插件静默失效 ——
/// 那会被当成「装坏了」，排查成本极高。
static void NLWLoadSwitches(void)
{
    gEnabled          = NLWReadBool(NLWEnabledKey,     YES);
    gBlockCameraSwipe = NLWReadBool(NLWBlockCameraKey, YES);
}

// ============================================================================
//  二、应急阀（设置 App 都进不去时的最后手段）
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
//  三、判据：类名链
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
///
/// ★ 为什么写成单次遍历查多个关键字（而不是调用 NLWChainHas 五次）：
///   本函数在 didMoveToWindow 里对每个视图、每层祖先都会被调用，
///   遍历类链 × 5 次是 5 倍开销。一次遍历内比对 5 个串才是热路径写法。
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

/// 沿父类链找任意关键字。用于「相机相关视图」这类低频判据。
static BOOL NLWChainHas(id obj, const char *needle)
{
    if (!obj || !needle) return NO;
    for (Class c = object_getClass(obj); c != Nil; c = class_getSuperclass(c)) {
        const char *n = class_getName(c);
        if (n && strstr(n, needle)) return YES;
    }
    return NO;
}

// ============================================================================
//  四、判据：手势类型
// ============================================================================

/// 是否「长按类」手势。
///
/// ★ 为什么不只判 isKindOfClass:[UILongPressGestureRecognizer class]：
///   iOS 17 锁屏进入自定义模式用的手势，可能是系统自定义子类
///   （继承 UILongPressGestureRecognizer 时 isKindOfClass 已经覆盖），
///   但也存在「不继承、自己从 UIGestureRecognizer 派生」的实现可能。
///   类名带 LongPress 的按名判一次，属于零成本兜底。
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

/// 是否「滑动类」手势（pick up 出来做类名兜底用）。
static BOOL NLWIsPanish(UIGestureRecognizer *g)
{
    if (!g) return NO;
    if ([g isKindOfClass:[UIPanGestureRecognizer class]]) return YES;   // 含 edge pan 子类
    @try {
        const char *n = class_getName(object_getClass(g));
        if (n && strstr(n, "Pan")) return YES;
    } @catch (NSException *e) { }
    return NO;
}

/// 是否「从右边缘往左滑 → 进相机」的手势。
///
/// ★★ 为什么这个判据是安全的（不会废掉上滑解锁）：
///   锁屏「左滑进相机」= 手指从**屏幕右边缘**往左划 → 横向 edge pan。
///   「上滑解锁」「下拉开通知」= 垂直 pan，且不从上边缘起手。
///   `UIScreenEdgePanGestureRecognizer.edges` 是**公开 API**，
///   值为 UIRectEdgeRight 时只匹配「右边缘起手」，与解锁手势零交集。
///
/// ★★ 为什么绝不用「压掉锁屏上所有 pan」这种粗暴做法：
///   上滑解锁就是垂直 pan。一刀切会把**解锁废掉** —— 那比原需求
///   破坏性大得多，属于绝对不可接受的误伤。宁可漏压也不能这样做。
///
/// ★ 三层判据，从精确到兜底：
///   a) 是 UIScreenEdgePanGestureRecognizer 且 edges 含 Right
///      最精确、零猜测。锁屏相机入口的标准实现。
///   b) 手势类名含 EdgePan / ScreenEdge（自定义子类的兜底）
///      系统若用自有类做边缘手势，继承关系可能不是 UIKit 那个类，
///      但命名习惯一定留痕。
///   c) 宿主类名链含 Camera 且手势是滑动类
///      盖住「不用边缘手势、直接把 pan 挂相机预览容器」的实现。
///      ★ 限定 pan，避免把相机界面上的点按（快门）一起废掉。
///
///   三条都限定在「已确认是锁屏可压制区域」之后才被调用，
///   作用域已经收窄，不会扩散到主屏或 App 内。
static BOOL NLWIsCameraSwipeish(UIGestureRecognizer *g, UIView *host)
{
    if (!g) return NO;

    // a) 右边缘 edge pan —— 锁屏相机的标准手势
    @try {
        if ([g isKindOfClass:[UIScreenEdgePanGestureRecognizer class]]) {
            UIRectEdge e = ((UIScreenEdgePanGestureRecognizer *)g).edges;
            if (e & UIRectEdgeRight) return YES;
        }
    } @catch (NSException *e) { }

    // b) 类名兜底：自定义边缘手势类
    @try {
        const char *n = class_getName(object_getClass(g));
        if (n && (strstr(n, "EdgePan") || strstr(n, "ScreenEdge"))) {
            // ★ 必须同时是「滑动」语义。只按类名压，可能把同族的
            //   非滑动手势一起废掉。
            // ★ 这里读不到 edges（它不是 UIScreenEdgePanGestureRecognizer，
            //   强转读属性属于猜类型，可能拿到垃圾值），所以按「锁屏上
            //   的边缘滑动」直接认定。
            //   为什么这个放宽是可接受的：锁屏壁纸区不存在「左边缘返回」
            //   这类手势（那是 App 内导航），锁屏上的边缘 pan 只有相机入口。
            //   且整条判据已经被「锁屏可压制区域」收窄过一次。
            if (NLWIsPanish(g)) return YES;
        }
    } @catch (NSException *e) { }

    // c) 宿主在相机容器上，且手势是滑动类
    @try {
        if (NLWIsPanish(g) && NLWChainHas(host, "Camera")) return YES;
    } @catch (NSException *e) { }

    return NO;
}

// ============================================================================
//  五、压制动作（含「记住原状」以支持实时恢复）
// ============================================================================

/// weak key → 原状（dict：dur / en），用来支持「关掉开关立即恢复」。
///
/// ★ 为什么必须记住原值而不是恢复成硬编码值：
///   ① 系统给锁屏手势设的 minimumPressDuration 未必是 UIKit 默认 0.5。
///      恢复成猜的值，用户关掉开关后长按手感会变（变快或变慢），
///      属于「关了但没完全关」的隐性缺陷。
///   ② 更要紧的是 enabled：有些手势被添加时系统就设成 NO（稍后才启用）。
///      一律恢复成 YES 会**擅自打开本不该开的手势**，可能引发意料外的行为。
///      必须原样还原。
/// ★ 为什么用 weak 表：这些手势随视图销毁而释放，强持有会拖住整棵视图树。
static NSString *const NLWOrigDurKey = @"dur";
static NSString *const NLWOrigEnKey  = @"en";
static NSMapTable *gSuppressed = nil;

static void NLWSuppressGesture(UIGestureRecognizer *g)
{
    if (!g) return;

    // 记原状（必须在改动之前取）
    @try {
        if (!gSuppressed) {
            gSuppressed = [NSMapTable weakToStrongObjectsMapTable];
        }
        // ★★ 只在**首次**记录。绝不能覆盖 —— 这是必然触发的问题，不是理论风险：
        //   didMoveToWindow 每次上屏都会走一遍压制，同一个手势会被反复处理。
        //   若每次都写入「当前值」，第二次取到的就已是我改过的 90.0 / NO，
        //   覆盖后「原状」变成 90 秒，用户关掉开关后长按依然是 90 秒 ——
        //   等于开关失效，而且极难排查（表面看恢复代码执行了）。
        if ([gSuppressed objectForKey:g] == nil) {
            BOOL wasEnabled = YES;
            @try { wasEnabled = g.enabled; } @catch (NSException *e) { }

            NSMutableDictionary *orig = [NSMutableDictionary dictionary];
            orig[NLWOrigEnKey] = @(wasEnabled);
            if ([g isKindOfClass:[UILongPressGestureRecognizer class]]) {
                orig[NLWOrigDurKey] =
                    @(((UILongPressGestureRecognizer *)g).minimumPressDuration);
            }
            [gSuppressed setObject:orig forKey:g];
        }
    } @catch (NSException *e) { }

    // 双保险：
    //   enabled=NO           → 常规手段
    //   minimumPressDuration → 万一系统把 enabled 改回 YES，
    //                          90 秒的长按人类按不出来，等于永久失效
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
}

/// 把所有已压制的手势恢复原状。开关从「开」切到「关」时调用。
///
/// ★ 为什么需要这一步（否则开关是假的）：
///   压制手段是改手势实例本身。已经改掉的实例不会因为「以后不再压制」
///   就自动复活 —— 它们活在 SpringBoard 内存里。不恢复的话，用户关掉
///   开关后长按依然没反应，会认为「开关坏了」。
static void NLWRestoreAllSuppressed(void)
{
    @try {
        if (!gSuppressed) return;
        NSArray *keys = [[gSuppressed keyEnumerator] allObjects];
        for (UIGestureRecognizer *g in keys) {
            @try {
                NSDictionary *orig = [gSuppressed objectForKey:g];
                if (![orig isKindOfClass:[NSDictionary class]]) continue;

                id dur = orig[NLWOrigDurKey];
                if ([g isKindOfClass:[UILongPressGestureRecognizer class]] &&
                    [dur isKindOfClass:[NSNumber class]]) {
                    ((UILongPressGestureRecognizer *)g).minimumPressDuration =
                        [dur doubleValue];
                }
                // ★ 还原成**原值**，不是无条件 YES
                id en = orig[NLWOrigEnKey];
                if ([en isKindOfClass:[NSNumber class]]) {
                    g.enabled = [en boolValue];
                }
            } @catch (NSException *e) { }
        }
        [gSuppressed removeAllObjects];
    } @catch (NSException *e) { }
}

// ============================================================================
//  六、压制主逻辑
// ============================================================================

/// 视图 v 是否属于「锁屏壁纸区」，可以安全压制其手势。
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

/// 处理一个视图**自身挂载**的手势。
/// ★ 绝不下钻 subviews —— 见文件头「最重要的安全边界」。
static NSUInteger NLWProcessView(UIView *v)
{
    NSUInteger n = 0;
    @try {
        // ★ 总开关放在最前面：关闭时一次 BOOL 判断就返回，零分配零开销。
        //   两个子功能都关掉时同样是零开销。
        if (!gEnabled) return 0;
        if (!v) return 0;

        // ★ 顺序很重要：先做判据、后取 gestureRecognizers。
        //   v.gestureRecognizers 每次调用都会新建一个数组（视图无手势时返回
        //   空数组而非 nil，仍会分配）。didMoveToWindow 是所有视图上屏的
        //   热路径，SpringBoard 启动期几百个视图各走一遍 —— 先取数组等于
        //   白分配几百次。判据只是类名比对，纳秒级且零分配，所以提到前面。
        if (!NLWIsSuppressibleLockScreenArea(v)) return 0;

        NSArray *gs = v.gestureRecognizers;
        if (gs.count == 0) return 0;

        for (UIGestureRecognizer *g in gs) {
            @try {
                if (NLWIsLongPressish(g)) {
                    NLWSuppressGesture(g);
                    n++;
                    continue;
                }
                if (gBlockCameraSwipe && NLWIsCameraSwipeish(g, v)) {
                    NLWSuppressGesture(g);
                    n++;
                }
            } @catch (NSException *e) { }
        }
    } @catch (NSException *e) { }
    return n;
}

// ============================================================================
//  七、hook 安装（纯运行时，不用 Logos）
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
///   找不到该符号，整份 dylib 加载失败** —— 表现就是「插件完全没生效」。
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
//  八、hook 实现
// ============================================================================

// ---- 第 0 层（主）：addGestureRecognizer: ----
// 签名：- (void)addGestureRecognizer:(UIGestureRecognizer *)g   →  v@:@
static void NLWAddGestureRecognizer(id self, SEL _cmd, UIGestureRecognizer *g)
{
    BOOL hit = NO;
    @try {
        // 三道便宜过滤：
        //   ① 总开关关着 → 不过问
        //   ② 不是「长按」也不是「相机滑动」→ 立刻放过（绝大多数调用走这条路）
        //   ③ 宿主不是「锁屏壁纸区」→ 放过（含通知/滚动容器/UIControl 排除判据）
        // ★ 手势类型判断统一走 NLWIsLongPressish / NLWIsCameraSwipeish，
        //   与后面的压制循环用同一判据。两处各写一套迟早会不一致
        //   （一处放宽一处没放宽 → 判为命中却一个都没压，或者反过来漏压）。
        if (gEnabled && (NLWIsLongPressish(g) ||
                         (gBlockCameraSwipe && NLWIsCameraSwipeish(g, (UIView *)self)))) {
            hit = NLWIsSuppressibleLockScreenArea((UIView *)self);
        }
    } @catch (NSException *e) { }

    // ★ 先转发：不转发会让 gesture.view 保持 nil，系统后续代码读它可能崩。
    //   「转发 → 压制」才是安全顺序。
    if (gOrigAddGesture) {
        ((void (*)(id, SEL, id))gOrigAddGesture)(self, _cmd, g);
    }

    if (hit) NLWProcessView((UIView *)self);
}

// ---- 第 1 层（补）：didMoveToWindow ----
// 签名：- (void)didMoveToWindow   →  v@:
static void NLWDidMoveToWindow(id self, SEL _cmd)
{
    @try { NLWProcessView((UIView *)self); }
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

/// 扫描「本视图 + 其直接子视图」自身挂载的手势。
///
/// ★ 为什么允许下探一层子视图：
///   锁屏控制器 .view 往往是顶层容器，手势实际挂在它的壁纸容器子视图上。
///   只扫顶层就会漏。相机预览容器通常也是这一层。
///
/// ★ 为什么只下探一层、不递归：
///   锁屏真正的层级是 控制器.view → (壁纸容器 | 通知列表 | 按钮栏)。
///   通知列表 / 手电筒 / 相机按钮都在同一层或更深，但它们**不在锁屏容器的
///   直接子视图链上带长按**（通知是 UICollectionView 的 cell 内长按，
///   属于更深层）。只下探一层既能覆盖壁纸容器，又不会误伤通知。
///   而且 NLWProcessView 内部还有「路径经过 UIScrollView 即排除」这道
///   独立防线，即使层级判断有偏差也不会误伤通知。
///   ★ 若实测发现通知长按被影响，把这里改成只处理 0 层即可。
static NSUInteger NLWProcessViewShallow(UIView *v)
{
    NSUInteger n = 0;
    @try {
        if (!v) return 0;
        n += NLWProcessView(v);

        NSArray *subs = v.subviews;      // nil 安全
        NSUInteger cap = 0;
        for (UIView *s in subs) {
            if (++cap > 80) break;       // 防御：子视图异常多时不拖慢启动
            n += NLWProcessView(s);
        }
    } @catch (NSException *e) { }
    return n;
}

static void NLWViewDidAppear(id self, SEL _cmd, BOOL animated)
{
    @try { NLWProcessViewShallow([self view]); }
    @catch (NSException *e) { }

    // ★ 无论如何都要转发原实现 —— 这是锁屏控制器自己的生命周期回调，
    //   不转发等于把锁屏的初始化流程砍掉，比「功能不生效」严重得多。
    IMP o = NLWOrigViewDidAppearFor(self);
    if (o && !NLWIsForwardingIMP(o)) {
        ((void (*)(id, SEL, BOOL))o)(self, _cmd, animated);
    }
}

// ============================================================================
//  九、设置页改动实时生效（Darwin 通知）
// ============================================================================
//  ★ 为什么必须实时生效，而不是「改完请手动 respring」：
//    压制手段是改手势实例本身。用户改完开关若没有任何反馈，他会认为
//    开关坏了 —— 这是设计缺陷，不是用户的问题。
//  ★ 为什么用 Darwin 通知而不是轮询：
//    轮询要么放在热路径（禁），要么起定时器（SpringBoard 里常驻定时器
//    是耗电与 watchdog 风险的来源）。通知是被动收到，零开销。
//  ★ 通知名必须与 Root.plist 的 PostNotification 一致，见 NLWChangedNotification。

static void NLWOnPrefsChanged(CFNotificationCenterRef center, void *observer,
                              CFStringRef name, const void *object,
                              CFDictionaryRef userInfo)
{
    // ★ 通知回调不保证在主线程。UI 相关操作（改手势 enabled）必须在主线程。
    dispatch_async(dispatch_get_main_queue(), ^{
        @try {
            BOOL wasEnabled = gEnabled;
            NLWLoadSwitches();      // 重新读两个开关

            if (wasEnabled && !gEnabled) {
                // 开 → 关：把已经改掉的手势恢复原样，立刻生效
                NLWRestoreAllSuppressed();
            }
            // 关 → 开：什么也不用做。后续视图上屏 / 锁屏出现时，
            //          hook 会因为 gEnabled 变回 YES 而重新压制。
        } @catch (NSException *e) { }
    });
}

static void NLWStartObservingPrefs(void)
{
    @try {
        CFNotificationCenterAddObserver(CFNotificationCenterGetDarwinNotifyCenter(),
                                        NULL,
                                        NLWOnPrefsChanged,
                                        (__bridge CFStringRef)NLWChangedNotification,
                                        NULL,
                                        CFNotificationSuspensionBehaviorDeliverImmediately);
    } @catch (NSException *e) { }
}

// ============================================================================
//  十、构造：装 hook
// ============================================================================

__attribute__((constructor)) static void NLWInit(void)
{
    @try {
        // ★ 应急阀优先：它是最后的逃生口，无论开关怎么设都要先看它。
        if (NLWIsKilled()) return;

        // ★ 开关状态读一次，之后热路径只读内存里的 BOOL。
        NLWLoadSwitches();

        // ★ 即使当前是关闭状态也照样装 hook + 装通知监听：
        //   否则用户在设置页把开关打开后，没有任何机制让插件「醒过来」。
        //   装着 hook 的代价是每次回调多一次 BOOL 判断，纳秒级。

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
            }
        }
        {
            IMP o = NULL;
            if (NLWInstallHook([UIView class], @selector(didMoveToWindow),
                               (IMP)NLWDidMoveToWindow, &o, "v@:")) {
                gOrigDidMove = o;
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
            if (!c) continue;
            IMP o = NULL;
            if (NLWInstallHook(c, @selector(viewDidAppear:),
                               (IMP)NLWViewDidAppear, &o, "v@:B")) {
                if (gCtlCount < NLW_MAX_CTL) {
                    // ★ orig 按类成对存，绝不共用 —— 见 NLWOrigViewDidAppearFor
                    gCtlCls[gCtlCount]  = c;
                    gCtlOrig[gCtlCount] = o;
                    gCtlCount++;
                } else {
                    // 槽位满：orig 存不下来就意味着该类的生命周期回调会被截断，
                    // 风险高于「这层补丁少装一个」。宁可撤销 hook 也不能丢 orig。
                    Method m = class_getInstanceMethod(c, @selector(viewDidAppear:));
                    if (m && o && !NLWIsForwardingIMP(o)) {
                        method_setImplementation(m, o);
                    }
                }
            }
        }

        // ---- 设置页改动实时生效 ----
        NLWStartObservingPrefs();
    } @catch (NSException *e) {
        // 构造器异常绝不外泄：最坏是插件不生效，宿主必须照常启动
    }
}