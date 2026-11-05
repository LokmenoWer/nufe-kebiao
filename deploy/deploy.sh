#!/usr/bin/env bash
# 在本机执行：把代码同步到服务器，并热重载或重启服务。
#   FORCE_RESTART=1 deploy/deploy.sh   # 改了 main.lifespan 或后台循环体这类热重载更新不到的代码时用（见 app/reloader.py）
#   cp deploy/deploy.env.example deploy/deploy.env   # 填好服务器信息（该文件不进 git）
#   deploy/deploy.sh
# 也可以直接用环境变量：HOST=root@your-server PUBLIC_BASE_URL=https://kebiao.example.com deploy/deploy.sh
# 部署的是当前提交（HEAD），没提交的改动不会上线。首次部署后服务器上的 .env / data/ 不会被覆盖。
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
[ -f deploy/deploy.env ] && . deploy/deploy.env
: "${HOST:?请设置 HOST，例如 HOST=root@your-server}"
: "${PUBLIC_BASE_URL:?请设置 PUBLIC_BASE_URL，例如 https://kebiao.example.com}"
APP_DIR=/opt/nufe-kebiao
SSH_OPTS=(-o IdentitiesOnly=yes)
[ -n "${SSH_KEY:-}" ] && SSH_OPTS+=(-i "$SSH_KEY")
SSH=(ssh "${SSH_OPTS[@]}" "$HOST")

# 只部署已提交的代码：工作区里没进 git 的文件（签名密钥、本地配置、构建产物）不会传上服务器
[ -z "$(git status --porcelain)" ] || echo "注意：工作区有未提交的改动，这次只部署 $(git rev-parse --short HEAD)，不包含它们。" >&2
SRC=$(mktemp -d)
trap 'rm -rf "$SRC"' EXIT
git archive HEAD | tar -x -C "$SRC"
chmod 755 "$SRC"  # rsync 会把这个权限带给 APP_DIR；mktemp 默认的 700 会让服务用户进不去

# 下面的 exclude 保护服务器上已有的文件不被 --delete 删掉
rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
  --exclude .venv --exclude data --exclude .env --exclude debug --exclude my.json \
  --exclude .nufe_device_id --exclude __pycache__ --exclude .pytest_cache --exclude .git \
  --exclude deploy/deploy.env --exclude .DS_Store \
  --exclude node_modules --exclude test-results --exclude playwright-report \
  --exclude .ruff_cache --exclude .claude \
  "$SRC/" "$HOST:$APP_DIR/"

"${SSH[@]}" "PUBLIC_BASE_URL='$PUBLIC_BASE_URL' ADMIN_PASSWORD_HASH='${ADMIN_PASSWORD_HASH:-}' FORCE_RESTART='${FORCE_RESTART:-0}' bash -s" <<'REMOTE'
set -euo pipefail
APP_DIR=/opt/nufe-kebiao
id kebiao >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin kebiao
mkdir -p "$APP_DIR/data"
# 代码归 root；data/（数据库）必须整体归服务用户，否则 SQLite 只读、服务起不来
find "$APP_DIR" -path "$APP_DIR/data" -prune -o -exec chown root:root {} +
chown -R kebiao:kebiao "$APP_DIR/data" && chmod 700 "$APP_DIR/data"

if [ ! -f "$APP_DIR/.env" ]; then
  (umask 077; cat > "$APP_DIR/.env" <<ENV
APP_SECRET_KEY=$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
PUBLIC_BASE_URL=$PUBLIC_BASE_URL
DB_PATH=$APP_DIR/data/kebiao.db
COOKIE_SECURE=true
FORCE_HTTPS=true
SCHEDULER_ENABLED=true
ENV
  )
fi
# 管理员页密码：deploy.env 里配了哈希就同步到服务器（只传哈希，不传明文）
if [ -n "${ADMIN_PASSWORD_HASH:-}" ]; then
  (umask 077
   grep -v '^ADMIN_PASSWORD_HASH=' "$APP_DIR/.env" > "$APP_DIR/.env.tmp" || true
   printf "ADMIN_PASSWORD_HASH='%s'\n" "$ADMIN_PASSWORD_HASH" >> "$APP_DIR/.env.tmp"
   mv "$APP_DIR/.env.tmp" "$APP_DIR/.env")
