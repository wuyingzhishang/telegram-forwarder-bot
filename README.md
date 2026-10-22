# Telegram 消息提取与转发

一个自托管的 Telegram 转发工具。支持使用 Bot 或已登录的个人 Telegram 账号读取和发布消息；默认由 Bot API 接收用户转发的消息。

## 当前状态

Bot API 私聊接收流程支持单条消息、图片和相册；也可提交 Telegram 消息链接及选择目标。管理员可通过 `/start` 菜单或仪表盘创建机器人账号、个人账号和图片专用规则，并管理目标、默认转发对象、实时规则及历史克隆任务。可选用户会话 Worker 支持个人账号发布、链接提取及最近 1–5000 条历史克隆，并提供任务暂停、继续和取消。

Telegram 命令菜单提供 `/start`、`/botforward`、`/userforward`、`/photo`、`/extract`、`/tasks`、`/defaulttarget`、`/account` 等快捷入口。`/botforward`（“直接转发消息”）和 `/photo` 是直接投递入口，不需要填写来源 ID；收到消息后可选择机器人账号，或在已登录时选择个人账号。实时转发规则统一从“实时转发”或 `/newrule` 配置。个人账号功能需按下方说明完成二维码登录并启用 `user-session` profile。

直接把 Telegram 消息转发给机器人即可：收到后可选机器人账号或个人 Telegram 账号；已设置默认目标时发送到该目标，否则显示目标选择。个人账号发送要求 user-session 已在线，且登录账号必须与提交这条消息的账号相同。即使正在填写来源 ID 等配置，转发消息也会退出未保存的配置并正常处理；`/start` 也可退出配置回到主菜单。

## VPS 一键部署（推荐）

适用于 Ubuntu/Debian VPS。先准备 Bot Token：在 Telegram 联系 `@BotFather`，创建机器人并复制 Token。管理员数字 ID 可通过 Telegram 联系 `@userinfobot` 查询。然后 SSH 登录 VPS，在服务器终端运行：

```bash
git clone https://github.com/wuyingzhishang/telegram-forwarder-bot.git
cd telegram-forwarder-bot
bash deploy.sh
```

脚本会检查并在需要时安装 Docker，然后只要求输入从 `@BotFather` 获取的 Bot Token 和管理员 Telegram 数字用户 ID。它会自动生成仪表盘密码、保存配置并启动服务。再次运行会沿用现有配置并更新服务。

仪表盘默认只监听 VPS 本机，脚本会显示 SSH 隧道命令，用它在本地安全访问 `http://127.0.0.1:8080`。Bot 与仪表盘共享 Docker 命名卷 `relay-data`，容器更新不会清除数据。查看状态和日志：

```bash
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs -f bot dashboard
```

Windows 本地 Docker Desktop 用户也可以双击 `启动.bat`。

发布到 GitHub 前，请只提交项目源码和文档。不要上传 `.env`、`data/`、`compose.override.yaml`、虚拟环境或数据库文件；其中 `.env` 和覆盖文件可能含有凭据或个人管理员 ID。仓库自带 `.gitignore` 会排除这些本地文件，但手动使用 GitHub 网页上传时仍要逐项检查所选文件。

Cloudflare Tunnel 部署见[部署说明](docs/DEPLOYMENT.md)。

## 本地快速开始

