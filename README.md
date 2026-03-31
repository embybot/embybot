# EmbyBot

一个功能完整的 Telegram 机器人，用于管理 [Emby](https://emby.media/) 媒体服务器。EmbyBot 将 Telegram 与 Emby 无缝连接，让管理员可以直接在 Telegram 中监控播放会话、管理用户并接收实时通知。

---

## 目录

- [功能](#功能)
- [架构](#架构)
- [项目结构](#项目结构)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [Bot 命令](#bot-命令)
- [Telegram 模式](#telegram-模式)
- [数据库](#数据库)
- [代理支持](#代理支持)
- [国际化](#国际化)
- [Docker](#docker)

---

## 功能

| 类别 | 详情 |
|---|---|
| **播放监控** | 播放开始 / 暂停 / 停止实时通知，附带用户信息和 IP 地理位置 |
| **会话控制** | 按用户限制并发流数量，远程终止会话，向播放中的客户端广播消息 |
| **用户管理** | 绑定 Telegram 账号与 Emby 用户，邀请码，时长订阅码，角色管理（管理员 / 用户 / 封禁） |
| **积分与签到** | 每日签到奖励，群组发言积分，积分转账，可配置每日上限 |
| **媒体搜索** | 在 Telegram 中搜索 Emby 媒体库，展示 TMDB 元数据、海报、视频/音频/字幕规格 |
| **媒体库通知** | 新增或删除内容时推送至群组 / 频道 / 管理员 |
| **管理控制** | 内联设置菜单，用户策略编辑器，兑换码生成器，媒体库扫描/刷新，机器人重启 |
| **自动化** | 每小时检查订阅到期并禁用账号，可配置降级/删除策略 |
| **抽奖** | 与积分体系集成的抽奖功能 |
| **兑换商店** | 用户可用积分兑换时长码、邀请码或自定义商品 |

---

## 架构

```
Telegram  ──────────────────────────────────────────────┐
  │  (Webhook POST / 长轮询)                              │
  ▼                                                      │
http_server.py  (Python HTTPServer，监听 :8888)           │
  │                                                      │
  ├── webhook_handler.py  ──► telegram_handler.py        │
  │       解析传入更新，                                   │
  │       分发命令与回调                                   │
  │                                                      │
  ├── api/emby.py          Emby REST API 调用             │
  ├── api/tmdb.py          TMDB 元数据与海报               │
  ├── api/geo.py           IP 地理位置查询                 │
  ├── api/base_client.py   HTTP 重试逻辑 + 代理            │
  │                                                      │
  ├── notifications/       Telegram 消息发送封装           │
  ├── logic/               媒体格式化，剧集处理             │
  ├── core/config.py       YAML 配置加载器                 │
  ├── core/database.py     SQLAlchemy ORM 初始化           │
  └── core/cache.py        内存缓存                        │
                                                         │
Emby 服务器 ◄────────────────────────────────────────────┘
  (Webhook 事件：playback.start/stop/pause，媒体库变更，用户事件)
```

Emby 配置为将 Webhook 事件 POST 到 EmbyBot 的 HTTP 服务器，EmbyBot 处理这些事件后将格式化通知转发至配置的 Telegram 群组、频道或管理员。

---

## 项目结构

```
EmbyBot/
├── Dockerfile
├── requirements.txt
└── EmbyBot/
    ├── main.py                  入口文件；启动 HTTP 服务器与后台线程
    ├── models.py                SQLAlchemy 数据模型（User、DurationCode、InvitationCode、BannedUser）
    ├── api/
    │   ├── base_client.py       make_request_with_retry() — 所有出站 HTTP 请求的统一入口
    │   ├── emby.py              Emby API 封装（会话、用户、媒体库、策略）
    │   ├── tmdb.py              TMDB 元数据与海报获取
    │   └── geo.py               IP 地理位置（百度 / IP138 / PConline / Vore / ip-api.com）
    ├── core/
    │   ├── config.py            加载 config.yaml，暴露类型化全局变量，定义设置菜单结构
    │   ├── database.py          SQLAlchemy 引擎与 Session 工厂
    │   └── cache.py             海报、搜索结果、用户上下文的内存缓存
    ├── handlers/
    │   ├── telegram_handler.py  所有命令和 callback_query 处理逻辑
    │   └── webhook_handler.py   HTTP 处理器；路由 Telegram 更新和 Emby 事件
    ├── notifications/
    │   ├── telegram_driver.py   底层 Telegram 消息发送/编辑/删除函数
    │   └── manager.py           高层通知分发（群组 / 频道 / 管理员）
    ├── services/
    │   ├── http_server.py       启动 Python HTTPServer
    │   └── telegram_poller.py   长轮询循环（无 Webhook 时使用）
    ├── logic/
    │   ├── media_manager.py     格式化搜索结果的富媒体卡片
    │   └── series_helper.py     剧集/季/集聚合工具
    ├── utils/
    │   ├── formatters.py        流规格格式化（视频/音频/字幕）
    │   └── helpers.py           杂项工具（机器人重启、转义等）
    └── i18n/
        ├── __init__.py          gettext 封装；使用 i18n._("...") 调用
        └── locales/             en、zh_hans、zh_hant、ja、ko、ru、de、fr、es、it、pt 的 .po/.mo 文件
```

---

## 环境要求

- Python 3.11+
- 已启用 Webhook 插件的 Emby 服务器
- Telegram Bot Token（通过 [@BotFather](https://t.me/BotFather) 获取）
- TMDB API Token（用于元数据和海报）

Python 依赖（`requirements.txt`）：

```
PyYAML>=6.0
requests>=2.28.0
requests[socks]>=2.28.0   # 使用 SOCKS5 代理时必须
SQLAlchemy>=2.0
psycopg2-binary>=2.9.0
mysqlclient>=2.1.0
requests[socks]>=2.28.0
```

---

## 快速开始

### Docker（推荐）

```bash
# 1. 创建配置目录并将 config.yaml 放入其中
mkdir -p /opt/embybot/config

# 2. 启动容器
docker run -d \
  --name embybot \
  -p 8888:8888 \
  -v /opt/embybot/config:/config \
  embybot:latest
```

### Docker Compose

```yaml
services:
  embybot:
    build: .
    container_name: embybot
    restart: unless-stopped
    ports:
      - "8888:8888"
    volumes:
      - ./config:/config
```

```bash
docker compose up -d
```

### 直接运行

```bash
pip install -r requirements.txt
# 将 config.yaml 放置于 /config/config.yaml
python -m EmbyBot.main
```

---

## 配置说明

EmbyBot 启动时读取 `/config/config.yaml`。以下是各顶层配置块的说明。

### `emby`

```yaml
emby:
  server_url: http://10.1.1.216:8096      # Emby 内网地址（用于 API 调用）
  api_key: <your_emby_api_key>
  user_id: <emby_user_id>                 # 用于媒体库查询
  username: admin                          # 用于 Token 认证
  password: secret
  remote_url: https://emby.example.com    # 公网地址（用于"在服务器中查看"按钮）
  app_scheme: emby                         # 深链接协议（emby / jellyfin）
  template_user_id: <user_id>             # 创建新用户时应用的模板
```

### `telegram`

```yaml
telegram:
  token: <bot_token>
  webhook_url: https://your.domain.com    # Emby 和 Telegram 推送的公网地址
  admin_user_id: 123456789                # 可以是单个 ID 或逗号分隔的列表
  group_id: -1001234567890                # 接收通知的 Telegram 群组
  channel_id: -1009876543210              # 接收媒体库通知的 Telegram 频道
  customer_service_id: 123456789          # 展示给用户的客服联系方式
```

### `tmdb`

```yaml
tmdb:
  api_token: <tmdb_read_access_token>
```

### `proxy`

```yaml
proxy:
  url: "socks5://user:password@host:1080"  # 可选；仅作用于 Telegram API 请求
```

> **注意：** SOCKS5 代理需要安装 `requests[socks]`。

### `server`

```yaml
server:
  port: 8888   # HTTP 服务器监听端口
```

### `database`

```yaml
database:
  url: ""   # 留空使用 SQLite（/config/data/embybot.db）
            # PostgreSQL: postgresql://user:pass@host/db
            # MySQL:      mysql://user:pass@host/db
```

### `settings`

`settings` 块控制所有运行时行为，也可通过机器人内的 `/settings` 菜单实时修改。主要子配置：

| 子配置块 | 用途 |
|---|---|
| `content_settings` | 控制每种通知类型显示的字段（海报、简介、规格、TMDB 链接等） |
| `notification_management` | 按目标（群组 / 频道 / 管理员）开关各类通知事件 |
| `auto_delete_settings` | 机器人消息在指定延迟后自动删除 |
| `session_control` | 启用按用户并发流限制并设置最大数量 |
| `points` | 启用积分、群组发言积分、每日上限、转账 |
| `checkin` | 启用每日签到、设置奖励积分、验证码选项 |
| `permissions` | 每个命令的权限级别（`anyone` / `bound_user` / `admin_only`）及允许的上下文 |
| `captcha` | 按操作配置验证码 |
| `redemption` | 启用兑换商店并配置价格 |
| `lottery` | 启用抽奖功能 |
| `automation` | 订阅到期提醒、降级策略、自动删除策略 |
| `timezone` | IANA 时区字符串，例如 `Asia/Shanghai` |
| `language` | 界面语言（`en`、`zh_hans`、`zh_hant`、`ja`、`ko`、`ru`、`de`、`fr`、`es`、`it`、`pt`） |
| `telegram_mode` | `webhook` 或 `polling` |

---

## Bot 命令

| 命令 | 权限 | 说明 |
|---|---|---|
| `/start` | 所有人 | 欢迎消息，展示可用操作 |
| `/bind` | 所有人 | 绑定 Telegram 账号与 Emby 账号 |
| `/search <关键词>` | 已绑定用户 | 搜索 Emby 媒体库 |
| `/checkin` | 已绑定用户 | 每日签到获取积分 |
| `/points` | 已绑定用户 | 查看积分余额和转账 |
| `/redeem` | 所有人 | 兑换时长码或邀请码 |
| `/account` | 所有人 | 查看账号详情和订阅状态 |
| `/status` | 管理员 | 列出当前活跃播放会话 |
| `/manage <关键词>` | 管理员 | 搜索并管理媒体项目（扫描、刷新、删除） |
| `/settings` | 管理员 | 打开内联设置菜单 |

---

## Telegram 模式

**Webhook（推荐）**

Telegram 主动将更新推送至您的服务器。需要可公开访问的 HTTPS 地址。设置 `settings.telegram_mode: webhook` 并配置 `telegram.webhook_url`。

**长轮询**

EmbyBot 主动向 Telegram 轮询更新，无需公网地址。设置 `settings.telegram_mode: polling`。

> Emby 到 EmbyBot 的 Webhook（用于播放和媒体库事件）无论 Telegram 使用哪种模式，都要求 HTTP 服务器可被 Emby 服务器访问。

---

## 数据库

EmbyBot 使用 SQLAlchemy，支持三种后端：

| 后端 | `database.url` 值 |
|---|---|
| **SQLite**（默认） | `""` — 文件自动创建于 `/config/data/embybot.db` |
| **PostgreSQL** | `postgresql://user:pass@host:5432/dbname` |
| **MySQL** | `mysql://user:pass@host:3306/dbname` |

### 数据表

| 表名 | 用途 |
|---|---|
| `users` | Telegram ↔ Emby 账号绑定、角色、积分、签到日期、订阅到期时间 |
| `duration_codes` | 时限访问码（天数） |
| `invitation_codes` | 新用户注册用的一次性邀请码 |
| `banned_users` | 已封禁的 Telegram 用户 ID |

---

## 代理支持

只有出站的 **Telegram API** 请求会通过代理路由，Emby、TMDB 和地理位置请求始终使用直连。

```yaml
proxy:
  url: "socks5://user:password@host:1080"
```

启动日志中出现以下内容说明代理已生效：
```
🌐 Proxy configured: socks5://...
```

在容器内测试代理出口 IP：
```bash
docker exec <容器名> python -c \
  "import requests; print(requests.get('https://api.ipify.org?format=json', proxies={'https': 'socks5://user:pass@host:1080'}).json())"
```

---

## 国际化

所有面向用户的字符串均通过 `i18n._("...")` 包装，并编译为 `EmbyBot/i18n/locales/` 下的 gettext `.mo` 文件。活跃语言通过 `config.yaml` 中的 `settings.language` 设置，也可在 `/settings` 菜单中实时切换。

支持语言：English · 简体中文 · 繁體中文 · 日本語 · 한국어 · Русский · Deutsch · Français · Español · Italiano · Português

---

## Docker

镜像采用两阶段构建，确保最终层干净精简：

1. **基础阶段** — 安装系统构建依赖和 `requirements.txt` 中的 Python 包。
2. **最终阶段** — 仅复制已安装的 site-packages 和应用源码，不包含任何构建工具。

`/config` 目录声明为 `VOLUME`，是所有持久化数据的唯一挂载点：

| 路径 | 内容 |
|---|---|
| `/config/config.yaml` | 主配置文件 |
| `/config/data/embybot.db` | SQLite 数据库（如使用） |
| `/config/cache/poster_cache.json` | TMDB 海报 URL 缓存 |
| `/config/cache/id_map.json` | Emby ↔ TMDB ID 映射缓存 |
| `/config/static/language_map.json` | 国家代码 → 语言名称映射 |
