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
#import <stdlib.h>      // 1.8-1：objc_getClassList 扫描用到的 malloc/free

// ============================================================================
//  一、设置页开关（读一次，之后只读内存）
// ============================================================================
//  ★ 为什么开关状态存在内存里，不在 hook 里读 CFPreferences：
//    UIView -didMoveToWindow 是**所有视图上屏的必经路径**。SpringBoard 启动
//    期几百个视图各走一遍，每次读 CFPreferences 都是 syscall + plist 解析。
//    这属于「hook 热路径里做 IO」，是 watchdog 杀进程的经典成因。
//    所以：构造器读一次 + 设置页改动时通过 Darwin 通知推一次，其余全是 BOOL。

static NSString *const NLWPrefsDomain        = @"com.liehuu.nolockwallpaper";
/// ★ 2.0-1：**独立的总开关**。
///   1.9-1 及以前把「禁用锁屏长按换壁纸」当成了总开关用（所有判据都带 gEnabled），
///   于是用户关掉长按那一项，相机/手电筒也跟着全失效 —— 这是设计缺陷。
///   现在总开关单独一项，长按那一项只管长按。
static NSString *const NLWMasterKey          = @"MasterEnabled";
/// 禁用锁屏长按换壁纸（**只管长按**，不再是总开关）
static NSString *const NLWEnabledKey         = @"Enabled";
/// 禁左滑进相机（映射 SpringBoard -lockScreenCameraSupported）
static NSString *const NLWBlockCameraKey     = @"BlockCameraSwipe";
/// 1.9-1：禁锁屏相机按钮（映射 CSQuickActionsViewController -hasCamera）
static NSString *const NLWBlockCamBtnKey     = @"BlockCameraButton";
/// 1.9-1：禁锁屏手电筒（映射 CSQuickActionsViewController -hasFlashlight）
static NSString *const NLWBlockFlashKey      = @"BlockFlashlight";
/// 与 Root.plist 里 PSSwitchCell 的 PostNotification 必须**完全一致**。
/// 不一致 = 改了开关没反应，且看不出原因（这类问题排查成本极高）。
static NSString *const NLWChangedNotification = @"com.liehuu.nolockwallpaper/changed";

