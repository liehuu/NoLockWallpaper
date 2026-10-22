//
//  NoLockWallpaperRootListController.m
//
//  ============================================================================
//  这个文件只做三件事，其余一概不做
//  ============================================================================
//   ① 提供 specifiers（必须自己实现，父类没有默认实现）
//   ② 两个开关的读写交给 Preferences 框架（PSSwitchCell + defaults 域）
//   ③ 一个「重启桌面」按钮：打开方向需要它
//
//  ============================================================================
//  ★★ 本项目踩过的 Preferences 坑（每条都对应一次真实失败或真实风险）
//  ============================================================================
//   1. 绝不引用 UIColorWell 类符号
//      iOS 13 的设置 App 里没有这个类，一旦符号被绑定，设置 App 直接打不开。
//      本文件不含任何颜色选择器，也不需要颜色。
//
//   2. 写配置走 CFPreferences + Synchronize，不要直接写 plist 文件
//      直接写文件会被 cfprefsd 的缓存覆盖回去，表现为「设置改了又变回去」。
//      本文件的开关由 PSSwitchCell 自己走框架写，不需要我们手写。
//
//   3. reloadSpecifier: 传 nil 会崩
//      必须传具体 specifier 实例。本文件的重载只用 reloadSpecifiers（无参版）。
//
//   4. **必须自己实现 specifiers**
//      PSListController 没有「自动从 Root.plist 载入」的默认实现，
//      不写这个方法就是一张空表（用户看到「整块空白」）。
//
//   5. setProperty:forKey: 与 NSStream 的同名方法冲突
//      clang 会硬报 "multiple methods named ... found with mismatched result"。
//      本文件不调用它 —— 需要的属性全部写在 Root.plist 里。
//
//   6. 图标要 icon.png 基础名（@2x/@3x 由系统自动选）
//
//   7. ★★★ `_specifiers` 是父类的 ivar，**绝不自己声明**
//      自己声明只能骗过编译器，骗不过运行时：ivar 的访问偏移是编译期
//      按声明布局算的，与真实 PSListController 差一个字段，就会读到错误的
//      内存位置 —— 那不是「编译不过」，是**运行期崩设置 App**。
//      正解：用 objc runtime 按名字取偏移（见下面的 NLWSpecifiersOffset）。
//

#import "NoLockWallpaperRootListController.h"
#import <objc/runtime.h>
#import <sys/sysctl.h>
#import <signal.h>
#import <string.h>

// ★ 这里刻意**不**声明配置域常量。
//   开关的读写由 PSSwitchCell 自己走 Preferences 框架完成（按 Root.plist 的
//   defaults + key），本文件不需要直接碰 CFPreferences。
//   声明一个用不到的常量只会招来 -Wunused-const-variable 警告。
//   域/键/通知名三者的对齐由 tools/check_source.py 的 A7b 静态校验，
//   不靠这里的重复声明。

@implementation NoLockWallpaperRootListController

#pragma mark - _specifiers ivar（按名字取偏移，绝不自己声明）

/// 取父类 PSListController 的 `_specifiers` ivar 偏移（字节）。
///
/// ★ 返回 0 表示「取不到」，调用方必须退化成「不缓存、每次重新载入」——
///   慢一点但绝不崩。**不能把 0 当成合法偏移去读写**（那就是读 self->isa）。
static ptrdiff_t NLWSpecifiersOffset(BOOL *found)
{
    static ptrdiff_t off = 0;
    static BOOL resolved = NO;
    static BOOL ok = NO;
    if (!resolved) {
        resolved = YES;
        @try {
            Ivar iv = class_getInstanceVariable([PSListController class],
                                                "_specifiers");
            if (iv) {
                off = ivar_getOffset(iv);
                ok  = (off > 0);        // 偏移 0 不可能（那是 isa）
            }
        } @catch (NSException *e) { ok = NO; }
    }
    if (found) *found = ok;
    return ok ? off : 0;
}

static NSArray *NLWGetSpecifiers(id self)
{
    BOOL ok = NO;
    ptrdiff_t off = NLWSpecifiersOffset(&ok);
    if (!ok) return nil;
    @try {
        return *(NSArray **)((char *)(__bridge void *)self + off);
    } @catch (NSException *e) {
        return nil;
    }
}

