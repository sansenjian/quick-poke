from typing import List, Tuple, Type, Optional,Dict,Any
import json
import random
import time

from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system import (
    ConfigField,
    BasePlugin,
    register_plugin,
    BaseAction,
    BaseEventHandler,
    EventType,
    MaiMessages,
)
from src.plugin_system.apis import generator_api, person_api, database_api
from src.plugin_system.base.component_types import ComponentInfo, ActionActivationType

logger = get_logger("poke_plugin")

# ---------- 通用小工具 ----------
def _dig(obj, path: str, default=None):
    """点分路径安全取值（支持 attr / dict)"""
    cur = obj
    for seg in path.split("."):
        if cur is None:
            return default
        if hasattr(cur, seg):
            cur = getattr(cur, seg)
        elif isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return default
    return cur


# ---------- 常量 ----------
CMD_SEND_POKE: str = "SEND_POKE"
NOTICE_POKE: Dict[str, str] = {"post_type": "notice", "sub_type": "poke"}


# ---------- 事件处理器 ----------
class PokeEventHandler(BaseEventHandler):
    event_type   = EventType.ON_MESSAGE
    handler_name = "poke_message_handler"
    handler_description = "处理 QQ 戳一戳并自动回戳+文本回复"
    
    # 冷却记录：{user_id: last_trigger_time}
    _cooldown: Dict[str, float] = {}
    # 全局频率限制：记录最近一分钟内的处理时间戳
    _poke_timestamps: List[float] = []

    async def execute(self, message: MaiMessages | None) -> Tuple[bool, bool, Optional[str], None, None]:
        """早退策略：任何不符合条件的情况立即返回，减少嵌套"""
        if not message:
            return True, True, "非戳一戳消息", None, None

        raw: Optional[str] = getattr(message, "raw_message", None)
        if not raw:
            return True, True, "非戳一戳消息", None, None

        try:
            event: Dict[str, Any] = json.loads(raw)
        except Exception:
            return True, True, "非 JSON 消息", None, None

        # 确保 event 是字典
        if not isinstance(event, dict):
            return True, True, "非字典格式消息", None, None

        # 卫语句：只处理 notice/poke
        if event.get("post_type") != NOTICE_POKE["post_type"] or event.get("sub_type") != NOTICE_POKE["sub_type"]:
            return True, True, "非戳一戳消息", None, None

        # 发送者 ID（统一路径 + 早退）
        user_id_raw = _dig(message, "user_id") or _dig(message, "message_base_info.user_id")
        if not user_id_raw:
            return False, True, "无法获取发送者 user_id", None, None
        user_id: str = str(user_id_raw)

        try:
            person_id = person_api.get_person_id("qq", user_id)
            if not person_id:
                return False, True, "找不到人物 ID", None, None
            person_name = await person_api.get_person_value(person_id, "person_name")
        except Exception as e:
            logger.exception(f"[poke] 获取用户信息失败: {e}")
            return False, True, "获取用户信息异常", None, None

        # 被戳对象必须是 bot 自身
        target_id: Optional[int] = event.get("target_id")
        bot_qq = str(global_config.bot.qq_account)
        if str(target_id) != bot_qq:
            # 跟戳：看到别人戳别人，有概率跟着戳（不戳自己）
            follow_enabled = self.get_config("poke_config.follow_poke_enabled", True)
            follow_prob = self.get_config("poke_config.follow_poke_probability", 0.3)
            if (follow_enabled 
                and user_id != bot_qq 
                and str(target_id) != bot_qq
                and random.random() < follow_prob):
                await self.send_command(
                    message.stream_id,
                    CMD_SEND_POKE,
                    {"qq_id": str(target_id)},
                    storage_message=False
                )
                logger.info(f"[poke] 跟戳 | target={target_id}")
            return True, True, "戳的对象不是 bot", None, None

        # 冷却检查
        rate_limit = self.get_config("poke_config.rate_limit_seconds", 30)
        current_time = time.monotonic()
        last_time = self._cooldown.get(user_id, 0)
        if current_time - last_time < rate_limit:
            logger.info(f"[poke] 冷却中 | user={user_id} 剩余{rate_limit - (current_time - last_time):.1f}秒")
            return True, True, "冷却中", None, None
        self._cooldown[user_id] = current_time

        # 全局频率限制检查
        max_pokes = self.get_config("poke_config.max_pokes_per_minute", 10)
        # 清理超过60秒的旧记录
        self._poke_timestamps = [t for t in self._poke_timestamps if current_time - t < 60]
        if len(self._poke_timestamps) >= max_pokes:
            logger.info(f"[poke] 达到频率上限 | 当前{len(self._poke_timestamps)}/{max_pokes}次/分钟")
            return True, True, "达到频率上限", None, None
        self._poke_timestamps.append(current_time)

        # 生成回复文本
        reply_reason = person_name + (message.plain_text or "")
        logger.info(f"[poke] 接收戳一戳 | user={user_id} reason={reply_reason!r}")

        # 1. 先回戳（随机1~poke_back_max_times次，按概率触发）
        if self.get_config("poke_config.auto_poke_back", True):
            poke_back_prob = self.get_config("poke_config.poke_back_probability", 0.8)
            if random.random() < poke_back_prob:
                poke_back_max = self.get_config("poke_config.poke_back_max_times", 3)
                poke_times = random.randint(1, poke_back_max)
                for _ in range(poke_times):
                    poke_success = await self.send_command(
                        message.stream_id,
                        CMD_SEND_POKE,
                        {"qq_id": user_id},
                        storage_message=False
                    )
                    if not poke_success:
                        logger.warning("[poke] 回戳命令发送失败")

        # 2. 生成文本回复（按概率触发）
        if not self.get_config("poke_config.auto_reply_enabled", True):
            return True, True, "戳一戳已响应（仅回戳）", None, None
        
        reply_prob = self.get_config("poke_config.reply_probability", 0.7)
        if random.random() >= reply_prob:
            return True, True, "戳一戳已响应（跳过文字回复）", None, None
        
        try:
            success, data = await generator_api.generate_reply(
                chat_id=message.stream_id,
                reply_reason=reply_reason,
                enable_chinese_typo=False,
                extra_info=f"用户「{person_name}」戳了你一下{('，附带消息：' + message.plain_text) if message.plain_text else ''}。请用简短俏皮的方式回应。",
            )
            if success and data.reply_set.reply_data:
                for seg in data.reply_set.reply_data:
                    text = seg.content
                    await self.send_text(message.stream_id, text, storage_message=True)
                    logger.info(f"[poke] 文本回复：{text!r}")
                return True, True, "戳一戳已响应", None, None
        except Exception as e:
            logger.exception(f"[poke] 生成回复失败：{e}")

        return False, True, "生成回复异常", None, None


