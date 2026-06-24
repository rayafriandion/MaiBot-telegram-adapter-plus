# Telegram 适配器插件修改记录

本文档记录对 `exynos967_telegram-adapter` 插件的所有修改。

---

## 2026-06-22 贴纸发送修复

### 七、修复贴纸发送方式

**问题**：此前 Bot 发送表情/贴纸时，通过 `sendAnimation`（发送 GIF 动图）实现，而非 Telegram 原生的 `sendSticker` 方式。这导致用户收到的是动画文件而非原生贴纸体验。

#### 7.1 新增 sendSticker 方法

**文件**: `telegram_client.py`

新增 `send_sticker()` 方法，调用 Bot API 的 `sendSticker` 端点：
- 参数：`chat_id`、`sticker`（file_id 或 URL）、`emoji`、`reply_to`、`disable_notification`、`protect_content`、`reply_markup` 等
- 参考: https://core.telegram.org/bots/api#sendsticker

#### 7.2 入站保留 sticker file_id

**文件**: `codecs/__init__.py`

修改 sticker 入站处理逻辑：
- 在 `_build_binary_segment("emoji", raw_bytes)` 返回的段中额外附加 `file_id` 字段
- 这样当同一个 sticker 被 Bot 回复时，可以使用原始 file_id 通过 `sendSticker` 发送

#### 7.3 出站优先使用 sendSticker

**文件**: `codecs/outbound.py`

修改 emoji 类型出站逻辑：
- 优先检查段中是否有 `file_id` 字段
- 如果有，调用 `send_sticker()` 以原生贴纸方式发送
- 如果没有（例如非 sticker 来源的 emoji），降级到 `send_animation_bytes()`

#### 7.4 文件变更汇总

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `telegram_client.py` | 修改 | 新增 `send_sticker()` 方法 |
| `codecs/__init__.py` | 修改 | sticker 段保留 `file_id` |
| `codecs/outbound.py` | 修改 | emoji 出站优先使用 `sendSticker` |

---

## 2026-06-22 重大更新

### 一、修复 API 参数缺失问题

**文件**: `telegram_client.py`

