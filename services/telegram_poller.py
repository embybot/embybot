import requests
import time
import traceback

from .. import i18n
from ..core.config import TELEGRAM_TOKEN
from ..handlers import telegram_handler
from ..notifications import telegram_driver

def poll_telegram_updates():
    print(i18n._("ℹ️ Ensuring no webhook is set before starting polling..."))
    success, description = telegram_driver.remove_telegram_webhook()
    if not success:
        print(i18n._("⚠️ Could not ensure webhook was removed. Polling may fail if a webhook is active. Reason: {reason}").format(reason=description))
    else:
        print(i18n._("✅ Webhook check complete."))
    time.sleep(2)

    update_id = 0
    bot_id = None

    if TELEGRAM_TOKEN:
        try:
            bot_id = int(TELEGRAM_TOKEN.split(':')[0])
        except (ValueError, IndexError):
            print(i18n._("❌ Could not parse Bot ID from TELEGRAM_TOKEN. Auto-deleting reply messages may not work correctly."))

    try:
        print(i18n._("🧹 Clearing old updates before starting polling..."))
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        params = {'offset': -1, 'timeout': 1}
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            updates = response.json().get('result', [])
            if updates:
                last_update_id = updates[-1]['update_id']
                update_id = last_update_id
                print(i18n._("✅ Old updates cleared. Starting polling from update ID {id}.").format(id=update_id + 1))
    except Exception as e:
        print(i18n._("⚠️ Could not clear old updates. This might cause issues after a restart. Error: {error}").format(error=e))

    print(i18n._("🤖 Telegram command polling service started..."))

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {'offset': update_id + 1, 'timeout': 30}

            response = requests.get(url, params=params, timeout=40)

            if response.status_code == 200:
                data = response.json()
                if not data.get('ok'):
                    error_desc = data.get('description', 'Unknown API error')
                    print(i18n._("❌ API error while polling for Telegram updates: {error}").format(error=error_desc))
                    time.sleep(10)
                    continue

                updates = data.get('result', [])
                for update in updates:
                    try:
                        update_id = update['update_id']

                        if 'message' in update:
                            message = update['message']
                            if not all(k in message for k in ['chat', 'message_id']) or not ('from' in message or 'sender_chat' in message):
                                print(i18n._("⚠️ Received a message with missing core fields, skipping: {msg}").format(msg=message))
                                continue

                            chat_id = message['chat']['id']
                            message_id = message['message_id']

                            is_group_chat = chat_id < 0
                            should_delete = False

                            if not is_group_chat:
                                should_delete = True
                            else:
                                msg_text = message.get('text', '')
                                if msg_text.startswith('/'):
                                    should_delete = True
                                elif 'reply_to_message' in message and bot_id:
                                    if message['reply_to_message']['from']['id'] == bot_id:
                                        should_delete = True

                            if should_delete:
                                telegram_driver.delete_user_message_later(chat_id, message_id, delay_seconds=60)

                            telegram_handler.handle_telegram_command(message)

                        elif 'callback_query' in update:
                            telegram_handler.handle_callback_query(update['callback_query'])

                    except Exception as e:
                        print(i18n._("❌ Error processing a single Telegram update (Update ID: {uid}). This update will be skipped. Error: {err}").format(uid=update.get('update_id', 'N/A'), err=e))
                        traceback.print_exc()
                        if 'update_id' in update:
                            update_id = update['update_id']

            else:
                print(i18n._("❌ Failed to poll for Telegram updates: {code} - {text}").format(code=response.status_code, text=response.text))
                time.sleep(10)

        except requests.exceptions.RequestException as e:
            error_message = str(e)
            if TELEGRAM_TOKEN:
                error_message = error_message.replace(TELEGRAM_TOKEN, "[REDACTED_TOKEN]")
            print(i18n._("❌ Network error while polling Telegram: {error}").format(error=error_message))
            time.sleep(10)

        except Exception as e:
            print(i18n._("❌ A critical error occurred while polling Telegram: {error}").format(error=e))
            traceback.print_exc()
            time.sleep(5)
