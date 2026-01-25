# 主动戳人功能优化说明

> 优化时间：2025-01-26
> 版本：v1.2.0

---

## 🎯 优化目标

解决群聊中"戳我一下"功能无法正常工作的问题，提升主动戳人功能的可用性和智能性。

---

## 🔧 主要优化内容

### 1. 群组ID自动推断

**问题**：群聊中戳人需要 group_id 参数，但之前没有自动推断逻辑

**解决方案**：
```python
def _infer_group_id_from_context(self) -> Optional[str]:
    """从上下文推断群组ID"""
    # 优先从 action_data 获取
    group_id = self.action_data.get("group_id")
    
    # 从 message 对象获取
    if not group_id and hasattr(self, "message"):
        group_id = getattr(self.message.message_info, "group_id", None)
    
    # 从 chat_stream 获取
    if not group_id and hasattr(self, "chat_stream"):
        group_id = self.chat_stream.group_id
    
    return str(group_id) if group_id else None
```

**效果**：
- ✅ 私聊：自动识别为私聊（group_id=None）
- ✅ 群聊：自动从上下文获取 group_id
- ✅ 无需 LLM 手动指定 group_id

---

### 2. 多种用户识别方式

**问题**：只支持精确的用户名称，不支持"我"、"自己"等自然表达

**解决方案**：
```python
async def _get_user_id(self, name: str) -> Optional[str]:
    """获取用户ID，支持多种输入方式"""
    # 情况1：用户说"我"、"自己"
    if name in {"我", "我自己", "自己", "me"}:
        return str(self.user_id)
    
    # 情况2：直接输入QQ号
    if name.isdigit():
        return name
    
    # 情况3：通过昵称查找
    person_id = person_api.get_person_id_by_name(name)
    user_id = await person_api.get_person_value(person_id, "user_id")
    return str(user_id)
```

**支持的输入方式**：
- ✅ "我" / "我自己" / "自己" / "me"
- ✅ 直接输入 QQ 号（纯数字）
- ✅ 用户昵称
- ✅ 用户名称

---

### 3. 多种参数格式兼容

**问题**：只使用 `{"qq_id": user_id}` 格式，可能不兼容某些适配器

**解决方案**：
```python
def _build_send_poke_args(self, user_id: str, group_id: Optional[str]) -> List[dict]:
    """构建多种参数格式，提高兼容性"""
    candidates = []
    
    # 格式1：qq_id（主要格式）
    args1 = {"qq_id": user_id}
    if group_id:
        args1["group_id"] = group_id
    candidates.append(args1)
    
    # 格式2：target_id（备用格式）
    args2 = {"target_id": user_id}
    if group_id:
        args2["group_id"] = group_id
    candidates.append(args2)
    
    return candidates
```

**效果**：
- ✅ 自动尝试多种参数格式
- ✅ 提高适配器兼容性
- ✅ 失败时有详细日志

---

### 4. 冷却机制

**问题**：没有针对主动戳人的冷却限制，可能短时间内重复戳同一个人

**解决方案**：
```python
# 类级别的冷却记录
_last_poke_user: Optional[str] = None
_last_poke_group: Optional[str] = None
_last_poke_time: float = 0.0

# 检查冷却时间
cooldown_seconds = self.get_config("poke_action.cooldown_seconds", 300)
current_time = time.time()

if (self._last_poke_user == user_id 
    and self._last_poke_group == group_id
    and current_time - self._last_poke_time < cooldown_seconds):
    return False, f"冷却中，请{remaining}秒后再试"
```

**效果**：
- ✅ 防止短时间内重复戳同一个人
- ✅ 默认冷却时间：300秒（5分钟）
- ✅ 可通过配置文件调整

---

### 5. 使用场景优化

**问题**：action_require 说明过于严格，可能导致 LLM 不敢使用

**优化前**：
```python
action_require = [
    "**仅在以下非常具体的情况下使用：**",
    "1. 当用户**明确要求**或**明确同意**你戳他时...",
    "**重要限制：**",
    "- **绝不能**用它来代替正常的文字交流...",
]
```

