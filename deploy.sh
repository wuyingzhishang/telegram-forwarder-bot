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
select_access_mode() {
  echo "选择仪表盘访问方式："
  echo "  1) 公网 IP 直连 HTTP（最简单；密码和页面不加密，存在被监听风险）"
  echo "  2) Nginx + HTTPS（需要域名；推荐用于长期公网访问）"
  echo "  3) SSH 隧道（只允许本机访问；需要从电脑运行 SSH 命令）"
  read -r -p "请输入 1、2 或 3 [默认 1]: " access_mode
  access_mode="${access_mode:-1}"
  case "$access_mode" in
    1) dashboard_bind="0.0.0.0" ;;
    2|3) dashboard_bind="127.0.0.1" ;;
    *) echo "请输入 1、2 或 3。" >&2; exit 1 ;;
  esac
}

set_env_value() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$env_file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

if [[ -f "$env_file" ]]; then
  chmod 600 "$env_file"
  read -r -p "检测到已有 .env，沿用当前配置并启动？[Y/n] " answer
  if [[ "$answer" =~ ^[Nn]([Oo])?$ ]]; then
    echo "已保留现有 .env。需要修改配置时请编辑 .env 后重新运行本脚本。"
    exit 0
  fi
  select_access_mode
  set_env_value DASHBOARD_BIND "$dashboard_bind"
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

  select_access_mode

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
DASHBOARD_BIND=$dashboard_bind
DASHBOARD_PORT=8080
CLOUDFLARE_TUNNEL_TOKEN=
EOF
  chmod 600 "$env_file"
fi

"${docker_cli[@]}" compose -f compose.yaml up -d --build

dashboard_username="$(sed -n 's/^DASHBOARD_USERNAME=//p' "$env_file" | head -n 1)"
dashboard_password="$(sed -n 's/^DASHBOARD_PASSWORD=//p' "$env_file" | head -n 1)"
dashboard_bind="$(sed -n 's/^DASHBOARD_BIND=//p' "$env_file" | head -n 1)"
dashboard_port="$(sed -n 's/^DASHBOARD_PORT=//p' "$env_file" | head -n 1)"
cat <<EOF

部署完成，Bot 和仪表盘已启动。
仪表盘用户名：${dashboard_username:-admin}
仪表盘密码：${dashboard_password:-请查看 .env}
EOF

if [[ "$dashboard_bind" == "0.0.0.0" ]]; then
  cat <<EOF

访问方式：公网 IP 直连
浏览器访问：http://<VPS公网IP>:${dashboard_port:-8080}
需要在 VPS 防火墙和云服务商安全组中放行 TCP ${dashboard_port:-8080}。
安全提示：此方式使用未加密 HTTP，密码和页面内容可能被网络中间方截获；只建议临时或可信网络使用。
切换到 HTTPS 或 SSH 隧道时重新运行本脚本并选择对应方式。
EOF
else
  cat <<EOF

访问方式：仪表盘只绑定 VPS 本机地址。
  2) Nginx + HTTPS：按 README 中的部署文档配置域名、Nginx 与 Certbot。
  3) SSH 隧道：在你的电脑运行以下命令，再打开 http://127.0.0.1:8080：
     ssh -L 8080:127.0.0.1:8080 <VPS用户名>@<VPS地址>
EOF
fi

cat <<EOF

常用命令：${docker_cli[*]} compose -f compose.yaml ps
查看日志：${docker_cli[*]} compose -f compose.yaml logs -f bot dashboard
EOF
