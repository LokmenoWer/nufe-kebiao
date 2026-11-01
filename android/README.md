# 南财课表 · 安卓桌面小组件

用 Jetpack Glance 写的安卓 App，给安卓用户提供和 iOS Scriptable 小组件同样的桌面体验。数据直接读服务器现成的 `GET /widget/<token>.json`（与 Scriptable 脚本同一条只读链接），周次、颜色、调课标记都由服务端算好，App 只负责排版、定时刷新和上课提醒，服务端不需要任何改动。

## 功能

- **三种小组件**（都可以自由拉伸，按实际尺寸排版，跟随系统深浅色，Android 12+ 用 Material You 动态取色）：
  - **今天**：2×2 只显示最要紧的一节，正在上课时倒计时到下课，下一节一小时内倒计时到上课（系统 `Chronometer` 每秒自己走，不用反复重画）；更大的尺寸按高度列出剩下的课，放不下的合成“还有 N 节”。当天课上完、周末、假期里预告下次上课；开学前显示“距开学还有 N 天”，结课后显示“本学期课程已结束”。
  - **明天**：明天的全部课；明天没课时预告下次上课。
  - **本周**：和 Scriptable 的 full 模式一样，纵轴按真实时间，没人上课的时段压成一道细缝；**按内容排**——每张卡片先保证写得下，剩下的高度再按时长分。卡片里依次写：课名写全 → 下课时间（开始时间标在左边时间轴上，卡片写“–12:15”；列宽够就写“10:00–12:15”）→ 教室（放不下整行时楼名单独一行，再不行只写“Z4-118”）→ 调课的原安排 → 教师。字不靠估：用画小组件的同一套系统字体（`StaticLayout`）量出每段字多高，跟随系统字体大小。已上完的课变灰，正在上的课实色高亮，停课标“停”，调课标“调”，撞课记作“+1”。周末本周没课了自动看下周。
  - **随意拉伸**：版面全按当前尺寸算（`SizeMode.Exact`）。拉高了先写全楼名和教师，再把字放大（最多 1.2 倍）；拉矮了逐级少写（教师 → 楼名 → 时间 → 课名缩成两行 / 一行），要紧的内容才缩字（最小 0.8 倍）；拉窄到一列不足 40dp 时先省掉没课的日子，还放不下就只画从今天起的几天，标题里的日期跟着变。
- **标题栏提示**：需重新登录、N 天未更新、离线、更新中、有调课、第几周；右上角刷新按钮会请服务器插队向学校拉一次（`?refresh=1`，服务器自己限流）。
- **点击**：打开网站对应那天的日视图 / 那一周的周视图；本周小组件点某一列进那天。
- **上课提醒**：按未来 7 天的课在本机定闹钟发通知（课程、时间、教室，调过的课注明原安排），可选关闭 / 提前 5–30 分钟。每次拿到新课表整批重排，开机、改时区后自动恢复。
- **刷新时机**：WorkManager 每 30 分钟兜底一次；另外在下一个上下课时刻、课前 1 小时（切换成倒计时）、课前 25 分钟（服务器刚在课前半小时确认完课表）、零点后各重画一次；服务器正在向学校拉课表时约 2 分钟后再看。断网时用本机缓存并标“离线”。
- **设置链接**：网站 → 账号与设置 → 桌面小组件 → “在安卓 App 中打开”（`nufekebiao://setup?url=…`），或复制链接后在 App 里粘贴，也可以从别的 App 分享文字过来。

## 构建

需要 JDK 17+ 和 Android SDK（compileSdk 37）。用 Android Studio 打开 `android/` 目录即可，或命令行：

```bash
cd android && ./gradlew :app:assembleDebug
```

产物在 `app/build/outputs/apk/debug/app-debug.apk`。发布包需要签名：在 `android/keystore.properties`（已在 `.gitignore`）里写 `storeFile` / `storePassword` / `keyAlias` / `keyPassword`，再 `./gradlew :app:assembleRelease`。

## 调试

- 本机演示：`.venv/bin/python scripts/demo_server.py` 起离线演示站（端口 8765），登录后在设置里生成小组件链接；模拟器里先 `adb reverse tcp:8765 tcp:8765`，链接里的 `localhost` 就能直接用（`network_security_config` 只给 localhost / 10.0.2.2 放行明文 HTTP）。
- 小组件预览（只在 debug 包里）：把三种小组件按 2×2 到 4×5、以及拉窄的 2×3、3×3 画在一页上，不用反复往桌面上拖；`--es at 09:30` 把时钟拨到今天这个时刻，看上课中 / 课前倒计时 / 课上完等状态：

  ```bash
  adb shell appwidget grantbind --package wiki.nufe.kebiao
  adb shell am start -S -n wiki.nufe.kebiao/.preview.WidgetPreviewActivity --es at 09:30
  ```

- Glance 的 Row / Column / Box 最多 10 个子元素，超出会整块小组件报错：列间距用内边距而不是 Spacer，一天的课每节只占一个子元素。

## 代码结构

```text
app/src/main/java/wiki/nufe/kebiao/
  data/      Models（解析 JSON）、Repository（链接 / 缓存 / 下载）、Plan（今天 / 明天 / 本周要画什么，移植自 Scriptable 脚本）
  widget/    Glance 小组件：Common（主题色、标题栏、倒计时、课程卡片）、DayWidget（今天 / 明天）、
             WeekWidget（本周）、WeekLayout（本周按内容排版：量字、分几档内容、解纵轴分界线）
  sync/      WorkManager 同步与重画时机
  remind/    上课提醒（AlarmManager + 通知）、开机恢复
  ui/        设置页（Compose）
app/src/debug/  小组件预览页
```

## 已知限制

- 国产系统（MIUI / HyperOS、ColorOS、OriginOS、鸿蒙等）默认会限制后台，需要在电池设置里允许 App 自启动、后台运行，否则刷新和提醒可能不准时。
- 没有“闹钟和提醒”权限（Android 12+ 需要用户手动打开）时，提醒退化为非精确闹钟，可能晚几分钟。
- 4×2 的本周小组件太矮，短课只画色块不写字；4×4 以上才写得下楼名和教师。
