# MaiBot-Telegram-Adapter-Plus

**MaiBot 的 Telegram 平台适配器插件** — 基于 [exynos967/MaiBot-Telegram-Adapter](https://github.com/exynos967/MaiBot-Telegram-Adapter) 的个人维护分支。

将 Telegram Bot 与 [MaiBot](https://github.com/Mai-with-u/MaiBot) 无缝桥接。

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-AGPL--v3-green.svg)](LICENSE)

---

## 与原版的区别

本仓库为个人维护分支，在原版基础上按需调整。如有功能建议或 Bug 反馈，请优先提交到[原版仓库](https://github.com/exynos967/MaiBot-Telegram-Adapter)。

## 安装

```bash
cd /path/to/MaiBot/plugins
git clone https://github.com/rayafriandion/MaiBot-telegram-adapter-plus.git
```

### 依赖

- `maibot_sdk` >= 2.0.0（由 MaiBot 主程序提供）
- `aiohttp` >= 3.9.0（MaiBot 主程序已包含）
- `aiohttp-socks` >= 0.8.4（可选，使用 SOCKS 代理时需要）

如需 SOCKS 代理支持：

```bash
pip install aiohttp-socks
```

## 配置

首次加载后会在插件目录生成 `config.toml`，编辑该文件：

```toml
[plugin]
enabled = true
config_version = "0.1.0"

[telegram_bot]
token = "你的Bot Token"
api_base = "https://api.telegram.org"
poll_timeout = 20
proxy_enabled = false
proxy_url = ""
proxy_from_env = false

[chat]
group_list_type = "whitelist"
group_list = []
private_list_type = "whitelist"
private_list = []
ban_user_id = []
```

配置也可通过 MaiBot WebUI 的插件配置页面进行热重载修改。

### MaiBot 主配置

在 MaiBot 主配置的 `[bot]` 中加入 Telegram Bot 的**数字 ID**：

```toml
[bot]
platforms = ["telegram:123456789"]
```

Bot 数字 ID 会在插件启动成功后的日志中输出。若缺少该配置，MaiBot 可能无法识别 bot 自身。

## 功能

### 消息类型支持

| 消息类型 | 入站（TG → MaiBot） | 出站（MaiBot → TG） |
| :---: | :---: | :---: |
| 文本 | ✅ | ✅ |
| 图片 | ✅ 自动下载转 base64 | ✅ base64 / URL |
| 语音 | ✅ 自动下载转 base64 | ✅ base64 |
| 贴纸 | ✅ 转 emoji 类型 | ✅ 以动图发送 |
| GIF 动图 | ✅ 转 emoji 类型 | ✅ 以动图发送 |
| 视频 | — | ✅ URL |
| 文件 | ✅ 转文本标记 | ✅ URL |
| 回复消息 | ✅ 关联消息 ID | ✅ reply_parameters |
| @Bot | ✅ 多种识别方式 | — |

### 流式传输

支持 Telegram Bot API 9.3+ 的原生流式传输（`sendMessageDraft`）和模拟流式（`editMessageText`）。

#### 原生流式（推荐）

使用 `sendMessageDraft` API，在私聊中实现打字机效果。通过 `additional_config` 的 `draft_id` 触发。

#### 模拟流式（editMessageText）

使用 `sendMessage` + `editMessageText` 模拟流式效果。通过 `additional_config` 的 `simulate_stream` 触发。

### 其他特性

- **黑白名单**：群组和私聊分别支持白名单/黑名单模式
- **代理支持**：HTTP / HTTPS / SOCKS5 代理
- **自定义 API 地址**：适用于自建 Telegram API 代理
- **Topic 分流**：同群不同话题独立会话
- **@Bot 识别**：支持 mention entity、reply、文本兜底匹配
- **WebUI 热重载**：配置修改后自动重连
- **流式传输**：原生 sendMessageDraft + 模拟 editMessageText 双模式
- **富文本消息**：支持 sendRichMessage / sendRichMessageDraft（Bot API 10.1+）

## 创建 Telegram Bot

1. 在 Telegram 中搜索 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot`，按提示创建
3. 获得 Bot Token，填入配置

### 群聊使用

如需在群聊中接收所有消息，必须关闭 Bot 的 Privacy Mode：

1. 向 BotFather 发送 `/setprivacy`
2. 选择你的 Bot
3. 选择 **Disable**

## 架构

```
MaiBot Host
    ↕ maibot_sdk MessageGateway (duplex)
MaiBot-Telegram-Adapter-Plus Plugin
    ↕ HTTPS (long-polling / Bot API)
Telegram
```

- 入站：长轮询 → 消息转换 → `ctx.gateway.route_message()` → Host
- 出站：Host → `@MessageGateway` handler → Telegram Bot API

## 许可证

本项目基于 AGPLv3 许可证开源。
