# 南财课表（非官方）

用南京财经大学统一身份认证登录，后端代登录 CAS → 抓取教务课表 → 存库 → 后台定时刷新；前端按真实时间渲染周/日视图，可导出 `.ics` 订阅。

> 本项目为非官方第三方工具，与南京财经大学无隶属关系。代登录可能受学校信息系统使用条款约束，部署和使用前请自行评估。

## 功能

- **两种登录方式**：账号密码 / 学校统一身份认证扫码。学校的人机验证（图形验证码、滑块拼图）和二次认证（短信、动态口令）只转交给真人完成，程序不识别、不代拖、不绕过。
- **周视图 + 日视图**：当前时间线、正在上课进度、下一节提示、今天跳转、按周/日深链接；手机上周视图可横向滚动，时间轴固定在左侧。
- **现代前端**：无构建步骤的 ES modules，原生 `dialog` / `popover` / `color-mix`，浅色/深色/跟随系统，移动端底部抽屉设置页。
- **后台刷新**：按用户自选时段串行刷新，失败指数退避；登录态过期、验证码、二次认证会在界面顶部提示并暂停自动重试。
- **邮件通知**（可选）：绑定邮箱后，后台自动刷新暂停（需要重新登录 / 验证码）、连续失败或发现课表变动时发邮件提醒；两类提醒可分别关闭。
- **iOS 桌面小组件**（Scriptable）：桌面和锁屏显示今天 / 明天 / 本周的课，正在上课和下一节高亮，周末预告下次上课。
- **日历订阅**：生成可撤销的 `.ics` 链接，iOS / macOS 日历、Google 日历、Outlook 均可添加。
- **数据自主**：可删除已保存密码，也可一键删除账号与全部数据。

## 快速开始

要求 Python 3.10+。

```bash
uv venv .venv && uv pip install -p .venv -r requirements.txt
cp .env.example .env    # 然后填 APP_SECRET_KEY
```

### 1. 先用命令行验证登录 + 抓课表

```bash
.venv/bin/python -m app.cli 你的学号 -o my.json
```

密码通过交互输入，不回显，不落盘。成功会打印 `登录成功` 和本学期课程列表，并把规范化后的 JSON 写到 `my.json`。

- 要求**图形验证码**时：图片保存到临时目录并自动打开，由你本人输入。
- 要求**手机短信二次认证**时：回车发送短信，输入收到的验证码。
- 加 `--dump debug/` 会把登录每一步的落地页（URL + HTML，不含密码）存下来，方便学校页面变化时排查。

### 2. 启动 Web 服务

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
```

生产环境放在 Caddy/Nginx/Apache 后面做 HTTPS，把 `PUBLIC_BASE_URL` 设成 `https://...`，系统会自动开启 Secure cookie、HSTS 和 http→https 跳转。后台刷新线程跑在进程内，**只启动一个 worker**（不要用 `--workers N`）。

### 离线演示（不访问学校服务器）

```bash
.venv/bin/python scripts/demo_server.py   # http://localhost:8765
```

用 `examples/sample_timetable.json`（脱敏示例数据）跑完整前端：账号密码登录填**任意学号 + 密码 `demo`**；扫码登录点“显示二维码”，几秒后自动“确认”。密码填 `captcha` / `slider` / `sms` / `otp` 可分别演示图形验证码（填 abcd）、滑块拼图、短信和动态口令二次认证。适合改前端时使用。演示模式的登录限速已放宽，不会误挡反复调试。

### 3. 测试

```bash
.venv/bin/python -m pytest -q        # 后端 / API（上游全部打桩）
node --test tests/js/*.test.js       # 前端纯逻辑（Node 20+，无需 npm install）
uv pip install -p .venv ruff         # 可选：Python 静态检查
.venv/bin/ruff check app tests scripts
```

测试会给上游（CAS/教务）打桩，不访问学校服务器。前端测试覆盖日期/周次、重叠课程分道、课程颜色与节次兜底、URL hash；浏览器端的登录/课表交互可用演示服务器手工验证。

