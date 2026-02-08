"""Quick Poke 插件

处理 QQ 戳一戳事件，支持：
- 自动回戳
- LLM 文字回复
- 跟戳功能
- 冷却机制
- 主动戳人
"""
from typing import List, Tuple, Type, Optional, Dict, Any
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
    """戳一戳事件处理器

    处理 QQ 戳一戳事件，支持：
    - 自动回戳
    - LLM 文字回复
    - 跟戳功能
    - 冷却机制
    """
    event_type   = EventType.ON_MESSAGE
    handler_name = "poke_message_handler"
    handler_description = "处理 QQ 戳一戳并自动回戳+文本回复"

    # 冷却记录：{user_id: last_trigger_time}
    _cooldown: Dict[str, float] = {}
    # 全局频率限制：记录最近一分钟内的处理时间戳
    _poke_timestamps: List[float] = []
    # 跟戳冷却记录：{target_id: last_follow_poke_time}
    _follow_poke_cooldown: Dict[str, float] = {}

    def _validate_message(self, message: MaiMessages | None) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """验证消息是否为有效的戳一戳事件

        Args:
            message: 消息对象

        Returns:
            (是否有效, 事件数据)
        """
        # 检查 message 是否存在
        if not message:
            return False, None

        # 检查 raw_message
        raw = getattr(message, "raw_message", None)
        if not raw:
            return False, None

        # 解析 JSON
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return False, None

        # 检查事件类型
        if not isinstance(event, dict):
            return False, None

        # 检查是否为 notice/poke 事件
        if (event.get("post_type") != NOTICE_POKE["post_type"]
            or event.get("sub_type") != NOTICE_POKE["sub_type"]):
            return False, None

        return True, event

    async def _get_user_info(
        self,
        message: MaiMessages
    ) -> Tuple[Optional[str], Optional[str]]:
        """获取发送者的用户ID和用户名

        Args:
            message: 消息对象

        Returns:
            (user_id, person_name)
        """
        # 获取 user_id
        user_id_raw = _dig(message, "user_id") or _dig(message, "message_base_info.user_id")
        if not user_id_raw:
            return None, None

        user_id = str(user_id_raw)

        # 获取 person_name
        try:
            person_id = person_api.get_person_id("qq", user_id)
            if not person_id:
                return None, None

            person_name = await person_api.get_person_value(person_id, "person_name")
            return user_id, person_name

        except Exception as e:
            logger.exception(f"[poke] 获取用户信息失败: {e}")
            return None, None

    async def _handle_follow_poke(
        self,
        message: MaiMessages,
        event: Dict[str, Any],
        user_id: str
    ) -> Tuple[bool, str]:
        """处理跟戳逻辑（看到别人戳别人时跟着戳）

        Args:
            message: 消息对象
            event: 事件数据
            user_id: 发送者ID

        Returns:
            (是否应该早退, 早退原因)
        """
        target_id = event.get("target_id")
        bot_qq = str(global_config.bot.qq_account)

        # 如果戳的是 bot，不处理跟戳
        if str(target_id) == bot_qq:
            return False, ""

        # 检查跟戳功能是否启用
        follow_enabled = self.get_config("follow_poke_config.follow_poke_enabled", True)
        if not follow_enabled:
            return True, "戳的对象不是 bot"

        # 检查跟戳概率
        follow_prob = self.get_config("follow_poke_config.follow_poke_probability", 0.3)
        if (user_id == bot_qq
            or str(target_id) == bot_qq
            or random.random() >= follow_prob):
            return True, "戳的对象不是 bot"

        # 检查跟戳冷却
        follow_cooldown = self.get_config("follow_poke_config.follow_poke_cooldown_seconds", 60)
        current_time = time.monotonic()
        target_id_str = str(target_id)
        last_follow_time = self._follow_poke_cooldown.get(target_id_str, 0)

        if current_time - last_follow_time < follow_cooldown:
            remaining = follow_cooldown - (current_time - last_follow_time)
            logger.info(f"[poke] 跟戳冷却中 | target={target_id} 剩余{remaining:.1f}秒")
            return True, "戳的对象不是 bot"

        # 执行跟戳
        await self.send_command(
            message.stream_id,
            CMD_SEND_POKE,
            {"qq_id": str(target_id)},
            storage_message=False
        )
        self._follow_poke_cooldown[target_id_str] = current_time
        logger.info(f"[poke] 跟戳 | target={target_id}")

        return True, "戳的对象不是 bot"

    def _check_cooldown(self, user_id: str) -> Tuple[bool, float]:
        """检查用户是否在冷却期内

        Args:
            user_id: 用户ID

        Returns:
            (是否在冷却中, 剩余时间)
        """
        rate_limit = self.get_config("poke_config.rate_limit_seconds", 30)
        current_time = time.monotonic()
        last_time = self._cooldown.get(user_id, 0)

        # 检查是否在冷却中
        if current_time - last_time < rate_limit:
            remaining = rate_limit - (current_time - last_time)
            return True, remaining

        # 更新冷却记录
        self._cooldown[user_id] = current_time
        return False, 0.0

    def _check_rate_limit(self) -> bool:
        """检查全局频率限制

        Returns:
            是否达到频率上限
        """
        max_pokes = self.get_config("poke_config.max_pokes_per_minute", 10)
        current_time = time.monotonic()

        # 清理超过60秒的旧记录
        self._poke_timestamps = [
            t for t in self._poke_timestamps
            if current_time - t < 60
        ]

        # 检查是否达到上限
        if len(self._poke_timestamps) >= max_pokes:
            logger.info(
                f"[poke] 达到频率上限 | "
                f"当前{len(self._poke_timestamps)}/{max_pokes}次/分钟"
            )
            return True

        # 记录当前时间戳
        self._poke_timestamps.append(current_time)
        return False

    async def _handle_poke_back(self, message: MaiMessages, user_id: str) -> None:
        """处理自动回戳逻辑

        Args:
            message: 消息对象
            user_id: 用户ID
        """
        # 检查回戳功能是否启用
        if not self.get_config("poke_config.auto_poke_back", True):
            return

        # 检查回戳概率
        poke_back_prob = self.get_config("poke_config.poke_back_probability", 0.8)
        if random.random() >= poke_back_prob:
            return

        # 获取回戳次数（添加配置验证保护）
        poke_back_max = self.get_config("poke_config.poke_back_max_times", 3)
        if poke_back_max < 1:
            logger.warning(f"[poke] 配置错误：poke_back_max_times={poke_back_max}，必须 >= 1，跳过回戳")
            return
        
        poke_times = random.randint(1, poke_back_max)

        # 执行回戳
        for _ in range(poke_times):
            poke_success = await self.send_command(
                message.stream_id,
                CMD_SEND_POKE,
                {"qq_id": user_id},
                storage_message=False
            )
            if not poke_success:
                logger.warning("[poke] 回戳命令发送失败")

    async def _handle_text_reply(
        self,
        message: MaiMessages,
        user_id: str,
        person_name: str
    ) -> bool:
        """处理 LLM 文字回复逻辑

        Args:
            message: 消息对象
            user_id: 用户ID
            person_name: 用户名

        Returns:
            是否成功发送回复
        """
        # 检查文字回复功能是否启用
        if not self.get_config("poke_config.auto_reply_enabled", True):
            return False

        # 检查回复概率
        reply_prob = self.get_config("poke_config.reply_probability", 0.7)
        if random.random() >= reply_prob:
            return False

        # 生成回复内容
        reply_reason = person_name + (message.plain_text or "")
        extra_info = (
            f"用户「{person_name}」戳了你一下"
            f"{('，附带消息：' + message.plain_text) if message.plain_text else ''}。"
            f"请用简短俏皮的方式回应。"
        )

        try:
            success, data = await generator_api.generate_reply(
                chat_id=message.stream_id,
                reply_reason=reply_reason,
                enable_chinese_typo=False,
                extra_info=extra_info,
            )

            if success and data.reply_set.reply_data:
                for seg in data.reply_set.reply_data:
                    text = seg.content
                    await self.send_text(message.stream_id, text, storage_message=True)
                    logger.info(f"[poke] 文本回复：{text!r}")
                return True

        except Exception as e:
            logger.exception(f"[poke] 生成回复失败：{e}")

        return False

    async def execute(self, message: MaiMessages | None) -> Tuple[bool, bool, Optional[str], None, None]:
        """处理戳一戳事件的主流程

        流程：
        1. 验证消息
        2. 获取用户信息
        3. 处理跟戳（如果不是戳 bot）
        4. 检查冷却和频率限制
        5. 执行回戳
        6. 生成文字回复
        """
        # 1. 验证消息
        is_valid, event = self._validate_message(message)
        if not is_valid:
            return True, True, "非戳一戳消息", None, None

        # 2. 获取用户信息
        user_id, person_name = await self._get_user_info(message)
        if not user_id:
            return False, True, "无法获取用户信息", None, None

        # 3. 处理跟戳（如果不是戳 bot，可能早退）
        should_exit, exit_reason = await self._handle_follow_poke(message, event, user_id)
        if should_exit:
            return True, True, exit_reason, None, None

        # 4. 检查冷却
        in_cooldown, remaining = self._check_cooldown(user_id)
        if in_cooldown:
            logger.info(f"[poke] 冷却中 | user={user_id} 剩余{remaining:.1f}秒")
            return True, True, "冷却中", None, None

        # 5. 检查频率限制
        rate_limited = self._check_rate_limit()
        if rate_limited:
            return True, True, "达到频率上限", None, None

        # 6. 记录接收
        logger.info(f"[poke] 接收戳一戳 | user={user_id} person={person_name}")

        # 7. 执行回戳
        await self._handle_poke_back(message, user_id)

        # 8. 生成文字回复
        reply_sent = await self._handle_text_reply(message, user_id, person_name)

        # 返回结果
        result_msg = "戳一戳已响应" if reply_sent else "戳一戳已响应（仅回戳）"
        return True, True, result_msg, None, None


