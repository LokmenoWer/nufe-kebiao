#!/usr/bin/env bash
# 在本机执行：把代码同步到服务器并重启服务。
#   cp deploy/deploy.env.example deploy/deploy.env   # 填好服务器信息（该文件不进 git）
#   deploy/deploy.sh
# 也可以直接用环境变量：HOST=root@your-server PUBLIC_BASE_URL=https://example.com/kebiao deploy/deploy.sh
# 首次部署后服务器上的 .env / data/ 不会被覆盖。
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
[ -f deploy/deploy.env ] && . deploy/deploy.env
: "${HOST:?请设置 HOST，例如 HOST=root@your-server}"
: "${PUBLIC_BASE_URL:?请设置 PUBLIC_BASE_URL，例如 https://example.com/kebiao}"
APP_DIR=/opt/nufe-kebiao
SSH_OPTS=(-o IdentitiesOnly=yes)
[ -n "${SSH_KEY:-}" ] && SSH_OPTS+=(-i "$SSH_KEY")
SSH=(ssh "${SSH_OPTS[@]}" "$HOST")

rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
  --exclude .venv --exclude data --exclude .env --exclude debug --exclude my.json \
  --exclude .nufe_device_id --exclude __pycache__ --exclude .pytest_cache --exclude .git \
  --exclude deploy/deploy.env --exclude .DS_Store \
  ./ "$HOST:$APP_DIR/"

"${SSH[@]}" "PUBLIC_BASE_URL='$PUBLIC_BASE_URL' bash -s" <<'REMOTE'
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
REFRESH_INTERVAL_HOURS=12
REFRESH_ALLOWED_HOURS=0-7,12-13,22-23
ENV
  )
fi
chown root:kebiao "$APP_DIR/.env" && chmod 640 "$APP_DIR/.env"

[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
chmod -R go+rX "$APP_DIR/.venv" "$APP_DIR/app" "$APP_DIR/static"

install -m 644 "$APP_DIR/deploy/kebiao.service" /etc/systemd/system/kebiao.service
systemctl daemon-reload
systemctl enable -q kebiao
systemctl restart kebiao
for _ in $(seq 10); do systemctl is-active -q kebiao && curl -sf -o /dev/null http://127.0.0.1:8010/ && break; sleep 1; done
systemctl is-active kebiao
curl -s -o /dev/null -w "local / -> %{http_code}\n" http://127.0.0.1:8010/
REMOTE