对照 [Telegram Bot API 官方文档](https://core.telegram.org/bots/api) 进行全面修复。

#### 1.1 ReplyParameters 重构

- **旧方式**：使用已弃用的 `reply_to_message_id` + `allow_sending_without_reply` 参数
- **新方式**：使用 Bot API 7.0+ 标准的 `reply_parameters` 对象
- 新增支持：`quote`、`quote_parse_mode`、`quote_entities`、`quote_position`
- 新增 `_build_reply_parameters()`、`_append_reply()`、`_append_reply_form()` 方法

#### 1.2 sendMessage 参数补充

新增参数：
- `parse_mode` — 文本解析模式（HTML / MarkdownV2）
- `entities` — 消息实体列表
- `link_preview_options` — 链接预览选项
- `disable_notification` — 静默发送
- `protect_content` — 保护内容不被转发
- `reply_markup` — 回复键盘
- `business_connection_id` — 业务连接 ID
- `message_effect_id` — 消息特效 ID

#### 1.3 sendPhoto / sendPhotoUrl 参数补充

新增参数：
- `parse_mode` — 标题解析模式
- `caption_entities` — 标题实体列表
- `show_caption_above_media` — 标题显示在媒体上方
- `has_spoiler` — 剧透标记
- `disable_notification` — 静默发送
- `protect_content` — 保护内容
- `reply_markup` — 回复键盘
- `business_connection_id` — 业务连接 ID
- `message_effect_id` — 消息特效 ID

#### 1.4 sendVoice 参数补充

新增参数：
- `parse_mode`、`caption_entities`、`duration`
- `disable_notification`、`protect_content`、`reply_markup`
- `business_connection_id`、`message_effect_id`

#### 1.5 sendAnimation 参数补充

新增参数：
- `parse_mode`、`caption_entities`、`show_caption_above_media`
- `has_spoiler`、`width`、`height`、`duration`
- `disable_notification`、`protect_content`、`reply_markup`
- `business_connection_id`、`message_effect_id`

#### 1.6 sendVideo / sendDocument 参数补充

新增参数：
- `parse_mode`、`caption_entities`
- `show_caption_above_media`（仅 sendVideo）
- `has_spoiler`（仅 sendVideo）
- `supports_streaming`（仅 sendVideo）
- `disable_content_type_detection`（仅 sendDocument）
- `disable_notification`、`protect_content`、`reply_markup`
- `business_connection_id`、`message_effect_id`

#### 1.7 getUpdates 参数补充

新增参数：
- `limit` — 限制返回更新数量（1-100）

#### 1.8 工具方法

- 新增 `_append_if_set()` — 通用参数追加（仅当值不为 None）
- 新增 `_append_if_set_form()` — form-data 版本的通用参数追加

---

### 二、新增 API 方法

**文件**: `telegram_client.py`

#### 2.1 sendMessageDraft（流式草稿）

```python
async def send_message_draft(
    chat_id, draft_id, text, *,
    parse_mode=None, entities=None,
    link_preview_options=None, reply_parameters=None,
    message_thread_id=None, reply_markup=None,
)
```

- Bot API 9.3+ 引入，9.5+ 对所有 Bot 开放
- 用于私聊中实时流式传输消息内容
- 相同 `draft_id` 的多次调用会显示平滑动画
- 参考: https://core.telegram.org/bots/api#sendmessagedraft

#### 2.2 sendRichMessageDraft（富文本流式草稿）

```python
async def send_rich_message_draft(
    chat_id, draft_id, rich_message, *,
    reply_parameters=None, message_thread_id=None, reply_markup=None,
)
```

- Bot API 10.1+ 引入
- 支持 HTML / Markdown 格式的富文本流式传输
- 参考: https://core.telegram.org/bots/api#sendrichmessagedraft

#### 2.3 editMessageText（编辑消息）

```python
async def edit_message_text(
    chat_id, message_id, text, *,
    parse_mode=None, entities=None,
    link_preview_options=None, reply_markup=None,
)
```

- 用于模拟流式传输（编辑已发送消息）
- 也可用于流式完成后更新最终文本
- 参考: https://core.telegram.org/bots/api#editmessagetext

#### 2.4 sendRichMessage（富文本消息）

```python
async def send_rich_message(
    chat_id, rich_message, *,
    reply_parameters=None, message_thread_id=None,
    direct_messages_topic_id=None, disable_notification=None,
    protect_content=None, reply_markup=None,
    business_connection_id=None, message_effect_id=None,
)
```

- Bot API 10.1+ 引入
- 支持表格、列表、引用等复杂排版
- 参考: https://core.telegram.org/bots/api#sendrichmessage

---

### 三、出站编解码器重构

**文件**: `codecs/outbound.py`

#### 3.1 新增模拟流式传输

- 新增 `_last_message_ids` LRU 缓存（最多 200 条）
- 新增 `_get_cached_message_id()` / `_cache_message_id()` / `_clear_cached_message_id()`
- 新增 `_edit_last_message()` — 编辑 chat 中最近一条消息
- 新增 `_send_simulated_streaming()` — 模拟流式发送路径
- 新增 `_send_streaming_text()` — 原生流式文本发送
- 新增 `_send_native_streaming()` — 原生流式发送路径
- 新增 `_send_normal()` — 普通发送路径（原逻辑）

#### 3.2 三种发送模式

| 模式 | 触发方式 | API | 适用场景 |
|------|---------|-----|---------|
| 原生流式 | `additional_config.draft_id` | `sendMessageDraft` | 私聊，Bot API 9.3+ |
| 模拟流式 | `additional_config.simulate_stream=True` | `sendMessage` + `editMessageText` | 私聊+群聊，无版本要求 |
| 普通 | 无特殊标记 | `sendMessage` | 默认 |

#### 3.3 通用参数传递增强

- 新增 `_extract_send_kwargs()` 提取以下参数：
  - `parse_mode`、`entities`、`link_preview_options`
  - `disable_notification`、`protect_content`
  - `reply_markup`、`message_effect_id`
  - `caption_entities`、`show_caption_above_media`、`has_spoiler`
- 兼容旧的 `disable_web_page_preview` 字段
- segment 级别参数可覆盖全局参数

---

### 四、流式传输说明

#### 4.1 原生流式（推荐）

通过 `additional_config.draft_id` 触发：

```python
{
    "message_info": {
        "additional_config": {
            "draft_id": 12345,
            "parse_mode": "HTML",
            "platform_io_target_user_id": "987654321",
        }
    },
    "raw_message": [{"type": "text", "data": "正在生成中..."}]
}
```

**特点**：无通知、无"edited"标签、平滑动画
**限制**：仅私聊，需要 Bot API 9.3+

#### 4.2 模拟流式

通过 `additional_config.simulate_stream=True` 触发：

```python
{
    "message_info": {
        "additional_config": {
            "simulate_stream": True,
            "platform_io_target_user_id": "987654321",
        }
    },
    "raw_message": [{"type": "text", "data": "正在生成中..."}]
}
```

**特点**：支持群聊、无版本要求
**限制**：有通知、显示"edited"标签、有编辑频率限制

#### 4.3 当前限制

MaiBot Host 目前为一次性生成完整回复，不会多次调用网关更新同一条消息。因此：
- 模拟流式需要 Host 层面支持分批输出才能真正生效
- 插件已准备好所有基础设施，Host 支持后可立即启用

---

### 五、文件变更汇总

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `telegram_client.py` | 重大修改 | 补充 API 参数、新增 4 个 API 方法 |
| `codecs/outbound.py` | 重大修改 | 重构发送逻辑、新增流式支持 |
| `codecs/__init__.py` | 未修改 | 入站逻辑无需变更 |
| `plugin.py` | 未修改 | 网关逻辑无需变更 |
| `config.py` | 未修改 | 配置模型无需变更 |
| `constants.py` | 未修改 | 常量无需变更 |
| `filters.py` | 未修改 | 过滤逻辑无需变更 |
| `utils.py` | 未修改 | 工具函数无需变更 |
| `README.md` | 修改 | 新增流式传输说明 |
| `CHANGELOG.md` | 新增 | 本文档 |

---

### 六、未修改的文件（严格遵守限制）

以下文件**未做任何修改**：
- `src/` 目录下所有文件（MaiBot 本体）
- `plugins/` 目录下其他插件
- 本插件目录下的 `config_back/`、`__pycache__/`、`.git/` 等非代码目录