# ---------- 动作 ----------
class PokeAction(BaseAction):
    """主动戳人动作

    允许麦麦主动戳用户，支持：
    - 通过昵称、QQ号或"我"来指定目标
    - 冷却机制防止频繁戳人
    - 自动推断群组ID
    """
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

    # 类级别的冷却记录（使用 monotonic 时间避免系统时间调整影响）
    _last_poke_user: Optional[str] = None
    _last_poke_group: Optional[str] = None
    _last_poke_time: float = 0.0  # monotonic 基准时间（秒）

    def _infer_group_id_from_context(self) -> Optional[str]:
        """从上下文推断群组ID"""
        debug = self.get_config("poke_action.debug", False)
        
        if debug:
            logger.debug("[poke] _infer_group_id: 开始推断群组ID")
        
        group_id = self.action_data.get("group_id")
        if debug:
            logger.debug(f"[poke] _infer_group_id: 从 action_data 获取 | group_id={group_id}")
        
        if group_id in (None, "", "None"):
            group_id = None

        # 从 message 对象获取
        if not group_id and hasattr(self, "message") and getattr(self.message, "message_info", None):
            group_id = getattr(self.message.message_info, "group_id", None)
            if debug:
                logger.debug(f"[poke] _infer_group_id: 从 message.message_info 获取 | group_id={group_id}")

        # 从 chat_stream 获取
        if not group_id and hasattr(self, "chat_stream") and getattr(self.chat_stream, "group_id", None):
            group_id = self.chat_stream.group_id
            if debug:
                logger.debug(f"[poke] _infer_group_id: 从 chat_stream 获取 | group_id={group_id}")

        # 从其他可能的属性获取
        if not group_id and hasattr(self, "group_id"):
            group_id = getattr(self, "group_id", None)
            if debug:
                logger.debug(f"[poke] _infer_group_id: 从 self.group_id 获取 | group_id={group_id}")

        result = str(group_id) if group_id not in (None, "", "None") else None
        
        if debug:
            logger.debug(f"[poke] _infer_group_id: 推断结果 | group_id={result}")
        
        return result

    async def _get_user_id(self, name: str) -> Optional[str]:
        """获取用户ID，支持多种输入方式"""
        debug = self.get_config("poke_action.debug", False)
        
        if not name:
            if debug:
                logger.debug("[poke] _get_user_id: name 为空")
            return None

        name = name.strip()
        
        if debug:
            logger.debug(f"[poke] _get_user_id: 开始识别 | name={name}")

        # 情况1：用户说"我"、"自己"
        if name in {"我", "我自己", "自己", "me"}:
            if debug:
                logger.debug("[poke] _get_user_id: 识别为'我'，尝试从上下文获取")
            
            # 从上下文获取当前用户ID
            if hasattr(self, "user_id") and self.user_id:
                logger.info(f"[poke] 识别'我' -> user_id={self.user_id}")
                if debug:
                    logger.debug(f"[poke] _get_user_id: 从 self.user_id 获取 -> {self.user_id}")
                return str(self.user_id)
            if hasattr(self, "message") and hasattr(self.message, "user_id"):
                user_id = self.message.user_id
                logger.info(f"[poke] 从message识别'我' -> user_id={user_id}")
                if debug:
                    logger.debug(f"[poke] _get_user_id: 从 message.user_id 获取 -> {user_id}")
                return str(user_id)
            
            if debug:
                logger.debug("[poke] _get_user_id: 无法从上下文获取用户ID")

        # 情况2：直接输入QQ号
        if name.isdigit():
            logger.info(f"[poke] 直接使用QQ号 -> user_id={name}")
            if debug:
                logger.debug(f"[poke] _get_user_id: 识别为QQ号 -> {name}")
            return name

        # 情况3：通过昵称查找
        if debug:
            logger.debug(f"[poke] _get_user_id: 尝试通过昵称查找 | name={name}")
        
        try:
            person_id = person_api.get_person_id_by_name(name)
            if debug:
                logger.debug(f"[poke] _get_user_id: person_id={person_id}")
            
            if person_id:
                user_id = await person_api.get_person_value(person_id, "user_id")
                if user_id:
                    logger.info(f"[poke] 通过昵称'{name}'查找 -> user_id={user_id}")
                    if debug:
                        logger.debug(f"[poke] _get_user_id: 昵称查找成功 -> {user_id}")
                    return str(user_id)
        except Exception as e:
            logger.warning(f"[poke] 通过昵称查找失败: {e}")
            if debug:
                logger.debug(f"[poke] _get_user_id: 昵称查找异常 | error={e}")

        if debug:
            logger.debug(f"[poke] _get_user_id: 所有方式都失败，无法识别用户 | name={name}")
        
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
        debug = self.get_config("poke_action.debug", False)
        
        if debug:
            logger.debug(
                f"[poke] _send_poke: 开始发送 | "
                f"user_id={user_id} group_id={group_id} target_name={target_name}"
            )
        
        candidates = self._build_send_poke_args(user_id, group_id)
        
        if debug:
            logger.debug(f"[poke] _send_poke: 生成参数候选 | count={len(candidates)}")
        
        for idx, args in enumerate(candidates, 1):
            try:
                logger.info(f"[poke] 尝试发送戳一戳 | args={args}")
                if debug:
                    logger.debug(f"[poke] _send_poke: 尝试第{idx}种参数格式 | args={args}")
                
                ok = await self.send_command(
                    CMD_SEND_POKE,
                    args,
                    storage_message=False
                )
                
                if debug:
                    logger.debug(f"[poke] _send_poke: send_command 返回 | ok={ok}")
                
                if ok:
                    logger.info(f"[poke] 戳一戳发送成功 | target={target_name} user_id={user_id} group_id={group_id}")
                    if debug:
                        logger.debug(f"[poke] _send_poke: 第{idx}种参数格式成功")
                    return True, "戳一戳成功"
            except Exception as e:
                logger.warning(f"[poke] 尝试参数 {args} 失败: {e}")
                if debug:
                    logger.debug(f"[poke] _send_poke: 第{idx}种参数格式失败 | error={e}")

        if debug:
            logger.debug("[poke] _send_poke: 所有参数格式都失败")
        
        return False, "所有参数格式都失败"

    async def execute(self) -> Tuple[bool, str]:
        """执行主动戳人动作

        Returns:
            (是否成功, 结果消息)
        """
        # 读取调试配置
        debug = self.get_config("poke_action.debug", False)
        
        if debug:
            logger.debug(f"[poke] 开始执行主动戳人 | action_data={self.action_data}")
        
        # 检查主动戳人功能是否启用
        if not self.get_config("poke_action.enabled", True):
            logger.info("[poke] 主动戳人功能已禁用")
            return False, "[poke] 主动戳人功能已禁用"

        name: Optional[str] = self.action_data.get("name")
        if not name:
            if debug:
                logger.debug("[poke] 参数解析失败：缺少 name 参数")
            return False, "[poke] 缺少参数 name"

        if debug:
            logger.debug(f"[poke] 参数解析 | name={name}")

        # 获取用户ID（支持多种方式）
        user_id = await self._get_user_id(name)
        if not user_id:
            if debug:
                logger.debug(f"[poke] 用户识别失败 | name={name}")
            return False, f"[poke] 无法识别用户'{name}'"

        if debug:
            logger.debug(f"[poke] 用户识别成功 | name={name} -> user_id={user_id}")

        # 推断群组ID
        group_id = self._infer_group_id_from_context()
        if debug:
            logger.debug(f"[poke] 群组ID推断 | group_id={group_id}")

        # 检查冷却时间（使用 monotonic 时间避免系统时间调整影响）
        cooldown_seconds = self.get_config("poke_action.cooldown_seconds", 300)
        current_time = time.monotonic()

        if debug:
            logger.debug(
                f"[poke] 冷却检查 | "
                f"last_user={self._last_poke_user} "
                f"last_group={self._last_poke_group} "
                f"last_time={self._last_poke_time:.2f} "
                f"current_time={current_time:.2f} "
                f"cooldown={cooldown_seconds}s"
            )

        if (self._last_poke_user == user_id
            and self._last_poke_group == group_id
            and current_time - self._last_poke_time < cooldown_seconds):
            remaining = int(cooldown_seconds - (current_time - self._last_poke_time))
            logger.info(f"[poke] 冷却中 | user={user_id} 剩余{remaining}秒")
            if debug:
                logger.debug(f"[poke] 冷却检查失败 | 剩余时间={remaining}秒")
            return False, f"冷却中，请{remaining}秒后再试"

        if debug:
            logger.debug("[poke] 冷却检查通过，准备发送戳一戳")

        # 发送戳一戳
        ok, result = await self._send_poke(user_id, group_id, name)

        if not ok:
            if debug:
                logger.debug(f"[poke] 戳一戳发送失败 | result={result}")
            return False, f"[poke] {result}"

        if debug:
            logger.debug(f"[poke] 戳一戳发送成功 | user_id={user_id} group_id={group_id}")

        # 更新冷却记录
        self._last_poke_user = user_id
        self._last_poke_group = group_id
        self._last_poke_time = current_time

        if debug:
            logger.debug(
                f"[poke] 冷却记录已更新 | "
                f"user={user_id} group={group_id} time={current_time:.2f}"
            )

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
        
        if debug:
            logger.debug(f"[poke] 动作记录已保存 | reason={reason}")
        
        return True, result


# ---------- 插件注册（必须放在最后，保证类已定义） ----------
@register_plugin
class PokePlugin(BasePlugin):
    """Quick Poke 插件

    提供戳一戳相关功能：
    - 被戳自动回复（回戳 + 文字）
    - 跟戳功能
    - 主动戳人
    """
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
        """获取插件组件列表

        Returns:
            组件信息和类型的列表
        """
        return [
            (PokeEventHandler.get_handler_info(), PokeEventHandler),
            (PokeAction.get_action_info(), PokeAction),
        ]
