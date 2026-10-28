# 南财课表（非官方）

用学号 + 统一身份认证密码登录，后端代登录 CAS → 抓取教务课表 → 存库 → 后台定时刷新；前端按真实时间渲染周视图，可导出 .ics 订阅。

> 本项目为非官方第三方工具，与南京财经大学无隶属关系。代登录可能受学校信息系统使用条款约束，部署和使用前请自行评估。

## 快速开始

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

生产环境放在 Caddy/Nginx 后面做 HTTPS，把 `PUBLIC_BASE_URL` 设成 `https://...`，系统会自动开启 Secure cookie、HSTS 和 http→https 跳转。后台刷新线程跑在进程内，**只启动一个 worker**（不要用 `--workers N`）。

### 部署到自己的服务器

```bash
cp deploy/deploy.env.example deploy/deploy.env   # 填 HOST / SSH_KEY / PUBLIC_BASE_URL（该文件不进 git）
deploy/deploy.sh
```

- 代码同步到 `/opt/nufe-kebiao`，systemd 服务 `kebiao`（用户 `kebiao`，只监听 `127.0.0.1:8010`，1 个 worker）。
- 服务器上的 `.env`（含独立生成的 `APP_SECRET_KEY`）和 `data/` 只在首次部署时创建，之后不会被覆盖。
- 反向代理示例（Apache）：在站点的 `*:443` vhost 里加一行 `Include /etc/apache2/kebiao-proxy.conf`（内容见 `deploy/apache-kebiao.conf`），把 `/kebiao/` 反代到 8010 并去掉前缀。前端全部使用相对路径，会话 cookie 的 path 是 `/kebiao/`。
- 查看日志：`journalctl -u kebiao -f`

### 离线演示（不访问学校服务器）

```bash
.venv/bin/python scripts/demo_server.py   # http://localhost:8765
```

用 `examples/sample_timetable.json`（脱敏示例数据）跑完整前端：账号密码登录填任意学号 + 密码 `demo`；扫码登录几秒后自动“确认”。适合改前端时使用。

### 3. 测试

```bash
.venv/bin/python -m pytest -q
```

测试会给上游（CAS/教务）打桩，不访问学校服务器。

## 登录链路（实测）

1. `GET /student/for-std/course-table`：未登录时**先落到教务自己的 `/student/login`**，不会直接跳 CAS。
2. `GET /student/sso/login` → 302 到 `authserver/login?service=https://jwxt.nufe.edu.cn/student/sso/login`。这个 URL 就是 POST 的 action。
3. 从 `#pwdFromId` 表单取 `pwdEncryptSalt` / `execution` / `lt`（页面上有 5 个表单，`execution` 一般是 `e1s1`）。
4. `checkNeedCaptcha.htl`：需要验证码就直接抛 `CaptchaRequired`，停止自动重试，不尝试绕过。
5. POST 加密后的密码（`randomString(64)+pwd`，AES-CBC，key=salt，随机 IV 不回传）。
6. 若落到二次认证页（`reAuthCheck` / `isMultifactor=true`）：`dynamicCode/getDynamicCodeByReauth.do` 发短信 → `reAuthCheck/reAuthSubmit.do`（`reAuthType=3`）提交验证码 → 再访问登录地址拿 ticket。接口路径优先从页面脚本里解析。
7. 拿到 `CASTGC` 后再访问一次课表页，解析 `personId` / `currentSemester`，最后调 `print-data`。

**扫码登录**（复刻 `qrcode.js`）：`qrCode/getToken` 拿 uuid → `qrCode/getCode?uuid=` 取二维码图片 → 调度线程在上游节流下轮询 `qrCode/getStatus.htl`（1=已确认，2=已扫描，3=失效），前端每 2 秒只读本站内存 → 确认后按 `#qrLoginForm` 的字段 POST `login?display=qrLogin&service=…`，之后与账号密码登录一样（可能进入短信二次认证）。扫码登录不涉及密码，也不会改动用户已保存的凭据设置；学号从教务数据里取。单个流程前端最多询问 120 次，刷新二维码最多 10 次，且不会把 5 分钟的登录流程无限续期。

**保持登录**：登录时勾选"保持登录 180 天"＝持久 cookie（`SESSION_DAYS_LONG`）；不勾＝浏览器会话 cookie，服务端最长 `SESSION_DAYS_SHORT`（默认 7）天。

图形验证码和短信验证码都只**转发给真人填写**（CLI 里提示输入，网页上显示图片 / 短信输入框），程序不做识别或绕过。未完成的登录流程只放在内存里，5 分钟过期；每个流程最多发 3 次短信、尝试 5 次。后台刷新没有真人在场，遇到这两步会停止自动重试，并提示用户回来重新登录。

## 凭据与安全设计

