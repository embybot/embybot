import sys
import os

PACKAGE_PARENT = '..'

SCRIPT_DIR = os.path.dirname(os.path.realpath(os.path.join(os.getcwd(), os.path.expanduser(__file__))))
sys.path.append(os.path.normpath(os.path.join(SCRIPT_DIR, PACKAGE_PARENT)))

import threading
import time
from datetime import datetime

if os.getenv("PYTHONUNBUFFERED", "") == "":
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1, encoding='utf-8')
    sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1, encoding='utf-8')

from EmbyBot.core.database import SessionLocal
from EmbyBot import models
from EmbyBot.core import config, cache, database
from EmbyBot import i18n
from EmbyBot.api import base_client, emby as emby_api
from EmbyBot.services import telegram_poller, http_server
from EmbyBot.handlers.webhook_handler import QuietWebhookHandler

initial_lang = config.get_setting('settings.language') or 'en'
i18n.set_language(initial_lang)

def sync_admins_from_config():
    print(i18n._("⚙️ Synchronizing administrators from configuration file to database..."))
    db = SessionLocal()
    try:
        admin_ids_from_config = config.ADMIN_USER_ID
        if not admin_ids_from_config:
            print(i18n._("ℹ️ No admin_user_id found in configuration, skipping synchronization."))
            return

        admin_ids = [int(uid) for uid in admin_ids_from_config]

        users_in_db = db.query(models.User).filter(models.User.telegram_user_id.in_(admin_ids)).all()
        existing_admin_ids = {user.telegram_user_id for user in users_in_db}

        updated_count = 0
        created_count = 0

        for user in users_in_db:
            if user.role != 'admin':
                print(i18n._("ℹ️ Updating user {user.telegram_user_id} role to 'admin'."))
                user.role = 'admin'
                updated_count += 1

        new_admin_ids = set(admin_ids) - existing_admin_ids
        for admin_id in new_admin_ids:
            print(i18n._("ℹ️ Creating new admin user record for {admin_id}."))
            new_admin = models.User(telegram_user_id=admin_id, role='admin')
            db.add(new_admin)
            created_count += 1

        if updated_count > 0 or created_count > 0:
            db.commit()
            print(i18n._("✅ Admin synchronization complete. Updated: {upd}, Created: {new}").format(upd=updated_count, new=created_count))
        else:
            print(i18n._("✅ All administrators are already up-to-date in the database."))

    except Exception as e:
        print(i18n._("❌ An error occurred during admin synchronization: {error}").format(error=e))
        db.rollback()
    finally:
        db.close()

def main():
    database.init_db()

    sync_admins_from_config()

    def expiration_check_thread_func():
        print(i18n._("🕒 Expiration check thread started."), flush=True)
        while True:
            time.sleep(3600)
            print(i18n._("🕒 Performing scheduled check for expired users..."))
            db = SessionLocal()
            try:
                now = datetime.now()
                expired_users = db.query(models.User).filter(
                    models.User.subscription_expires_at != None,
                    models.User.subscription_expires_at < now,
                    models.User.emby_user_id != None
                ).all()

                if not expired_users:
                    print(i18n._("✅ No expired users found."))
                    continue

                for user in expired_users:
                    print(i18n._("⚠️ User {tg_id} (Emby ID: {emby_id}) has expired. Disabling access...").format(
                        tg_id=user.telegram_user_id, emby_id=user.emby_user_id
                    ))
                    success = emby_api.update_user_access(user.emby_user_id, enabled=False)
                    if success:
                        user.subscription_expires_at = None
                        db.commit()
                        print(i18n._("✅ Access disabled for expired user {emby_id}.").format(emby_id=user.emby_user_id))
            except Exception as e:
                print(i18n._("❌ An error occurred during the expiration check: {error}").format(error=e))
                db.rollback()
            finally:
                db.close()

    expiration_thread = threading.Thread(
        target=expiration_check_thread_func,
        name="ExpirationCheckThread",
        daemon=True
    )
    expiration_thread.start()

    if not config.EMBY_USER_ID:
        warning_message = i18n._(
            "============================================================\n"
            "⚠️ CRITICAL WARNING: 'user_id' not found in config.yaml.\n"
            " This may cause some Emby API requests that require user context to fail.\n"
            " It is strongly recommended to configure a valid user ID to ensure all features work correctly.\n"
            "============================================================"
        )
        print(warning_message)

    final_mode = config.get_setting('settings.telegram_mode') or 'polling'

    if final_mode == 'polling':
        print(i18n._("🚀 Starting in Long Polling mode..."))
        telegram_poll_thread = threading.Thread(
            target=telegram_poller.poll_telegram_updates,
            name="TelegramPollerThread",
            daemon=True
        )
        telegram_poll_thread.start()
    else:
        print(i18n._("🚀 Starting in Webhook mode. Waiting for incoming requests..."))

    http_server.run_server(handler_class=QuietWebhookHandler)

if __name__ == '__main__':
    main()
