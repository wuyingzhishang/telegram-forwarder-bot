# 部署说明

## Docker Compose

Ubuntu/Debian VPS 首次部署运行 `bash deploy.sh`。脚本会在缺少 Docker 时尝试安装 Docker Engine 和 Compose 插件，然后提示输入 Bot Token 和管理员 Telegram 数字用户 ID，自动生成仪表盘密码、写入权限为 `600` 的 `.env` 并启动服务。若已有 `.env`，可直接沿用当前配置。Windows 本地 Docker Desktop 用户也可双击 `启动.bat`。

Bot 与仪表盘以两个容器运行，共享一个 SQLite 命名卷。Bot 容器接收 Telegram 凭据和管理员 ID；仪表盘容器不接收 Bot Token，但需要管理员 ID 将网页规则关联到 Bot 管理员。

`.env` 至少应包含：

```dotenv
BOT_TOKEN=你的机器人令牌
ADMIN_IDS=你的Telegram用户ID
API_ID=
API_HASH=
USER_SESSION_ENABLED=0
DATABASE_PATH=data/forwarder.sqlite3
LOG_LEVEL=INFO
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=请替换成随机长密码
DASHBOARD_URL=
DASHBOARD_BIND=127.0.0.1
DASHBOARD_PORT=8080
CLOUDFLARE_TUNNEL_TOKEN=
```

启动或更新（手动维护 `.env` 时）：

```bash
docker compose -f compose.yaml up -d --build
```

部署向导会依访问方式设置监听地址：方式 1 使用 `0.0.0.0:8080` 供公网直连，方式 2 和 3 只绑定 `127.0.0.1:8080`。HTTP Basic 登录仍启用。添加频道目标后，需确认 Bot 已加入该频道并具备发送权限。

如果通过 HTTPS 域名访问仪表盘，可将完整地址填入 `DASHBOARD_URL`，管理员 `/start` 菜单会显示仪表盘按钮。

### 使用 Nginx 和 HTTPS 公开访问

部署脚本提供三种仪表盘访问方式，默认选择 1：

1. **公网 IP 直连 HTTP（默认，最简单）**：访问 `http://VPS公网IP:8080`。需要开放 8080，但 HTTP 不加密，登录密码和页面内容可能被截获。仅在你接受此风险时使用，建议只临时使用或限制来源 IP。
2. **Nginx + HTTPS**：需要域名，适合长期公网访问。容器继续绑定 `127.0.0.1:8080`，只向公网开放 SSH、HTTP 和 HTTPS。
3. **SSH 隧道**：不需要域名，容器只绑定本机；从自己电脑建立 SSH 隧道后访问。

再次运行 `bash deploy.sh` 可切换选项。手动修改 `.env` 的 `DASHBOARD_BIND` 后，运行 `docker compose -f compose.yaml up -d` 使绑定生效。

### 方式 1：公网 IP 直连

部署脚本选项 1 会把 `DASHBOARD_BIND` 设为 `0.0.0.0`，浏览器直接打开 `http://VPS公网IP:8080`，把地址中的部分替换为实际 VPS IP。UFW 启用时放行 TCP 8080：

```bash
sudo ufw allow 8080/tcp
sudo ufw status
```

还要在云服务商的防火墙/安全组中放行 TCP 8080。若能设置来源 IP 白名单，尽量只允许自己的公网 IP。此方式使用明文 HTTP，公网链路上的登录密码和页面内容没有传输加密；访问不可信网络前，请切换到方式 2 或 3。用户选择此方式表示接受风险。

### 方式 2：Nginx 和 HTTPS 公开访问

以下步骤适用于 Ubuntu/Debian VPS。先在域名 DNS 中将 `A` 记录指向 VPS 的公网 IPv4 地址；如果配置了 `AAAA` 记录，也要确保 VPS 的 IPv6 可用。仪表盘容器继续绑定 `127.0.0.1:8080`。

安装 Nginx 和 Certbot：

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

将下面配置保存为 `/etc/nginx/sites-available/telegram-forwarder`，把 `我的域名.com` 替换成实际域名。先用此 HTTP 配置让 Certbot 验证域名并签发证书：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name 我的域名.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用配置并检查 Nginx：