# ---------- 动作 ----------
class PokeAction(BaseAction):
    action_name = "poke"
    action_description = "使用"戳一戳"功能友好地戳一下某人，不能代表消息内容，仅弱提示。"
    activation_type = ActionActivationType.ALWAYS
    parallel_action = True
    associated_types = ["command"]

    action_parameters = {
        "name": "要戳的用户名称",
        "group_id": "群ID",
        "reply_id": "回复消息ID",
        "poke_mode": "主动或被动",
        "reason": "戳一戳的原因说明"
    }

    action_require = [
        "**仅在以下非常具体的情况下使用：**",
        "1. 当用户**明确要求**或**明确同意**你戳他时（例如用户说'你戳我一下'）。",
        "2. 作为对用户**多次、重复戳你**这一行为的一种**温和的、非文字的回应**，且你已用文字回复过。",
        "3. 在极少数需要**非文字方式强调**你的上一句话（通常是提醒或轻微不满），且认为戳一下比再发一条文字更合适时。",
        "",
        "**重要限制：**",
        "- **绝不能**用它来代替正常的文字交流、回答问题或提供信息。",
        "- **绝不能**在用户正常提问或聊天时使用。",
        "- 如果你不确定是否适用，**一律选择使用 'reply' 进行文字回复**。",
        "- 避免对同一用户短时间内连续使用。"
    ]

    async def execute(self) -> Tuple[bool, str]:
        name: Optional[str] = self.action_data.get("name")
        if not name:
            return False, "[poke] 缺少参数 name"

        person_id = person_api.get_person_id_by_name(name)
        if not person_id:
            return False, "[poke] 找不到人物"

        user_id = await person_api.get_person_value(person_id, "user_id")
        if not user_id:
            return False, "[poke] 找不到 QQ 号"

        ok = await self.send_command(CMD_SEND_POKE, {"qq_id": user_id}, storage_message=False)
        if not ok:
            return False, "[poke] 命令发送失败"

        # 记录到记忆
        await database_api.store_action_info(
            chat_stream=self.chat_stream,
            action_build_into_prompt=True,
            action_prompt_display=f"戳了{name}一下",
            action_done=True,
            action_data=self.action_data,
            action_name=self.action_name,
        )
        return True, "戳一戳成功"


