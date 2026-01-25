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
    # 跟戳冷却记录：{target_id: last_follow_poke_time}
    _follow_poke_cooldown: Dict[str, float] = {}

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
            follow_enabled = self.get_config("follow_poke_config.follow_poke_enabled", True)
            follow_prob = self.get_config("follow_poke_config.follow_poke_probability", 0.3)
            if (follow_enabled 
                and user_id != bot_qq 
                and str(target_id) != bot_qq
                and random.random() < follow_prob):
                
                # 检查跟戳冷却
                follow_cooldown = self.get_config("follow_poke_config.follow_poke_cooldown_seconds", 60)
                current_time = time.monotonic()
                target_id_str = str(target_id)
                last_follow_time = self._follow_poke_cooldown.get(target_id_str, 0)
                
                if current_time - last_follow_time < follow_cooldown:
                    remaining = follow_cooldown - (current_time - last_follow_time)
                    logger.info(f"[poke] 跟戳冷却中 | target={target_id} 剩余{remaining:.1f}秒")
                else:
                    await self.send_command(
                        message.stream_id,
                        CMD_SEND_POKE,
                        {"qq_id": str(target_id)},
                        storage_message=False
                    )
                    self._follow_poke_cooldown[target_id_str] = current_time
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
    action_description = "使用'戳一戳'功能友好地戳一下某人，不能代表消息内容，仅弱提示。"
    activation_type = ActionActivationType.ALWAYS
    parallel_action = True
    associated_types = ["command"]

    action_parameters = {
        "name": "要戳的用户名称（支持：昵称、'我'、'自己'、QQ号）",
        "group_id": "群ID（可选，会自动从上下文推断）",
        "reply_id": "回复消息ID（可选）",
        "poke_mode": "主动或被动（可选）",
        "reason": "戳一戳的原因说明（可选）"
    }

    action_require = [
        "**适合使用的场景：**",
        "1. 用户明确要求'戳我'、'戳一下'、'poke'时",
        "2. 友好互动氛围中，作为轻松的互动方式",
        "3. 作为对用户多次戳你的友好回应",
        "3. 在极少数需要**非文字方式强调**你的上一句话（通常是提醒或轻微不满），且认为戳一下比再发一条文字更合适时。",
        "",
        "**重要限制：**",
        "- 不要在严肃话题或用户情绪不佳时使用",
        "- **绝不能**用它来代替正常的文字交流、回答问题或提供信息。",
        "- **绝不能**在用户正常提问或聊天时使用。",
        "- 如果你不确定是否适用，**优先选择使用 'reply' 进行文字回复**。",
        "- 'reply'可以和'poke'一起使用 ",
        "- 避免对同一用户短时间内连续使用。"
    ]
    
    # 类级别的冷却记录
    _last_poke_user: Optional[str] = None
    _last_poke_group: Optional[str] = None
    _last_poke_time: float = 0.0

    def _infer_group_id_from_context(self) -> Optional[str]:
        """从上下文推断群组ID"""
        group_id = self.action_data.get("group_id")
        if group_id in (None, "", "None"):
            group_id = None

        # 从 message 对象获取
        if not group_id and hasattr(self, "message") and getattr(self.message, "message_info", None):
            group_id = getattr(self.message.message_info, "group_id", None)
        
        # 从 chat_stream 获取
        if not group_id and hasattr(self, "chat_stream") and getattr(self.chat_stream, "group_id", None):
            group_id = self.chat_stream.group_id
        
        # 从其他可能的属性获取
        if not group_id and hasattr(self, "group_id"):
            group_id = getattr(self, "group_id", None)

        return str(group_id) if group_id not in (None, "", "None") else None

    async def _get_user_id(self, name: str) -> Optional[str]:
        """获取用户ID，支持多种输入方式"""
        if not name:
            return None
        
        name = name.strip()
        
        # 情况1：用户说"我"、"自己"
        if name in {"我", "我自己", "自己", "me"}:
            # 从上下文获取当前用户ID
            if hasattr(self, "user_id") and self.user_id:
                logger.info(f"[poke] 识别'我' -> user_id={self.user_id}")
                return str(self.user_id)
            if hasattr(self, "message") and hasattr(self.message, "user_id"):
                user_id = self.message.user_id
                logger.info(f"[poke] 从message识别'我' -> user_id={user_id}")
                return str(user_id)
        
        # 情况2：直接输入QQ号
        if name.isdigit():
            logger.info(f"[poke] 直接使用QQ号 -> user_id={name}")
            return name
        
        # 情况3：通过昵称查找
        try:
            person_id = person_api.get_person_id_by_name(name)
            if person_id:
                user_id = await person_api.get_person_value(person_id, "user_id")
                if user_id:
                    logger.info(f"[poke] 通过昵称'{name}'查找 -> user_id={user_id}")
                    return str(user_id)
        except Exception as e:
            logger.warning(f"[poke] 通过昵称查找失败: {e}")
        
        return None

    def _build_send_poke_args(self, user_id: str, group_id: Optional[str]) -> List[dict]:
        """构建多种参数格式，提高兼容性"""
        candidates: List[dict] = []
        
        # 格式1：qq_id（主要格式）
        args1: dict = {"qq_id": user_id}
        if group_id:
            args1["group_id"] = group_id
        candidates.append(args1)
        
        # 格式2：target_id（备用格式）
        args2: dict = {"target_id": user_id}
        if group_id:
            args2["group_id"] = group_id
        candidates.append(args2)
        
        return candidates

    async def _send_poke(self, user_id: str, group_id: Optional[str], target_name: str) -> Tuple[bool, str]:
        """发送戳一戳命令，尝试多种参数格式"""
        for args in self._build_send_poke_args(user_id, group_id):
            try:
                logger.info(f"[poke] 尝试发送戳一戳 | args={args}")
                ok = await self.send_command(
                    CMD_SEND_POKE,
                    args,
                    storage_message=False
                )
                if ok:
                    logger.info(f"[poke] 戳一戳发送成功 | target={target_name} user_id={user_id} group_id={group_id}")
                    return True, "戳一戳成功"
            except Exception as e:
                logger.warning(f"[poke] 尝试参数 {args} 失败: {e}")
        
        return False, "所有参数格式都失败"

    async def execute(self) -> Tuple[bool, str]:
        # 检查主动戳人功能是否启用
        if not self.get_config("poke_action.enabled", True):
            logger.info("[poke] 主动戳人功能已禁用")
            return False, "[poke] 主动戳人功能已禁用"
        
        name: Optional[str] = self.action_data.get("name")
        if not name:
            return False, "[poke] 缺少参数 name"

        # 获取用户ID（支持多种方式）
        user_id = await self._get_user_id(name)
        if not user_id:
            return False, f"[poke] 无法识别用户'{name}'"

        # 推断群组ID
        group_id = self._infer_group_id_from_context()
        
        # 检查冷却时间
        cooldown_seconds = self.get_config("poke_action.cooldown_seconds", 300)
        current_time = time.time()
        
        if (self._last_poke_user == user_id 
            and self._last_poke_group == group_id
            and current_time - self._last_poke_time < cooldown_seconds):
            remaining = int(cooldown_seconds - (current_time - self._last_poke_time))
            logger.info(f"[poke] 冷却中 | user={user_id} 剩余{remaining}秒")
            return False, f"冷却中，请{remaining}秒后再试"
        
        # 发送戳一戳
        ok, result = await self._send_poke(user_id, group_id, name)
        
        if ok:
            # 更新冷却记录
            self._last_poke_user = user_id
            self._last_poke_group = group_id
            self._last_poke_time = current_time
            
            # 记录到记忆
            reason = self.action_data.get("reason", "无")
            await database_api.store_action_info(
                chat_stream=self.chat_stream,
                action_build_into_prompt=True,
                action_prompt_display=f"戳了{name}一下",
                action_done=True,
                action_data={"reason": reason, "user_id": user_id, "group_id": group_id},
                action_name=self.action_name,
            )
            return True, result
        else:
            return False, f"[poke] {result}"


