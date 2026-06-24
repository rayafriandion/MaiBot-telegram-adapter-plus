"""Telegram Bot API HTTP 客户端。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import json

if TYPE_CHECKING:
    import aiohttp

try:
    import aiohttp
    from aiohttp import ClientTimeout

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    from aiohttp_socks import ProxyConnector  # type: ignore

    SOCKS_AVAILABLE = True
except ImportError:
    SOCKS_AVAILABLE = False


def _detect_system_proxy() -> Optional[str]:
    """自动检测系统代理设置。

    优先顺序：
    1. urllib.request.getproxies() 获取系统代理（读取 Windows 系统代理设置）
    2. 环境变量 HTTP_PROXY / HTTPS_PROXY
    3. 返回 None（无代理）
    """
    import os
    import urllib.request

    # 1. 读取系统代理（Windows 控制面板 / macOS 系统偏好设置中的代理）
    try:
        proxies = urllib.request.getproxies()
        system_proxy = proxies.get("https") or proxies.get("http")
        if system_proxy:
            return system_proxy
    except Exception:
        pass

    # 2. 读取环境变量
    env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if env_proxy:
        return env_proxy

    return None


class TelegramClient:
    """Telegram Bot API 异步客户端。"""

    def __init__(
        self,
        token: str,
        api_base: str = "https://api.telegram.org",
        *,
        proxy_mode: str = "auto",
        proxy_url: Optional[str] = None,
    ) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

        # 解析代理配置
        self._proxy_url: Optional[str] = None
        self._trust_env: bool = False

        if proxy_mode == "disabled":
            # 显式禁用代理
            self._proxy_url = None
            self._trust_env = False
        elif proxy_mode == "manual":
            # 手动指定代理地址
            self._proxy_url = proxy_url if proxy_url else None
            self._trust_env = False
        elif proxy_mode == "env":
            # 从环境变量读取
            self._proxy_url = None
            self._trust_env = True
        else:
            # auto: 先检测系统代理，没有则尝试环境变量
            self._proxy_url = _detect_system_proxy()
            self._trust_env = False
            if not self._proxy_url:
                # 系统代理检测失败，回退到环境变量
                self._trust_env = True

        self._proxy_is_socks = self._is_socks(self._proxy_url) if self._proxy_url else False

    @classmethod
    def is_available(cls) -> bool:
        return AIOHTTP_AVAILABLE

    async def ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = ClientTimeout(total=80)
            connector = None
            if self._proxy_is_socks and self._proxy_url and SOCKS_AVAILABLE:
                connector = ProxyConnector.from_url(self._proxy_url)
            self._session = aiohttp.ClientSession(
                timeout=timeout, connector=connector, trust_env=self._trust_env
            )
        # 代理信息会在 plugin.py 中通过 logger 输出
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.token}/{method}"

    def _http_proxy(self) -> Optional[str]:
        if self._proxy_url and not self._proxy_is_socks:
            return self._proxy_url
        return None

    @staticmethod
    def _is_socks(proxy_url: Optional[str]) -> bool:
        if not proxy_url:
            return False
        try:
            return urlparse(proxy_url).scheme.lower().startswith("socks")
        except Exception:
            return False

    # ---- ReplyParameters 构建 ----

    @staticmethod
    def _build_reply_parameters(
        reply_to: Optional[int] = None,
        chat_id: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
        allow_sending_without_reply: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """构建 ReplyParameters 对象（Bot API 7.0+ 替代 reply_to_message_id）。"""
        if reply_to is None:
            return None
        params: Dict[str, Any] = {"message_id": reply_to}
        if chat_id is not None:
            params["chat_id"] = chat_id
        if quote is not None:
            params["quote"] = quote
        if quote_parse_mode is not None:
            params["quote_parse_mode"] = quote_parse_mode
        if quote_entities is not None:
            params["quote_entities"] = quote_entities
        if quote_position is not None:
            params["quote_position"] = quote_position
        if allow_sending_without_reply is not None:
            params["allow_sending_without_reply"] = allow_sending_without_reply
        return params

    @classmethod
    def _append_reply(
        cls,
        payload: Dict[str, Any],
        reply_to: Optional[int] = None,
        chat_id: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
        allow_sending_without_reply: Optional[bool] = None,
    ) -> None:
        """将 reply_parameters 追加到 payload（JSON 请求）。"""
        reply_params = cls._build_reply_parameters(
            reply_to=reply_to,
            chat_id=chat_id,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        if reply_params is not None:
            payload["reply_parameters"] = reply_params

    @classmethod
    def _append_reply_form(
        cls,
        form: aiohttp.FormData,
        reply_to: Optional[int] = None,
        chat_id: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
        allow_sending_without_reply: Optional[bool] = None,
    ) -> None:
        """将 reply_parameters 追加到 form-data 请求。"""
        reply_params = cls._build_reply_parameters(
            reply_to=reply_to,
            chat_id=chat_id,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        if reply_params is not None:
            form.add_field("reply_parameters", json.dumps(reply_params))

    # ---- Topic 相关 ----

    @staticmethod
    def _append_topic(
        payload: Dict[str, Any],
        message_thread_id: Optional[int],
        direct_messages_topic_id: Optional[int],
    ) -> None:
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if direct_messages_topic_id is not None:
            payload["direct_messages_topic_id"] = direct_messages_topic_id

    @staticmethod
    def _append_topic_form(
        form: aiohttp.FormData,
        message_thread_id: Optional[int],
        direct_messages_topic_id: Optional[int],
    ) -> None:
        if message_thread_id is not None:
            form.add_field("message_thread_id", str(message_thread_id))
        if direct_messages_topic_id is not None:
            form.add_field("direct_messages_topic_id", str(direct_messages_topic_id))

    # ---- 通用可选参数追加 ----

    @staticmethod
    def _append_if_set(payload: Dict[str, Any], key: str, value: Any) -> None:
        """仅当 value 不为 None 时追加到 payload。"""
        if value is not None:
            payload[key] = value

    @staticmethod
    def _append_if_set_form(form: aiohttp.FormData, key: str, value: Any) -> None:
        """仅当 value 不为 None 时追加到 form-data。"""
        if value is not None:
            form.add_field(key, str(value))

    # ---- API 方法 ----

    async def get_me(self) -> Dict[str, Any]:
        session = await self.ensure_session()
        async with session.get(self._url("getMe"), proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def get_updates(
        self,
        offset: Optional[int] = None,
        timeout: int = 20,
        allowed_updates: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        if limit is not None:
            payload["limit"] = limit
        async with session.post(
            self._url("getUpdates"), json=payload, proxy=self._http_proxy()
        ) as resp:
            return await resp.json()

    async def get_file_path(self, file_id: str) -> Optional[str]:
        session = await self.ensure_session()
        async with session.post(
            self._url("getFile"), json={"file_id": file_id}, proxy=self._http_proxy()
        ) as resp:
            data = await resp.json()
            if data.get("ok") and data.get("result"):
                return data["result"].get("file_path")
        return None

    async def download_file_bytes(self, file_path: str) -> bytes:
        session = await self.ensure_session()
        file_url = f"{self.api_base}/file/bot{self.token}/{file_path}"
        async with session.get(file_url, proxy=self._http_proxy()) as resp:
            resp.raise_for_status()
            return await resp.read()

    async def send_message_draft(
        self,
        chat_id: Union[int, str],
        draft_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        entities: Optional[List[Dict[str, Any]]] = None,
        link_preview_options: Optional[Dict[str, Any]] = None,
        reply_parameters: Optional[Dict[str, Any]] = None,
        message_thread_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """流式发送消息草稿（sendMessageDraft）。

        用于在私聊中实时流式传输消息内容，类似 ChatGPT 的打字效果。
        通过相同的 draft_id 多次调用，Telegram 客户端会显示动画过渡。

        参考: https://core.telegram.org/bots/api#sendmessagedraft

        Args:
            chat_id: 目标私聊 ID。
            draft_id: 草稿唯一标识符（非零），同一 draft_id 的多次更新会显示动画。
            text: 消息文本，1-4096 字符。
            parse_mode: 解析模式（HTML / MarkdownV2）。
            entities: 消息实体列表。
            link_preview_options: 链接预览选项。
            reply_parameters: 回复参数（ReplyParameters 对象）。
            message_thread_id: 话题/论坛主题 ID。
            reply_markup: 回复键盘标记。
        """
        session = await self.ensure_session()
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "draft_id": draft_id,
            "text": text,
        }
        self._append_if_set(payload, "parse_mode", parse_mode)
        self._append_if_set(payload, "entities", entities)
        self._append_if_set(payload, "link_preview_options", link_preview_options)
        self._append_if_set(payload, "reply_parameters", reply_parameters)
        self._append_if_set(payload, "message_thread_id", message_thread_id)
        self._append_if_set(payload, "reply_markup", reply_markup)

        async with session.post(
            self._url("sendMessageDraft"), json=payload, proxy=self._http_proxy()
        ) as resp:
            return await resp.json()

    async def send_rich_message_draft(
        self,
        chat_id: Union[int, str],
        draft_id: int,
        rich_message: Dict[str, Any],
        *,
        reply_parameters: Optional[Dict[str, Any]] = None,
        message_thread_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """流式发送富文本消息草稿（sendRichMessageDraft）。

        用于流式传输富文本消息（Bot API 10.1+）。
        支持 HTML / Markdown 格式的富文本内容。

        参考: https://core.telegram.org/bots/api#sendrichmessagedraft

        Args:
            chat_id: 目标私聊 ID。
            draft_id: 草稿唯一标识符（非零）。
            rich_message: 富文本消息对象，支持 html / markdown / is_rtl / skip_entity_detection 字段。
            reply_parameters: 回复参数。
            message_thread_id: 话题/论坛主题 ID。
            reply_markup: 回复键盘标记。
        """
        session = await self.ensure_session()
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "draft_id": draft_id,
            "rich_message": rich_message,
        }
        self._append_if_set(payload, "reply_parameters", reply_parameters)
        self._append_if_set(payload, "message_thread_id", message_thread_id)
        self._append_if_set(payload, "reply_markup", reply_markup)

        async with session.post(
            self._url("sendRichMessageDraft"), json=payload, proxy=self._http_proxy()
        ) as resp:
            return await resp.json()

    async def send_rich_message(
        self,
        chat_id: Union[int, str],
        rich_message: Dict[str, Any],
        *,
        reply_parameters: Optional[Dict[str, Any]] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """发送富文本消息（sendRichMessage，Bot API 10.1+）。

        支持 HTML / Markdown 格式的富文本内容，包含表格、列表、引用等复杂排版。

        参考: https://core.telegram.org/bots/api#sendrichmessage

        Args:
            chat_id: 目标聊天 ID。
            rich_message: 富文本消息对象，支持 html / markdown / is_rtl / skip_entity_detection 字段。
            reply_parameters: 回复参数。
            message_thread_id: 话题/论坛主题 ID。
            direct_messages_topic_id: 私聊话题 ID。
            disable_notification: 是否静默发送。
            protect_content: 是否保护内容。
            reply_markup: 回复键盘标记。
            business_connection_id: 业务连接 ID。
            message_effect_id: 消息特效 ID。
        """
        session = await self.ensure_session()
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": rich_message,
        }
        self._append_reply(payload, **(reply_parameters or {}))
        self._append_topic(payload, message_thread_id, direct_messages_topic_id)
        self._append_if_set(payload, "disable_notification", disable_notification)
        self._append_if_set(payload, "protect_content", protect_content)
        self._append_if_set(payload, "reply_markup", reply_markup)
        self._append_if_set(payload, "business_connection_id", business_connection_id)
        self._append_if_set(payload, "message_effect_id", message_effect_id)

        async with session.post(
            self._url("sendRichMessage"), json=payload, proxy=self._http_proxy()
        ) as resp:
            return await resp.json()

    async def edit_message_text(
        self,
        chat_id: Union[int, str],
        message_id: int,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        entities: Optional[List[Dict[str, Any]]] = None,
        link_preview_options: Optional[Dict[str, Any]] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """编辑已发送消息的文本（editMessageText）。

        可用于流式完成后的最终文本更新，或非流式的逐步编辑。

        参考: https://core.telegram.org/bots/api#editmessagetext
        """
        session = await self.ensure_session()
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        self._append_if_set(payload, "parse_mode", parse_mode)
        self._append_if_set(payload, "entities", entities)
        self._append_if_set(payload, "link_preview_options", link_preview_options)
        self._append_if_set(payload, "reply_markup", reply_markup)

        async with session.post(
            self._url("editMessageText"), json=payload, proxy=self._http_proxy()
        ) as resp:
            return await resp.json()

    async def send_message(
        self,
        chat_id: Union[int, str],
        text: str,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        *,
        parse_mode: Optional[str] = None,
        entities: Optional[List[Dict[str, Any]]] = None,
        link_preview_options: Optional[Dict[str, Any]] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """发送文本消息。

        遵循 Bot API 7.0+ 规范：
        - 使用 reply_parameters 替代已弃用的 reply_to_message_id
        - 支持 parse_mode / entities / link_preview_options
        - 支持 disable_notification / protect_content / reply_markup
        """
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}

        # reply_parameters
        self._append_reply(
            payload,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )

        # topic
        self._append_topic(payload, message_thread_id, direct_messages_topic_id)

        # 可选参数
        self._append_if_set(payload, "parse_mode", parse_mode)
        self._append_if_set(payload, "entities", entities)
        self._append_if_set(payload, "link_preview_options", link_preview_options)
        self._append_if_set(payload, "disable_notification", disable_notification)
        self._append_if_set(payload, "protect_content", protect_content)
        self._append_if_set(payload, "reply_markup", reply_markup)
        self._append_if_set(payload, "business_connection_id", business_connection_id)
        self._append_if_set(payload, "message_effect_id", message_effect_id)

        async with session.post(self._url("sendMessage"), json=payload, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_photo_bytes(
        self,
        chat_id: Union[int, str],
        photo_bytes: bytes,
        caption: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        *,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        show_caption_above_media: Optional[bool] = None,
        has_spoiler: Optional[bool] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以字节流发送图片。"""
        session = await self.ensure_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption)

        # reply_parameters
        self._append_reply_form(
            form,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )

        # topic
        self._append_topic_form(form, message_thread_id, direct_messages_topic_id)

        # 可选参数
        if parse_mode is not None:
            form.add_field("parse_mode", parse_mode)
        if caption_entities is not None:
            form.add_field("caption_entities", json.dumps(caption_entities))
        if show_caption_above_media is not None:
            form.add_field("show_caption_above_media", str(show_caption_above_media).lower())
        if has_spoiler is not None:
            form.add_field("has_spoiler", str(has_spoiler).lower())
        if disable_notification is not None:
            form.add_field("disable_notification", str(disable_notification).lower())
        if protect_content is not None:
            form.add_field("protect_content", str(protect_content).lower())
        if reply_markup is not None:
            form.add_field("reply_markup", json.dumps(reply_markup))
        if business_connection_id is not None:
            form.add_field("business_connection_id", business_connection_id)
        if message_effect_id is not None:
            form.add_field("message_effect_id", message_effect_id)

        form.add_field("photo", photo_bytes, filename="image.jpg", content_type="image/jpeg")
        async with session.post(self._url("sendPhoto"), data=form, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_photo_url(
        self,
        chat_id: Union[int, str],
        url: str,
        caption: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        *,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        show_caption_above_media: Optional[bool] = None,
        has_spoiler: Optional[bool] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以 URL 发送图片。"""
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"chat_id": chat_id, "photo": url}
        if caption:
            payload["caption"] = caption

        self._append_reply(
            payload,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic(payload, message_thread_id, direct_messages_topic_id)
        self._append_if_set(payload, "parse_mode", parse_mode)
        self._append_if_set(payload, "caption_entities", caption_entities)
        self._append_if_set(payload, "show_caption_above_media", show_caption_above_media)
        self._append_if_set(payload, "has_spoiler", has_spoiler)
        self._append_if_set(payload, "disable_notification", disable_notification)
        self._append_if_set(payload, "protect_content", protect_content)
        self._append_if_set(payload, "reply_markup", reply_markup)
        self._append_if_set(payload, "business_connection_id", business_connection_id)
        self._append_if_set(payload, "message_effect_id", message_effect_id)

        async with session.post(self._url("sendPhoto"), json=payload, proxy=self._http_proxy()) as resp:
            return await resp.json()

    @staticmethod
    def _detect_audio_format(data: bytes) -> tuple[str, str]:
        """通过文件头魔数检测音频格式，返回 (filename, content_type)。"""
        if data[:4] == b'OggS':
            return "voice.ogg", "audio/ogg"
        if data[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'):
            return "voice.mp3", "audio/mpeg"
        if data[:4] == b'RIFF' and data[8:12] == b'WAVE':
            return "voice.wav", "audio/wav"
        if data[:4] == b'fLaC':
            return "voice.flac", "audio/flac"
        if data[:4] == b'\x1aE\xdf\xa3':
            return "voice.webm", "audio/webm"
        if len(data) > 8 and data[4:8] in (b'ftyp', b'moov'):
            return "voice.m4a", "audio/mp4"
        return "voice.ogg", "audio/ogg"

    async def send_voice_bytes(
        self,
        chat_id: Union[int, str],
        voice_bytes: bytes,
        caption: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        *,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        duration: Optional[int] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以字节流发送语音消息。"""
        session = await self.ensure_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption)

        self._append_reply_form(
            form,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic_form(form, message_thread_id, direct_messages_topic_id)

        if parse_mode is not None:
            form.add_field("parse_mode", parse_mode)
        if caption_entities is not None:
            form.add_field("caption_entities", json.dumps(caption_entities))
        if duration is not None:
            form.add_field("duration", str(duration))
        if disable_notification is not None:
            form.add_field("disable_notification", str(disable_notification).lower())
        if protect_content is not None:
            form.add_field("protect_content", str(protect_content).lower())
        if reply_markup is not None:
            form.add_field("reply_markup", json.dumps(reply_markup))
        if business_connection_id is not None:
            form.add_field("business_connection_id", business_connection_id)
        if message_effect_id is not None:
            form.add_field("message_effect_id", message_effect_id)

        filename, content_type = self._detect_audio_format(voice_bytes)
        form.add_field("voice", voice_bytes, filename=filename, content_type=content_type)
        async with session.post(self._url("sendVoice"), data=form, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_voice_url(
        self,
        chat_id: Union[int, str],
        url: str,
        *,
        caption: Optional[str] = None,
        duration: Optional[int] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以 URL 发送语音消息（sendVoice）。"""
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"chat_id": chat_id, "voice": url}
        if caption:
            payload["caption"] = caption
        if duration is not None:
            payload["duration"] = duration

        self._append_reply(
            payload,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic(payload, message_thread_id, direct_messages_topic_id)
        self._append_if_set(payload, "parse_mode", parse_mode)
        self._append_if_set(payload, "caption_entities", caption_entities)
        self._append_if_set(payload, "disable_notification", disable_notification)
        self._append_if_set(payload, "protect_content", protect_content)
        self._append_if_set(payload, "reply_markup", reply_markup)
        self._append_if_set(payload, "business_connection_id", business_connection_id)
        self._append_if_set(payload, "message_effect_id", message_effect_id)

        async with session.post(self._url("sendVoice"), json=payload, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_audio_bytes(
        self,
        chat_id: Union[int, str],
        audio_bytes: bytes,
        *,
        caption: Optional[str] = None,
        duration: Optional[int] = None,
        performer: Optional[str] = None,
        title: Optional[str] = None,
        thumbnail: Optional[bytes] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以字节流发送音频文件（sendAudio）。

        sendAudio 比 sendVoice 更通用，支持标题(thumbnail/performer/title)等元数据。
        自动检测音频格式（OGG/MP3/WAV/FLAC/M4A）。

        Args:
            chat_id: 目标聊天 ID。
            audio_bytes: 音频原始字节数据。
            caption: 音频说明文字。
            duration: 时长（秒）。
            performer: 表演者。
            title: 音频标题。
            thumbnail: 封面缩略图字节数据（JPEG，可选）。
        """
        session = await self.ensure_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))

        if caption:
            form.add_field("caption", caption)
        if duration is not None:
            form.add_field("duration", str(duration))
        if performer is not None:
            form.add_field("performer", performer)
        if title is not None:
            form.add_field("title", title)

        self._append_reply_form(
            form,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic_form(form, message_thread_id, direct_messages_topic_id)

        if parse_mode is not None:
            form.add_field("parse_mode", parse_mode)
        if caption_entities is not None:
            form.add_field("caption_entities", json.dumps(caption_entities))
        if disable_notification is not None:
            form.add_field("disable_notification", str(disable_notification).lower())
        if protect_content is not None:
            form.add_field("protect_content", str(protect_content).lower())
        if reply_markup is not None:
            form.add_field("reply_markup", json.dumps(reply_markup))
        if business_connection_id is not None:
            form.add_field("business_connection_id", business_connection_id)
        if message_effect_id is not None:
            form.add_field("message_effect_id", message_effect_id)

        filename, content_type = self._detect_audio_format(audio_bytes)
        form.add_field("audio", audio_bytes, filename=filename, content_type=content_type)

        if thumbnail is not None:
            form.add_field("thumbnail", thumbnail, filename="thumb.jpg", content_type="image/jpeg")

        async with session.post(self._url("sendAudio"), data=form, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_audio_url(
        self,
        chat_id: Union[int, str],
        url: str,
        *,
        caption: Optional[str] = None,
        duration: Optional[int] = None,
        performer: Optional[str] = None,
        title: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以 URL 发送音频文件（sendAudio）。"""
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"chat_id": chat_id, "audio": url}
        if caption:
            payload["caption"] = caption
        if duration is not None:
            payload["duration"] = duration
        if performer is not None:
            payload["performer"] = performer
        if title is not None:
            payload["title"] = title

        self._append_reply(
            payload,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic(payload, message_thread_id, direct_messages_topic_id)
        self._append_if_set(payload, "parse_mode", parse_mode)
        self._append_if_set(payload, "caption_entities", caption_entities)
        self._append_if_set(payload, "disable_notification", disable_notification)
        self._append_if_set(payload, "protect_content", protect_content)
        self._append_if_set(payload, "reply_markup", reply_markup)
        self._append_if_set(payload, "business_connection_id", business_connection_id)
        self._append_if_set(payload, "message_effect_id", message_effect_id)

        async with session.post(self._url("sendAudio"), json=payload, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_animation_bytes(
        self,
        chat_id: Union[int, str],
        anim_bytes: bytes,
        caption: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        *,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        show_caption_above_media: Optional[bool] = None,
        has_spoiler: Optional[bool] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration: Optional[int] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以字节流发送动画/GIF。"""
        session = await self.ensure_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        if caption:
            form.add_field("caption", caption)

        self._append_reply_form(
            form,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic_form(form, message_thread_id, direct_messages_topic_id)

        if parse_mode is not None:
            form.add_field("parse_mode", parse_mode)
        if caption_entities is not None:
            form.add_field("caption_entities", json.dumps(caption_entities))
        if show_caption_above_media is not None:
            form.add_field("show_caption_above_media", str(show_caption_above_media).lower())
        if has_spoiler is not None:
            form.add_field("has_spoiler", str(has_spoiler).lower())
        if width is not None:
            form.add_field("width", str(width))
        if height is not None:
            form.add_field("height", str(height))
        if duration is not None:
            form.add_field("duration", str(duration))
        if disable_notification is not None:
            form.add_field("disable_notification", str(disable_notification).lower())
        if protect_content is not None:
            form.add_field("protect_content", str(protect_content).lower())
        if reply_markup is not None:
            form.add_field("reply_markup", json.dumps(reply_markup))
        if business_connection_id is not None:
            form.add_field("business_connection_id", business_connection_id)
        if message_effect_id is not None:
            form.add_field("message_effect_id", message_effect_id)

        form.add_field("animation", anim_bytes, filename="animation.gif", content_type="image/gif")
        async with session.post(self._url("sendAnimation"), data=form, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_video_url(
        self,
        chat_id: Union[int, str],
        url: str,
        caption: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        *,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        show_caption_above_media: Optional[bool] = None,
        has_spoiler: Optional[bool] = None,
        supports_streaming: Optional[bool] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以 URL 发送视频。"""
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"chat_id": chat_id, "video": url}
        if caption:
            payload["caption"] = caption

        self._append_reply(
            payload,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic(payload, message_thread_id, direct_messages_topic_id)
        self._append_if_set(payload, "parse_mode", parse_mode)
        self._append_if_set(payload, "caption_entities", caption_entities)
        self._append_if_set(payload, "show_caption_above_media", show_caption_above_media)
        self._append_if_set(payload, "has_spoiler", has_spoiler)
        self._append_if_set(payload, "supports_streaming", supports_streaming)
        self._append_if_set(payload, "disable_notification", disable_notification)
        self._append_if_set(payload, "protect_content", protect_content)
        self._append_if_set(payload, "reply_markup", reply_markup)
        self._append_if_set(payload, "business_connection_id", business_connection_id)
        self._append_if_set(payload, "message_effect_id", message_effect_id)

        async with session.post(self._url("sendVideo"), json=payload, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_document_url(
        self,
        chat_id: Union[int, str],
        url: str,
        caption: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        *,
        parse_mode: Optional[str] = None,
        caption_entities: Optional[List[Dict[str, Any]]] = None,
        disable_content_type_detection: Optional[bool] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以 URL 发送文档。"""
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"chat_id": chat_id, "document": url}
        if caption:
            payload["caption"] = caption

        self._append_reply(
            payload,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic(payload, message_thread_id, direct_messages_topic_id)
        self._append_if_set(payload, "parse_mode", parse_mode)
        self._append_if_set(payload, "caption_entities", caption_entities)
        self._append_if_set(payload, "disable_content_type_detection", disable_content_type_detection)
        self._append_if_set(payload, "disable_notification", disable_notification)
        self._append_if_set(payload, "protect_content", protect_content)
        self._append_if_set(payload, "reply_markup", reply_markup)
        self._append_if_set(payload, "business_connection_id", business_connection_id)
        self._append_if_set(payload, "message_effect_id", message_effect_id)

        async with session.post(self._url("sendDocument"), json=payload, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_sticker_bytes(
        self,
        chat_id: Union[int, str],
        sticker_bytes: bytes,
        *,
        emoji: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """以字节流发送贴纸（sendSticker）。

        Args:
            chat_id: 目标聊天 ID
            sticker_bytes: 贴纸的原始字节数据（PNG/WEBP 格式）
            emoji: 贴纸对应的 emoji（可选，用于搜索贴纸集）
        """
        session = await self.ensure_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))

        self._append_reply_form(
            form,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic_form(form, message_thread_id, direct_messages_topic_id)

        if emoji is not None:
            form.add_field("emoji", emoji)
        if disable_notification is not None:
            form.add_field("disable_notification", str(disable_notification).lower())
        if protect_content is not None:
            form.add_field("protect_content", str(protect_content).lower())
        if reply_markup is not None:
            form.add_field("reply_markup", json.dumps(reply_markup))
        if business_connection_id is not None:
            form.add_field("business_connection_id", business_connection_id)
        if message_effect_id is not None:
            form.add_field("message_effect_id", message_effect_id)

        # 根据文件头自动判断格式，选择合适的 filename 和 content_type
        if sticker_bytes[:4] == b'\x1aE\xdf\xa3':
            filename = "sticker.webm"
            content_type = "video/webm"
        elif sticker_bytes[:4] == b'RIFF' and sticker_bytes[8:12] == b'WEBP':
            filename = "sticker.webp"
            content_type = "image/webp"
        elif sticker_bytes[:4] == b'\x89PNG':
            filename = "sticker.png"
            content_type = "image/png"
        elif sticker_bytes[:2] == b'\xff\xd8':
            filename = "sticker.jpg"
            content_type = "image/jpeg"
        else:
            filename = "sticker.png"
            content_type = "image/png"

        form.add_field("sticker", sticker_bytes, filename=filename, content_type=content_type)
        async with session.post(self._url("sendSticker"), data=form, proxy=self._http_proxy()) as resp:
            return await resp.json()

    async def send_chat_action(
        self,
        chat_id: Union[int, str],
        action: str,
        *,
        message_thread_id: Optional[int] = None,
        business_connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """发送聊天动作（sendChatAction）。

        用于在 Bot 处理期间向用户展示状态提示，如"正在输入..."、"正在上传图片..."等。

        支持的 action：
        - typing: 正在输入（文本消息）
        - upload_photo: 正在上传图片
        - record_video: 正在录制视频
        - upload_video: 正在上传视频
        - record_audio: 正在录制语音
        - upload_audio: 正在上传语音/音频
        - upload_document: 正在上传文档
        - find_location: 正在定位
        - choose_sticker: 正在选择贴纸

        参考: https://core.telegram.org/bots/api#sendchataction

        Args:
            chat_id: 目标聊天 ID。
            action: 动作类型字符串。
            message_thread_id: 话题/论坛主题 ID（可选）。
            business_connection_id: 业务连接 ID（可选）。

        Returns:
            Telegram API 响应，成功时 {"ok": True, "result": True}。
        """
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"chat_id": chat_id, "action": action}
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if business_connection_id is not None:
            payload["business_connection_id"] = business_connection_id

        async with session.post(
            self._url("sendChatAction"), json=payload, proxy=self._http_proxy()
        ) as resp:
            return await resp.json()

    async def send_sticker(
        self,
        chat_id: Union[int, str],
        sticker: str,
        *,
        emoji: Optional[str] = None,
        reply_to: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        direct_messages_topic_id: Optional[int] = None,
        disable_notification: Optional[bool] = None,
        protect_content: Optional[bool] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        business_connection_id: Optional[str] = None,
        message_effect_id: Optional[str] = None,
        allow_sending_without_reply: Optional[bool] = None,
        chat_id_reply: Optional[Union[int, str]] = None,
        quote: Optional[str] = None,
        quote_parse_mode: Optional[str] = None,
        quote_entities: Optional[List[Dict[str, Any]]] = None,
        quote_position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """发送贴纸（sendSticker）。

        Args:
            chat_id: 目标聊天 ID
            sticker: 贴纸的 file_id 或 HTTPS URL
            emoji: 贴纸对应的 emoji（可选，用于搜索贴纸集）
        """
        session = await self.ensure_session()
        payload: Dict[str, Any] = {"chat_id": chat_id, "sticker": sticker}
        self._append_reply(
            payload,
            reply_to=reply_to,
            chat_id=chat_id_reply,
            quote=quote,
            quote_parse_mode=quote_parse_mode,
            quote_entities=quote_entities,
            quote_position=quote_position,
            allow_sending_without_reply=allow_sending_without_reply,
        )
        self._append_topic(payload, message_thread_id, direct_messages_topic_id)
        self._append_if_set(payload, "emoji", emoji)
        self._append_if_set(payload, "disable_notification", disable_notification)
        self._append_if_set(payload, "protect_content", protect_content)
        self._append_if_set(payload, "reply_markup", reply_markup)
        self._append_if_set(payload, "business_connection_id", business_connection_id)
        self._append_if_set(payload, "message_effect_id", message_effect_id)

        async with session.post(self._url("sendSticker"), json=payload, proxy=self._http_proxy()) as resp:
            return await resp.json()