# ---------- 插件注册（必须放在最后，保证类已定义） ----------
@register_plugin
class PokePlugin(BasePlugin):
    plugin_name: str = "poke_plugin"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = []
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "plugin": "插件基本信息",
        "poke_config": "戳一戳功能配置",
        "usage_policy": "使用策略/文案配置(未实现在webui修改)",
    }
    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用戳一戳插件"),
            "config_version": ConfigField(type=str, default="1.1.0", description="配置文件版本"),
        },
        "poke_config": {
            "auto_reply_enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用 LLM 文字回复"
            ),
            "reply_probability": ConfigField(
                type=float,
                default=0.7,
                description="文字回复概率(0~1)",
                example="0.5"
            ),
            "auto_poke_back": ConfigField(
                type=bool,
                default=True,
                description="是否自动回戳"
            ),
            "poke_back_probability": ConfigField(
                type=float,
                default=0.8,
                description="回戳概率(0~1)",
                example="0.5"
            ),
            "poke_back_max_times": ConfigField(
                type=int,
                default=3,
                description="反戳最大次数(随机1~此值)",
                example="5"
            ),
            "follow_poke_enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用跟戳（看到别人戳别人时跟着戳）"
            ),
            "follow_poke_probability": ConfigField(
                type=float,
                default=0.3,
                description="跟戳概率(0~1)",
                example="0.5"
            ),
            "rate_limit_seconds": ConfigField(
                type=int,
                default=30,
                description="同一用户戳一戳冷却时间（秒）",
                example="60"
            ),
            "max_pokes_per_minute": ConfigField(
                type=int,
                default=10,
                description="每分钟最多处理戳一戳次数（全局）",
                example="20"
            ),
        },
        "usage_policy": {
            "action_require": ConfigField(
                type=str,
                default=[
                    "**仅在以下非常具体的情况下使用：**",
                    "1. 当用户**明确要求**或**明确同意**你戳他时（例如用户说'你戳我一下'）。",
                    "2. 作为对用户**多次、重复戳你**这一行为的一种**温和的、非文字的回应**，且你已用文字回复过。",
                    "3. 在极少数需要**非文字方式强调**你的上一句话（通常是提醒或轻微不满），且认为戳一下比再发一条文字更合适时。",
                    "",
                    "**重要限制：**",
                    "- **绝不能**用它来代替正常的文字交流、回答问题或提供信息。",
                    "- **绝不能**在用户正常提问或聊天时使用。",
                    "- 如果你不确定是否适用，**一律选择使用 'reply' 进行文字回复**。",
                    "- 避免对同一用户短时间内连续使用。"
                ],
                description="PokeAction 的 agent 提示语，一行一条，支持 markdown",
                input_type="textarea",
            ),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (PokeEventHandler.get_handler_info(), PokeEventHandler),
            (PokeAction.get_action_info(), PokeAction),
        ]
