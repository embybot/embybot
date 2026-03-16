<div align="center">

<img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"><img src="https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram"><img src="https://img.shields.io/badge/Emby-Media_Server-52B54B?style=for-the-badge&logo=emby&logoColor=white" alt="Emby"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="License"><img src="https://img.shields.io/badge/i18n-11_Languages-orange?style=for-the-badge" alt="i18n">

# 🎬 EmbyBot

**A powerful Telegram bot for managing your Emby Media Server**

[English](#) · [简体中文](#简体中文) · [Features](#-features) · [Quick Start](#-quick-start) · [Configuration](#-configuration) · [Wiki](../../wiki)

</div>

* * *

## ✨ Features

EmbyBot bridges Telegram and your Emby server, delivering rich notifications and full management capabilities right in your chat.

### 📡 Real-time Notifications

* **New content alerts** — Notifies groups, channels, or admins when movies/series are added to your library, with poster art, media specs, and TMDB links
* **Playback events** — Start, pause, resume, and stop notifications with user, device, location, and progress details
* **Library deletions** — Alerts when content is removed
* **System events** — User logins (success/failure), user creation/deletion, password changes, server restart required

### 🎦 Media Search

* Search by **title**, **title + year**, or **TMDB ID** directly in Telegram
* Displays posters, overviews, video/audio/subtitle specs per season
* Shows series update progress (which episodes are missing vs. TMDB)
* TMDB fallback search for alias/alternate titles

### 👥 User Management

* Create Emby users cloned from a template (permissions inherited automatically)
* Rename users, change passwords, manage permissions per-user
* Delete users with automatic Telegram ↔ Emby binding cleanup
* Subscription expiry tracking with automatic access revocation

### 🔑 Invitation & Duration Code System

* Generate invitation codes for new user self-registration
* Generate duration codes to extend subscriptions (e.g. `5 90` → 5 codes × 90 days)
* Enable/disable/query/bulk-clear codes via inline menus

### 💰 Points & Check-in

* Daily check-in with optional emoji CAPTCHA verification
* Points earned for group chat messages (configurable threshold)
* Points transfer between users
* Admin tools to adjust balances and gift codes

### 🛡️ Session Control

* Enforce maximum concurrent playback sessions per user
* Automatic countdown warning → session termination sequence
* Admin `/status` command with per-session controls (stop, message, stop-all)

### 🗂️ File & Library Management

* Scan individual items or entire libraries
* Refresh metadata (full re-scrape from internet)
* Delete items from Emby, local storage, cloud drive, or all three
* **Cloud sync** — Sync new shows from a cloud drive by generating `.strm` link files and copying metadata

### ⚙️ Bot-side Settings (Inline Menu)

All settings are configurable live via Telegram inline keyboards — no config file editing needed for most options:

* Toggle every notification type on/off
* Control which fields appear in each notification
* Switch between Single-message list mode and Multi-message card mode for `/status`
* Select IP geolocation provider (5 options)
* Switch between Long Polling and Webhook mode
* Change bot language (11 languages supported)

### 🌍 Internationalization

Supports **11 languages** out of the box:

| Language | Code | Language | Code |
| --- | --- | --- | --- |
| 🇺🇸 English | `en` | 🇨🇳 简体中文 | `zh_hans` |
| 🇨🇳 繁體中文 | `zh_hant` | 🇯🇵 日本語 | `ja` |
| 🇰🇷 한국어 | `ko` | 🇷🇺 Русский | `ru` |
| 🇩🇪 Deutsch | `de` | 🇫🇷 Français | `fr` |
| 🇪🇸 Español | `es` | 🇵🇹 Português | `pt` |
| 🇮🇹 Italiano | `it` |     |     |

* * *

## 🚀 Quick Start

### Prerequisites

* Python 3.10+
* An Emby Media Server with API access
* A Telegram Bot token (from [@BotFather](https://t.me/BotFather))
* (Optional) A TMDB API key for poster art and metadata

### Docker (Recommended)

    # docker-compose.yml
    services:
      embybot:
        image: python:3.11-slim
        container_name: embybot
        working_dir: /app
        volumes:
          - ./EmbyBot:/app
          - ./config:/config
        command: pip install -r requirements.txt && python -m EmbyBot.main
        restart: unless-stopped

    # 1. Clone the repo
    git clone https://github.com/yourusername/EmbyBot.git
    cd EmbyBot
    
    # 2. Copy and edit the config
    cp config/config.example.yaml config/config.yaml
    nano config/config.yaml
    
    # 3. Start
    docker compose up -d

### Manual Installation

    # 1. Clone
    git clone https://github.com/yourusername/EmbyBot.git
    cd EmbyBot
    
    # 2. Install dependencies
    pip install -r requirements.txt
    
    # 3. Configure
    cp config/config.example.yaml /config/config.yaml
    nano /config/config.yaml
    
    # 4. Run
    python -m EmbyBot.main

* * *

## 📋 Configuration

Copy `config/config.example.yaml` to `/config/config.yaml` and fill in the required fields.

### Minimal Required Config

    telegram:
      token: "YOUR_BOT_TOKEN"           # From @BotFather
      admin_user_id: 123456789          # Your Telegram user ID (or list)
    
    emby:
      server_url: "http://your-emby-server:8096"
      api_key: "YOUR_EMBY_API_KEY"
      user_id: "YOUR_EMBY_USER_ID"      # Used for search/browse requests

### Full Config Reference

    telegram:
      token: "YOUR_BOT_TOKEN"
      admin_user_id:                    # Single ID or list
        - 123456789
        - 987654321
      group_id:                         # Group(s) to receive library notifications
        - -1001234567890
      channel_id:                       # Channel(s) to receive library notifications
        - -1009876543210
      webhook_url: "https://your.domain" # Required only for Webhook mode
      customer_service_id: 123456789    # Optional: shown in bind/redeem error messages
    
    emby:
      server_url: "http://192.168.1.100:8096"
      api_key: "abc123..."
      user_id: "emby-user-uuid-here"
      username: "admin"                 # Required for item deletion (needs user token)
      password: "your-password"         # Required for item deletion
      template_user_id: "template-uuid" # New users inherit this user's permissions
      remote_url: "https://emby.yourdomain.com"  # Used for "View on Server" buttons
      app_scheme: "emby"                # Deep link scheme for mobile apps
    
    tmdb:
      api_token: "YOUR_TMDB_API_KEY"   # For poster art, TMDB links, series progress
    
    database:
      url: "postgresql://user:pass@host/db"  # Optional; defaults to SQLite
    
    settings:
      language: "en"                    # Bot language
      timezone: "Asia/Shanghai"         # For timestamps in notifications
      telegram_mode: "polling"          # "polling" or "webhook"
      bot_name: "EmbyBot"
      debounce_seconds: 10              # Ignore duplicate playback events within N seconds
      poster_cache_ttl_days: 30
      media_base_path: "/media"         # Local path Emby uses
      media_cloud_path: "/cloud"        # Cloud/source path for sync operations
      ip_api_provider: "baidu"          # IP geolocation: baidu/ip138/pconline/vore/ipapi

* * *

## 🤖 Bot Commands

| Command | Access | Description |
| --- | --- | --- |
| `/start` | All | Welcome message and command overview |
| `/bind` | All (private chat) | Link Telegram account to an Emby account |
| `/search [query]` | Bound users | Search the Emby library |
| `/redeem` | All | Redeem an invitation or duration code |
| `/checkin` | Group members | Daily check-in for points |
| `/points` | Group members | View points balance |
| `/status` | Admins | View current playback sessions |
| `/manage [query]` | Admins | Open the media management menu |
| `/settings` | Admins | Open the bot settings menu |

* * *

## 🔔 Emby Webhook Setup

For EmbyBot to receive library and playback events from Emby:

1. In Emby, go to **Dashboard → Plugins → Webhook** (install if needed)
2. Add a new webhook with URL: `http://your-bot-host:8080/`(or `https://your.domain/` if using Webhook mode)
3. Enable the events you want: Library New, Library Deleted, Playback Start/Pause/Stop/Resume, User events, System events
4. Set Content-Type to `application/json`

For Telegram Webhook mode, the bot's webhook endpoint is: `https://your.domain/telegram_webhook`

* * *

## 🏗️ Project Structure

    EmbyBot/
    ├── EmbyBot/
    │   ├── main.py                 # Entry point, startup, expiry check thread
    │   ├── models.py               # SQLAlchemy ORM models
    │   ├── api/
    │   │   ├── base_client.py      # HTTP client with retry logic
    │   │   ├── emby.py             # Emby API wrapper
    │   │   ├── tmdb.py             # TMDB API wrapper
    │   │   └── geo.py              # IP geolocation providers
    │   ├── core/
    │   │   ├── config.py           # Config loading, settings menu structure
    │   │   ├── database.py         # SQLAlchemy engine & session
    │   │   └── cache.py            # In-memory caches
    │   ├── handlers/
    │   │   ├── telegram_handler.py # Command & callback query handlers
    │   │   └── webhook_handler.py  # Emby & Telegram webhook HTTP server
    │   ├── notifications/
    │   │   ├── manager.py          # Notification routing & auth checks
    │   │   └── telegram_driver.py  # Low-level Telegram API calls
    │   ├── logic/
    │   │   ├── media_manager.py    # File operations (delete, sync)
    │   │   └── series_helper.py    # Episode progress vs. TMDB comparison
    │   ├── services/
    │   │   ├── http_server.py      # HTTP server runner
    │   │   └── telegram_poller.py  # Long polling loop
    │   ├── utils/
    │   │   ├── helpers.py          # Text parsing, HTML escaping, etc.
    │   │   └── formatters.py       # Media spec formatting
    │   └── i18n/                   # Translations (11 languages)
    └── config/
        └── config.example.yaml     # Example configuration

* * *

## 🗄️ Database

EmbyBot uses **SQLite by default** (`/config/data/embybot.db`). For multi-instance or production deployments, configure a PostgreSQL/MySQL database via `database.url`.

**Tables:**

* `users` — Telegram ↔ Emby binding, role, points, subscription expiry
* `invitation_codes` — One-time registration codes
* `duration_codes` — Subscription extension codes
* `banned_users` — Users blocked from bind/redeem operations

* * *

## 📦 Dependencies

    requests
    sqlalchemy
    pyyaml
    babel          # i18n

* * *

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes
4. Open a Pull Request

For new languages, add a `.po` file under `EmbyBot/i18n/locales/<lang_code>/LC_MESSAGES/` following the existing format.

* * *

## 📄 License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

* * *

## 简体中文

EmbyBot 是一个功能完整的 Telegram 机器人，用于管理 Emby 媒体服务器。

**主要功能：** 媒体库新增/删除通知、播放事件推送、用户管理、邀请码/时长码系统、积分签到、会话并发控制、文件管理与云盘同步。

详细说明请参阅上方英文文档或访问 [Wiki](../../wiki)。

**快速开始：**

    git clone https://github.com/yourusername/EmbyBot.git
    cp config/config.example.yaml /config/config.yaml
    # 编辑配置文件，填入 Telegram Token、Emby 服务器信息等
    python -m EmbyBot.main

**配置文件路径：** `/config/config.yaml`（Docker 挂载）或项目根目录下的 `config/config.yaml`。