**优化后**：
```python
action_require = [
    "**适合使用的场景：**",
    "1. 用户明确要求'戳我'、'戳一下'、'poke'时",
    "2. 友好互动氛围中，作为轻松的互动方式",
    "3. 作为对用户多次戳你的友好回应",
    "",
    "**使用限制：**",
    "- 不要在短时间内重复戳同一个人",
    "- 不要在严肃话题或用户情绪不佳时使用",
    "- 如果不确定，优先使用文字回复"
]
```

**效果**：
- ✅ 更清晰的使用场景说明
- ✅ 更友好的限制说明
- ✅ LLM 更容易理解何时使用

---

### 6. 详细日志

**新增日志**：
```python
logger.info(f"[poke] 识别'我' -> user_id={self.user_id}")
logger.info(f"[poke] 直接使用QQ号 -> user_id={name}")
logger.info(f"[poke] 通过昵称'{name}'查找 -> user_id={user_id}")
logger.info(f"[poke] 尝试发送戳一戳 | args={args}")
logger.info(f"[poke] 戳一戳发送成功 | target={target_name} user_id={user_id} group_id={group_id}")
logger.info(f"[poke] 冷却中 | user={user_id} 剩余{remaining}秒")
```

**效果**：
- ✅ 便于调试和排查问题
- ✅ 清晰的执行流程记录
- ✅ 详细的参数信息

---

## 📊 新增配置项

```toml
[poke_action]
# 主动戳人冷却时间（秒），防止短时间内重复戳同一个人
cooldown_seconds = 300

# 是否开启调试日志
debug = false
```

---

## 🎯 使用示例

### 私聊场景

**用户**：戳我一下

**LLM 调用**：
```json
{
  "action": "poke",
  "name": "我"
}
```

**执行流程**：
1. 识别"我" → 获取当前用户ID
2. 推断 group_id → None（私聊）
3. 构建参数 → `{"qq_id": "123456"}`
4. 发送戳一戳 → 成功

---

### 群聊场景

**用户**：@麦麦 戳我一下

**LLM 调用**：
```json
{
  "action": "poke",
  "name": "我"
}
```

**执行流程**：
1. 识别"我" → 获取当前用户ID
2. 推断 group_id → 从 chat_stream 获取
3. 构建参数 → `{"qq_id": "123456", "group_id": "789012"}`
4. 发送戳一戳 → 成功

---

### 戳其他人

**用户**：戳一下小明

**LLM 调用**：
```json
{
  "action": "poke",
  "name": "小明"
}
```

**执行流程**：
1. 通过昵称"小明"查找 → user_id
2. 推断 group_id → 从上下文获取
3. 构建参数 → `{"qq_id": "654321", "group_id": "789012"}`
4. 发送戳一戳 → 成功

---

## ✅ 测试建议

### 私聊测试

1. 发送"戳我一下" → 应该成功戳回
2. 连续发送两次"戳我一下" → 第二次应该提示冷却中
3. 等待5分钟后再发送 → 应该成功

### 群聊测试

1. 在群里发送"@麦麦 戳我一下" → 应该成功戳回
2. 在群里发送"@麦麦 戳一下小明" → 应该戳小明
3. 连续发送两次 → 第二次应该提示冷却中

### 边界测试

1. 发送"戳我自己" → 应该识别为"我"
2. 发送"戳 123456"（QQ号） → 应该直接使用QQ号
3. 发送"戳不存在的人" → 应该提示找不到用户

---

## 🐛 已知问题

无

---

## 📝 后续优化建议

1. **智能冷却时间**：根据用户关系调整冷却时间
2. **群组权限检查**：检查是否有权限在群里戳人
3. **批量戳人**：支持一次戳多个人
4. **戳人统计**：记录戳人次数和频率

---

## 📚 相关文档

- [MaiBot 插件开发指南](../../.kiro/steering/maibot-plugin-guide.md)
- [代码设计规范](../../.kiro/steering/code-design.md)

---

**优化完成时间**：2025-01-26

**优化者**：Kiro AI Assistant

**版本**：v1.2.0