fi
chown root:kebiao "$APP_DIR/.env" && chmod 640 "$APP_DIR/.env"

DEPS_CHANGED=0
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
  DEPS_CHANGED=1
fi
if ! sha256sum -c "$APP_DIR/.venv/.requirements.sha256" >/dev/null 2>&1; then
  "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
  "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
  sha256sum "$APP_DIR/requirements.txt" > "$APP_DIR/.venv/.requirements.sha256"
  DEPS_CHANGED=1
fi
chmod -R go+rX "$APP_DIR/.venv" "$APP_DIR/app" "$APP_DIR/static" "$APP_DIR/scriptable"

# 先确认新代码能在服务器的 Python 上编译、导入，再重载/重启服务；失败则保留旧进程
cd "$APP_DIR"
PYTHONDONTWRITEBYTECODE=1 "$APP_DIR/.venv/bin/python" -m compileall -q app
PYTHONDONTWRITEBYTECODE=1 "$APP_DIR/.venv/bin/python" -c "import app.main" \
  && echo "import check: ok" \
  || { echo "import check failed; service not restarted" >&2; exit 1; }

# 平时只热重载（SIGHUP）：正在进行的扫码/验证码登录、学校 1 RPS 节流器状态和后台线程全部不断。
# 依赖、service 或 socket 配置变了、服务尚未运行、FORCE_RESTART=1，或热重载没成功（含 reloader.py 自身有改动）才走 restart。
SOCKET_CHANGED=0
SERVICE_CHANGED=0
cmp -s "$APP_DIR/deploy/kebiao.socket" /etc/systemd/system/kebiao.socket || SOCKET_CHANGED=1
cmp -s "$APP_DIR/deploy/kebiao.service" /etc/systemd/system/kebiao.service || SERVICE_CHANGED=1
install -m 644 "$APP_DIR/deploy/kebiao.socket" /etc/systemd/system/kebiao.socket
install -m 644 "$APP_DIR/deploy/kebiao.service" /etc/systemd/system/kebiao.service
systemctl daemon-reload
# 服务也要开机自启：后台刷新得在没人访问时照样跑，不能等第一个连接才把它拉起来
systemctl enable -q kebiao.socket kebiao
if [ "$SOCKET_CHANGED" = 1 ] || ! systemctl is-active -q kebiao.socket; then
  systemctl stop kebiao kebiao.socket
  systemctl start kebiao.socket
fi
START=$(date +%s%N)
if [ "$FORCE_RESTART" != 1 ] && [ "$SOCKET_CHANGED" = 0 ] && [ "$SERVICE_CHANGED" = 0 ] && [ "$DEPS_CHANGED" = 0 ] && systemctl is-active -q kebiao; then
  # 信号是异步的：删掉旧结果再发，等服务写出这一次的结果
  STATUS_FILE=$(PYTHONDONTWRITEBYTECODE=1 "$APP_DIR/.venv/bin/python" -c "from app import reloader; print(reloader.status_file())")
  rm -f "$STATUS_FILE"
  systemctl reload kebiao
  if "$APP_DIR/.venv/bin/python" "$APP_DIR/deploy/wait_reload.py" "$STATUS_FILE" 45; then
    ACTION="reload"
  else
    echo "hot reload did not succeed; restarting" >&2
    systemctl restart kebiao
    ACTION="restart"
  fi
else
  systemctl restart kebiao
  ACTION="restart"
fi
curl -s -o /dev/null --max-time 30 http://127.0.0.1:8010/healthz || true
echo "$ACTION: $(( ($(date +%s%N) - START) / 1000000 )) ms until /healthz answered"
systemctl is-active kebiao
curl -s -o /dev/null -w "local / -> %{http_code}\n" http://127.0.0.1:8010/
REMOTE