## 前端

前端在 `static/`，**不需要构建、不需要 Node 运行时**，部署时随 Python 一起同步即可：

```
static/
  index.html          # 模板（{{v}} 由后端替换为资源内容哈希）
  css/
    tokens.css        # 颜色/圆角/阴影令牌 + 深色主题
    base.css          # 重置、按钮、表单、提示条、骨架屏
    layout.css        # 顶栏、登录页、工具栏、页脚
    timetable.css     # 周视图网格、日视图、移动端滚动
    dialogs.css        # 确认框、课程详情、设置抽屉
  js/
    main.js            # 会话恢复、登录/课表切换
    api.js             # CSRF、错误信息、超时、401 处理
    dom.js icons.js    # 无 innerHTML 的 DOM 构造 + 内联 SVG 图标
    time.js courses.js overlap.js hash.js store.js theme.js
    components/        # toast / dialog
    views/             # login / timetable / settings
```

设计取舍：

- **CSP 友好**：页面没有内联脚本/样式，所有 DOM 都用 `textContent` / `setAttribute` 创建；服务端 CSP 额外启用 `require-trusted-types-for 'script'`。
- **无构建也能发版**：`/` 每次读取 `index.html`，把 `{{v}}` 替换成 `static/` 全部资源的内容哈希；入口 CSS/JS 带 `?v=`，模块 import 无法逐个带哈希，因此 `/static/` 统一 `Cache-Control: max-age=0, must-revalidate`（ETag 304 很便宜）。改完前端刷新即生效，不需要重启 Python。
- **深链接**：当前学期、周次、视图、日期写在 hash 里，刷新/分享不丢位置。例：
  - `#sem=161&w=8&view=week`
  - `#sem=161&w=8&view=day&d=2`
- **键盘**：`←` / `→` 翻周（日视图切换日期）、`T` 回到本周、`W` / `D` 切换周/日视图；焦点在输入框或弹窗内时不拦截。
- **移动端**：默认日视图；周视图为横向可滚动表格（列宽 ≥96px），时间轴、星期表头分别固定在左侧和顶部。
- **静态目录保护**：`/static/index.html` 直接返回 404，避免拿到未替换模板的版本。

Node 只用于跑 `node --test`，`package.json` 没有任何依赖。

## 部署到自己的服务器

```bash
cp deploy/deploy.env.example deploy/deploy.env   # 填 HOST / SSH_KEY / PUBLIC_BASE_URL（该文件不进 git）
deploy/deploy.sh
```

- 代码同步到 `/opt/nufe-kebiao`，systemd 服务 `kebiao`（用户 `kebiao`，只监听 `127.0.0.1:8010`，1 个 worker）。
- 服务器上的 `.env`（含独立生成的 `APP_SECRET_KEY`）和 `data/` 只在首次部署时创建，之后不会被覆盖。
- 反向代理示例（Apache）：在站点的 `*:443` vhost 里加一行 `Include /etc/apache2/kebiao-proxy.conf`（内容见 `deploy/apache-kebiao.conf`），把 `/kebiao/` 反代到 8010 并去掉前缀。前端全部使用相对路径，会话 cookie 的 path 是 `/kebiao/`。
- 查看日志：`journalctl -u kebiao -f`
- 健康检查：`GET /healthz` 返回 `{"ok": true}`。

> 订阅链接里的 token 就是凭证。应用已对自身的 uvicorn access log 做 `/ics/<redacted>` 脱敏；如果反向代理、CDN 或云厂商还会记录完整 URL，请自行对 `/ics/` 路径脱敏或不记录。

## 登录链路（实测）

