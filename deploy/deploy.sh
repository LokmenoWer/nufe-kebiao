#!/usr/bin/env bash
# 在本机执行：把代码同步到服务器，并热重载或重启服务。
#   FORCE_RESTART=1 deploy/deploy.sh   # 改了 main.lifespan 或后台循环体这类热重载更新不到的代码时用（见 app/reloader.py）
#   cp deploy/deploy.env.example deploy/deploy.env   # 填好服务器信息（该文件不进 git）
#   deploy/deploy.sh
#   DEPLOY_ENV=deploy/deploy-beta.env deploy/deploy.sh   # 部署到同机的 beta（目录 / 服务 / 端口在该文件里，见 deploy.env.example）
# 也可以直接用环境变量：HOST=root@your-server PUBLIC_BASE_URL=https://kebiao.example.com deploy/deploy.sh
# 部署的是当前提交（HEAD），没提交的改动不会上线。首次部署后服务器上的 .env / data/ 不会被覆盖。
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
DEPLOY_ENV=${DEPLOY_ENV:-deploy/deploy.env}
[ -f "$DEPLOY_ENV" ] && . "$DEPLOY_ENV"
: "${HOST:?请设置 HOST，例如 HOST=root@your-server}"
: "${PUBLIC_BASE_URL:?请设置 PUBLIC_BASE_URL，例如 https://kebiao.example.com}"
# 默认是正式服；同一台机器上的 beta 等实例在各自的 env 文件里覆盖这几项
APP_DIR=${APP_DIR:-/opt/nufe-kebiao}
SERVICE=${SERVICE:-kebiao}
SVC_USER=${SVC_USER:-kebiao}
PORT=${PORT:-8010}
# 1：用仓库里的 deploy/kebiao.{service,socket} 安装 / 更新 systemd 单元（正式服）；0：单元由服务器自己维护（beta）
MANAGE_UNITS=${MANAGE_UNITS:-1}
# 要确保写进服务器 .env 的非敏感开关，空格分隔的 KEY=VALUE，例如 ENV_SET="ASSIST_ENABLED=1"
ENV_SET=${ENV_SET:-}
case "$APP_DIR" in /opt/nufe-kebiao|/opt/nufe-kebiao-*) ;; *) echo "APP_DIR 必须是 /opt/nufe-kebiao 或 /opt/nufe-kebiao-*" >&2; exit 1;; esac
[[ "$PUBLIC_BASE_URL" =~ ^https?://[A-Za-z0-9._:-]+(/[A-Za-z0-9._/-]*)?$ ]] || { echo "PUBLIC_BASE_URL 格式不合法" >&2; exit 1; }
[[ "$SERVICE" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo "SERVICE 格式不合法" >&2; exit 1; }
[[ "$SVC_USER" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo "SVC_USER 格式不合法" >&2; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "PORT 必须是数字" >&2; exit 1; }
if [ -n "${ADMIN_PASSWORD_HASH:-}" ] && [[ ! "$ADMIN_PASSWORD_HASH" =~ ^pbkdf2_sha256\$[0-9]+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$ ]]; then
  echo "ADMIN_PASSWORD_HASH 格式不合法" >&2
  exit 1
fi
echo "部署 $(git rev-parse --short HEAD) → $HOST:$APP_DIR（服务 $SERVICE，端口 $PORT）"
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

REMOTE_ARGS=$(printf '%q ' \
  "$PUBLIC_BASE_URL" \
  "${ADMIN_PASSWORD_HASH:-}" \
  "${FORCE_RESTART:-0}" \
  "$APP_DIR" \
  "$SERVICE" \
  "$SVC_USER" \
  "$PORT" \
  "$MANAGE_UNITS" \
  "$ENV_SET")

"${SSH[@]}" "bash -s -- $REMOTE_ARGS" <<'REMOTE'
set -euo pipefail
PUBLIC_BASE_URL="$1"
ADMIN_PASSWORD_HASH="$2"
FORCE_RESTART="$3"
APP_DIR="$4"
SERVICE="$5"
SVC_USER="$6"
PORT="$7"
MANAGE_UNITS="$8"
ENV_SET="$9"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP_DIR/data"
# 代码归 root；data/（数据库）必须整体归服务用户，否则 SQLite 只读、服务起不来
find "$APP_DIR" -path "$APP_DIR/data" -prune -o -exec chown root:root {} +
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR/data" && chmod 700 "$APP_DIR/data"

if [ ! -f "$APP_DIR/.env" ]; then
  (umask 077; cat > "$APP_DIR/.env" <<ENV
APP_SECRET_KEY=$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
CREDENTIAL_SECRET_KEY=$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
PASSWORD_STORAGE_ENABLED=false
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
  [[ "$ADMIN_PASSWORD_HASH" =~ ^pbkdf2_sha256\$[0-9]+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$ ]] || { echo "ADMIN_PASSWORD_HASH 格式不合法" >&2; exit 1; }
  (umask 077
   grep -v '^ADMIN_PASSWORD_HASH=' "$APP_DIR/.env" > "$APP_DIR/.env.tmp" || true
   printf "ADMIN_PASSWORD_HASH='%s'\n" "$ADMIN_PASSWORD_HASH" >> "$APP_DIR/.env.tmp"
   mv "$APP_DIR/.env.tmp" "$APP_DIR/.env")
fi
read -r -a _env_items <<< "$ENV_SET"
for kv in "${_env_items[@]}"; do
  [ -z "$kv" ] && continue
  [[ "$kv" =~ ^[A-Z_][A-Z0-9_]*=[A-Za-z0-9_./:@-]+$ ]] || { echo "ENV_SET 里的 $kv 不是合法的 KEY=VALUE" >&2; exit 1; }
  key=${kv%%=*}
  (umask 077
   grep -v "^$key=" "$APP_DIR/.env" > "$APP_DIR/.env.tmp" || true
   printf '%s\n' "$kv" >> "$APP_DIR/.env.tmp"
   mv "$APP_DIR/.env.tmp" "$APP_DIR/.env")
done
chown "root:$SVC_USER" "$APP_DIR/.env" && chmod 640 "$APP_DIR/.env"

DEPS_CHANGED=0
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
  DEPS_CHANGED=1
fi
if ! sha256sum -c "$APP_DIR/.venv/.requirements.sha256" >/dev/null 2>&1; then
  "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
  "$APP_DIR/.venv/bin/pip" install -q --require-hashes --no-deps -r "$APP_DIR/deploy/requirements-frozen.txt"
  sha256sum "$APP_DIR/deploy/requirements-frozen.txt" > "$APP_DIR/.venv/.requirements.sha256"
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
if [ "$MANAGE_UNITS" = 1 ]; then
  cmp -s "$APP_DIR/deploy/kebiao.socket" "/etc/systemd/system/$SERVICE.socket" || SOCKET_CHANGED=1
  cmp -s "$APP_DIR/deploy/kebiao.service" "/etc/systemd/system/$SERVICE.service" || SERVICE_CHANGED=1
  install -m 644 "$APP_DIR/deploy/kebiao.socket" "/etc/systemd/system/$SERVICE.socket"
  install -m 644 "$APP_DIR/deploy/kebiao.service" "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
fi
# 服务也要开机自启：后台刷新得在没人访问时照样跑，不能等第一个连接才把它拉起来
systemctl enable -q "$SERVICE.socket" "$SERVICE"
if [ "$SOCKET_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE.socket"; then
  systemctl stop "$SERVICE" "$SERVICE.socket"
  systemctl start "$SERVICE.socket"
fi
START=$(date +%s%N)
# ENV_SET 改了 .env：配置只在启动时读，必须重启
if [ "$FORCE_RESTART" != 1 ] && [ -z "$ENV_SET" ] && [ "$SOCKET_CHANGED" = 0 ] && [ "$SERVICE_CHANGED" = 0 ] && [ "$DEPS_CHANGED" = 0 ] && systemctl is-active -q "$SERVICE"; then
  # 信号是异步的：删掉旧结果再发，等服务写出这一次的结果
  STATUS_FILE=$(PYTHONDONTWRITEBYTECODE=1 "$APP_DIR/.venv/bin/python" -c "from app import reloader; print(reloader.status_file())")
  rm -f "$STATUS_FILE"
  systemctl reload "$SERVICE"
  if "$APP_DIR/.venv/bin/python" "$APP_DIR/deploy/wait_reload.py" "$STATUS_FILE" 45; then
    ACTION="reload"
  else
    echo "hot reload did not succeed; restarting" >&2
    systemctl restart "$SERVICE"
    ACTION="restart"
  fi
else
  systemctl restart "$SERVICE"
  ACTION="restart"
fi
curl -s -o /dev/null --max-time 30 "http://127.0.0.1:$PORT/healthz" || true
echo "$ACTION: $(( ($(date +%s%N) - START) / 1000000 )) ms until /healthz answered"
systemctl is-active "$SERVICE"
curl -s -o /dev/null -w "local / -> %{http_code}\n" "http://127.0.0.1:$PORT/"
REMOTE