```bash
sudo ln -s /etc/nginx/sites-available/telegram-forwarder /etc/nginx/sites-enabled/telegram-forwarder
sudo nginx -t
sudo systemctl reload nginx
```

确认域名已解析到 VPS，且公网可以访问 80 端口后，使用域名签发证书，并让 Certbot 配置 HTTPS 和 HTTP 到 HTTPS 跳转：

```bash
sudo certbot --nginx --redirect -d 我的域名.com
```

签发后，Nginx 的 HTTPS 虚拟主机应包含以下代理配置。Certbot 通常会自动添加 `listen 443 ssl`、证书路径和 80 端口跳转；如果需要手动核对，HTTPS `server` 块可按下例设置（证书路径中的域名也要替换）：

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name 我的域名.com;

    ssl_certificate /etc/letsencrypt/live/我的域名.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/我的域名.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

SSH 登录 VPS 后，先进入项目目录；把 `/path/to/telegram-forwarder-bot` 换成实际路径。编辑 `.env`，将 `DASHBOARD_URL` 设为 `https://我的域名.com`，然后重启 Bot，让 Telegram 管理菜单显示正确的仪表盘地址：

```bash
cd /path/to/telegram-forwarder-bot
sudo nano .env
docker compose -f compose.yaml up -d
```

放行 SSH、HTTP、HTTPS。若你之前选择过方式 1 并放行了 8080，此时可删除 8080 公网规则，因为 Nginx 通过本机回环访问仪表盘：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw status numbered
```

如果状态里有 8080 的 `ALLOW` 规则，按编号删除（每条规则执行一次；删除后编号可能变化，因此每次先重新查看状态）：

```bash
sudo ufw delete <规则编号>
```

`Nginx Full` 会放行 TCP 80 和 443。只需要向公网开放这两个 Web 端口以及 SSH 端口（通常是 TCP 22；若 SSH 改过端口，应放行实际 SSH 端口）。Docker Compose 发布的 `127.0.0.1:8080:8080` 继续保持原样，无需改为 `0.0.0.0`。Certbot 自动续期可用以下命令测试：

```bash
sudo certbot renew --dry-run
```

### 方式 3：SSH 隧道

部署脚本选择选项 3 后，仪表盘只绑定 VPS 本机。无需开放 UFW 或云防火墙的 8080 端口。在自己的电脑执行下列命令，将占位内容替换为 VPS 用户名和 IP；保持终端运行，再打开 `http://127.0.0.1:8080`：

```bash
ssh -L 8080:127.0.0.1:8080 <VPS用户名>@<VPS公网IP>
```

常用运维命令：

```bash
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs -f bot dashboard
docker compose -f compose.yaml restart bot
docker compose -f compose.yaml down
```

`docker compose down` 不会删除数据卷。只有显式删除 `relay-data` 卷才会清除频道配置和仪表盘活动记录。

### 停止或卸载项目

先 SSH 登录 VPS，并进入项目目录：

```bash
cd /path/to/telegram-forwarder-bot
```

只想暂时停止服务、以后可能继续使用时，停止并移除容器即可。Docker 命名卷和 `.env` 配置会保留：

```bash
docker compose -f compose.yaml down
```

确定永久卸载且要删除项目数据时，先移除 Nginx 站点并重载 Nginx，再删除 Compose 容器和命名卷：

```bash
sudo rm /etc/nginx/sites-enabled/telegram-forwarder
sudo nginx -t
sudo systemctl reload nginx
docker compose -f compose.yaml --profile user-session down --volumes --remove-orphans
```

`--volumes` 会永久删除本项目的 SQLite 数据和 Telegram 用户会话登录态。确定不再需要后，再删除项目目录（请核对路径，避免误删其他文件）：

```bash
cd ~
rm -rf /path/to/telegram-forwarder-bot
```

如果该域名不再使用，也可以删除 Certbot 证书；若同一张证书还服务其他站点，请勿删除：

