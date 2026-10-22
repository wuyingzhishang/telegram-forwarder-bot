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

仪表盘地址为 `http://127.0.0.1:8080`。HTTP Basic 登录由浏览器处理。频道目标和实时转发规则可直接在网页维护；添加目标后，需确认 Bot 已加入该频道并具备发送权限。端口默认只绑定回环地址；不要把 `DASHBOARD_BIND` 改为 `0.0.0.0` 后直接暴露到公网。

如果通过 HTTPS 域名访问仪表盘，可将完整地址填入 `DASHBOARD_URL`，管理员 `/start` 菜单会显示仪表盘按钮。

常用运维命令：

```bash
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs -f bot dashboard
docker compose -f compose.yaml restart bot
docker compose -f compose.yaml down
```

`docker compose down` 不会删除数据卷。只有显式删除 `relay-data` 卷才会清除频道配置和仪表盘活动记录。

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
