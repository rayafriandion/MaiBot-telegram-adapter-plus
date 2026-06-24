"""Telegram 出站消息编解码：将 Host 侧 MessageDict 转换为 Telegram 发送动作。"""

from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..telegram_client import TelegramClient
from ..utils import parse_topic_group_id

# 插件目录下的 voice_temp 文件夹（用于音频转码临时文件）
_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_VOICE_TEMP_DIR = _PLUGIN_DIR / "voice_temp"


def _get_voice_temp_dir() -> Path:
    """获取 voice_temp 目录，不存在则创建。"""
    _VOICE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return _VOICE_TEMP_DIR


# Telegram 视频贴纸限制
_STICKER_MAX_DURATION = 2.9  # 秒，留一点余量
_STICKER_MAX_SIZE = 256 * 1024  # 256 KB
_STICKER_RESOLUTION = 512

# 音频格式转换：Telegram sendVoice 推荐 OGG/Opus
_AUDIO_CONVERTIBLE_FORMATS = {"wav", "flac", "webm", "m4a"}  # 需要转码为 ogg 的格式


def _detect_image_format(data: bytes) -> str:
    """通过文件头魔数检测图片格式。"""
    if data[:4] == b'\x89PNG':
        return "png"
    if data[:2] == b'\xff\xd8':
        return "jpeg"
    if data[:4] == b'GIF8':
        return "gif"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "webp"
    if data[:4] == b'\x1aE\xdf\xa3':
        return "webm"
    return "unknown"