| 项 | 做法 |
|---|---|
| 站点账号 | 不另设站点密码：登录本站 = 实时用 CAS 验证一次，首次成功自动建号。数据库里没有可被撞库的站点密码哈希 |
| 默认凭据 | **只存 CAS 登录态 cookie**（含 TGC，`rememberMe=true`），AES-256-GCM 加密；过期后标记 `needs_rebind`，用户回来重新登录即可续期 |
| 可选：存密码 | 登录时勾选并看到风险说明后才会保存；同样 AES-256-GCM 加密。刷新时**先试 cookie**，失效才解密密码重登。设置里可一键删除 |
| 密钥 | `APP_SECRET_KEY`（32 字节）。AAD 绑定 `用途:用户id`，密文挪到别的行上解不出来。上 KMS 时用 KMS 解出数据密钥填这个变量即可 |
| 日志 | 不记录密码/cookie；异常只记录类型。urllib3 日志调到 WARNING |
| 会话 cookie | 随机 token，库里只存 SHA-256；cookie `HttpOnly; SameSite=Lax; Secure(https)` |
| CSRF | 所有写请求校验 Origin/Referer（同时防登录 CSRF）+ 已登录接口要求 `X-CSRF-Token` |
| 其他头 | 严格 CSP（无内联脚本，含 `base-uri` / `object-src` / `form-action`）、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`（防订阅 token 外泄） |
| 会话 | 可多设备同时登录（每人最多保留最近 8 个会话），设置里可“退出所有设备”。密码登录只有在本浏览器的 `kb_dev` 指向同一用户时才复用学校「信任此设备」的指纹，否则按新设备走（后台刷新仍用库存指纹） |
| 删除 | 设置 → “删除我的账号与所有数据”，外键级联删除会话、课表、刷新记录和订阅 token |

### 会话与设备

- 同一账号可在多台设备同时登录，每人最多保留最近 8 个会话（`MAX_SESSIONS_PER_USER`）；设置里有“退出所有设备”。
- 扫码登录的状态轮询跑在独立线程（`qr-pump`），不受后台刷新批次影响，`SCHEDULER_ENABLED=false` 时也照常工作。
- `index.html` 不缓存，CSS/JS 地址带内容哈希（`?v=`），部署后浏览器立即使用新前端。

## 对学校服务器的节流

- 所有上游请求（包括重定向的每一跳）都经过 `PoliteSession.send`：**全站任意两次请求的发起间隔 ≥ 1 秒**（硬下限，`UPSTREAM_MIN_INTERVAL` 只能调大），另加 0–0.2 秒抖动，最多 2 个并发。请求多时在这里排队，而不是拒绝用户。
- 后台刷新只在 `REFRESH_ALLOWED_HOURS`（默认 0–7、12–13、22–23 点）运行，单线程串行，用户之间随机间隔 5–20s；每个用户按 `REFRESH_INTERVAL_HOURS`（默认 12h，±15% 抖动）刷新。
- 用户可在“设置”里自选每天最多 `MAX_USER_REFRESH_TIMES`（默认 3）个刷新整点，但只能从 `REFRESH_ALLOWED_HOURS` 里选；实际开始时间在该小时内随机偏移 0–45 分钟。不选则按默认间隔。
- 网络/学校侧错误：指数退避（30min → 24h 封顶），连续 `REFRESH_MAX_FAILS` 次后在界面上提示。
- 密码错误/登录态过期且未存密码：`needs_rebind`，停止自动重试，避免把账号刷到锁定。
- 验证码：`captcha`，停止自动重试，提示用户去浏览器手动登录一次。
- 站点登录（每次都会打到 CAS）按 IP 10 次/15min、按学号 5 次/15min 限速。手动刷新不限次数（同一用户同时只能有一个刷新在跑），对学校的总频率由上面的全站限速保证。后台刷新若发现有人正在登录或手动刷新，会让出这一轮。

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
  security.py   会话、CSRF、限速
  main.py       FastAPI 路由
static/         前端（原生 JS，周视图）
tests/          pytest（上游打桩）
examples/       脱敏示例课表
scripts/        离线演示服务器
deploy/         systemd / Apache 示例与部署脚本
```

## 尚未做的

- 通知只做了站内横幅（`needs_rebind` / `captcha` / 连续失败）；要发邮件或推送，可以在 `service._save_failure` 里接。
- SQLite 起步。换 PostgreSQL 需要改 `db.py` 的连接和少量 SQL（`ON CONFLICT` 语法两边通用）。

## 许可证

本项目以 [GNU General Public License v3.0 或更高版本](LICENSE)（GPL-3.0-or-later）发布。你可以自由使用、修改和再分发，但分发修改后的版本时须以相同许可证开放源代码。

本项目与南京财经大学无任何隶属关系，不提供任何担保。使用者需自行确认代登录行为符合学校相关规定，并对自己的授权与使用负责。
