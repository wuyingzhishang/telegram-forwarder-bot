#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

run_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "请用 root 运行，或先安装 sudo。" >&2
    exit 1
  fi
}

install_docker() {
  if ! command -v curl >/dev/null 2>&1; then
    echo "缺少 curl，无法自动安装 Docker。请安装 curl 后重试。" >&2
    exit 1
  fi
  echo "正在安装 Docker Engine 和 Compose 插件……"
  curl -fsSL https://get.docker.com | run_root sh
  run_root systemctl enable --now docker
}

if ! command -v docker >/dev/null 2>&1; then
  install_docker
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "当前 Docker 没有 Compose 插件，尝试安装 Docker Engine 和 Compose 插件……"
  install_docker
fi

docker_cli=(docker)
if ! docker info >/dev/null 2>&1; then
  echo "正在启动 Docker 服务……"
  run_root systemctl enable --now docker
fi
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    docker_cli=(sudo docker)
  else
    echo "无法连接 Docker 服务，请检查 Docker 是否正常运行以及当前用户权限。" >&2
    exit 1
  fi
fi

env_file="$PWD/.env"
if [[ -f "$env_file" ]]; then
  chmod 600 "$env_file"
  read -r -p "检测到已有 .env，沿用当前配置并启动？[Y/n] " answer
  if [[ "$answer" =~ ^[Nn]([Oo])?$ ]]; then
    echo "已保留现有 .env。需要修改配置时请编辑 .env 后重新运行本脚本。"
    exit 0
  fi
else
  echo "首次部署需要 Bot Token 和管理员的 Telegram 数字用户 ID。"
  read -r -s -p "Bot Token（从 @BotFather 获取）: " bot_token
  printf '\n'
  if [[ -z "$bot_token" || "$bot_token" == *$'\n'* ]]; then
    echo "Bot Token 不能为空。" >&2
    exit 1
  fi

  read -r -p "管理员 Telegram 数字用户 ID（多个 ID 用逗号分隔）: " admin_ids
  if [[ ! "$admin_ids" =~ ^[[:space:]]*[0-9]+([[:space:]]*,[[:space:]]*[0-9]+)*[[:space:]]*$ ]]; then
    echo "管理员 ID 格式错误，请输入数字 ID；多个 ID 用逗号分隔。" >&2
    exit 1
  fi
  admin_ids="$(printf '%s' "$admin_ids" | tr -d '[:space:]')"

  if ! command -v openssl >/dev/null 2>&1; then
    echo "缺少 openssl，无法安全生成仪表盘密码。" >&2
    exit 1
  fi
  dashboard_password="$(openssl rand -hex 24)"
  umask 077
  cat > "$env_file" <<EOF
BOT_TOKEN=$bot_token
ADMIN_IDS=$admin_ids
API_ID=
API_HASH=
USER_SESSION_ENABLED=0
LOG_LEVEL=INFO
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=$dashboard_password
DASHBOARD_URL=
DASHBOARD_BIND=127.0.0.1
DASHBOARD_PORT=8080
CLOUDFLARE_TUNNEL_TOKEN=
EOF
  chmod 600 "$env_file"
fi

"${docker_cli[@]}" compose -f compose.yaml up -d --build

dashboard_username="$(sed -n 's/^DASHBOARD_USERNAME=//p' "$env_file" | head -n 1)"
dashboard_password="$(sed -n 's/^DASHBOARD_PASSWORD=//p' "$env_file" | head -n 1)"
cat <<EOF

部署完成，Bot 和仪表盘已启动。
仪表盘仅绑定 VPS 本机地址，避免 HTTP 密码在公网明文传输。
在你的电脑运行以下命令建立 SSH 隧道，然后打开 http://127.0.0.1:8080：
  ssh -L 8080:127.0.0.1:8080 <VPS用户名>@<VPS地址>
仪表盘用户名：${dashboard_username:-admin}
仪表盘密码：${dashboard_password:-请查看 .env}

常用命令：${docker_cli[*]} compose -f compose.yaml ps
查看日志：${docker_cli[*]} compose -f compose.yaml logs -f bot dashboard
EOF