1. 在 `@BotFather` 创建机器人，启动一次机器人私聊。
2. 复制 `.env.example` 为 `.env`，填写 `BOT_TOKEN` 和管理员自己的 Telegram 数字 ID（`ADMIN_IDS`）。
3. 安装并启动：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dashboard,user-session,dev]"
python -m forwarder
```

4. 打开仪表盘的“频道目标”页面，添加 `@公开用户名` 或数字聊天 ID。机器人必须已加入目标聊天并拥有发帖/发消息权限；目标也可以继续通过 `/addtarget` 命令添加。
5. 用户从 Telegram 原消息菜单中选择“转发”，发给机器人，再选择机器人账号或个人 Telegram 账号。默认目标可在 `/start` 的“默认转发对象”中设置；设置后消息会发送到该目标。未设置默认目标时可选择一个或多个目标。个人账号发送需由同一个 Telegram 账号登录 user-session。

在仪表盘或 Telegram `/start` 管理菜单设置实时规则。来源读取可选 Bot API 或用户账号：Bot API 模式需要 Bot 能接收来源更新；用户账号模式需要登录账号能访问来源。管理员可创建实时规则和历史克隆任务；普通用户提交消息链接后选择目标进行提取。任务记录支持暂停、继续和取消。

启用用户账号模式前，先在 `.env` 配置 Telegram `API_ID`、`API_HASH` 和 `USER_SESSION_ENABLED=1`，再运行：

```powershell
docker compose --profile user-session run --rm --no-deps -it user-session python -m forwarder.user_session_login
docker compose --profile user-session up -d --build
```

登录命令默认在本地终端显示二维码，用手机 Telegram 的“设置 → 设备 → 连接桌面设备”扫码；二维码过期会自动刷新。也可以追加 `--method phone` 使用手机号验证码登录。两步验证密码只在本地终端输入。登录态保存在 `user-session-data` 命名卷中；该文件等同账号凭据。来源账号必须已能访问对应私密频道/群组。Telegram 内容保护消息不会被提取。

## 权限和边界

- 来源频道管理员权限不是前提。用户需能查看消息，并且 Telegram 客户端允许手动转发该消息给 Bot。
- 私密频道/群组成员可以提交允许转发的内容；启用了 Telegram 内容保护/禁止转发的内容无法提交，本项目不会绕过。
- 链接读取和历史克隆由用户账号会话执行；账号无法访问的私密来源仍不可读取，Telegram 内容保护也不会绕过。
- `copyMessage` 可去除原始转发来源链接；`copyMessages` 可批量复制并保留相册分组。特定服务/付费/抽奖/账单等消息类型不可复制。
- Telegram Bot API 的复制接口始终去掉原转发来源标记；规则中的说明文字选项只处理媒体 caption，不改变纯文字消息正文。
- 可为个人账号设置默认目标以免每次选择；未设置默认目标时会要求选择。去来源链接不代表内容获得重新发布授权。
- 一个 Bot 即可完成接收和复制；如果拆成两个 Bot，应用后端需要在中间交接任务，不能依赖 bot-to-bot 私聊。
- 不要把 `.env`、bot token 或数据库提交到版本库。

仪表盘展示 Bot 心跳、24 小时成功/失败量、7 日趋势、目标频道和最近活动。活动记录只保存结果、目标和消息数量，不保存消息文本、媒体或提交者身份。

Bot API 的限制、项目参照与架构说明见 [调研报告](docs/RESEARCH.md)、[功能可行性调研](docs/FEATURE_FEASIBILITY.md) 和 [项目立项书](docs/PROJECT_CHARTER.md)。

### 默认发布账号与网页工作台

网页首页或机器人 /start 可先选择发布账号，默认有效30天。到期需重新选择，使用不会自动延长；再次选择账号可续期，清除后恢复逐次选择。网页设置对应 ADMIN_IDS 的首位管理员，其余 Telegram 用户拥有独立设置。

默认目标与账号分别保存；两者均已设置时，新提交的消息会直接发送到默认目标。个人账号必须在线且与提交者一致，不可用时不会自动改用机器人身份。自动规则有独立发布账号，首页默认仅用于手动转发及新建规则预填。

/start 提供常用操作，“自动转发与管理”进入完整管理菜单；/publisher 或 /userforward 可更改默认发布账号。

## 普通转发规则

普通转发可以保存独立规则，不需要来源 ID：发布账号、目标频道、消息类型、包含/排除词、筛选范围、文字替换、删除词/链接、前后缀、媒体说明、相册方式和机器人链接按钮。

- 网页：进入「普通转发规则」，新建并保存，然后点击「使用此规则」。
- 机器人：/start → 普通转发规则（或 /manualrules），可新建、编辑、启停、删除和选择自己的规则。创建从发布账号开始，不询问来源 ID。
- 一次选用一条普通规则，仅处理主动提交的转发消息、图片和相册。规则内发布账号和目标优先于快捷转发默认设置。消息链接提取和自动监听使用各自流程。
- 选择规则持续生效，直至主动切换；规则配置长期保存。原30天默认发布账号属于「快捷转发（不套用规则）」模式。
- 当前规则停用或删除时停止处理并提示重新选择，不会自动退回原样发送。筛选不通过时提示跳过。可切回快捷转发，继续使用默认账号与默认目标。
- 普通规则按 Telegram 用户隔离；网页管理 ADMIN_IDS 首位管理员的规则。自动监听规则保持独立。
- 个人账号任务保存提交时的处理快照。当前部署仍未启用个人账号会话，真实个人账号转发需完成登录后端到端验证。