1. `GET /student/for-std/course-table`：未登录时**先落到教务自己的 `/student/login`**，不会直接跳 CAS。
2. `GET /student/sso/login` → 302 到 `authserver/login?service=https://jwxt.nufe.edu.cn/student/sso/login`。这个 URL 就是 POST 的 action。
3. 从 `#pwdFromId` 表单取 `pwdEncryptSalt` / `execution` / `lt`（页面上有 5 个表单，`execution` 一般是 `e1s1`）。
4. `checkNeedCaptcha.htl`：需要验证码就停下来交给用户，不尝试绕过。用哪种验证看登录页的 `captchaSwitch`：
   - `1` 图形验证码 → `captcha` 阶段：`getCaptcha.htl` 的图片原样给用户，用户填写后随表单提交；
   - `2` 滑块拼图 → `slider` 阶段（复刻 `ids-sliderCaptcha.js` / `longbow.slidercaptcha.js`）：`common/openSliderCaptcha.htl` 返回背景图和拼图条，网页按学校插件同样的 280×155 尺寸显示，**由用户本人拖动**；记录的位移与轨迹（采样规则与插件一致，坐标换算回 280 宽）用小图末尾附带的 16 位密钥加密成 `sign`，POST `common/verifySliderCaptcha.htl`，`errorCode=1` 后再提交账号密码。每张图只校验一次，失败自动换图。签名密钥只留在服务端。
5. POST 加密后的密码（`randomString(64)+pwd`，AES-CBC，key=salt，随机 IV 不回传）。
6. 若落到二次认证页（`reAuthCheck` / `isMultifactor=true`）：按页面判断方式——
   - 短信（`reAuthType=3` 或有 `dynamicCode` 输入框）：`dynamicCode/getDynamicCodeByReauth.do` 发短信 → `reAuthCheck/reAuthSubmit.do` 提交 `dynamicCode`；
   - 动态口令（有 `otpCode` 输入框，身份验证器 App）：直接把用户输入的口令放进 `otpCode` 提交；
   - 其他方式（密保问题、邮箱、App 扫码确认等）本站暂不能转发：立即结束流程并告诉用户是哪种方式、可以改用扫码登录。
   通过后再访问登录地址拿 ticket。接口路径优先从页面脚本里解析，且必须仍在 `authserver.nufe.edu.cn` 上。
7. 拿到 `CASTGC` 后再访问一次课表页，解析 `personId` / `currentSemester`，最后调 `print-data`。

**扫码登录**（复刻 `qrcode.js`）：`qrCode/getToken` 拿 uuid → `qrCode/getCode?uuid=` 取二维码图片 → 调度线程在上游节流下轮询 `qrCode/getStatus.htl`（1=已确认，2=已扫描，3=失效），前端每 2 秒只读本站内存 → 确认后按 `#qrLoginForm` 的字段 POST `login?display=qrLogin&service=…`，之后与账号密码登录一样（可能进入短信二次认证）。扫码登录不涉及密码，也不会改动用户已保存的凭据设置；学号从教务数据里取。单个流程前端最多询问 120 次，刷新二维码最多 10 次，且不会把 5 分钟的登录流程无限续期。

**保持登录**：登录时勾选“保持登录 180 天”＝持久 cookie（`SESSION_DAYS_LONG`）；不勾＝浏览器会话 cookie，服务端最长 `SESSION_DAYS_SHORT`（默认 7）天。两种都是**滑动续期**：每次打开页面（每天最多一次）把有效期续满，经常用的人不会因为“从登录起满 N 天”被踢出。

**学校登录态续期**：教务会话过期后，先用认证中心的 CASTGC（登录时勾了“记住我”）静默走一次 SSO 续期；只有 CASTGC 也失效、或认证中心要求短信二次认证时，才需要用户回来重新登录（存了密码的用户此时再用密码自动登录）。

所有人机验证和二次认证都只**转发给真人完成**（网页上显示图片 / 拼图 / 输入框；CLI 支持图形验证码、短信和动态口令，滑块请用网页），程序不做识别或绕过。未完成的登录流程只放在内存里，5 分钟过期；每个流程最多发 3 次短信、尝试 5 次。后台刷新没有真人在场，遇到这两步会停止自动重试，并提示用户回来重新登录。

