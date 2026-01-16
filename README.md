# Quick Poke Plugin

MaiBot 戳一戳插件，支持自动回戳、LLM 文字回复和跟戳功能。

## 功能

- 被戳时自动回戳（随机 1~N 次）
- 被戳时生成 LLM 文字回复
- 跟戳：看到别人戳别人时有概率跟着戳
- 提供 `poke` Action 供 AI 主动戳人

## 安装

将本插件目录放入 MaiBot 的 `plugins` 文件夹即可。

## 配置

插件会自动生成 `config.toml`，配置项说明：

### plugin 基本配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enabled` | bool | true | 是否启用戳一戳插件 |
| `config_version` | string | 1.1.1 | 配置文件版本号 |

### poke_config 戳一戳配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `auto_reply_enabled` | bool | true | 是否启用 LLM 文字回复。关闭后被戳只回戳不回复文字 |
| `auto_poke_back` | bool | true | 是否自动回戳。关闭后被戳只回复文字不回戳 |
| `rate_limit_seconds` | int | 30 | 同一用户戳一戳频率限制（秒）。冷却期内不响应 |
| `max_pokes_per_minute` | int | 10 | 每分钟最多处理戳一戳次数 |
| `poke_back_max_times` | int | 3 | 回戳最大次数。实际回戳次数为随机 1~此值 |
| `follow_poke_enabled` | bool | true | 是否启用跟戳功能。开启后看到别人戳别人会有概率跟着戳 |
| `follow_poke_probability` | float | 0.3 | 跟戳触发概率（0~1）。0 为从不跟戳，1 为必定跟戳 |

### usage_policy 使用策略

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `action_require` | list | [...] | PokeAction 的 AI 提示语，定义 AI 何时可以主动戳人 |

## 许可证

MIT
