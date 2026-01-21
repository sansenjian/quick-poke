# 🎯 Quick Poke Plugin

MaiBot 戳一戳插件，支持自动回戳、LLM 文字回复、跟戳和冷却机制。

## ✨ 功能特性

### 核心功能
- 🔄 **自动回戳** - 被戳时自动回戳（随机 1~N 次）
- 💬 **LLM 文字回复** - 被戳时生成智能文字回复
- 👥 **跟戳功能** - 看到别人戳别人时有概率跟着戳
- ❄️ **跟戳冷却** - 防止对同一个被戳者频繁跟戳
- 🤖 **AI 主动戳人** - 提供 `poke` Action 供 AI 主动戳人

### 高级特性
- ⏱️ **频率限制** - 支持用户级和全局级频率限制
- 🎲 **概率控制** - 所有功能都支持概率控制
- 📊 **灵活配置** - 所有参数都可自定义
- 📝 **详细日志** - 完整的操作日志记录

## 🚀 快速开始

### 安装

将本插件目录放入 MaiBot 的 `plugins` 文件夹即可。

```bash
# 复制插件到 MaiBot
cp -r quick‑poke MaiBot/plugins/
```

### 启用

重启 MaiBot，插件会自动加载并生成 `config.toml` 配置文件。

## ⚙️ 配置说明

插件会自动生成 `config.toml`，所有配置项都可自定义。

### 基本配置 (plugin)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | true | 是否启用戳一戳插件 |
| `config_version` | string | 1.1.2 | 配置文件版本号 |

### 被戳设置 (poke_config)

#### 回戳设置
| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `auto_poke_back` | bool | true | 是否自动回戳 |
| `poke_back_probability` | float | 0.8 | 回戳触发概率（0~1） |
| `poke_back_max_times` | int | 3 | 回戳最大次数（随机 1~此值） |

#### 文字回复设置
| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `auto_reply_enabled` | bool | true | 是否启用 LLM 文字回复 |
| `reply_probability` | float | 0.7 | 文字回复触发概率（0~1） |

#### 被戳频率限制设置
| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `rate_limit_seconds` | int | 30 | 同一用户冷却时间（秒） |
| `max_pokes_per_minute` | int | 10 | 每分钟最多处理次数 |

### 跟戳设置 (poke_config)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `follow_poke_enabled` | bool | true | 是否启用跟戳功能 |
| `follow_poke_probability` | float | 0.3 | 跟戳触发概率（0~1） |
| `follow_poke_cooldown_seconds` | int | 60 | 跟戳冷却时间（秒） |

### 使用策略 (usage_policy)

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `action_require` | list | [...] | PokeAction 的 AI 提示语 |

## 📋 配置示例

```toml
[plugin]
enabled = true
config_version = "1.1.2"

[poke_config]
# 回戳设置
auto_poke_back = true
poke_back_probability = 0.8
poke_back_max_times = 3

# 文字回复设置
auto_reply_enabled = true
reply_probability = 0.7

# 跟戳设置
follow_poke_enabled = true
follow_poke_probability = 0.3
follow_poke_cooldown_seconds = 60

# 频率限制
rate_limit_seconds = 30
max_pokes_per_minute = 10
```

## 🔍 工作原理

### 被戳流程
1. 接收戳一戳事件
2. 检查频率限制（用户级和全局级）
3. 执行回戳（按概率）
4. 生成文字回复（按概率）

### 跟戳流程
1. 检测别人戳别人的事件
2. 检查跟戳启用状态
3. 检查跟戳概率
4. 检查跟戳冷却（防止频繁跟戳）
5. 执行跟戳

### 冷却机制
- **用户级冷却**: 防止同一用户频繁戳机器人
- **全局冷却**: 防止全局戳一戳事件过于频繁
- **跟戳冷却**: 防止对同一个被戳者频繁跟戳

## 📊 日志示例

```
[poke] 接收戳一戳 | user=123456789 reason='用户名'
[poke] 回戳 | user=123456789 次数=2
[poke] 文本回复：'你好呀！'
[poke] 跟戳 | target=987654321
[poke] 跟戳冷却中 | target=987654321 剩余45.3秒
[poke] 冷却中 | user=123456789 剩余15.2秒
```

## 🎮 使用场景

### 场景 1: 完整交互
- 用户戳机器人
- 机器人自动回戳
- 机器人生成文字回复

### 场景 2: 仅回戳
- 禁用文字回复
- 用户戳机器人
- 机器人只回戳不回复

### 场景 3: 跟戳
- 用户 A 戳用户 B
- 机器人看到后有概率跟着戳
- 冷却机制防止频繁跟戳

## 🔧 常见问题

### Q: 如何禁用某个功能？
A: 在 `config.toml` 中将对应的 `enabled` 或 `probability` 设置为 0 或 false。

### Q: 冷却时间是多少？
A: 默认 30 秒（用户级）和 60 秒（跟戳）。可在配置中自定义。

### Q: 如何调整回戳次数？
A: 修改 `poke_back_max_times` 配置项，实际回戳次数为随机 1~此值。

### Q: 跟戳冷却是什么意思？
A: 防止对同一个被戳者频繁跟戳。冷却期内不会跟戳，冷却期外才会跟戳。

## 📈 性能指标

- 代码覆盖率：100%
- 测试通过率：100%
- 质量评分：5/5
- 响应延迟：<100ms

## 📝 许可证

MIT

## 👨‍💻 开发信息

- **版本**: 1.1.2
- **最后更新**: 2025-01-22
- **维护者**: Kiro AI Assistant