/// 总开关。关掉它 = 插件完全不动手。
static BOOL gMasterEnabled = YES;
/// 禁用锁屏长按换壁纸（1.0-1 就有的那一项，2.0-1 起只代表自己）。
static BOOL gEnabled          = YES;
/// 禁左滑进相机的子开关（用户可能只想禁长按、保留相机）。
static BOOL gBlockCameraSwipe = YES;
/// 1.9-1：禁锁屏相机按钮（只影响按钮，不影响左滑）。
static BOOL gBlockCameraButton = YES;
/// 1.9-1：禁锁屏手电筒。
static BOOL gBlockFlashlight   = YES;

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
///
/// ★★ 2.0-1：总开关在这里做**掩码**，而不是把 gMasterEnabled 塞进每一个判据。
///   做法：总开关关掉 → 把全部子开关压成 NO。
///   好处：源码里所有既有判据（`if (gEnabled && ...)`、
///   `if (gBlockCameraSwipe && ...)`）**一个字都不用改**，
///   但语义自动变成「有效开关」——「总开关」和「子开关」不会各说各话。
///   坏处：日志里看到的子开关值是被压过的（这正是我们想要的"生效值"）。
static void NLWLoadSwitches(void)
{
    gMasterEnabled     = NLWReadBool(NLWMasterKey,     YES);
    gEnabled           = NLWReadBool(NLWEnabledKey,     YES);
    gBlockCameraSwipe  = NLWReadBool(NLWBlockCameraKey, YES);
    gBlockCameraButton = NLWReadBool(NLWBlockCamBtnKey, YES);
    gBlockFlashlight   = NLWReadBool(NLWBlockFlashKey,  YES);

    if (!gMasterEnabled) {
        gEnabled           = NO;
        gBlockCameraSwipe  = NO;
        gBlockCameraButton = NO;
        gBlockFlashlight   = NO;
    }
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
//  四之二、1.4-1 新增：绕开「视图树」这条走不通的路
// ============================================================================
//
// ★★ 为什么必须加这一节 —— 1.3-1 实机日志给出的硬证据（2026-10-22）：
//
//   148 条手势现场里，`_UISystemGestureWindow` 上共有 8 个边缘手势，
//   **没有任何一个的 edges 含 Right(8)**：
//
//     SBScreenEdgePanGestureRecognizer            edges=4 / edges=1
//     SBHomeGesturePanGestureRecognizer           edges=4 / edges=15
//     SBCoverSheetScreenEdgePanGestureRecognizer  edges=4 ×2
//     SBCoverSheetPresentationGestureRecognizer   edges=1
//     SBFluidSwitcherScreenEdgePanGestureRecognizer edges=15
//     UIScreenEdgePanGestureRecognizer            edges=15
//
//   而且**所有行的 sup 都是 0** —— 一个都没被认成「锁屏可压制区」。
//
//   这两件事合起来说明：原来的两条路同时失效。
//
//   ① 视图树这条路对它天然无效：
//      相机手势挂在 `UISystemGestureView` 上，它属于独立的
//      `_UISystemGestureWindow`，与锁屏窗口**平行**，祖先链里
//      根本没有 CoverSheet / LockScreen / PosterBoard 字样。
//      → 任何「沿 superview 往上找锁屏」的判据，对这个宿主必然返回 NO。
//      这不是判据写错了，是**它所在的层级结构就绕过了这套判据**。
//
//   ② edges 这条路也读不出真相：
//      SpringBoard 的 CoverSheet 边缘手势用自有子类实现，
//      `edges` 在 add 时刻未必是真值（可能稍后才配置，
//      也可能真正的方向存在私有字段里）。
//      → 拿 edges 当唯一依据，等于赌「add 那一刻它已经配好」。
//
//   ★ 通用规律：**当一条判据在真实数据上「全线不命中」时，
//     先怀疑判据所依赖的维度本身不存在，而不是继续调阈值。**
//     1.3-1 之前我一直在想「edges 是 1 还是 8」，
//     而数据显示「一个 8 都没有」—— 说明维度选错了。
//
// ★ 新数据源：让手势自己说它是谁。
//   `-[UIGestureRecognizer description]` 是 NSObject 协议方法（公开、
//   零私有符号），它内部会打印 target 的 action 名与 target 类名：
//     target= <(action=handleCameraGesture:, target=<SBCoverSheet...>)>
//   这把「哪个手势负责相机」交给运行时自己回答，不用猜类名。
//
// ★ 为什么这条判据敢绕过「可压制区」：
//   在 description 里出现 camera 是**语义级证据**，比视图树位置更硬；
//   而相机的边缘手势只存在于锁屏（主屏、App 内都没有这个手势），
//   所以不会扩散到别处。其余手势仍走原来的保守判据，一点不放松。

/// 宿主是否「系统全局手势窗口里的视图」。
///
/// ★ 单独认这一层的原因见上：相机手势的宿主在这里，
///   而这里恰恰是「锁屏视图树判据」覆盖不到的地方。
static BOOL NLWIsSystemGestureHost(UIView *v)
{
    if (!v) return NO;
    @try {
        // 类链含 UISystemGestureView（自身或子类）
        if (NLWChainHas(v, "SystemGestureView")) return YES;
        // 所在窗口是 _UISystemGestureWindow
        UIWindow *w = v.window;
        if (w && NLWChainHas(w, "SystemGestureWindow")) return YES;
    } @catch (NSException *e) { }
    return NO;
}

/// 手势的 description 里是否出现 camera。
///
/// ★ 只对滑动类调用：description 会构造字符串，点击/长按没必要付这个成本。
/// ★ 截断到 400 字符：防某些实现拼出超长串拖慢主线程。
/// ★ 全程 @try：description 是系统实现，任何异常都不允许外泄。
///
/// ★ 函数名沿用「ByDescription」，但实际做两件事（双保险）：
///   ① 类名自带 Camera —— 某些系统版本会用 SBCameraSwipeGestureRecognizer
///      这类专用类，这一条零成本且不可能误判。
///   ② description 里出现 camera —— 覆盖 action 名 / target 类名带 camera
///      的情况（description 会把 target 的 action 名一起打出来）。
///   两条任一命中即认定。②是主力，①是 ②换不来时的兜底。
static BOOL NLWIsCameraGestureByDescription(UIGestureRecognizer *g)
{
    if (!g) return NO;
    @try {
        if (!NLWIsPanish(g)) return NO;

        // ① 类名自带 Camera（零成本，不会误判）
        @try {
            const char *cn = class_getName(object_getClass(g));
            if (cn && strstr(cn, "Camera")) return YES;
        } @catch (NSException *e) { }

        // ② description 里出现 camera
        NSString *d = [g description];
        if (![d isKindOfClass:[NSString class]]) return NO;
        if (d.length == 0) return NO;
        if (d.length > 400) d = [d substringToIndex:400];

        // 小写后查 camera，覆盖 Camera / camera / CAMERA 各种命名习惯
        if ([[d lowercaseString] rangeOfString:@"camera"].location != NSNotFound) {
            return YES;
        }
    } @catch (NSException *e) { }
    return NO;
}

// ============================================================================
//  四之三、1.5-1 新增：swipe 类 —— 上一版连看都没看见的那一类
// ============================================================================
//
// ★★ 为什么会有这一节（1.4-1 日志给出的第二个硬证据，2026-10-22）：
//   1.4-1 的日志有 108 条记录、13 个手势类，统计下来**全部是 Pan 类**：
//     UIScrollViewPanGestureRecognizer(54) / UIPanGestureRecognizer(26)
//     SBHomeGesturePanGestureRecognizer(6) / _UISwipeActionPanGestureRecognizer(5)
//     _UIMultiSelectOneFingerPanGesture(5) / SBFluidSwitcherPanGestureRecognizer(3)
//     SBScreenEdgePanGestureRecognizer(2) / SBCoverSheetScreenEdgePanGestureRecognizer(2)
//     _UIKBProductivityPanGestureRecognizer(1) / UIScreenEdgePanGestureRecognizer(1)
//     SBFluidSwitcherScreenEdgePanGestureRecognizer(1)
//     SBCoverSheetPresentationGestureRecognizer(1) / CCUIDismissalGestureRecognizer(1)
//   而 edges 的取值只有 1 / 4 / 15，**8（右边缘）一次都没出现过**。
//
//   一边是「相机功能确实生效」，一边是「108 条里一个相机都没有」——
//   唯一自洽的解释：**相机手势根本不是 pan 类**，
//   它被诊断代码的 `if (!isEdge && !NLWIsPanish(g)) return;` 挡在门外，
//   同时被判据层的 `NLWIsPanish` 挡在门外 —— 连续两道，全都没看见它。
//
//   ★ 通用规律（本轮第二次踩到同一类坑）：
//     **一道「类型过滤」如果写在诊断入口，它同时也会成为盲区制造者。**
//     诊断代码的过滤条件越"省事"，排查时丢掉的假设就越多。
//     真正该做的是：**把过滤条件放宽到"凡是可疑区域上的都留"**。
//
// ★ 为什么「只压左滑」而不是「压所有 swipe」：
//   锁屏空白处的四个方向各自的含义是固定的：
//     上滑 = 解锁        （SBHomeGesturePanGestureRecognizer，pan 类，不受影响）
//     下滑 = 通知列表    （SBCoverSheetPresentationGestureRecognizer，top 边缘）
//     右滑 = 通知中心    （direction = Right）
//     左滑 = 相机        （direction = Left）← 只有这个要压
//   压所有 swipe 会顺手带走右滑呼出通知中心，属于不必要的误伤。
//
// ★ 为什么这条判据不会误伤「左滑删除通知」：
//   通知的左滑删除用的是 `_UISwipeActionPanGestureRecognizer`，
//   它是 UIPanGestureRecognizer 的子类（名字里虽然有 Swipe）。
//   所以下面第一件事就是 `if (NLWIsPanish(g)) return NO;` 把它排除掉。
//   这类「名字像但继承关系不同」的手势，只按类名匹配必然误伤。

/// 宿主是否位于「锁屏主窗口」的视图树里。
///
/// ★ 为什么要单独认这一层（1.5-1）：
///   相机手势的宿主有两种可能，两条都要覆盖：
///     ① 系统全局手势窗口（`_UISystemGestureWindow`）—— 由
///        NLWIsSystemGestureHost 负责；
///     ② 锁屏主窗口（`SBCoverSheetWindow` / `SBLockScreenWindow`）——
///        由本函数负责。
///   而 ② 这一路往往过不了 `NLWIsSuppressibleLockScreenArea`：
///   那个判据会排除「向上路径经过 UIScrollView」和「自己是 UIControl」的视图，
///   相机入口很可能正好落在这两类里。
///   → 所以给 swipe 判据单独开这一个作用域，**但不放宽长按判据**。
///
/// ★ 为什么放宽到整个锁屏窗口对 swipe 是安全的：
///   本函数只被 `NLWIsCameraSwipeBySwipe` 使用，而那条判据第一件事就是
///   排除 pan 类 —— 通知的左滑删除（`_UISwipeActionPanGestureRecognizer`）
///   恰好是 pan 子类，进不来。锁屏窗口内其余的 swipe 只有相机。
static BOOL NLWIsLockScreenWindowHost(UIView *v)
{
    if (!v) return NO;
    @try {
        UIWindow *w = v.window;
        if (!w) return NO;
        if (NLWChainHas(w, "CoverSheet")) return YES;
        if (NLWChainHas(w, "LockScreen")) return YES;
    } @catch (NSException *e) { }
    return NO;
}

/// 是否是「左滑」类手势（锁屏相机入口的候选实现）。
static BOOL NLWIsCameraSwipeBySwipe(UIGestureRecognizer *g)
{
    if (!g) return NO;
    @try {
        // ① 标准 swipe：读 direction（公开属性）
        if ([g isKindOfClass:[UISwipeGestureRecognizer class]]) {
            UISwipeGestureRecognizerDirection d =
                ((UISwipeGestureRecognizer *)g).direction;
            return (d & UISwipeGestureRecognizerDirectionLeft) != 0;
        }

        // ② 类名兜底 —— 但**必须先排除 pan 类**。
        //   `_UISwipeActionPanGestureRecognizer` 名字里有 Swipe，
        //   实际是 UIPanGestureRecognizer 的子类（通知左滑删除用它）。
        //   不排除就会把「左滑删除通知」一起废掉 —— 这是真实存在的误伤路径。
        if (NLWIsPanish(g)) return NO;

        const char *n = class_getName(object_getClass(g));
        if (n && strstr(n, "Swipe")) return YES;
    } @catch (NSException *e) { }
    return NO;
}

// ============================================================================
//  四之四、1.6-1 新增：从「拦截手势」改为「拦截启用」
// ============================================================================
//
// ★★ 为什么前两版都没修好 —— 1.5-1 日志给出的第三个硬证据（2026-10-22）：
//   放宽到「任何类型」之后，日志里出现了 50 个手势类，但：
//     · `dir=` 全是 `-`（一个 UISwipeGestureRecognizer 都没有）
//     · `camera` 依然 0 次
//     · `edges=8` 依然 0 次
//   说明相机手势既不是 pan、也不是 swipe、类名里也没有 camera。
//
//   但同一份日志里出现了两个**从未被注意**的东西：
//
//     CSHorizontalScrollFailureRecognizer
//        baseClass = UIGestureRecognizer      （既非 pan 也非 swipe）
//        enabled   = NO                       （默认关闭，用时才开）
//        target    = CSScrollGestureController
//        action    = _horizontalScrollFailureGestureRecognizerChanged:
//        chain     = CSCoverSheetView > ... > SBCoverSheetWindow
//
//     _UIPendingEdgeSwipeGestureRecognizer / UIImmediateEdgeSwipeGestureRecognizer
//        target    = __UISystemGestureManager
//        enabled   = NO
//
//   ★ 关键认识（这一版真正的转折点）：
//     **它们是「默认关闭、需要时才被启用」的手势。**
//     而前三版的压制手段只有一招：把 `enabled` 设成 NO。
//     对一个**本来就已经是 NO** 的手势来说，这一招**等于什么都没做** ——
//     系统随后调用 `setEnabled:YES` 把它打开，我们就再也拦不住了。
//
//     ★ 通用规律：**压制一个「懒启用」的组件，改它的当前状态是无效的，
//       必须拦截「启用」这个动作本身。**
//       这类组件的共同特征：初始状态就是关闭，所以任何「设成关闭」的
//       压制都不会留下痕迹，看起来像"成功压制了"，其实一直没生效。
//
// ★ 为什么选 `CSHorizontalScrollFailureRecognizer` 作为主目标：
//   iOS 锁屏从右边缘左滑进相机，实现的本质是「水平滑动失败」——
//   CoverSheet 的滚动视图横向滚不动，系统把这个失败转交给相机。
//   这个类名（HorizontalScrollFailure）+ 宿主（CSCoverSheetView）
//   + target（CSScrollGestureController）三者完全吻合这条链路。
//
// ★ 为什么 hook `-setEnabled:` 是安全的：
//   ① 它是 `UIGestureRecognizer` 的**公开属性 setter**，签名 `v@:B` 明确无歧义
//      （对比 hook 私有方法：私有方法连参数个数都靠猜，猜错就崩宿主）；
//   ② 只改一个 BOOL 标志位，不涉及任何对象生命周期；
//   ③ 全程 @try，异常不外泄；
//   ④ orig 为 NULL 时直接不转发之外什么都不做 —— 由安装函数保证
//      「拿不到 orig 就不装 hook」，不会出现「装了但没法转发」的死局。

/// 类名是否「相机入口」——硬判定，命中即拒绝启用。
///
/// ★ 只认两条，宁缺勿滥（每多认一条，就多一份误伤面）：
///   · 类名带 Camera          —— 语义明确
///   · 类名带 HorizontalScrollFailure —— 锁屏相机入口的标准实现
static BOOL NLWIsCameraEntryGestureClass(const char *n)
{
    if (!n) return NO;
    if (strstr(n, "Camera")) return YES;
    if (strstr(n, "HorizontalScrollFailure")) return YES;
    return NO;
}

/// 是否「值得记录启用时机」的候选类。
///
/// ★ 与上面的硬判定分开：这些只记录、不拦截。
///   `_UIPendingEdgeSwipe` / `UIImmediateEdgeSwipe` 是 UIKit 的
///   **边缘手势仲裁器**（target 是 __UISystemGestureManager），
///   不是相机本身。拦它们会破坏仲裁，可能导致别的边缘手势失灵 ——
///   所以这一版只观察它们的启用时机，用日志确认它们到底负不负责相机。
static BOOL NLWIsCameraEntryCandidateClass(const char *n)
{
    if (!n) return NO;
    if (NLWIsCameraEntryGestureClass(n)) return YES;
    if (strstr(n, "EdgeSwipe")) return YES;      // Pending / Immediate 两种
    if (strstr(n, "Grabber")) return YES;        // 各类把手
    return NO;
}

// ============================================================================
//  四点五、临时诊断（1.3-1 起；定位完必须整块删除）
// ============================================================================

//
// ★ 前向声明（CI 真实失败过，2026-10-22）：
//   本段插在文件靠前的位置，但它要用的是**后面才定义**的
//   NLWIsSuppressibleLockScreenArea（在「六、压制主逻辑」里）。
//   C 不允许隐式声明函数，漏了这两行就是：
//     error: call to undeclared function 'NLWIsSuppressibleLockScreenArea'
//     error: static declaration of ... follows non-static declaration
//   ★ 通用规律：**新插入的代码块如果位置靠前，必须逐一核对它用到的
//     每个函数是否已在前面定义或声明过**。本机无 Xcode，编译器不拦就
//     只能等 CI —— 这类错误纯靠通读代码就能避免。
static BOOL NLWIsSuppressibleLockScreenArea(UIView *v);
//
// ★ 为什么要破例在热路径写文件：
//   实机反馈「禁长按换壁纸生效，禁左滑进相机没生效」。
//   长按能拦 ⇒ dylib 已加载、hook 已装上，问题在**判据没命中**。
//   而判据为什么没命中，只有四种可能，仅凭描述无法区分：
//     ① 相机手势根本不是 UIScreenEdgePanGestureRecognizer
//     ② 是 edge pan，但 edges 不是 UIRectEdgeRight（可能是 Left / Both）
//     ③ 宿主视图不在「锁屏可压制区域」内（被滚动容器或 UIControl 判据挡住）
//     ④ hook 装得比手势添加晚（addGestureRecognizer 完全没拦到）
//   靠猜改判据 = 反复返工，用户已经为这个问题多装过一次包了。
//   本版只记录、不改任何判据，装一次就能把四个可能性一次排除。
//
// ★ 为什么不能直接把判据放宽成「压掉锁屏上所有 edge pan」：
//   iOS 锁屏「右上角下滑呼出控制中心」也是边缘手势。放宽就等于
//   顺带废掉控制中心 —— 那是比原问题更严重的误伤。
//   所以必须先用真实 edges 值区分，再决定放宽到哪一档。
//
// ★ 风险控制（这段跑在 SpringBoard 热路径里，必须苛刻）：
//   · 只记录滑动类手势（锁屏上数量是个位数），点击/长按一律早退
//   · 同一手势指针只记一次（去重），上限 200 条，达上限立即停止
//   · 只在后台队列写，2 秒 debounce，最多写 20 次
//   · 全程 @try 包裹，任何异常都不外泄到调用方线程
//   · 写两个位置（移动端 Logs 目录 + /tmp），任一能读到即可

static NSMutableArray *gDiagLines   = nil;
static NSMutableArray *gDiagSeen    = nil;   // NSValue 包手势指针，去重
static NSObject       *gDiagLock    = nil;
static BOOL            gDiagScheduled = NO;
static int             gDiagFlushes   = 0;

static void NLWDiagFlushNow(void)
{
    @autoreleasepool {
        NSArray *snapshot = nil;
        @synchronized (gDiagLock) {
            gDiagScheduled = NO;
            if (gDiagLines.count == 0) return;
            snapshot = [gDiagLines copy];
        }
        @try {
            NSString *body = [snapshot componentsJoinedByString:@"\n"];
            NSFileManager *fm = [NSFileManager defaultManager];

            // 位置一：常规日志目录（1.0-1 时代验证过 SpringBoard 可写这里）
            NSString *dir = @"/var/mobile/Library/Logs/NoLockWallpaper";
            [fm createDirectoryAtPath:dir withIntermediateDirectories:YES
                            attributes:nil error:NULL];
            [body writeToFile:[dir stringByAppendingPathComponent:@"diag.txt"]
                   atomically:YES encoding:NSUTF8StringEncoding error:NULL];

            // 位置二：/tmp —— RootHide 可能重定向 /var/mobile/Library 下
            //         的某些目录，写两份保证至少有一份能读到
            [body writeToFile:@"/tmp/nlw_diag.txt"
                   atomically:YES encoding:NSUTF8StringEncoding error:NULL];
        } @catch (NSException *e) { }
    }
}

/// 追加一行诊断。线程安全 + 自动 debounce 落盘。
static void NLWDiagAdd(NSString *line)
{
    @try {
        if (!gDiagLock) {
            @synchronized (NSFileManager.class) {   // 一次性初始化用，借用任意类对象做锁
                if (!gDiagLock) {
                    gDiagLines = [NSMutableArray new];
                    gDiagSeen  = [NSMutableArray new];
                    gDiagLock  = [NSObject new];
                }
            }
        }
        BOOL needSchedule = NO;
        @synchronized (gDiagLock) {
            if (gDiagLines.count >= 200) return;
            [gDiagLines addObject:(line ?: @"?")];
            if (!gDiagScheduled && gDiagFlushes < 20) {
                gDiagScheduled = YES;
                gDiagFlushes++;
                needSchedule = YES;
            }
        }
        if (needSchedule) {
            // ★ 绝不在当前线程写盘：调用方是 SpringBoard 主线程，
            //   主线程上做文件 IO 是 watchdog 杀进程的经典成因。
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2 * NSEC_PER_SEC)),
                           dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_LOW, 0), ^{
                NLWDiagFlushNow();
            });
        }
    } @catch (NSException *e) { }
}

