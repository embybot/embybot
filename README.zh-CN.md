<div align="center">

<img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram">
<img src="https://img.shields.io/badge/Emby-媒体服务器-52B54B?style=for-the-badge&logo=emby&logoColor=white" alt="Emby">
<img src="https://img.shields.io/badge/许可证-MIT-yellow?style=for-the-badge" alt="License">
<img src="https://img.shields.io/badge/多语言-11种语言-orange?style=for-the-badge" alt="i18n">

# 🎬 EmbyBot

**功能完整的 Emby 媒体服务器 Telegram 管理机器人**

[English](README.md) · [简体中文](#) · [功能特性](#-功能特性) · [快速开始](#-快速开始) · [配置说明](#-配置说明) · [Wiki](../../wiki)

</div>

---

## ✨ 功能特性

EmbyBot 将 Telegram 与 Emby 服务器连接起来，在聊天中直接提供丰富的通知推送与完整的管理功能。

### 📡 实时通知
- **新内容入库提醒** — 电影或剧集添加到媒体库时，自动通知群组、频道或管理员，支持海报、媒体规格、TMDB 链接
- **播放事件推送** — 开始、暂停、继续、停止播放，推送用户、设备、位置、进度等信息
- **内容删除提醒** — 媒体库内容被删除时发出通知
- **系统事件推送** — 用户登录（成功/失败）、用户创建/删除、密码修改、服务器重启提醒

### 🎦 媒体搜索
- 支持按**标题**、**标题+年份**或 **TMDB ID** 搜索媒体库
- 展示海报、简介、视频/音频/字幕规格（电影）或按季规格（剧集）
- 显示剧集更新进度（对比 TMDB，标注缺失集数）
- 搜索无结果时自动回退到 TMDB 别名搜索

### 👥 用户管理
- 从模板用户克隆权限，创建新 Emby 用户
- 重命名、修改密码、精细化权限管理（逐项开关）
- 删除用户时自动解绑 Telegram 账号关联
- 订阅到期跟踪，到期后自动禁用 Emby 访问权限

### 🔑 邀请码 / 时长码系统
- 生成**邀请码**，供新用户自助注册 Emby 账号
- 生成**时长码**，续费订阅（如 `5 90` 即生成 5 张 90 天的码）
- 支持单个或批量启用/禁用/清除兑换码

### 💰 积分与签到
- 每日签到，可选表情验证码
- 群组发消息达到字数门槛可获得积分
- 用户之间互相转让积分
- 管理员可调整余额、赠送兑换码

### 🛡️ 会话并发控制
- 限制每个 Emby 用户的最大同时播放数
- 超限后自动通过 Emby 发送倒计时警告，随后强制终止所有会话
- `/status` 命令查看当前所有播放会话，支持单独停止、发消息、全部停止

### 🗂️ 文件与媒体库管理
- 扫描单个项目或整个媒体库
- 刷新元数据（从网络重新抓取）
- 从 Emby、本地存储、云盘或三者同时删除内容
- **云盘同步** — 从云盘生成 `.strm` 链接文件并复制元数据到本地

### ⚙️ 机器人内联设置菜单
所有功能均可通过 Telegram 内联键盘实时配置，无需手动编辑配置文件：
- 分类开关每种通知类型
- 精细控制每种通知显示的字段
- `/status` 命令支持单条消息列表模式和多条消息卡片模式切换
- 选择 IP 归属地查询服务（5 种可选）
- 切换 Long Polling 与 Webhook 模式
- 切换机器人界面语言（11 种语言）

### 🌍 多语言支持
内置 **11 种语言**：

| 语言 | 代码 | 语言 | 代码 |
|---|---|---|---|
| 🇺🇸 English | `en` | 🇨🇳 简体中文 | `zh_hans` |
| 🇨🇳 繁體中文 | `zh_hant` | 🇯🇵 日本語 | `ja` |
| 🇰🇷 한국어 | `ko` | 🇷🇺 Русский | `ru` |
| 🇩🇪 Deutsch | `de` | 🇫🇷 Français | `fr` |
| 🇪🇸 Español | `es` | 🇵🇹 Português | `pt` |
| 🇮🇹 Italiano | `it` | | |

---

## 🚀 快速开始

### 前置条件

- Python 3.10+
- 一台 Emby 媒体服务器，且已启用 API 访问
- Telegram Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- （可选）TMDB API Key，用于获取海报和元数据

### Docker 方式（推荐）

```yaml
# docker-compose.yml
services:
  embybot:
    image: python:3.11-slim
    container_name: embybot
    working_dir: /app
    volumes:
      - ./EmbyBot:/app
      - ./config:/config
    command: >
      sh -c "pip install -r requirements.txt -q &&
             python -m EmbyBot.main"
    restart: unless-stopped
    ports:
      - "8080:8080"   # 仅 Webhook 模式需要对外暴露
```

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/EmbyBot.git
cd EmbyBot

# 2. 复制并编辑配置文件
mkdir -p config
cp config/config.example.yaml config/config.yaml
nano config/config.yaml   # 填入 Token、Emby 地址等必填项

# 3. 启动
docker compose up -d
docker compose logs -f    # 查看启动日志
```

### 手动安装

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/EmbyBot.git
cd EmbyBot

# 2. 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 准备配置文件
sudo mkdir -p /config
sudo cp config/config.example.yaml /config/config.yaml
sudo nano /config/config.yaml

# 5. 运行
python -m EmbyBot.main
```

---

## 📋 配置说明

将 `config/config.example.yaml` 复制到 `/config/config.yaml` 并填写必要字段。

### 最小必填配置

```yaml
telegram:
  token: "YOUR_BOT_TOKEN"           # 从 @BotFather 获取
  admin_user_id: 123456789          # 你的 Telegram 用户 ID

emby:
  server_url: "http://your-emby:8096"
  api_key: "YOUR_EMBY_API_KEY"
  user_id: "YOUR_EMBY_USER_UUID"    # 用于搜索/浏览 API 请求
```

### 完整配置示例

```yaml
telegram:
  token: "YOUR_BOT_TOKEN"
  admin_user_id:                    # 单个 ID 或列表
    - 123456789
    - 987654321
  group_id:                         # 接收媒体库通知的群组
    - -1001234567890
  channel_id:                       # 接收媒体库通知的频道
    - -1009876543210
  webhook_url: "https://your.domain" # 仅 Webhook 模式需要
  customer_service_id: 123456789    # 可选：显示在错误提示中的客服 ID

emby:
  server_url: "http://192.168.1.100:8096"
  api_key: "abc123..."
  user_id: "emby-user-uuid"
  username: "admin"                 # 删除媒体库条目时需要
  password: "your-password"         # 删除媒体库条目时需要
  template_user_id: "template-uuid" # 新建用户时继承此用户的权限
  remote_url: "https://emby.yourdomain.com"  # 用于"在服务器上查看"按钮
  app_scheme: "emby"

tmdb:
  api_token: "YOUR_TMDB_API_KEY"   # 海报、TMDB 链接、剧集进度

settings:
  language: "zh_hans"               # 机器人界面语言
  timezone: "Asia/Shanghai"         # 通知时间戳时区
  telegram_mode: "polling"          # polling 或 webhook
  bot_name: "EmbyBot"
  debounce_seconds: 10              # 播放事件去重间隔（秒）
  media_base_path: "/media"         # Emby 使用的本地路径
  media_cloud_path: "/cloud"        # 云盘源路径（用于同步操作）
  ip_api_provider: "baidu"          # IP 归属地：baidu/pconline/vore/ipapi/ip138
```

---

## 🤖 Bot 命令

| 命令 | 可用范围 | 说明 |
|---|---|---|
| `/start` | 所有人 | 欢迎消息与命令说明 |
| `/bind` | 所有人（私聊） | 绑定 Telegram 与 Emby 账号 |
| `/search [关键词]` | 已绑定用户 | 搜索 Emby 媒体库 |
| `/redeem` | 所有人 | 兑换邀请码或时长码 |
| `/checkin` | 群组成员 | 每日签到获取积分 |
| `/points` | 群组成员 | 查看积分余额 |
| `/status` | 管理员 | 查看当前播放会话 |
| `/manage [关键词]` | 管理员 | 打开媒体管理菜单 |
| `/settings` | 管理员 | 打开机器人设置菜单 |

---

## 🔔 Emby Webhook 配置

让 EmbyBot 接收来自 Emby 的媒体库和播放事件：

1. 在 Emby 中，进入 **控制台 → 插件 → 目录**，安装 **Webhook** 插件
2. 添加新的 Webhook，URL 填写：`http://your-bot-host:8080/`
3. Content-Type 选择 `application/json`
4. 勾选需要的事件类型：Library New、Library Deleted、Playback Start/Pause/Stop/Resume、User 事件、System 事件
5. 保存

Telegram Webhook 模式下，机器人监听端点为：`https://your.domain/telegram_webhook`

---

## 🏗️ 项目结构

```
EmbyBot/
├── EmbyBot/
│   ├── main.py                 # 入口，启动流程，订阅到期检查线程
│   ├── models.py               # SQLAlchemy ORM 模型
│   ├── api/
│   │   ├── base_client.py      # HTTP 客户端，带重试逻辑
│   │   ├── emby.py             # Emby API 封装
│   │   ├── tmdb.py             # TMDB API 封装
│   │   └── geo.py              # IP 归属地查询（多服务商）
│   ├── core/
│   │   ├── config.py           # 配置加载、设置菜单结构定义
│   │   ├── database.py         # SQLAlchemy 引擎与会话
│   │   └── cache.py            # 内存缓存
│   ├── handlers/
│   │   ├── telegram_handler.py # 命令与回调查询处理器
│   │   └── webhook_handler.py  # Emby & Telegram Webhook HTTP 服务器
│   ├── notifications/
│   │   ├── manager.py          # 通知路由与权限检查
│   │   └── telegram_driver.py  # 底层 Telegram API 调用
│   ├── logic/
│   │   ├── media_manager.py    # 文件操作（删除、同步）
│   │   └── series_helper.py    # 剧集进度与 TMDB 对比分析
│   ├── services/
│   │   ├── http_server.py      # HTTP 服务器启动
│   │   └── telegram_poller.py  # Long Polling 循环
│   ├── utils/
│   │   ├── helpers.py          # 文本解析、HTML 转义等工具
│   │   └── formatters.py       # 媒体规格格式化
│   └── i18n/                   # 多语言翻译（11 种语言）
└── config/
    └── config.example.yaml     # 配置文件模板
```

---

## 🗄️ 数据库

默认使用 **SQLite**（`/config/data/embybot.db`），首次运行自动创建。如需多实例或生产部署，可通过 `database.url` 配置 PostgreSQL 或 MySQL。

**数据表：**
- `users` — Telegram ↔ Emby 绑定关系、角色、积分、订阅到期时间
- `invitation_codes` — 一次性注册邀请码
- `duration_codes` — 续费订阅时长码
- `banned_users` — 被封禁的用户（禁止绑定和兑换）

---

## 📦 依赖

```
requests
sqlalchemy
pyyaml
babel
```

---

## 🤝 参与贡献

欢迎提交 Pull Request！请：

1. Fork 本仓库
2. 创建功能分支（`git checkout -b feature/my-feature`）
3. 提交更改
4. 发起 Pull Request

**新增语言翻译：** 在 `EmbyBot/i18n/locales/<语言代码>/LC_MESSAGES/` 下添加 `.po` 文件，参考现有语言的格式。详见 [Wiki - 国际化](wiki/Internationalization.zh-CN.md)。

---

## 📄 许可证

本项目基于 MIT 许可证开源，详见 [LICENSE](LICENSE)。