static void NLWSetSpecifiers(id self, NSArray *v)
{
    BOOL ok = NO;
    ptrdiff_t off = NLWSpecifiersOffset(&ok);
    if (!ok) return;
    @try {
        *(NSArray **)((char *)(__bridge void *)self + off) = v;
    } @catch (NSException *e) { }
}

#pragma mark - specifiers

/// ★ 必须自己实现：父类没有默认实现，不写就是空表（用户看到整块空白）。
- (NSArray *)specifiers
{
    // 有缓存就直接用（框架切换页面时不会重复载入）
    NSArray *cached = NLWGetSpecifiers(self);
    if (cached) return cached;

    NSArray *specs = nil;
    @try {
        specs = [self loadSpecifiersFromPlistName:@"Root" target:self];
    } @catch (NSException *e) {
        specs = nil;
    }
    if (!specs) specs = @[];

    // 偏移取不到时 NLWSetSpecifiers 是空操作 → 每次都重新载入，
    // 只是慢一点，不会崩。
    NLWSetSpecifiers(self, specs);
    return specs;
}

#pragma mark - 重启桌面

/// 「重启桌面以生效」按钮。
///
/// ★ 为什么需要它（而不是让开关完全即时生效）：
///   关闭方向可以即时生效 —— 插件收到通知后把已压制的手势恢复原状
///   （见 Tweak.m 的 NLWRestoreAllSuppressed）。
///   但打开方向无法即时生效：锁屏此时已经建好了，插件只能等手势下次
///   被添加/上屏时才去压制。与其让用户猜「为什么打开了没反应」，
///   不如给一个明确按钮。
///
/// ★ 实现方式沿用本项目**已实机验证过**的做法（SingleVPN 设置页同款）：
///   sysctl 枚举进程 → 按可执行文件名匹配 → SIGTERM。
///   不用 "killall" 命令行（rootless 下 /usr/bin/killall 未必存在），
///   也不用 notify_post 某个自定义通知名（名字对不对没有证据，
///   猜错就是「点了没反应」）。
- (void)respring
{
    @try {
        int argmax = 0;
        size_t sz = sizeof(argmax);
        if (sysctl((int[]){ CTL_KERN, KERN_ARGMAX }, 2, &argmax, &sz, NULL, 0) < 0) {
            argmax = 4096;
        }

        size_t len = 0;
        if (sysctl((int[]){ CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0 }, 3,
                   NULL, &len, NULL, 0) < 0) {
            return;
        }

        struct kinfo_proc *procs = (struct kinfo_proc *)malloc(len + 1);
        if (!procs) return;
        memset(procs, 0, len + 1);

        if (sysctl((int[]){ CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0 }, 3,
                   procs, &len, NULL, 0) < 0) {
            free(procs);
            return;
        }

        char *args = (char *)malloc((size_t)argmax + 1);
        if (!args) { free(procs); return; }

        int cnt = (int)(len / sizeof(struct kinfo_proc));
        for (int i = 0; i < cnt; i++) {
            pid_t pid = procs[i].kp_proc.p_pid;
            if (pid <= 1) continue;

            size_t asz = (size_t)argmax;
            if (sysctl((int[]){ CTL_KERN, KERN_PROCARGS2, pid, 0 }, 3,
                       NULL, &asz, NULL, 0) < 0) continue;

            memset(args, 0, (size_t)argmax + 1);
            if (sysctl((int[]){ CTL_KERN, KERN_PROCARGS2, pid, 0 }, 3,
                       args, &asz, NULL, 0) < 0) continue;

            // args 前 4 字节是 argc，其后紧跟可执行文件路径
            const char *execPath = args + sizeof(int);
            const char *base = strrchr(execPath, '/');
            base = base ? base + 1 : execPath;

            if (strcmp(base, "SpringBoard") == 0) {
                // 软杀：让 SpringBoard 有机会正常退出，比 SIGKILL 干净
                kill(pid, SIGTERM);
            }
        }

        free(args);
        free(procs);
    } @catch (NSException *e) { }
}

#pragma mark - 生命周期

- (void)viewDidLoad
{
    [super viewDidLoad];
    // 标题兜底：Root.plist 里已设 title，这里再设一次，
    // 防止个别版本不读 plist 的 title。
    @try {
        self.title = @"NoLockWallpaper";
    } @catch (NSException *e) { }
}

@end