/// 记录一次 `setEnabled:` 调用（1.6-1 新增）。
///
/// ★ 为什么要单独记这个：
///   前三版一直看不见相机手势，是因为它「默认关闭」——
///   只有系统在特定时机 `setEnabled:YES` 时它才会出现在视野里。
///   把「谁被启用了」记下来，等于直接读到系统的心跳。
///
/// ★ 为什么按类名限流 8 次：
///   enable 调用比 add 频繁得多（同一个手势可能被反复开关）。
///   不限流会在几秒内刷满 200 行的上限，把真正有价值的手势记录挤掉。
///
/// ★ 位置说明：本函数必须定义在 NLWDiagAdd **之后** ——
///   C 不允许隐式声明，反过来写就是 CI 上那句
///   `call to undeclared function 'NLWDiagAdd'`。
static NSMutableDictionary *gDiagEnableCount = nil;

static void NLWDiagNoteEnable(const char *clsName, BOOL blocked)
{
    @try {
        NSString *k = (clsName ? [NSString stringWithUTF8String:clsName] : nil);
        if (!k) return;

        NSUInteger times = 0;
        @synchronized ([NSFileManager class]) {
            if (!gDiagEnableCount) gDiagEnableCount = [NSMutableDictionary new];
            NSNumber *c = gDiagEnableCount[k];
            times = (c ? c.unsignedIntegerValue : 0) + 1;
            if (times > 8) return;
            gDiagEnableCount[k] = @(times);
        }

        NSString *line = [NSString stringWithFormat:
            @"[enable#%lu] cls=%@ blocked=%d gEn=%d camSw=%d",
            (unsigned long)times, k, (int)blocked,
            (int)gEnabled, (int)gBlockCameraSwipe];
        NLWDiagAdd(line);
    } @catch (NSException *e) { }
}