# ---------- 插件注册（必须放在最后，保证类已定义） ----------
@register_plugin
class PokePlugin(BasePlugin):
    plugin_name: str = "quick_poke"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = []
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "plugin": "插件基本信息",
        "poke_config": "被戳设置",
        "follow_poke_config": "跟戳设置",
        "poke_action": "主动戳人设置",
        "usage_policy": "使用策略/文案配置(未实现在webui修改)",
    }
    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用戳一戳插件"),
            "config_version": ConfigField(type=str, default="1.2.0", description="配置文件版本"),
        },
        "poke_config": {
            "auto_poke_back": ConfigField(
                type=bool,
                default=True,
                description="是否自动回戳"
            ),
            "poke_back_probability": ConfigField(
                type=float,
                default=0.8,
                description="回戳概率(0~1)"
            ),
            "poke_back_max_times": ConfigField(
                type=int,
                default=3,
                description="反戳最大次数(随机1~此值)"
            ),
            "auto_reply_enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用 LLM 文字回复"
            ),
            "reply_probability": ConfigField(
                type=float,
                default=0.7,
                description="文字回复概率(0~1)"
            ),
            "rate_limit_seconds": ConfigField(
                type=int,
                default=30,
                description="同一用户戳一戳冷却时间（秒）"
            ),
            "max_pokes_per_minute": ConfigField(
                type=int,
                default=10,
                description="每分钟最多处理戳一戳次数（全局）"
            ),
        },
        "follow_poke_config": {
            "follow_poke_enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用跟戳（看到别人戳别人时跟着戳）"
            ),
            "follow_poke_probability": ConfigField(
                type=float,
                default=0.3,
                description="跟戳概率(0~1)"
            ),
            "follow_poke_cooldown_seconds": ConfigField(
                type=int,
                default=60,
                description="跟戳冷却时间（秒），防止对同一个被戳者频繁跟戳"
            ),
        },
        "poke_action": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用主动戳人功能"
            ),
            "cooldown_seconds": ConfigField(
                type=int,
                default=300,
                description="主动戳人冷却时间（秒），防止短时间内重复戳同一个人"
            ),
            "debug": ConfigField(
                type=bool,
                default=False,
                description="是否开启调试日志"
            ),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (PokeEventHandler.get_handler_info(), PokeEventHandler),
            (PokeAction.get_action_info(), PokeAction),
        ]