```bash
sudo certbot certificates
sudo certbot delete --cert-name 我的域名.com
```

只有这台 VPS 没有其他网站需要 Nginx 的 HTTP/HTTPS 规则时，才删除 UFW 的 `Nginx Full` 规则：

```bash
sudo ufw status numbered
sudo ufw delete allow 'Nginx Full'
```

不要为卸载这个项目而删除 Docker、关闭 UFW 或移除 SSH 放行规则；这些服务和规则可能被 VPS 上的其他项目使用。永久卸载后，建议在 Telegram 的 `@BotFather` 中撤销该机器人的 Token；若曾登录个人 Telegram 用户会话，也可在 Telegram「设置 → 设备」中终止对应会话。

## 用户账号来源与消息链接

用户会话用于 Bot API 收不到的来源、个人账号发布和历史读取。到 `my.telegram.org` 创建 Telegram API 应用，将 `API_ID` 和 `API_HASH` 写入本地 `.env`，并设置 `USER_SESSION_ENABLED=1`。随后在本机交互终端完成一次登录：

```bash
docker compose -f compose.yaml --profile user-session run --rm --no-deps -it user-session python -m forwarder.user_session_login
docker compose -f compose.yaml --profile user-session up -d --build
```

登录命令默认在该终端显示二维码。用手机 Telegram 打开“设置 → 设备 → 连接桌面设备”扫码；二维码过期时会自动刷新。如需继续使用手机号验证码登录，运行时追加 `--method phone`。两步验证密码仍在本地终端输入。Telegram `API_ID` 和 `API_HASH` 对两种方式都必需。登录态保存在独立的 `user-session-data` 卷中，请勿分享二维码或 `.session` 文件。将 Telegram 消息链接私聊发送给 Bot 后，选择目标即可创建提取任务；管理员也可从 `/start` 创建历史克隆任务（最近 1–5000 条），并在任务记录中暂停、继续或取消。

用户名来源可填写 `@username`；私密来源可使用 `-100...` 聊天 ID 或 `t.me/c/...` 消息链接。登录账号必须已加入该私密来源。普通手动转发到 Bot 后选择“个人 Telegram 账号”时，user-session 登录账号还必须与提交这条消息的 Telegram 账号一致；否则请选择机器人账号。启用了 Telegram 内容保护的消息会被拒绝处理；请勿备份或分享 `.session` 文件。

## Cloudflare Tunnel

在 Cloudflare Zero Trust 创建远程 Tunnel，将 connector token 写入 `.env` 的 `CLOUDFLARE_TUNNEL_TOKEN`，并在 Tunnel 的 Public Hostname 中配置：

- Hostname：Cloudflare 托管的域名，例如 `relay.example.com`
- Service 类型：`HTTP`
- Service URL：`http://dashboard:8080`

启动 Tunnel 容器：

```bash
docker compose -f compose.yaml --profile cloudflare up -d --build
```

为该 hostname 建立 Cloudflare Access 自助应用和允许策略，再通过 `https://relay.example.com` 登录。仪表盘自身的 Basic 登录仍然有效；仪表盘端口仍只绑定本机，cloudflared 容器通过 Docker 内部网络访问仪表盘。Tunnel token 是秘密，不要提交到 Git 或贴进公开日志。

本项目的 Python Bot 仍运行在 Docker 主机上，Cloudflare Tunnel 只代理仪表盘页面。Cloudflare Python Workers 面向请求和事件执行，并基于 Pyodide 提供 Python 支持；当前项目需要长期运行 Telegram polling 并访问持久化 SQLite，不适合直接作为 Pages/Workers 项目部署。若只把静态页面放在 Pages，API 与 Bot 仍需由主机提供。对当前结构，Docker + Tunnel 是较直接的 Cloudflare 接入方式。

## 官方资料

- [Cloudflare Tunnel 概览](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
- [通过 Cloudflare Dashboard 创建远程 Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/)
- [Cloudflare Workers Python 运行时](https://developers.cloudflare.com/workers/languages/python/)
- [Cloudflare Workers 限制](https://developers.cloudflare.com/workers/platform/limits/)