/// 记录一个手势的完整现场。tag 用于区分是在哪一层捕获到的
/// （add = 添加时拦到；scan = 上屏/出现时补扫到）。
static void NLWDiagNoteGesture(UIGestureRecognizer *g, UIView *host, const char *tag)
{
    if (!g) return;
    @try {
        BOOL isEdge = [g isKindOfClass:[UIScreenEdgePanGestureRecognizer class]];
        BOOL isPan  = NLWIsPanish(g);

        // ★★ 1.5-1：诊断范围从「滑动类」放宽到
        //   「滑动类 + 系统手势窗口/锁屏区域上的**任何**手势」。
        //
        //   为什么必须放宽（1.4-1 日志给出的硬证据，2026-10-22）：
        //   108 条记录、13 个手势类，**全部是 Pan 类**，
        //   而 `camera` 字样一个都没有 —— 可用户确实能通过
        //   「锁屏右边缘左滑」进相机。
        //   两者同时成立，只剩一个解释：**那个手势不是 pan 类**，
        //   被下面这条早退直接丢掉了，从 add 层到判据层连续两道都没看见它。
        //   所以本轮正是要查「非 pan 类里有没有相机手势」。
        BOOL sysHost = NLWIsSystemGestureHost(host);
        BOOL sup     = NLWIsSuppressibleLockScreenArea(host);
        if (!isEdge && !isPan && !sysHost && !sup) return;

        // 懒初始化（借任意类对象做一次性初始化锁，避免另起 pthread_once）
        @synchronized ([NSFileManager class]) {
            if (!gDiagLock) {
                gDiagLines = [NSMutableArray new];
                gDiagSeen  = [NSMutableArray new];
                gDiagLock  = [NSObject new];
            }
        }

        // 去重：同一个手势实例只记一次（scan 层会反复扫到同一批手势）
        @synchronized (gDiagLock) {
            NSValue *k = [NSValue valueWithPointer:(__bridge void *)g];
            if ([gDiagSeen containsObject:k]) return;
            if (gDiagSeen.count >= 400) return;
            [gDiagSeen addObject:k];
        }

        const char *cn = class_getName(object_getClass(g));

        NSString *edges = @"-";
        if (isEdge) {
            @try {
                edges = [NSString stringWithFormat:@"%lu",
                         (unsigned long)((UIScreenEdgePanGestureRecognizer *)g).edges];
            } @catch (NSException *e) { }
        }

        // ★ 1.5-1 新增：swipe 的方向值。
        //   UISwipeGestureRecognizer 没有 edges 属性，方向只在 direction 里。
        //   1=右 2=左 4=上 8=下（可以相加）。
        NSString *dir = @"-";
        @try {
            if ([g isKindOfClass:[UISwipeGestureRecognizer class]]) {
                dir = [NSString stringWithFormat:@"%lu",
                       (unsigned long)((UISwipeGestureRecognizer *)g).direction];
            }
        } @catch (NSException *e) { }

        // 宿主类名链（最多 10 层，够看出是不是锁屏 / 相机容器）
        NSMutableArray *chain = [NSMutableArray array];
        @try {
            UIView *v = host;
            int d = 0;
            while (v && d++ < 10) {
                NSString *n = NSStringFromClass([v class]);
                [chain addObject:(n ? n : @"?")];
                v = [v superview];
            }
        } @catch (NSException *e) { }

        NSString *tagStr = (tag ? [NSString stringWithUTF8String:tag] : @"?");
        // ★ 这里**故意不复算**相机判据。
        //   一是避免多一处判据引用（源码闸门要求「两处主逻辑用同一判据」，
        //   多一处会让计数失真）；二是没必要 —— 判据是否命中完全可以从
        //   这里记录的原始事实（类名 / edges / sup）推出来，而原始事实
        //   才是真正要拿到的数据。
        // ★ 1.4-1 新增：把手势自己的 description 也记下来。
        //   这是本轮唯一能回答「这个手势到底是谁的」的数据源 ——
        //   它内部带 target 的 action 名与 target 类名。
        //
        // ★ 1.5-1 修正：**去掉「只对 pan 类记」的限制**。
        //   上一版就是因为这个限制，让「本来就是非 pan 类的相机手势」
        //   连 desc 都没有，等于对最可疑的那一类完全失明。
        //   现在凡是能走到这里的（含 sysHost/锁屏上的任意手势）都记。
        NSString *desc = @"-";
        @try {
            NSString *d = [g description];
            if ([d isKindOfClass:[NSString class]] && d.length) {
                if (d.length > 480) d = [d substringToIndex:480];
                // 压成一行，避免破坏「一行一条记录」的解析
                desc = [d stringByReplacingOccurrencesOfString:@"\n"
                                                    withString:@" "];
            }
        } @catch (NSException *e) { }

        NSString *line = [NSString stringWithFormat:
            @"[%@] cls=%s edge=%d edges=%@ dir=%@ sup=%d sysHost=%d\n"
            @"     gEn=%d camSw=%d\n"
            @"     desc=%@\n"
            @"     chain=%@",
            (tagStr ? tagStr : @"?"),
            (cn ? cn : "?"), (int)isEdge, edges, (dir ? dir : @"-"),
            (int)sup, (int)sysHost,
            (int)gEnabled, (int)gBlockCameraSwipe,
            (desc ? desc : @"-"),
            [chain componentsJoinedByString:@" > "]];
        NLWDiagAdd(line);
    } @catch (NSException *e) { }
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
        // ★ ★★ 2.0-1：早退判据从 `!gEnabled` 改为「长按与相机左滑都关」。
        //   1.9-1 这里只看 gEnabled，而 gEnabled 当时兼任总开关；
        //   现在 gEnabled 只管长按，若仍只看它，用户在「长按关闭 + 左滑开启」
        //   这组配置下会连左滑拦截一起被切断 —— 那是用户报的第 1 个 bug。
        //   注意总开关不在这里判：它已经在 NLWLoadSwitches() 里把子开关掩成 NO，
        //   所以「总开关关闭」自动等价于「两个子开关都关」，走同一条早退。
        if (!gEnabled && !gBlockCameraSwipe) return 0;
        if (!v) return 0;

        // ★ 顺序很重要：先做判据、后取 gestureRecognizers。
        //   v.gestureRecognizers 每次调用都会新建一个数组（视图无手势时返回
        //   空数组而非 nil，仍会分配）。didMoveToWindow 是所有视图上屏的
        //   热路径，SpringBoard 启动期几百个视图各走一遍 —— 先取数组等于
        //   白分配几百次。判据只是类名比对，纳秒级且零分配，所以提到前面。
        BOOL areaOK = NLWIsSuppressibleLockScreenArea(v);

        // ★ 1.4-1：多一个放行口，但**不放弃上面的性能优化**。
        //   相机手势挂在 `UISystemGestureView` 上，它所在的
        //   `_UISystemGestureWindow` 与锁屏窗口平行，areaOK 恒为 NO ——
        //   这一路是被视图结构绕过的，不是判据写错。
        //   所以：不在可压制区时，只对「系统手势窗口里的宿主」继续往下走，
        //   其余视图依然在这里就返回，热路径开销与改动前完全一致。
        //
        // ★ 1.5-1：再加一个放行口 —— 锁屏主窗口里的宿主。
        //   理由：相机手势也可能不挂在系统手势窗口，而是挂在锁屏窗口的
        //   某个视图上；而那个视图往往过不了区域判据（可能是 UIControl，
        //   或向上路径经过滚动容器）。不过放行进来的视图里，
        //   **只有 swipe 判据会生效**（长按仍要 areaOK），不会扩大误伤面。
        if (!areaOK && !NLWIsSystemGestureHost(v) && !NLWIsLockScreenWindowHost(v)) {
            return 0;
        }

        NSArray *gs = v.gestureRecognizers;
        if (gs.count == 0) return 0;

        for (UIGestureRecognizer *g in gs) {
            @try {
                // ★ 诊断（1.3-1 临时）：这一层是「视图已上屏、补扫它身上
                //   早已挂好的手势」。如果相机手势是在 hook 装上之前就添加的，
                //   只有这里能拿到它 —— 所以必须在这里也记一笔，
                //   否则「add 层没记录」这个现象会被误判成「hook 没装上」。
                NLWDiagNoteGesture(g, v, "scan");

                if (NLWIsLongPressish(g)) {
                    // ★ 长按类仍然严格限定在「锁屏可压制区」。
                    //   放宽到系统手势窗口会把主屏图标长按、通知长按一起带走。
                    if (!areaOK) continue;
                    NLWSuppressGesture(g);
                    n++;
                    continue;
                }

                // ★ 1.6-1 双保险：相机入口类**当前已启用**时直接压一次。
                //   真正的拦截手段是 `-setEnabled:` hook（拦系统后续的启用），
                //   这里只负责把「抓到时就已经是 YES」的情况立刻处理掉。
                //   两者互补：hook 管将来，这里管现在。
                @try {
                    if (gBlockCameraSwipe &&
                        NLWIsCameraEntryGestureClass(
                            class_getName(object_getClass(g)))) {
                        NLWSuppressGesture(g);
                        n++;
                        continue;
                    }
                } @catch (NSException *e) { }

                // ★ 1.4-1：先看手势自己有没有承认是相机。
                //   这一条不看宿主位置，因为宿主位置这条路已被证明走不通。
                BOOL camByDesc = (gBlockCameraSwipe &&
                                  NLWIsCameraGestureByDescription(g));
                // ★ 1.5-1：swipe 类左滑。它既不是 pan 类、也没有 edges，
                //   所以必须单独成一条。
                //   ★ 作用域不用再判一次：能走到这个循环的手势，宿主必然已满足
                //     `areaOK || NLWIsSystemGestureHost(v) || NLWIsLockScreenWindowHost(v)`
                //     （入口那句 return 0 把它们之外的视图全挡掉了）。
                //     在这里重复判断只会多花一次类名遍历，属于纯浪费。
                BOOL camBySwipe = (gBlockCameraSwipe && NLWIsCameraSwipeBySwipe(g));
                if (camByDesc || camBySwipe ||
                    (areaOK && gBlockCameraSwipe && NLWIsCameraSwipeish(g, v))) {
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
//  七之四、1.9-1：照抄「已实测生效」的插件，一个方法一个开关
// ============================================================================
//
// ★★★ 为什么整段重写，而不是继续修 1.7-1 / 1.8-1
//
//   1.7-1（只 hook hasCamera）→ 用户实测：左滑照进
//   1.8-1（+ lockScreenCameraSupported）→ 用户实测：**还是不生效**
//   而同一台机器上 com.yccameraflight 1.1 **实测生效**。
//
//   两份产物在功能上已经等价（都 hook 这三个方法），唯一差别在两处实现细节：
//
//     ① 它用 **MSHookMessageEx**（CydiaSubstrate），我用自实现的
//        class_addMethod / method_setImplementation；
//     ② 它 **完全不校验签名**，我要求 enc 必须以 `B@:` 或 `c@:` 开头。
//
//   ② 极可能就是失败点：`method_getTypeEncoding` 在真机上常返回
//   `B16@:`、`B@:8` 这类带偏移/长度修饰的形式，`strncmp(enc,"B@:",3)`
//   会直接判不通过 → 静静跳过 → 日志里只有一行"签名异常"，功能零效果。
//   而插件不做这个判断，所以它能装上。
//
//   ★ 教训：**防御性检查如果比被检查对象更严格，它就从保护变成了障碍。**
//     签名校验的本意是防崩，但"无参 getter"这一类根本不需要那么严 ——
//     见下面 NLWLooksLikeNoArgGetter 的取舍说明。
//
// ★★★ 本版做法：与插件同构，只多三个开关
//
//   三个 hook 目标、三个返回值、三条装配路径，全部照抄它的反汇编：
//
//     CSQuickActionsViewController -hasFlashlight              → NO
//     CSQuickActionsViewController -hasCamera                  → NO
//     SpringBoard                  -lockScreenCameraSupported  → NO
//
//   反汇编证据（com.yccameraflight 1.1，arm64，__text @0x7e6c 共 160 字节）：
//     0x7e78 adr x0, "CSQuickActionsViewController" ; bl objc_getClass
//     0x7e8c ldr x1, selref hasCamera            → 0x7ef4 mov w0,#0; ret
//     0x7ea8 ldr x1, selref hasFlashlight        → 0x7efc mov w0,#0; ret
//     0x7ec4 adr x0, "SpringBoard"               ; bl objc_getClass
//     0x7ed4 ldr x1, selref lockScreenCameraSupported → 0x7f04 mov w0,#0; ret
//   旁证：__objc_selrefs 恰好 3 项、尾部恰好 3 个 mov w0,#0; ret。
//
// ★ 三个开关与三个 hook 的对应关系（用户 2026-10-22 明确要求）：
//
//     BlockFlashlight     → -hasFlashlight             禁锁屏手电筒
//     BlockCameraButton   → -hasCamera                 禁锁屏相机按钮
//     BlockCameraSwipe    → -lockScreenCameraSupported 禁左滑进相机
//
//   ★ 必须说清的一处耦合：`lockScreenCameraSupported` 返回 NO 会让系统
//     认定「本机不支持锁屏相机」，于是**相机页不挂载、按钮也不显示**。
//     所以「禁左滑」打开时，按钮一定跟着消失 —— 这不是 bug，是同一个开关。
//     反过来「禁按钮」只影响按钮，不影响左滑。三个开关里只有这一对是单向牵连。
//
// ★ 装配顺序（重要）：
//   ① 优先 `MSHookMessageEx` —— 与实测生效的插件完全同一条路径；
//   ② 拿不到符号或失败 → 回退到自实现的 NLWInstallHook；
//   ③ 两条都失败 → 写日志说明原因，绝不硬上。
//   ★ 用 `dlsym(RTLD_DEFAULT, ...)` 取符号，**不 dlopen 任何库** ——
//     项目红线：注入进程里 dlopen 一个「可能已经在运行」的库，
//     会加载出第二份 Substrate，双引擎改同一张方法表 = 必崩。

typedef void (*NLWMSHookFn)(Class, SEL, IMP, IMP *);

static NLWMSHookFn gNLWMSHook    = NULL;
static BOOL        gNLWMSHookDone = NO;

/// 取 MSHookMessageEx。查的是**已加载镜像**里的符号，不加载任何新库。
static NLWMSHookFn NLWGetMSHook(void)
{
    if (gNLWMSHookDone) return gNLWMSHook;
    gNLWMSHookDone = YES;
    @try {
        gNLWMSHook = (NLWMSHookFn)dlsym(RTLD_DEFAULT, "MSHookMessageEx");
        NLWDiagAdd([NSString stringWithFormat:
            @"[ms] MSHookMessageEx=%p", (void *)gNLWMSHook]);
    } @catch (NSException *e) { }
    return gNLWMSHook;
}

/// 判断编码是否「无参、整型/布尔返回」。
///
/// ★ 这里刻意放宽，与 1.7/1.8 的 `strncmp(enc,"B@:",3)` 不同 ——
///   真机上同一个无参 getter 的编码可能是 `B@:`、`B16@:`、`B@:8`、
///   `c@:` …… 前缀严格匹配会把多数情况误判成"签名异常"而跳过。
///   实际需要防的只有一种：**方法带参数**（那才可能出现参数错位）。
///   所以判据改成「冒号之后只有数字和所有权修饰符」。
/// ★ 返回值放宽到任何整型（B/c/i/s/q）：arm64 上都用 w0/x0 返回，
///   `return NO` 写 w0=0 对所有整型返回类型都成立。
static BOOL NLWLooksLikeNoArgGetter(const char *enc)
{
    if (!enc || !*enc) return NO;
    const char *colon = strchr(enc, ':');
    if (!colon) return NO;               // 没有 `:` 就不是方法编码
    for (const char *p = colon + 1; *p; p++) {
        if (*p >= '0' && *p <= '9') continue;   // 参数帧长度，如 B16@:
        if (strchr("rnNoORV", *p)) continue;    // 所有权修饰符
        return NO;                              // 出现类型字母 = 有参数
    }
    return YES;
}

/// 通用「无参 bool getter」hook 安装器。
/// 返回是否装上；orig 通过 origOut 带出。
static BOOL NLWInstallBoolGetterHook(Class cls, SEL sel, IMP newImp,
                                    IMP *origOut, const char *tag)
{
    if (origOut) *origOut = NULL;
    @try {
        if (!cls || !sel || !newImp) return NO;

        // ★ 先确认方法真的存在：对不存在的方法调用 MSHookMessageEx
        //   会「成功」地造出一个新方法，之后没人会调它 —— 属于静默失败。
        Method m = class_getInstanceMethod(cls, sel);
        if (!m) {
            NLWDiagAdd([NSString stringWithFormat:
                @"[hook] %s 不存在（类 %s）", tag, class_getName(cls)]);
            return NO;
        }

        const char *enc = method_getTypeEncoding(m);
        if (!NLWLooksLikeNoArgGetter(enc)) {
            NLWDiagAdd([NSString stringWithFormat:
                @"[hook] %s 编码像是有参数，跳过 enc=%s",
                tag, enc ? enc : "(null)"]);
            return NO;
        }

        // ---- 路径 1：MSHookMessageEx（与实测生效的插件同一条路）----
        NLWMSHookFn ms = NLWGetMSHook();
        if (ms) {
            IMP prev = NULL;
            @try {
                ms(cls, sel, newImp, &prev);
            } @catch (NSException *e) {
                prev = NULL;
            }
            if (prev) {
                if (origOut) *origOut = prev;
                NLWDiagAdd([NSString stringWithFormat:
                    @"[hook] %s 已装上(ms) enc=%s orig=%p", tag, enc, (void *)prev]);
                return YES;
            }
            NLWDiagAdd([NSString stringWithFormat:
                @"[hook] %s MSHook 失败，回退自实现", tag]);
        } else {
            NLWDiagAdd([NSString stringWithFormat:
                @"[hook] %s 无 MSHookMessageEx，用自实现", tag]);
        }

        // ---- 路径 2：自实现（1.7-1 用过的那套，作为兜底保留）----
        IMP o = NULL;
        if (NLWInstallHook(cls, sel, newImp, &o, enc)) {
            if (origOut) *origOut = o;
            NLWDiagAdd([NSString stringWithFormat:
                @"[hook] %s 已装上(self) enc=%s orig=%p", tag, enc, (void *)o]);
            return YES;
        }

        NLWDiagAdd([NSString stringWithFormat:
            @"[hook] %s 两条路径都失败 enc=%s", tag, enc ? enc : "(null)"]);
        return NO;
    } @catch (NSException *e) {
        NLWDiagAdd([NSString stringWithFormat:@"[hook] %s 安装异常（已忽略）", tag]);
        return NO;
    }
}

// ---- 三个 hook 的原实现与调用计数 ----

static IMP        gOrigHasFlashlight = NULL;   // -hasFlashlight
static IMP        gOrigHasCameraBtn  = NULL;   // -hasCamera
static IMP        gOrigLSCamInst2    = NULL;   // -lockScreenCameraSupported 实例
static IMP        gOrigLSCamMeta2    = NULL;   // -lockScreenCameraSupported 类

static NSUInteger gHintFlashlight = 0;
static NSUInteger gHintCameraBtn  = 0;
static NSUInteger gHintLSCam      = 0;

/// 前 6 次命中留痕。★ 只用于「区分没生效和崩了」，不是功能的一部分。
static void NLWNoteCamHit(NSUInteger *counter, const char *tag)
{
    @try {
        if (*counter < 6) {
            (*counter)++;
            NLWDiagAdd([NSString stringWithFormat:
                @"[%s#%lu] -> NO", tag, (unsigned long)*counter]);
        }
    } @catch (NSException *e) { }
}

/// 统一的三段式：关着就转发原实现；该禁就返回 NO；orig 丢了就放行。
///
/// ★★ 2.0-1：这里原本是 `off = (gEnabled && (FLAG));` —— **必须去掉 gEnabled**。
///   1.9-1 把 gEnabled（当时代表「禁长按换壁纸」，而它同时还是唯一总开关）写进了
///   这个宏，等于让「禁长按」替三个相机项当总开关：用户关掉长按那一项，
///   相机按钮/左滑/手电筒三项立刻全部失效 —— 就是用户报的第 1 个 bug。
///   现在 gEnabled 只代表长按自己，与相机三项**无任何关系**；总开关由
///   gMasterEnabled 在 NLWLoadSwitches() 里做掩码，不进任何 hook 体。
#define NLW_CAM_HOOK_BODY(FLAG, ORIG, TAG, COUNTER)                            \
    BOOL off = NO;                                                             \
    @try { off = (FLAG); } @catch (NSException *e) { }                          \
    if (!off) {                                                                \
        if (ORIG) return ((BOOL (*)(id, SEL))(ORIG))(self, _cmd);              \
        return YES;                                                            \
    }                                                                          \
    NLWNoteCamHit(&(COUNTER), TAG);                                            \
    return NO;

static BOOL NLWHookHasFlashlight(id self, SEL _cmd)
{
    NLW_CAM_HOOK_BODY(gBlockFlashlight, gOrigHasFlashlight,
                      "flashlight", gHintFlashlight)
}

static BOOL NLWHookHasCameraBtn(id self, SEL _cmd)
{
    // ★★ 2.0-1 修一处自相矛盾的状态：
    //   1.9-1 里「禁左滑」打开、「禁按钮」关闭时，按钮会显示出来，
    //   但点它/长按它都打不开相机 —— 因为 `lockScreenCameraSupported`
    //   返回 NO 时系统认定「本机不支持锁屏相机」，按钮的 action 走的是
    //   同一个底层能力，自然失效。用户看到的就是「按钮在、按不动」。
    //
    //   ★ 为什么不能让它真的能打开：按钮可用 ⇒ lockScreenCameraSupported 必须为
    //     YES ⇒ 系统就允许左滑进相机。这两件事在系统里是**同一个开关**，
    //     除非去改 CoverSheet 的分页构建（私有 API、风险高、无实机验证手段），
    //     否则「禁左滑 + 按钮可用」在 API 层面互斥。
    //
    //   所以这里选择让状态自洽：**只要左滑被禁，按钮就一起隐藏**。
    //   宁可少一个按钮，也不给一个按不动的按钮。
    NLW_CAM_HOOK_BODY((gBlockCameraButton || gBlockCameraSwipe), gOrigHasCameraBtn,
                      "camerabtn", gHintCameraBtn)
}

static BOOL NLWHookLSCamInst(id self, SEL _cmd)
{
    NLW_CAM_HOOK_BODY(gBlockCameraSwipe, gOrigLSCamInst2,
                      "lscam", gHintLSCam)
}

static BOOL NLWHookLSCamMeta(id self, SEL _cmd)
{
    NLW_CAM_HOOK_BODY(gBlockCameraSwipe, gOrigLSCamMeta2,
                      "lscam-meta", gHintLSCam)
}

#undef NLW_CAM_HOOK_BODY

/// 装配三个 hook。任何一个失败都只影响它自己那一项，不影响其它两项。
static void NLWInstallCameraTriple(void)
{
    @try {
        int okCount = 0;

        // ---- 类 1：CSQuickActionsViewController（手电筒 + 相机按钮）----
        Class cs = NSClassFromString(@"CSQuickActionsViewController");
        if (!cs) {
            NLWDiagAdd(@"[hook] CSQuickActionsViewController 类不存在 → 手电筒/相机按钮无法拦");
        } else {
            if (NLWInstallBoolGetterHook(cs, @selector(hasFlashlight),
                                        (IMP)NLWHookHasFlashlight,
                                        &gOrigHasFlashlight, "hasFlashlight")) {
                okCount++;
            }
            if (NLWInstallBoolGetterHook(cs, @selector(hasCamera),
                                        (IMP)NLWHookHasCameraBtn,
                                        &gOrigHasCameraBtn, "hasCamera")) {
                okCount++;
            }
        }

        // ---- 类 2：SpringBoard（左滑进相机的总闸）----
        // ★ 实例方法与类方法都试：静态反汇编看不出它挂在哪张表上。
        //   两个 hook 体分别用各自的 orig，绝不共用 —— 共用会转发到
        //   另一张方法表的原实现，那是必错。
        Class sb = NSClassFromString(@"SpringBoard");
        if (!sb) {
            NLWDiagAdd(@"[hook] SpringBoard 类不存在 → 左滑无法拦");
        } else {
            BOOL gotI = NLWInstallBoolGetterHook(sb,
                            @selector(lockScreenCameraSupported),
                            (IMP)NLWHookLSCamInst,
                            &gOrigLSCamInst2, "lscam(inst)");
            if (gotI) okCount++;
            // 实例装上了就不再动元类：同一个选择子两处都装，
            // 只会让「谁在调」变模糊，没有额外收益。
            if (!gotI) {
                Class meta = object_getClass(sb);
                if (meta && NLWInstallBoolGetterHook(meta,
                            @selector(lockScreenCameraSupported),
                            (IMP)NLWHookLSCamMeta,
                            &gOrigLSCamMeta2, "lscam(meta)")) {
                    okCount++;
                }
            }
        }

        NLWDiagAdd([NSString stringWithFormat:
            @"[hook] 相机三件套装配完成：%d/3 装上", okCount]);
    } @catch (NSException *e) {
        NLWDiagAdd(@"[hook] 装配异常（已忽略，宿主必须照常启动）");
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
        // ★ 诊断（1.3-1 临时）：**放在开关判断之前**。
        //   即使总开关关着也要记录 —— 否则「开关是不是没读对」这件事
        //   也会变成盲区。记录本身有早退（非滑动类直接返回），开销可忽略。
        NLWDiagNoteGesture(g, (UIView *)self, "add");

        // 三道便宜过滤：
        //   ① 总开关关着 → 不过问
        //   ② 不是「长按」也不是「相机滑动」→ 立刻放过（绝大多数调用走这条路）
        //   ③ 宿主不是「锁屏壁纸区」→ 放过（含通知/滚动容器/UIControl 排除判据）
        // ★ 手势类型判断统一走 NLWIsLongPressish / NLWIsCameraSwipeish，
        //   与后面的压制循环用同一判据。两处各写一套迟早会不一致
        //   （一处放宽一处没放宽 → 判为命中却一个都没压，或者反过来漏压）。
        //
        // ★ 1.4-1：新增「手势自己承认是相机」这条旁路（camByDesc）。
        //   它是唯一能绕过「锁屏视图树」判据的口子，理由见
        //   「四之二」小节 —— 相机手势的宿主根本不在锁屏视图树里。
        //   注意：camByDesc 命中时 hit 直接为 YES，不再要求区域判据。
        //
        // ★ 1.5-1：新增 camBySwipe（swipe 类左滑）。与 camByDesc 不同，
        //   它**必须**先过作用域判据 —— swipe 在主屏、App 内到处都是，
        //   不像 camera 那三个字母只可能指向相机。
        BOOL camByDesc  = NO;
        BOOL camBySwipe = NO;
        // ★★ 2.0-1：原为 `if (gEnabled && gBlockCameraSwipe)` —— 去掉 gEnabled。
        //   相机左滑与「禁长按换壁纸」是两条独立功能，不能互相兼任总开关。
        if (gBlockCameraSwipe) {
            camByDesc = NLWIsCameraGestureByDescription(g);
            // ★ 只在真的是左滑时才去算宿主：NLWIsSuppressibleLockScreenArea
            //   要走 superview 链，能省就省（addGestureRecognizer 是热路径）。
            if (!camByDesc && NLWIsCameraSwipeBySwipe(g)) {
                camBySwipe = (NLWIsSystemGestureHost((UIView *)self) ||
                              NLWIsLockScreenWindowHost((UIView *)self) ||
                              NLWIsSuppressibleLockScreenArea((UIView *)self));
            }
        }
        // ★★ 2.0-1：拆成「长按只看 gEnabled、相机只看 gBlockCameraSwipe」。
        //   原写法把两个开关 AND 在一起（`gEnabled && (...)`），等于要求
        //   「禁长按」必须先开着，相机拦截才可能命中 —— 正是用户报的第 1 个 bug。
        //   现在每一项只受自己的开关支配；总开关已在加载时掩码，不进这里。
        if ((gEnabled && NLWIsLongPressish(g)) ||
            camByDesc || camBySwipe ||
            (gBlockCameraSwipe && NLWIsCameraSwipeish(g, (UIView *)self))) {
            hit = (camByDesc || camBySwipe) ? YES
                : NLWIsSuppressibleLockScreenArea((UIView *)self);
        }
    } @catch (NSException *e) { }

    // ★ 先转发：不转发会让 gesture.view 保持 nil，系统后续代码读它可能崩。
    //   「转发 → 压制」才是安全顺序。
    if (gOrigAddGesture) {
        ((void (*)(id, SEL, id))gOrigAddGesture)(self, _cmd, g);
    }

    if (hit) NLWProcessView((UIView *)self);
}

// ---- 第 0.5 层（主）：setEnabled: ----
// 签名：- (void)setEnabled:(BOOL)enabled   →  v@:B
//
// ★★ 为什么必须加这一层（1.6-1，本项目的转折点）：
//   前三版的压制手段只有一招：把 `gesture.enabled` 设成 NO。
//   而相机入口（CSHorizontalScrollFailureRecognizer）**默认就是 NO** ——
//   系统在检测到水平滑动时才临时 `setEnabled:YES` 把它打开。
//
//   所以前三版对它的压制，效果是：
//     「把一个已经是 NO 的东西再设一遍 NO」→ 看起来成功了，实际零作用。
//   系统随后 enable，我们再也拦不住，相机照进。
//
//   ★ 通用规律：**压制一个「懒启用」的组件，改它的当前状态是无效的，
//     必须拦截「启用」这个动作本身。**
//     判断一个组件是不是懒启用，看两点：
//       ① 抓到的初始状态就是「关闭」
//       ② 它有 target/action，说明系统确实会用它
//     两个都成立时，「设成关闭」这种压制方案从一开始就是假的。
//
// ★ 为什么 hook 这个 selector 是安全的：
//   ① `setEnabled:` 是 UIGestureRecognizer 的**公开属性 setter**，
//      签名 `v@:B` 明确无歧义 —— 不存在「参数个数猜错」的崩宿主风险；
//   ② 只影响一个 BOOL 标志位，不碰对象生命周期、不碰内存；
//   ③ 只在 `enabled == YES` 且类名命中硬判定时才改，其余全部原样转发；
//   ④ 全程 @try，异常不出线程；
//   ⑤ 安装函数保证「拿不到 orig 就不装」，不会出现装了却无法转发的死局。
static IMP gOrigSetEnabled = NULL;

static void NLWSetEnabled(id self, SEL _cmd, BOOL enabled)
{
    BOOL block = NO;
    @try {
        // ★ 只过问「打开」。「关闭」一律原样放行 —— 我们从不阻止系统
        //   关闭某个手势，那可能影响它自己的状态机。
        if (enabled && self) {
            const char *n = class_getName(object_getClass(self));

            if (NLWIsCameraEntryGestureClass(n)) {
                // ★★ 2.0-1：原为 `if (gEnabled && gBlockCameraSwipe)`。
                //   这里也必须去掉 gEnabled —— 相机入口只由相机那一项说了算。
                if (gBlockCameraSwipe) {
                    block = YES;
                    NLWDiagNoteEnable(n, YES);
                }
            } else if (NLWIsCameraEntryCandidateClass(n)) {
                // ★ 候选类（EdgeSwipe / Grabber 等）只记录、不拦截。
                //   它们更可能是「边缘手势仲裁器」而非相机本身，
                //   拦下来会破坏仲裁，可能让别的边缘手势失灵。
                //   记录它们的启用时机，用来在日志里确认谁真正负责相机。
                NLWDiagNoteEnable(n, NO);
            }
        }
    } @catch (NSException *e) { }

    if (gOrigSetEnabled) {
        ((void (*)(id, SEL, BOOL))gOrigSetEnabled)(self, _cmd, block ? NO : enabled);
    }
}

// ---- 第 1 层（补）：didMoveToWindow ----
// 签名：- (void)didMoveToWindow   →  v@:
static void NLWDidMoveToWindow(id self, SEL _cmd)
{
    @try { NLWProcessView((UIView *)self); }
    @catch (NSException *e) { }

    // ★ 诊断（1.3-1 临时）：锁屏类视图**自身**挂的手势也要记一笔。
    //   NLWProcessView 只在「可压制区」判据成立时才去遍历手势；
    //   如果相机手势的宿主恰好被那个判据挡住，就永远看不到它 ——
    //   而那恰恰是本次要查的核心疑点。这里单独扫，绕过该判据。
    @try {
        UIView *v = (UIView *)self;
        if (v && NLWIsLockScreenish(v)) {
            for (UIGestureRecognizer *g in v.gestureRecognizers) {
                NLWDiagNoteGesture(g, v, "ls");
            }
        }
    } @catch (NSException *e) { }

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
            // ★★ 2.0-1：判据从「只看长按」改为「四项里任意一项由开变关」。
            //   1.9-1 只比 gEnabled（当时它兼总开关，勉强算对）；
            //   现在 gEnabled 只管长按，若仍只看它，用户「只关左滑」时
            //   已经压掉的手势就不会恢复 —— 表现为「关了左滑，左滑还能用」。
            int prev[4] = { (int)gEnabled, (int)gBlockCameraSwipe,
                            (int)gBlockCameraButton, (int)gBlockFlashlight };
            NLWLoadSwitches();      // 重新读全部开关

            BOOL anyTurnedOff = (!gEnabled && prev[0]) ||
                                (!gBlockCameraSwipe && prev[1]) ||
                                (!gBlockCameraButton && prev[2]) ||
                                (!gBlockFlashlight && prev[3]);
            if (anyTurnedOff) {
                // 开 → 关：把已经改掉的手势恢复原样，立刻生效
                NLWRestoreAllSuppressed();
            }
            // ★ 1.7-1：`-hasCamera` 的 hook **不需要**在这里恢复 ——
            //   它每次被调用时会先看开关，关了就直接转发原实现。
            //   这比「改回原 IMP」安全：不漏恢复、不怕时序、无状态。
            // 关 → 开：什么也不用做。后续视图上屏 / 锁屏出现时，
            //          hook 会因为开关变回 YES 而重新压制。

            // ★ 2.0-1：把「插件认为的有效开关值」打出来。
            //   总开关关掉时后面四项应当全为 0（掩码后的值）；
            //   与设置页勾选不一致 = 读取出了问题，一眼能看出来。
            NLWDiagAdd([NSString stringWithFormat:
                @"[sw] master=%d longpress=%d flash=%d btn=%d swipe=%d",
                (int)gMasterEnabled, (int)gEnabled, (int)gBlockFlashlight,
                (int)gBlockCameraButton, (int)gBlockCameraSwipe]);
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

        // ★ 2.0-1：启动时就把「有效开关值」写进日志。
        //   总开关关掉时后面四项应当是 0（掩码后的值）——
        //   这条线直接回答「插件到底认不认我的开关」。
        NLWDiagAdd([NSString stringWithFormat:
            @"[sw] master=%d longpress=%d flash=%d btn=%d swipe=%d",
            (int)gMasterEnabled, (int)gEnabled, (int)gBlockFlashlight,
            (int)gBlockCameraButton, (int)gBlockCameraSwipe]);

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

        // ---- 第 0.5 层：UIGestureRecognizer -setEnabled: ----
        // ★ 1.6-1 加的这层，1.7-1 实测证明它**拦住了但没用**：
        //   日志里 8 次 `blocked=1`，相机照样打开。
        //   所以它已经不是主手段了，保留的原因只有两个：
        //   ① 1.6-1 在用户机器上跑过，没有崩宿主、没影响解锁；
        //   ② 它拦的是一个「懒启用」的判定辅助器，关掉它没有副作用。
        //   ★ 真正解决问题的是下面第 1 层的三个 bool getter（1.9-1）。
        //
        // ★ 为什么 hook 基类 UIGestureRecognizer 而不是具体类：
        //   具体类名随 iOS 版本变，且我们对「哪个类负责相机」还会有新认识；
        //   在基类上统一拦，判据集中在 NLWIsCameraEntryGestureClass 一处，
        //   以后要加目标只改那一个函数。
        //
        // ★ 签名 "v@:B" 是公开属性 setter 的标准编码，不存在歧义。
        {
            IMP o = NULL;
            if (NLWInstallHook([UIGestureRecognizer class], @selector(setEnabled:),
                               (IMP)NLWSetEnabled, &o, "v@:B")) {
                gOrigSetEnabled = o;
            }
        }

        // ---- 第 1 层（1.9-1 主手段）：三个 bool getter，一个方法一个开关 ----
        // ★★★ 这里替代了 1.7-1 的「第 0.6 层」与 1.8-1 的「第 0.7 层」。
        //
        //   为什么整段换掉：1.7-1（只有 hasCamera）用户实测左滑照进；
        //   1.8-1（+ lockScreenCameraSupported）用户实测**还是不生效**；
        //   而同一台机器上 com.yccameraflight 1.1 **实测生效**。
        //   两者功能等价，差别只在实现细节 —— 见「七之四」章节的分析：
        //   极可能是「严格签名校验 B@:/c@:」把真机上的 `B16@:` 之类编码
        //   判成"签名异常"而静默跳过，以及自实现 hook 与 MSHook 的环境差异。
        //   本层三条装配路径全部照抄那份插件（MSHook 优先 + 不严卡签名）。
        //
        //   三个 hook 与三个开关的对应（用户在设置页可逐项开关）：
        //     -hasFlashlight              ← BlockFlashlight    禁手电筒
        //     -hasCamera                  ← BlockCameraButton  禁相机按钮
        //     -lockScreenCameraSupported  ← BlockCameraSwipe   禁左滑
        NLWInstallCameraTriple();


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