# 功能可行性与当前实现状态

更新日期：2026-10-19。依据当前 Bot、管理向导、用户会话 Worker、仪表盘和 Docker Compose 配置整理。截图中的规则字段与 Bot 菜单能力均可实现；下表区分已实现能力与仍需本机配置或尚未实现的功能。

## 功能状态

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| `/start` 主菜单 | 已实现 | 管理员菜单提供 Bot/个人账号转发、图片规则、实时规则、目标、历史克隆、任务、消息提取和账号登录入口；普通用户可使用图片/消息提取、任务记录、默认目标和帮助。菜单操作在服务端重新校验权限。 |
| Telegram 原生命令菜单 | 已实现 | 启动时设置 `/start`、`/rules`、`/newrule`、`/botforward`、`/userforward`、`/photo`、`/extract`、`/tasks`、`/defaulttarget`、`/account`、`/history`、`/targets`、`/addtarget`、`/cancel`。多层配置仍使用 Inline Keyboard。 |
| 来源和目标配置 | 已实现 | Bot 菜单、命令和仪表盘可维护来源/目标；目标保存到数据库，不固定为某个频道。移除仍被规则引用的目标会被拒绝。 |
| 规则新建、编辑、启停、删除 | 已实现 | Bot 菜单含向导和删除确认；仪表盘也可管理规则。 |
| 消息类型筛选 | 已实现 | 全部、文字、图片、视频、文件、音频、动画、语音、视频消息、贴纸，可多选。Telegram 的特殊服务消息不保证可复制。 |
| 包含词、排除词与筛选范围 | 已实现 | 关键词按不区分大小写的子串判断；包含词任意命中即通过，排除词任意命中即跳过。范围可为单条消息或整个相册。 |
| 隐藏或保留来源 | 已实现 | 复制模式隐藏原消息来源；转发模式保留 Telegram 来源展示。转发模式不提供正文/说明改写项。 |
| 说明文字、删除/替换词、前后缀和移除链接 | 已实现 | 复制模式可改写正文和媒体说明；选择性保留或重建消息实体。超出 Telegram 文本或说明限制时仍可能发送失败。 |
| 底部 URL 按钮 | 已实现 | 规则可配置文字和 HTTP(S) URL；组合行为受 Telegram 媒体组和消息类型限制。 |
| 相册处理 | 已实现 | 可按整组匹配，也可选择合并或逐条发送；Bot API 批量复制可保留相册分组。不同媒体类型和修改模式可能使用下载/重新上传流程。 |
| 用户手动转发提取 | 已实现 | 用户私聊转发后，从已配置目标中选择一个或多个目标；Bot API 复制不附原消息来源链接。 |
| 默认转发对象 | 已实现 | 用户可在 `/start` 为自己设置默认目标；手动提交后先选择发布账号，再自动发送。未设置默认对象或自动发送失败时仍显示目标选择。 |
| 消息链接提取 | 需本机配置 | 需要 Telegram API ID/API Hash、已授权的用户会话及启动 `user-session` profile；来源账号须有权读取消息。 |
| 用户账号实时来源规则 | 需本机配置 | 规则可选择用户账号读取及 Bot 或个人账号发布；个人账号发送由 Telethon 会话完成，登录账号需要有目标发布权限。 |
| 历史克隆 | 部分实现 | 管理员选择来源、目标及最近消息数量（1–5000），可在任务列表暂停、继续、取消。尚无日期区间、媒体类型筛选、失败项重试或撤回已发布消息。 |
| 仪表盘 | 已实现 | Docker 服务提供健康检查、规则/目标管理和运行活动视图；HTTPS 公网入口可选 Cloudflare Tunnel。 |

## 截图界面与实现方式

Telegram“菜单”按钮适合暴露少数命令；第二张参考图中的按钮网格若要点击后不向聊天发送按钮文字，应采用 Inline Keyboard。项目的组合方式是：

1. Telegram 原生菜单列出常用命令。
2. 用户发送 `/start` 后，根据身份显示不同 Inline Keyboard。
3. 规则字段、目标选择、保存确认和任务控制使用 Inline Keyboard 回调。
4. 文本字段由用户直接发送给 Bot；每一步保持可取消，并对管理员回调进行权限检查。

回复键盘会把按钮文字作为普通消息发出，不适合作为包含多种配置状态的主交互界面。Telegram 菜单按钮的显示不替代后端授权检查。

## 部署和启用条件

- 标准 Docker Compose 启动 Bot 与仪表盘，不需要用户账号会话即可使用手动转发提取、Bot API 来源规则和规则/目标管理。
- 链接提取、用户账号实时规则或历史克隆需要在本地配置 `API_ID`、`API_HASH`，运行交互登录并启动 `user-session` profile。完整步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。
- 私密来源必须已对登录账号开放。受保护来源或消息被拒绝；不通过会话或文件 ID 绕过 Telegram 限制。
- Cloudflare Tunnel 可安全代理仪表盘。Workers/Pages 不作为当前 Bot 和 MTProto 后台的主运行环境。

## 仍需留意的限制

- Bot API 来源需要 Telegram 把来源新消息更新投递给该 Bot；频道通常要求 Bot 成为管理员。用户会话模式不要求来源管理员权限，但登录账号本身必须拥有访问权。
- 用户会话 Worker 要下载媒体并调用 Bot API 重新上传。较大媒体、网络中断、API 限流和 Telegram 文件限制会影响成功率。
- 任务在遇到限流时会暂停并记录原因；当前菜单可手动继续，但没有单独的“重试失败项”功能。
- 历史克隆当前不按日期、关键词或媒体类型筛选，只复制最近限定数量的可处理消息。
- `copyMessage` 去除的是 Telegram 的来源转发标记，不会清除消息正文自身包含的用户名、链接或署名；这些需要规则改写或人工检查。
- 重新发布内容仍需遵守内容权利、隐私及 Telegram 使用规则。

## 官方资料与开源参考

- [Telegram Bot FAQ](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get)
- [Bot API copyMessage](https://core.telegram.org/bots/api#copymessage) 与 [copyMessages](https://core.telegram.org/bots/api#copymessages)
- [Bot Features](https://core.telegram.org/bots/features)、[setMyCommands](https://core.telegram.org/bots/api#setmycommands)、[setChatMenuButton](https://core.telegram.org/bots/api#setchatmenubutton)
- [MTProto messages.getHistory](https://core.telegram.org/method/messages.getHistory)、[Telethon iter_messages](https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.messages.MessageMethods.iter_messages)
- GitHub 对照项目：[tgcf](https://github.com/aahnik/tgcf)、[telemirror](https://github.com/khoben/telemirror)、[Telethon](https://github.com/LonamiWebs/Telethon)、[Telegram_Forwarder](https://github.com/MrMissx/Telegram_Forwarder)。项目说明和调研结论见 [RESEARCH.md](RESEARCH.md)。
