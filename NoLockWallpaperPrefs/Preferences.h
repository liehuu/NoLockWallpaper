//
//  Preferences.h —— 最小化的 Preferences 私有框架声明（本项目自用）
//
//  ★★ 为什么自己写，而不是 #import <Preferences/PSListController.h>：
//    本项目走「零 Theos」构建（用户明确要求不装 Theos，构建时间 680s → 24s）。
//    没有 Theos 就没有 theos/include/Preferences 那套头文件，
//    而设置 App 的 Preferences.framework 是不带公开头文件的私有框架。
//
//  ★★★ 为什么这里**故意不声明 _specifiers 这个 ivar**（极其关键）：
//    在 C/ObjC 里，ivar 的访问偏移是**编译期**按声明的布局算出来的。
//    自己声明一份 ivar 列表，只要与真实 PSListController 的布局差一个字段，
//    编译出来的代码就会去读错误的内存位置 —— 那不是「编译不过」，
//    而是**运行期读到垃圾指针 → 崩设置 App**。这类 bug 极难定位。
//    正解：ivar 一律通过 objc runtime 按名字取（见 .m 里的 NLWSpecifiersIvar），
//    偏移由运行时自己算，永远和真实布局一致。
//
//    因此本头文件只声明「类名 + 方法签名」这些不影响内存布局的东西。
//    方法调用走 objc_msgSend，selector 名对就行，与编译期布局无关。
//

#ifndef NLW_PREFS_MIN_H
#define NLW_PREFS_MIN_H

#import <UIKit/UIKit.h>
#import <Foundation/Foundation.h>

// ---- PSSpecifier ----
// 只需要读属性字典。真实类在 Preferences.framework 里。
@interface PSSpecifier : NSObject
- (id)propertyForKey:(NSString *)key;
- (void)setProperty:(id)property forKey:(NSString *)key;
- (NSString *)identifier;
@end

// ---- PSListController ----
// ★ 父类声明成 UIViewController 只是为了编译期能调 viewDidLoad 之类的
//   标准 UIViewController 方法。真实的继承链是
//   PSListController : PSViewController : UIViewController，
//   但我们不调用 PSViewController 的东西，所以声明成直接继承不影响任何事。
//   （运行期继承关系由真实类决定，与这里的声明无关。）
@interface PSListController : UIViewController

// 从 bundle 内的 plist 载入 specifier 数组。
- (NSArray *)loadSpecifiersFromPlistName:(NSString *)name target:(id)target;

// 读写某个 specifier 绑定的偏好值（内部会走 CFPreferences，写到 defaults 域）。
- (id)readPreferenceValue:(PSSpecifier *)specifier;
- (void)setPreferenceValue:(id)value specifier:(PSSpecifier *)specifier;

// ★ 重载单个 specifier 必须带 specifier 参数。
//   记忆里那条「reloadSpecifier:nil 会崩」说的就是这里：
//   传 nil 时内部会去取它的 identifier / index，直接 unrecognized selector 或越界。
- (void)reloadSpecifier:(PSSpecifier *)specifier animated:(BOOL)animated;
- (void)reloadSpecifiers;

@end

#endif /* NLW_PREFS_MIN_H */