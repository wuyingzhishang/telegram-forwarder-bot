# Telegram 消息提取、转发与 Bot 菜单调研

调研日期：2026-10-19。范围：核对用户主动提交消息、用户会话读取来源、实时规则，以及 Telegram 原生菜单和 `/start` 交互的实现条件。

## 结论

Bot API 适合接收用户主动转发到 Bot 私聊的消息，并通过 `copyMessage` / `copyMessages` 将消息复制到配置目标。复制结果不附原消息链接，相册批量复制可保留媒体组。此路径不要求来源频道管理员权限，但用户必须能查看并且 Telegram 允许转发该消息。

只提供消息链接时，Bot API 不能读取任意来源消息或历史。需要由有权限的 Telegram 用户账号通过 MTProto 客户端（项目采用 Telethon）访问来源，再把允许处理的内容交给 Bot API 发布。私密来源要求登录账号已加入且历史对该账号可见。

两种入口可以由同一 Bot 和一个可选用户会话 Worker 承担，无需两个 Bot。目标频道、来源与规则由数据库配置，不应固定在代码中。

## 三种 Telegram 菜单界面

Telegram 的“菜单”不是单一控件：

- **Bot 命令菜单**：通过 BotFather 或 Bot API 的 `setMyCommands` 配置命令。在项目中，启动时注册 `/start`、`/rules`、`/newrule`、`/history`、`/targets`、`/addtarget`、`/cancel`，并把聊天菜单按钮设为命令菜单。
- **回复键盘**：`ReplyKeyboardMarkup` 会把按钮文字作为普通消息发送。它适合固定、简单的快捷项；复杂规则向导需要处理文本冲突和键盘状态。
- **Inline Keyboard**：按钮附在 Bot 消息下方，点击通过 callback 传递操作，不会把按钮标题作为普通聊天文本发送。它适合 `/start` 主菜单、配置向导、确认和任务控制。

当前采用“原生命令菜单 + `/start` Inline Keyboard + 向导 Inline Keyboard”。管理员看到规则、目标、历史任务等管理项；普通用户只看到提交说明和帮助。隐藏按钮只改善界面，所有写操作仍需后端校验管理员身份、资源所有者和目标有效性。仪表盘按钮只在配置了 HTTPS 地址时显示。

## 架构与接口依据

- 用户手动转发 → Bot API 私聊 → 选择目标 → `copyMessage(s)`。
- 消息链接、用户账号实时规则、历史克隆 → Telethon 用户会话读取 → 媒体下载并交给 Bot API → 配置目标。

- [Telegram Bot FAQ：Bot 能收到哪些消息](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get)：Bot 可接收用户私聊；来源群组和频道更新受成员身份、权限及隐私设置约束。
- [Bot API copyMessage](https://core.telegram.org/bots/api#copymessage)：复制消息且不附原消息链接；部分服务、付费媒体、抽奖和账单类消息不可复制。
- [Bot API copyMessages](https://core.telegram.org/bots/api#copymessages)：批量复制同一聊天中的消息；支持保留相册分组，受可复制消息类型和消息数量规则限制。
- [Bot Features](https://core.telegram.org/bots/features) 与 [Bot API 菜单方法](https://core.telegram.org/bots/api#setmycommands)：说明命令、回复键盘、Inline Keyboard 和菜单按钮的用途。
- [MTProto messages.getHistory](https://core.telegram.org/method/messages.getHistory)：历史读取属于用户客户端能力，不是 Bot API 历史查询接口。
- [Telethon iter_messages](https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.messages.MessageMethods.iter_messages)：可用于用户账号可见消息的迭代读取。

用户会话读取到的媒体需经下载和 Bot API 上传，涉及磁盘、内存、网络和 Bot API 文件限制。Cloudflare Tunnel 可转发仪表盘流量；Cloudflare Workers 的请求/事件生命周期和存储模型不适合作为当前持续运行的 polling、MTProto 会话与 SQLite Worker。

## GitHub 开源项目参考

| 项目 | 调研结论 | 对本项目的参考 |
| --- | --- | --- |
| [tgcf](https://github.com/aahnik/tgcf) | MIT；README 提到实时/历史转发、Bot/用户账号模式、过滤和格式化。调研时仓库活跃度有限，README 中 Bot 管理界面仍标注开发中。 | 参考规则和过滤模型，不作为本项目的运行依赖。 |
| [telemirror](https://github.com/khoben/telemirror) | GPL-3.0；MTProto 实时映射与过滤工具，项目对使用个人主账号有明确警告。 | 仅参考能力边界；不直接依赖或复制代码。 |
| [Telethon](https://github.com/LonamiWebs/Telethon) | MIT；Python MTProto 客户端库，提供用户账号可访问消息的读取能力。 | 用户会话 Worker 的基础库；会话文件需按账号凭据保护。 |
| [Telegram_Forwarder](https://github.com/MrMissx/Telegram_Forwarder) | GPL-3.0；Bot API 转发工具，配置侧重预设来源和目标，不能解决 Bot API 无法查询历史的问题。 | 用于对照 Bot API 转发方式，不直接依赖或复制代码。 |

项目状态和功能差异以 [FEATURE_FEASIBILITY.md](FEATURE_FEASIBILITY.md) 为准。GitHub README 和仓库活跃度只能用于初筛，不能替代代码安全、维护和许可证审查。

## 关键限制

- 用户必须能访问来源；私密链接、聊天 ID 或用户名本身不能授予权限。
- Telegram 内容保护或禁止转发的内容不处理，也不通过其他 API 路径规避。
- Bot API 实时来源模式只能处理 Bot 收到的新消息。若不把 Bot 加入来源，须使用已获准访问的用户账号模式。
- 历史任务当前只按最近消息数量读取（最多 5000 条）；未实现日期筛选、媒体类型过滤和失败项重试。
- `copyMessage(s)` 与保留转发来源是不同语义。复制隐藏 Telegram 转发来源；转发会保留来源展示，并限制内容改写选项。
- 消息可技术性复制不代表可以合法或合规地重新发布。