async def _convert_to_webm_sticker(raw_bytes: bytes, src_format: str) -> bytes:
    """将 GIF/PNG/JPEG 转换为 Telegram 视频贴纸格式的 WEBM。

    使用 ffmpeg 进行转换，遵循 Telegram 视频贴纸要求：
    - 分辨率 512x512（保持宽高比，不足部分透明填充）
    - VP9 编码，yuva420p 像素格式（支持透明）
    - 最长 3 秒
    - 文件大小不超过 256KB
    - GIF 保留动画；静态图片直接转换
    """
    if src_format == "webm":
        return raw_bytes

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg 未安装，无法转换贴纸格式")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        input_path = tmp / f"input.{src_format}"
        output_path = tmp / "output.webm"
        input_path.write_bytes(raw_bytes)

        # 基础视频滤镜：缩放 + 透明填充到 512x512
        base_filter = (
            f"scale={_STICKER_RESOLUTION}:{_STICKER_RESOLUTION}:force_original_aspect_ratio=decrease,"
            f"pad={_STICKER_RESOLUTION}:{_STICKER_RESOLUTION}:(ow-iw)/2:(oh-ih)/2:color=0x00000000"
        )

        # 仅对 GIF（多帧动画）添加 loop 滤镜
        if src_format == "gif":
            vf = f"{base_filter},loop=loop=-1:size=30"
        else:
            vf = base_filter

        cmd = [
            ffmpeg_path,
            "-y",
            "-t", str(_STICKER_MAX_DURATION),
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-an",
            "-b:v", "256k",
            "-crf", "30",
            str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg 转换失败: {stderr.decode('utf-8', errors='replace')}")

        result = output_path.read_bytes()

        # 如果超过 256KB，尝试更高 CRF 重新编码
        if len(result) > _STICKER_MAX_SIZE:
            cmd_high_crf = [
                ffmpeg_path,
                "-y",
                "-t", str(_STICKER_MAX_DURATION),
                "-i", str(input_path),
                "-vf", vf,
                "-c:v", "libvpx-vp9",
                "-pix_fmt", "yuva420p",
                "-an",
                "-crf", "45",
                "-b:v", "128k",
                str(output_path),
            ]
            proc2 = await asyncio.create_subprocess_exec(
                *cmd_high_crf,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=120)
            if proc2.returncode == 0:
                result = output_path.read_bytes()

        return result


async def _convert_to_ogg_opus(raw_bytes: bytes, src_format: str) -> bytes:
    """将音频转换为 OGG/Opus 格式（Telegram sendVoice 推荐格式）。

    如果源格式已经是 OGG 或 MP3，直接返回原字节。
    对于 WAV/FLAC/WEBM/M4A 等格式，使用 ffmpeg 转码为 OGG/Opus。
    临时文件存放在插件目录下的 voice_temp/ 中，转码成功后自动清理。

    Args:
        raw_bytes: 原始音频字节数据。
        src_format: 通过文件头检测到的格式字符串。

    Returns:
        OGG/Opus 格式的音频字节数据。
    """
    if src_format in ("ogg", "mp3"):
        return raw_bytes

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return raw_bytes

    # 在插件 voice_temp 目录下创建本次转码的临时子目录
    tmp = _get_voice_temp_dir() / f"ogg_{src_format}_{id(raw_bytes) & 0xFFFF:04x}"
    tmp.mkdir(parents=True, exist_ok=True)
    input_path = tmp / f"input.{src_format}"
    output_path = tmp / "output.ogg"
    input_path.write_bytes(raw_bytes)

    try:
        cmd = [
            ffmpeg_path, "-y",
            "-i", str(input_path),
            "-c:a", "libopus", "-b:a", "64k",
            "-vbr", "on", "-application", "voip",
            "-vn", str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        if proc.returncode == 0 and output_path.exists():
            result = output_path.read_bytes()
            # 转码成功，清理临时目录
            _cleanup_voice_temp(tmp)
            return result
        else:
            # 转码失败，保留临时文件便于排查
            return raw_bytes
    except Exception:
        return raw_bytes


# 模拟流式：每个 chat 最多缓存的消息编辑记录数
_MAX_EDIT_CACHE_SIZE = 200

# 消息段类型 -> Telegram chat action 映射
_SEGMENT_TYPE_TO_ACTION = {
    "image": "upload_photo",
    "voice": "upload_audio",
    "record": "upload_audio",
    "audio": "upload_audio",
    "emoji": "choose_sticker",
}


class TelegramOutboundCodec:
    """将 Host 出站消息转换为 Telegram API 调用。"""

    def __init__(self, tg_client: TelegramClient, logger: Any) -> None:
        self._tg = tg_client
        self._logger = logger
        # 模拟流式：chat_id -> 最近一次发送的 message_id
        # 使用 OrderedDict 实现 LRU 淘汰
        self._last_message_ids: OrderedDict[str, int] = OrderedDict()

    # ---- Chat Action 辅助 ----

    async def _send_chat_action_for_segments(
        self,
        chat_id: str,
        payloads: List[Dict[str, Any]],
        message_thread_id: Optional[int],
    ) -> None:
        """根据消息段类型发送对应的 chat action。

        优先级：typing（有文本时）> 媒体类型对应的 action。
        只在有文本或媒体段时发送，忽略本地-only 段。
        """
        has_text = False
        has_media = False
        media_action: Optional[str] = None

        for seg in payloads:
            if self._is_local_only_segment(seg):
                continue
            seg_type = str(seg.get("type") or "").strip()
            if seg_type == "text":
                data = seg.get("data", "")
                if isinstance(data, str) and data.strip():
                    has_text = True
            elif seg_type in _SEGMENT_TYPE_TO_ACTION:
                has_media = True
                if media_action is None:
                    media_action = _SEGMENT_TYPE_TO_ACTION[seg_type]

        # 有文本时优先发 typing，否则发媒体对应的 action
        action = "typing" if has_text else (media_action if has_media else None)
        if action is None:
            return

        try:
            await self._tg.send_chat_action(
                chat_id, action, message_thread_id=message_thread_id
            )
        except Exception as e:
            self._logger.debug(f"发送 chat action '{action}' 失败: {e}")

    # ---- 模拟流式核心逻辑 ----

    def _get_cached_message_id(self, chat_id: str) -> Optional[int]:
        """获取 chat 最近一次发送的 message_id（模拟流式用）。"""
        msg_id = self._last_message_ids.get(chat_id)
        if msg_id is not None:
            # 移到末尾（LRU）
            self._last_message_ids.move_to_end(chat_id)
        return msg_id

    def _cache_message_id(self, chat_id: str, message_id: int) -> None:
        """缓存 chat 最近一次发送的 message_id。"""
        if chat_id in self._last_message_ids:
            self._last_message_ids.move_to_end(chat_id)
        self._last_message_ids[chat_id] = message_id
        # LRU 淘汰
        while len(self._last_message_ids) > _MAX_EDIT_CACHE_SIZE:
            self._last_message_ids.popitem(last=False)

    def _clear_cached_message_id(self, chat_id: str) -> None:
        """清除 chat 的缓存（比如收到新会话信号时）。"""
        self._last_message_ids.pop(chat_id, None)

    async def _edit_last_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: Optional[str] = None,
        entities: Optional[List[Dict[str, Any]]] = None,
        link_preview_options: Optional[Dict[str, Any]] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """尝试编辑 chat 中最近一条消息（模拟流式）。

        如果编辑成功，返回 {"ok": True, ...}。
        如果失败（无缓存消息、消息太旧等），返回 {"ok": False, ...}。
        """
        last_msg_id = self._get_cached_message_id(chat_id)
        if last_msg_id is None:
            return {"ok": False, "reason": "no_cached_message"}

        try:
            result = await self._tg.edit_message_text(
                chat_id=chat_id,
                message_id=last_msg_id,
                text=text,
                parse_mode=parse_mode,
                entities=entities,
                link_preview_options=link_preview_options,
                reply_markup=reply_markup,
            )
            if result.get("ok"):
                return {"ok": True, "result": result, "edited": True}
            else:
                # 编辑失败，清除缓存
                self._clear_cached_message_id(chat_id)
                return {"ok": False, "reason": "edit_failed", "detail": result}
        except Exception as e:
            self._logger.debug(f"模拟流式编辑失败: {e}")
            self._clear_cached_message_id(chat_id)
            return {"ok": False, "reason": "exception", "detail": str(e)}

    # ---- 主发送入口 ----

    async def send_outbound_message(
        self, message: Dict[str, Any], route: Dict[str, Any]
    ) -> Dict[str, Any]:
        """处理 Host 出站消息并发送到 Telegram。

        支持三种发送模式：
        1. 流式（draft_id）：使用 sendMessageDraft（Bot API 9.3+，私聊）
        2. 模拟流式（simulate_stream=True）：使用 sendMessage + editMessageText
        3. 普通：直接 sendMessage

        Returns:
            标准化发送结果 dict。
        """
        message_info = message.get("message_info", {})
        raw_message = message.get("raw_message", [])
        group_info = message_info.get("group_info")
        user_info = message_info.get("user_info")
        additional_config = message_info.get("additional_config", {})

        # 确定目标 chat_id
        chat_id: Optional[str] = None
        parsed_thread_id: Optional[int] = None
        parsed_dm_topic_id: Optional[int] = None

        target_group_id = self._clean_optional_str(additional_config.get("platform_io_target_group_id"))
        target_user_id = self._clean_optional_str(additional_config.get("platform_io_target_user_id"))

        if target_group_id:
            chat_id, parsed_thread_id, parsed_dm_topic_id = parse_topic_group_id(target_group_id)
        elif group_info and group_info.get("group_id"):
            chat_id, parsed_thread_id, parsed_dm_topic_id = parse_topic_group_id(group_info["group_id"])
        elif target_user_id:
            chat_id = target_user_id
        elif user_info and user_info.get("user_id"):
            chat_id = user_info["user_id"]

        if not chat_id:
            return {"success": False, "error": "无法确定目标 chat_id"}

        # 解析 reply_to
        reply_to = self._extract_reply_to_from_additional(additional_config, raw_message)

        # 解析 topic
        message_thread_id = self._safe_int(additional_config.get("message_thread_id"))
        direct_messages_topic_id = self._safe_int(additional_config.get("direct_messages_topic_id"))
        if message_thread_id is None:
            message_thread_id = parsed_thread_id
        if direct_messages_topic_id is None:
            direct_messages_topic_id = parsed_dm_topic_id

        # 解析通用发送参数
        send_kwargs = self._extract_send_kwargs(additional_config)

        # raw_message 是组件列表，直接使用
        payloads = raw_message if isinstance(raw_message, list) else []
        if not payloads:
            return {"success": False, "error": "消息段为空"}

        # ---- 模式判断 ----

        # 1. 原生流式（sendMessageDraft，需要 Bot API 9.3+）
        draft_id = self._safe_int(additional_config.get("draft_id"))
        if draft_id is not None and draft_id != 0:
            return await self._send_native_streaming(
                chat_id, payloads, draft_id, reply_to,
                message_thread_id, direct_messages_topic_id, send_kwargs,
            )

        # 2. 模拟流式（editMessageText）
        simulate_stream = additional_config.get("simulate_stream", False)
        if simulate_stream:
            return await self._send_simulated_streaming(
                chat_id, payloads, reply_to,
                message_thread_id, direct_messages_topic_id, send_kwargs,
            )

        # 3. 普通发送
        return await self._send_normal(
            chat_id, payloads, reply_to,
            message_thread_id, direct_messages_topic_id, send_kwargs,
        )

    # ---- 原生流式（sendMessageDraft）----

    async def _send_native_streaming(
        self,
        chat_id: str,
        payloads: List[Dict[str, Any]],
        draft_id: int,
        reply_to: Optional[int],
        message_thread_id: Optional[int],
        direct_messages_topic_id: Optional[int],
        send_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """原生流式发送（sendMessageDraft，Bot API 9.3+，仅私聊）。"""
        # 发送 chat action 提示用户
        await self._send_chat_action_for_segments(chat_id, payloads, message_thread_id)

        # 分离文本段和媒体段
        text_parts: List[str] = []
        media_segs: List[Dict[str, Any]] = []
        for seg in payloads:
            if self._is_local_only_segment(seg):
                continue
            seg_type = str(seg.get("type") or "").strip()
            if seg_type == "text":
                data = seg.get("data", "")
                if isinstance(data, str) and data.strip():
                    text_parts.append(data)
            else:
                media_segs.append(seg)

        combined_text = "".join(text_parts)

        if combined_text.strip():
            result = await self._send_streaming_text(
                chat_id, combined_text, draft_id,
                message_thread_id, direct_messages_topic_id,
                send_kwargs, reply_to,
            )
            if not result.get("ok"):
                return {"success": False, "error": f"流式文本发送失败: {result.get('description', '未知错误')}"}

            external_id = ""
            result_data = result.get("result", {})
            if isinstance(result_data, dict):
                external_id = str(result_data.get("message_id", ""))

            # 媒体段单独发送
            for media_seg in media_segs:
                media_result = await self._send_segment(
                    chat_id, media_seg, reply_to,
                    message_thread_id, direct_messages_topic_id, send_kwargs,
                )
                if not media_result.get("ok"):
                    self._logger.warning(f"流式消息媒体段发送失败: {media_result}")

            return {"success": True, "external_message_id": external_id or None, "streaming": True}

        elif media_segs:
            last_result: Dict[str, Any] = {}
            for seg in media_segs:
                result = await self._send_segment(
                    chat_id, seg, reply_to,
                    message_thread_id, direct_messages_topic_id, send_kwargs,
                )
                if result.get("ok"):
                    last_result = result

            if last_result:
                external_id = ""
                result_data = last_result.get("result", {})
                if isinstance(result_data, dict):
                    external_id = str(result_data.get("message_id", ""))
                return {"success": True, "external_message_id": external_id or None, "streaming": True}

            return {"success": False, "error": "所有媒体段发送失败"}

        return {"success": False, "error": "消息段为空"}

    async def _send_streaming_text(
        self,
        chat_id: str,
        text: str,
        draft_id: int,
        message_thread_id: Optional[int],
        direct_messages_topic_id: Optional[int],
        send_kwargs: Dict[str, Any],
        reply_to: Optional[int],
    ) -> Dict[str, Any]:
        """使用 sendMessageDraft 发送流式文本更新。"""
        reply_parameters = None
        if reply_to is not None:
            reply_parameters = TelegramClient._build_reply_parameters(reply_to=reply_to)

        stream_kwargs = {
            k: v for k, v in send_kwargs.items()
            if k in {"parse_mode", "entities", "link_preview_options", "reply_markup"}
        }
        if reply_parameters:
            stream_kwargs["reply_parameters"] = reply_parameters
        if message_thread_id is not None:
            stream_kwargs["message_thread_id"] = message_thread_id

        return await self._tg.send_message_draft(
            chat_id, draft_id, text,
            **stream_kwargs,
        )

    # ---- 模拟流式（editMessageText）----

    async def _send_simulated_streaming(
        self,
        chat_id: str,
        payloads: List[Dict[str, Any]],
        reply_to: Optional[int],
        message_thread_id: Optional[int],
        direct_messages_topic_id: Optional[int],
        send_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """模拟流式发送（sendMessage + editMessageText）。

        策略：
        - 第一条消息：sendMessage 发送初始内容
        - 后续消息：editMessageText 编辑上一条消息
        - 如果编辑失败，回退到 sendMessage
        """
        # 发送 chat action 提示用户
        await self._send_chat_action_for_segments(chat_id, payloads, message_thread_id)

        # 提取所有文本内容
        text_parts: List[str] = []
        has_media = False
        for seg in payloads:
            if self._is_local_only_segment(seg):
                continue
            seg_type = str(seg.get("type") or "").strip()
            if seg_type == "text":
                data = seg.get("data", "")
                if isinstance(data, str) and data.strip():
                    text_parts.append(data)
            else:
                has_media = True

        combined_text = "".join(text_parts)

        if not combined_text.strip() and not has_media:
            return {"success": False, "error": "消息段为空"}

        # 提取编辑用参数
        edit_kwargs = {
            k: v for k, v in send_kwargs.items()
            if k in {"parse_mode", "entities", "link_preview_options", "reply_markup"}
        }

        # 尝试编辑上一条消息
        if combined_text.strip():
            edit_result = await self._edit_last_message(
                chat_id=chat_id,
                text=combined_text,
                **edit_kwargs,
            )

            if edit_result.get("ok"):
                # 编辑成功 → 模拟流式效果
                result_data = edit_result["result"].get("result", {})
                external_id = str(result_data.get("message_id", "")) if isinstance(result_data, dict) else ""

                # 如果有媒体，单独发送
                if has_media:
                    for seg in payloads:
                        if self._is_local_only_segment(seg):
                            continue
                        seg_type = str(seg.get("type") or "").strip()
                        if seg_type != "text":
                            await self._send_segment(
                                chat_id, seg, reply_to,
                                message_thread_id, direct_messages_topic_id, send_kwargs,
                            )

                return {
                    "success": True,
                    "external_message_id": external_id or None,
                    "simulated_streaming": True,
                    "edited": True,
                }

            # 编辑失败 → 回退到发送新消息
            self._logger.debug(f"模拟流式编辑失败，回退到普通发送: {edit_result.get('reason')}")

        # 发送新消息（首次或编辑失败回退）
        last_result: Dict[str, Any] = {}
        errors: List[str] = []
        sent_any = False

        for seg in payloads:
            if self._is_local_only_segment(seg):
                continue
            current_reply = None if sent_any else reply_to
            result = await self._send_segment(
                chat_id, seg, current_reply,
                message_thread_id, direct_messages_topic_id, send_kwargs,
            )
            if result.get("ok"):
                sent_any = True
                last_result = result
                # 缓存 message_id 供后续编辑
                result_data = result.get("result", {})
                if isinstance(result_data, dict):
                    msg_id = result_data.get("message_id")
                    if msg_id:
                        self._cache_message_id(chat_id, int(msg_id))
            else:
                errors.append(self._format_send_error(seg, result))

        if not sent_any:
            return {"success": False, "error": "; ".join(errors) or "所有消息段发送失败"}

        external_id = ""
        result_data = last_result.get("result", {})
        if isinstance(result_data, dict):
            external_id = str(result_data.get("message_id", ""))

        return {
            "success": True,
            "external_message_id": external_id or None,
            "simulated_streaming": True,
        }

    # ---- 普通发送 ----

    async def _send_normal(
        self,
        chat_id: str,
        payloads: List[Dict[str, Any]],
        reply_to: Optional[int],
        message_thread_id: Optional[int],
        direct_messages_topic_id: Optional[int],
        send_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """普通发送模式。"""
        # 发送 chat action 提示用户
        await self._send_chat_action_for_segments(chat_id, payloads, message_thread_id)

        last_result: Dict[str, Any] = {}
        errors: List[str] = []
        sent_any = False

        for seg in payloads:
            if self._is_local_only_segment(seg):
                continue
            current_reply = None if sent_any else reply_to
            result = await self._send_segment(
                chat_id, seg, current_reply,
                message_thread_id, direct_messages_topic_id, send_kwargs,
            )
            if result.get("ok"):
                sent_any = True
                last_result = result
            else:
                errors.append(self._format_send_error(seg, result))

        if not sent_any:
            return {"success": False, "error": "; ".join(errors) or "所有消息段发送失败"}

        external_id = ""
        result_data = last_result.get("result", {})
        if isinstance(result_data, dict):
            external_id = str(result_data.get("message_id", ""))

        return {"success": True, "external_message_id": external_id or None}

    # ---- 参数提取 ----

    def _extract_send_kwargs(self, additional: Dict[str, Any]) -> Dict[str, Any]:
        """从 additional_config 中提取通用发送参数。"""
        kwargs: Dict[str, Any] = {}

        parse_mode = self._clean_optional_str(additional.get("parse_mode"))
        if parse_mode:
            kwargs["parse_mode"] = parse_mode

        entities = additional.get("entities")
        if entities and isinstance(entities, list):
            kwargs["entities"] = entities

        lpo = additional.get("link_preview_options")
        if lpo and isinstance(lpo, dict):
            kwargs["link_preview_options"] = lpo
        elif additional.get("disable_web_page_preview") is not None:
            kwargs["link_preview_options"] = {
                "is_disabled": bool(additional["disable_web_page_preview"])
            }

        if additional.get("disable_notification") is not None:
            kwargs["disable_notification"] = bool(additional["disable_notification"])

        if additional.get("protect_content") is not None:
            kwargs["protect_content"] = bool(additional["protect_content"])

        reply_markup = additional.get("reply_markup")
        if reply_markup and isinstance(reply_markup, dict):
            kwargs["reply_markup"] = reply_markup

        message_effect_id = self._clean_optional_str(additional.get("message_effect_id"))
        if message_effect_id:
            kwargs["message_effect_id"] = message_effect_id

        caption_entities = additional.get("caption_entities")
        if caption_entities and isinstance(caption_entities, list):
            kwargs["caption_entities"] = caption_entities

        if additional.get("show_caption_above_media") is not None:
            kwargs["show_caption_above_media"] = bool(additional["show_caption_above_media"])

        if additional.get("has_spoiler") is not None:
            kwargs["has_spoiler"] = bool(additional["has_spoiler"])

        return kwargs

    # ---- 工具方法 ----

    @staticmethod
    def _extract_audio_bytes_from_data(seg_data: Any) -> Optional[bytes]:
        """从消息段的 data 字段中提取音频字节数据。

        支持多种格式：
        - 纯 base64 字符串
        - base64:// 前缀的字符串
        - data:audio/xxx;base64, 前缀的字符串
        - {"file": "base64://..."} 字典格式（NapCat/mimo 风格）
        - {"file": "http://..."} 字典格式（URL，返回 None 由调用方处理 URL 发送）

        Returns:
            解码后的音频字节，或 None（无法提取或数据为 URL）
        """
        if not seg_data:
            return None

        # 字典格式：提取 file 字段
        if isinstance(seg_data, dict):
            file_val = seg_data.get("file", "")
            if isinstance(file_val, str):
                return TelegramOutboundCodec._extract_audio_bytes_from_data(file_val)
            return None

        if not isinstance(seg_data, str):
            return None

        # URL 格式：不在此处理，返回 None
        if seg_data.startswith("http"):
            return None

        # 去掉 data:audio/xxx;base64, 前缀
        b64_str = seg_data
        if b64_str.startswith("data:"):
            comma_idx = b64_str.find(",")
            if comma_idx != -1:
                b64_str = b64_str[comma_idx + 1:]
        elif b64_str.startswith("base64://"):
            b64_str = b64_str[len("base64://"):]

        b64_str = b64_str.strip()
        if not b64_str:
            return None

        try:
            return base64.b64decode(b64_str)
        except Exception:
            return None

    @staticmethod
    def _cleanup_voice_temp(tmp_dir: Path) -> None:
        """清理 voice_temp 下的临时目录。"""
        import shutil as _shutil
        try:
            if tmp_dir.exists():
                _shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    async def _send_audio_bytes_with_temp(
        self,
        chat_id: str,
        audio_bytes: bytes,
        reply_to: Optional[int],
        message_thread_id: Optional[int],
        direct_messages_topic_id: Optional[int],
        seg_kwargs: Dict[str, Any],
        sender_type: str = "voice",
    ) -> Dict[str, Any]:
        """将音频字节写入临时文件，转码为 OGG/Opus，发送后根据结果清理。

        流程：
        1. 检测音频格式
        2. 若非 OGG/MP3，用 ffmpeg 转码为 OGG/Opus（写入临时文件）
        3. 用 multipart/form-data 发送
        4. 发送成功 → 删除临时文件
        5. 发送失败 → 保留临时文件（文件名含 chat_id 和时间戳，便于排查）

        Args:
            chat_id: 目标聊天 ID。
            audio_bytes: 原始音频字节。
            sender_type: "voice" 用 sendVoice，"audio" 用 sendAudio。

        Returns:
            Telegram API 响应 dict。
        """
        # 用于记录临时目录路径（转码后的 OGG），None 表示无需清理
        tmpdir: Optional[Path] = None
        tmpdir_save: Optional[Path] = None
        final_bytes = audio_bytes

        try:
            # 1. 检测格式
            filename, _ = TelegramClient._detect_audio_format(audio_bytes)
            src_fmt = filename.rsplit(".", 1)[-1] if "." in filename else ""

            # 2. 非 OGG/MP3 → 转码为 OGG/Opus（写入本地临时文件）
            if src_fmt in _AUDIO_CONVERTIBLE_FORMATS:
                self._logger.info(f"音频格式 {src_fmt} 需要转码为 OGG/Opus")
                ffmpeg_path = shutil.which("ffmpeg")
                if not ffmpeg_path:
                    self._logger.warning("ffmpeg 不可用，直接发送原始音频数据")
                else:
                    # 在插件 voice_temp 目录下创建临时子目录
                    tmpdir_save = _get_voice_temp_dir() / f"voice_{chat_id}_{id(audio_bytes) & 0xFFFF:04x}"
                    tmpdir_save.mkdir(parents=True, exist_ok=True)
                    input_path = tmpdir_save / f"input.{src_fmt}"
                    output_path = tmpdir_save / "output.ogg"
                    try:
                        input_path.write_bytes(audio_bytes)
                    except Exception as e:
                        self._logger.error(f"写入临时音频文件失败: {input_path}, 错误: {e}")
                        self._cleanup_voice_temp(tmpdir_save)
                        return {"ok": False, "description": f"写入临时文件失败: {e}"}

                    cmd = [
                        ffmpeg_path, "-y",
                        "-i", str(input_path),
                        "-c:a", "libopus", "-b:a", "64k",
                        "-vbr", "on", "-application", "voip",
                        "-vn", str(output_path),
                    ]
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

                    if proc.returncode == 0 and output_path.exists():
                        final_bytes = output_path.read_bytes()
                        # 记录临时目录，发送成功后清理
                        tmpdir = tmpdir_save
                        self._logger.info(
                            f"音频转码成功: {src_fmt}→ogg, "
                            f"原始 {len(audio_bytes)}B → 转码后 {len(final_bytes)}B, "
                            f"临时目录: {tmpdir}"
                        )
                    else:
                        err_msg = stderr.decode("utf-8", errors="replace")[:200]
                        self._logger.warning(f"音频转码失败，使用原始数据发送: {err_msg}")
                        # 转码失败：清理临时目录
                        self._cleanup_voice_temp(tmpdir_save)
                        tmpdir = None

            # 3. 用 multipart/form-data 发送
            if sender_type == "audio":
                sender = self._tg.send_audio_bytes
            else:
                sender = self._tg.send_voice_bytes

            result = await sender(
                chat_id, final_bytes,
                reply_to=reply_to,
                message_thread_id=message_thread_id,
                direct_messages_topic_id=direct_messages_topic_id,
                **seg_kwargs,
            )

            # 4. 根据发送结果清理临时文件
            if result.get("ok"):
                self._logger.info(f"语音消息发送成功: chat_id={chat_id}")
                # 发送成功 → 删除 voice_temp 中的临时目录
                if tmpdir is not None:
                    self._cleanup_voice_temp(tmpdir)
            else:
                # 发送失败 → 保留临时文件，记录路径便于排查
                if tmpdir is not None:
                    self._logger.warning(
                        f"语音消息发送失败，临时文件已保留: {tmpdir}, "
                        f"error={result.get('description', result)}"
                    )
                else:
                    self._logger.warning(
                        f"语音消息发送失败: "
                        f"error={result.get('description', result)}"
                    )

            return result

        except Exception as e:
            self._logger.error(f"语音消息发送异常: {e}")
            # 异常时也保留临时文件
            if tmpdir is not None:
                self._logger.error(f"异常时临时文件保留: {tmpdir}")
            return {"ok": False, "description": str(e)}

    @staticmethod
    def _is_local_only_segment(seg: Dict[str, Any]) -> bool:
        seg_type = str(seg.get("type") or "").strip()
        if seg_type in {"reply", "at", "forward"}:
            return True
        # dict 类型：检查是否包含可处理的音频/语音数据
        if seg_type == "dict":
            data = seg.get("data", {})
            if isinstance(data, dict):
                # 直接包含 file 字段（如 {"file": "base64://..."}）
                file_val = data.get("file", "")
                if isinstance(file_val, str) and ("base64://" in file_val or file_val.startswith("http")):
                    return False
                # 嵌套格式：{"type": "record", "data": {"file": "base64://..."}}
                inner_data = data.get("data", {})
                if isinstance(inner_data, dict):
                    inner_file = inner_data.get("file", "")
                    if isinstance(inner_file, str) and ("base64://" in inner_file or inner_file.startswith("http")):
                        return False
            return True
        return False

    @staticmethod
    def _format_send_error(seg: Dict[str, Any], result: Dict[str, Any]) -> str:
        seg_type = str(seg.get("type") or "unknown").strip() or "unknown"
        description = str(
            result.get("description")
            or result.get("error")
            or result.get("error_code")
            or "发送失败"
        ).strip()
        return f"{seg_type}: {description}"

    async def _send_segment(
        self,
        chat_id: str,
        seg: Dict[str, Any],
        reply_to: Optional[int],
        message_thread_id: Optional[int],
        direct_messages_topic_id: Optional[int],
        send_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """发送单个消息段。"""
        seg_type = str(seg.get("type") or "").strip()
        seg_data = seg.get("data", "")
        binary_b64 = seg.get("binary_data_base64", "")

        seg_kwargs = dict(send_kwargs)
        if seg.get("parse_mode"):
            seg_kwargs["parse_mode"] = seg["parse_mode"]
        if seg.get("disable_notification") is not None:
            seg_kwargs["disable_notification"] = seg["disable_notification"]
        if seg.get("protect_content") is not None:
            seg_kwargs["protect_content"] = seg["protect_content"]
        if seg.get("reply_markup"):
            seg_kwargs["reply_markup"] = seg["reply_markup"]

        try:
            if seg_type == "text":
                text = seg_data if isinstance(seg_data, str) else str(seg_data)
                if not text.strip():
                    return {"ok": False}
                return await self._tg.send_message(
                    chat_id, text, reply_to,
                    message_thread_id=message_thread_id,
                    direct_messages_topic_id=direct_messages_topic_id,
                    **seg_kwargs,
                )
            elif seg_type == "image":
                if binary_b64:
                    image_bytes = base64.b64decode(binary_b64)
                    return await self._tg.send_photo_bytes(
                        chat_id, image_bytes,
                        reply_to=reply_to,
                        message_thread_id=message_thread_id,
                        direct_messages_topic_id=direct_messages_topic_id,
                        **seg_kwargs,
                    )
                elif isinstance(seg_data, str) and seg_data.startswith("http"):
                    return await self._tg.send_photo_url(
                        chat_id, seg_data,
                        reply_to=reply_to,
                        message_thread_id=message_thread_id,
                        direct_messages_topic_id=direct_messages_topic_id,
                        **seg_kwargs,
                    )
                return {"ok": False}
            elif seg_type in ("voice", "record", "audio"):
                audio_bytes: Optional[bytes] = None

                # 1. 优先从 binary_data_base64 提取
                if binary_b64:
                    try:
                        audio_bytes = base64.b64decode(binary_b64)
                    except Exception as e:
                        self._logger.warning(f"binary_data_base64 解码失败: {e}")

                # 2. 从 data 字段提取（支持多种格式）
                if audio_bytes is None:
                    audio_bytes = self._extract_audio_bytes_from_data(seg_data)

                # 3. 如果有音频字节，走临时文件转码 + multipart/form-data 发送
                if audio_bytes is not None:
                    sender_type = "audio" if seg_type == "audio" else "voice"
                    return await self._send_audio_bytes_with_temp(
                        chat_id, audio_bytes, reply_to,
                        message_thread_id, direct_messages_topic_id,
                        seg_kwargs, sender_type=sender_type,
                    )

                # 4. 尝试 data 字段作为 URL
                if isinstance(seg_data, str) and seg_data.startswith("http"):
                    if seg_type == "audio":
                        sender = self._tg.send_audio_url
                    else:
                        sender = self._tg.send_voice_url
                    return await sender(
                        chat_id, seg_data,
                        reply_to=reply_to,
                        message_thread_id=message_thread_id,
                        direct_messages_topic_id=direct_messages_topic_id,
                        **seg_kwargs,
                    )

                return {"ok": False}
            elif seg_type == "emoji":
                sticker_file_id = seg.get("file_id")
                if sticker_file_id:
                    return await self._tg.send_sticker(
                        chat_id, sticker_file_id,
                        reply_to=reply_to,
                        message_thread_id=message_thread_id,
                        direct_messages_topic_id=direct_messages_topic_id,
                        **seg_kwargs,
                    )
                if binary_b64:
                    raw_bytes = base64.b64decode(binary_b64)
                    src_format = _detect_image_format(raw_bytes)
                    if src_format in ("gif", "webp"):
                        sticker_bytes = await _convert_to_webm_sticker(raw_bytes, src_format)
                    elif src_format == "webm":
                        sticker_bytes = raw_bytes
                    else:
                        sticker_bytes = raw_bytes
                    return await self._tg.send_sticker_bytes(
                        chat_id, sticker_bytes,
                        reply_to=reply_to,
                        message_thread_id=message_thread_id,
                        direct_messages_topic_id=direct_messages_topic_id,
                        **seg_kwargs,
                    )
                return {"ok": False}
            elif seg_type == "dict":
                # 处理 dict 类型中可能包含的音频数据
                # 支持两种格式：
                #   直接：{"file": "base64://..."}
                #   嵌套：{"type": "record", "data": {"file": "base64://..."}}
                seg_data_dict = seg.get("data", {})
                if isinstance(seg_data_dict, dict):
                    file_val = seg_data_dict.get("file", "")
                    # 嵌套格式：data.data.file
                    if not file_val:
                        inner = seg_data_dict.get("data", {})
                        if isinstance(inner, dict):
                            file_val = inner.get("file", "")
                    if isinstance(file_val, str) and file_val:
                        if file_val.startswith("http"):
                            return await self._tg.send_voice_url(
                                chat_id, file_val,
                                reply_to=reply_to,
                                message_thread_id=message_thread_id,
                                direct_messages_topic_id=direct_messages_topic_id,
                                **seg_kwargs,
                            )
                        # base64 音频：解码 → 转码 → 发送
                        audio_bytes = self._extract_audio_bytes_from_data(file_val)
                        if audio_bytes is not None:
                            result = await self._send_audio_bytes_with_temp(
                                chat_id, audio_bytes, reply_to,
                                message_thread_id, direct_messages_topic_id,
                                seg_kwargs, sender_type="voice",
                            )
                            return result
                # 其他 dict 数据当作 local-only
                return {"ok": True}
            elif self._is_local_only_segment(seg):
                return {"ok": True}
            else:
                if seg_type:
                    self._logger.debug(f"跳过不支持的发送类型: {seg_type}")
                return {"ok": False}
        except Exception as e:
            self._logger.warning(f"Telegram 发送 {seg_type} 失败: {e}")
            return {"ok": False, "description": str(e)}

    def _extract_reply_to_from_additional(
        self, additional: Dict[str, Any], raw_message: List[Dict[str, Any]]
    ) -> Optional[int]:
        """提取回复目标消息 ID。"""
        reply_id = additional.get("reply_message_id")
        if reply_id:
            return self._safe_int(reply_id)

        for seg in raw_message:
            if isinstance(seg, dict) and seg.get("type") == "reply":
                data = seg.get("data")
                if isinstance(data, dict):
                    return self._safe_int(data.get("target_message_id"))
                return self._safe_int(data)
        return None

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_optional_str(value: Any) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None
