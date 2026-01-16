# Quick Poke Plugin

MaiBot 戳一戳插件，支持自动回复戳一戳消息并回戳。

## 功能

- 自动检测 QQ 戳一戳事件
- 自动回戳 + 生成文本回复
- 提供 `poke` Action 供 AI 主动戳人
- 可配置频率限制

## 安装

将本插件目录放入 MaiBot 的 `plugins` 文件夹即可。

## 配置

插件会自动生成 `config.toml`，可配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `auto_reply_enabled` | true | 是否自动回复戳一戳 |
| `auto_poke_back` | true | 是否自动回戳 |
| `rate_limit_seconds` | 30 | 同一用户频率限制（秒） |
| `max_pokes_per_minute` | 10 | 每分钟最多处理次数 |

## 许可证

MIT