## 凭据与安全设计

| 项 | 做法 |
|---|---|
| 站点账号 | 不另设站点密码：登录本站 = 实时用 CAS 验证一次，首次成功自动建号。数据库里没有可被撞库的站点密码哈希 |
| 默认凭据 | **只存 CAS 登录态 cookie**（含 TGC，`rememberMe=true`），AES-256-GCM 加密；过期后标记 `needs_rebind`，用户回来重新登录即可续期 |
| 可选：存密码 | 登录时勾选并看到风险说明后才会保存；同样 AES-256-GCM 加密。刷新时**先试 cookie**，失效才解密密码重登。设置里可一键删除 |
| 密钥 | `APP_SECRET_KEY`（32 字节，启动时校验 base64/长度）。AAD 绑定 `用途:用户id`，密文挪到别的行上解不出来。上 KMS 时用 KMS 解出数据密钥填这个变量即可 |
| 坏数据 | 密文/课表 JSON 损坏时标记 `needs_rebind` 或隐藏该学期，不会把内部异常抛成 500；异常原文不进日志、不回浏览器 |
| 日志 | 不记录密码/cookie；异常只记录类型；uvicorn access log 对 `/ics/` token 做脱敏；urllib3 日志调到 WARNING |
| 会话 cookie | 随机 token，库里只存 SHA-256；cookie `HttpOnly; SameSite=Lax; Secure(https)`；每人最多保留最近 8 个会话 |
| CSRF | 所有写请求校验 Origin/Referer（Referrer 被隐私设置去掉时用浏览器写入的 Sec-Fetch-Site 兜底；同时防登录 CSRF）+ 已登录接口要求 `X-CSRF-Token` |
| 其他头 | 严格 CSP（无内联脚本，含 `base-uri` / `object-src` / `form-action` / Trusted Types）、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`（防订阅 token 外泄）、CORP/COOP |
| 前端 XSS | 不拼接 HTML 字符串（没有 `innerHTML` / `eval`），所有用户/学校数据都走 `textContent`；测试会扫描这些危险写法 |
| 通知邮箱 | 用户主动绑定，必须收到 6 位验证码确认（只存 HMAC，10 分钟有效，最多试 5 次，60 秒才能重发）；邮箱 AES-256-GCM 加密落库；验证 / 测试邮件按人 5 封、按 IP 10 封每小时限量，防止被拿来轰炸别人的邮箱；日志里只记打码后的地址 |
| 删除 | 设置 → “删除我的账号与所有数据”，外键级联删除会话、课表、刷新记录、订阅 token 和通知邮箱，并清掉内存里未完成的登录流程/缓存 |

### 邮件通知

在 `.env` 里配好 SMTP 就会在设置页出现“邮件通知”（没配则整块隐藏）：

```
MAIL_MAILER=smtp            # log = 不真的发信，只打印到日志（本地开发 / 演示）
MAIL_HOST=smtp.163.com
MAIL_PORT=465
MAIL_USERNAME=you@163.com
MAIL_PASSWORD=<邮箱的 SMTP 授权码，不是登录密码>
MAIL_ENCRYPTION=ssl         # ssl（465）| tls（587，STARTTLS）| none
MAIL_FROM_ADDRESS=you@163.com
MAIL_FROM_NAME=南财课表
```

- 发信走单线程后台队列，SMTP 慢或连不上不影响网页和刷新；失败重试两次后丢弃。两封信之间至少隔 `MAIL_MIN_INTERVAL`（默认 3）秒。
- 只在**后台刷新**时提醒（手动刷新、登录时用户就在屏幕前）：
  - 刷新暂停：状态第一次变成需要重新登录 / 验证码时发一封；普通失败在连续次数恰好达到 `REFRESH_MAX_FAILS` 时发一封，不会每次失败都发。
  - 课表变动：与上次保存的同一学期课表比较（课程名、星期、节次与时间、地点、周次），列出新增和取消的条目。首次抓取不算变动。
- 每封信末尾都说明可在设置里关闭或解绑。

### iOS 桌面小组件（Scriptable）

设置 → “桌面小组件（iOS）”：生成只读链接 → “复制脚本”（脚本里已填好本人链接）→ 粘进免费的 [Scriptable](https://apps.apple.com/cn/app/scriptable/id1405459188) → 桌面添加 Scriptable 小组件并选这个脚本。Parameter 填 `today`（默认）/ `tomorrow` / `week`。

- 数据来自 `GET /widget/<token>.json`：服务端按北京时间算好今天、明天、本周（以及两周内下一个有课的日子），周次、缺失时间的兜底、课程颜色都和网页 / ICS 用同一套规则，脚本只负责排版。
- token 与日历订阅分开，可单独重新生成 / 停用；库里只存 SHA-256，访问日志对 `/widget/` 脱敏；删号即失效。
- 脚本源码在 `scriptable/nufe-kebiao-widget.js`：支持小 / 中 / 大 / 超大号和三种锁屏样式；在下一次上下课、零点或最多 30 分钟后请求刷新（实际时机由 iOS 决定）；离线时用上次缓存并标注；学校登录态失效时在标题栏提示“需重新登录”。
- 测试：`tests/js/scriptable-shim.js` 是一个极简的 Scriptable 运行时替身，`npm test` 会在 Node 里把整个脚本按各尺寸 / 模式跑一遍。

### 会话与设备

- 同一账号可在多台设备同时登录，每人最多保留最近 8 个会话（`MAX_SESSIONS_PER_USER`）；设置里有“退出所有设备”。
- 扫码登录的状态轮询跑在独立线程（`qr-pump`），不受后台刷新批次影响，`SCHEDULER_ENABLED=false` 时也照常工作。
- `index.html` 不缓存，入口 CSS/JS 地址带整体内容哈希，`/static/` 使用可重新验证的缓存；部署后浏览器立刻使用新前端，模块文件也不会长期陈旧。

## 对学校服务器的节流

- 所有上游请求（包括重定向的每一跳）都经过 `PoliteSession.send`：**全站任意两次请求的发起间隔 ≥ 1 秒**（`UPSTREAM_MIN_INTERVAL` 只能调大），另加 0–0.2 秒抖动，最多 2 个并发。请求多时在这里排队，而不是拒绝用户。
- 后台刷新只在 `REFRESH_ALLOWED_HOURS`（默认 0–7、12–13、22–23 点）运行，单线程串行，用户之间随机间隔 5–20s；每个用户按 `REFRESH_INTERVAL_HOURS`（默认 12h，±15% 抖动）刷新。
- 用户可在“设置”里自选每天最多 `MAX_USER_REFRESH_TIMES`（默认 3）个刷新整点，但只能从 `REFRESH_ALLOWED_HOURS` 里选；实际开始时间在该小时内随机偏移 0–45 分钟。不选则按默认间隔。
- 网络/学校侧错误：指数退避（30min → 24h 封顶），连续 `REFRESH_MAX_FAILS` 次后在界面上提示。
- 密码错误/登录态过期且未存密码：`needs_rebind`，停止自动重试，避免把账号刷到锁定。
- 验证码（图形或滑块）：`captcha`，停止自动重试，提示用户去浏览器手动登录一次。
- **按学校的反应自适应放慢**：收到 429/503 时按 `Retry-After` 全站暂停（最长 10 分钟，未给则 30 秒）；5xx、超时、连接失败时在最小间隔上再加惩罚间隔，连续出错翻倍（最长 60 秒），恢复正常后逐步减半。暂停期间后台刷新直接让路，交互请求最多等 10 秒，再久就提示“学校服务器繁忙”。
- 站点登录（每次都会打到 CAS）按 IP 10 次/15min、按学号 5 次/15min 限速。
- 手动刷新：同一用户同时只能有一个在跑；两次之间至少隔 `MANUAL_REFRESH_MIN_GAP`（默认 20）秒；该学期课表 `MANUAL_REFRESH_FRESH_SECONDS`（默认 120）秒内刚同步过且状态正常时直接返回已存的课表，不再请求学校。后台刷新若发现有人正在登录或手动刷新，会让出这一轮。

## 建议部署在独立主机名

如果像 `https://example.com/kebiao/` 这样和其他站点共用一个域名，它们就是**同一个源**。`Path=/kebiao/` 只限制 cookie 发往哪条路径，挡不住同域名下别的页面脚本带着 cookie 调用 `/kebiao/api/`。若同域名下有你不完全控制的页面，请把本服务放在独立主机名上（例如 `https://kebiao.example.com`），并把 `PUBLIC_BASE_URL` 设成该地址（无路径前缀），cookie 的 path 就会是 `/`。

## 校历

周次 → 日期需要“第 1 周周一”。优先级：`semesters.json`（按学期名或学期 id）> `/student/ws/semester/get/{id}` 返回的 `startDate` 等字段（对齐到周一）。新学期开始前在 `semesters.json` 里加一行即可，例如：

```json
{ "2026-2027-1": "2026-09-07", "2026-2027-2": "2027-02-22" }
```

## 目录

```
app/
  cas.py        统一身份认证登录 + 密码加密
  jwxt.py       课表页解析、print-data 抓取与规范化
  polite.py     节流 HTTP 会话、cookie 序列化
  cli.py        单用户命令行验证脚本
  service.py    代登录、凭据保管、刷新状态机
  scheduler.py  后台定时刷新、扫码状态轮询
  ics.py        iCalendar 导出
  mailer.py     SMTP 发信后台队列
  notify.py     通知邮箱绑定（验证码）与刷新暂停 / 课表变动提醒
  widget.py     桌面小组件数据（今天 / 明天 / 本周）
  security.py   会话、CSRF、限速
  main.py       FastAPI 路由 + 安全响应头 + 静态资源版本
static/
  index.html    前端模板（{{v}} 由后端替换）
  css/          设计令牌、布局、课表、浮层
  js/           无构建 ES modules（见上文）
tests/
  test_api.py   Web/API 端到端（上游打桩）
  test_core.py  解析、加解密、ICS、节流
  js/           前端纯逻辑 node:test
scriptable/     iOS 桌面小组件脚本（Scriptable）
examples/       脱敏示例课表
scripts/        离线演示服务器
deploy/         systemd / Apache 示例与部署脚本
package.json    前端测试脚本（无依赖）
pyproject.toml  ruff / pytest 配置
```

## 开发约定

- Python：`ruff check app tests scripts`，行宽 120；测试 `pytest`。
- 前端：保持无构建、无第三方运行时依赖；新增模块时同步在 `tests/js/` 补纯逻辑测试，并确保 `static/js/main.js` 的 import 图完整（`test_api.py` 会检查）。
- 所有新增 DOM 文本必须走 `textContent`，不要引入 `innerHTML`；CSP 没有 `unsafe-inline`。
- 改前端后刷新页面即可；改 Python / 环境变量需要重启服务。

## 尚未做的

- 通知只有站内横幅和邮件；没有微信 / App 推送。
- SQLite 起步。换 PostgreSQL 需要改 `db.py` 的连接和少量 SQL（`ON CONFLICT` 语法两边通用）。
- 前端没有 Service Worker / 离线缓存；课表数据每次打开都从本站读取。

## 许可证

本项目以 [GNU General Public License v3.0 或更高版本](LICENSE)（GPL-3.0-or-later）发布。你可以自由使用、修改和再分发，但分发修改后的版本时须以相同许可证开放源代码。

本项目与南京财经大学无任何隶属关系，不提供任何担保。使用者需自行确认代登录行为符合学校相关规定，并对自己的授权与使用负责。
