import json
import asyncio
import threading
import time
import uuid

from .. import i18n
from ..core.config import TELEGRAM_TOKEN, ADMIN_USER_ID, GROUP_ID
from ..api.base_client import make_request_with_retry
from ..core.cache import GROUP_MEMBER_CACHE, ADMIN_CACHE
from ..core.cache import PAGINATED_MESSAGE_CACHE
from ..utils import helpers
from ..core.database import SessionLocal
from .. import models

def send_telegram_notification(text: str, photo_url: str = None, chat_id=None, inline_buttons: list = None, disable_preview: bool = False):
    if not chat_id:
        print(i18n._("❌ Error: chat_id not specified."))
        return

    print(i18n._("💬 Sending Telegram notification to Chat ID {id}...").format(id=chat_id))
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/" + ('sendPhoto' if photo_url else 'sendMessage')

    payload = {
        'chat_id': chat_id,
        'parse_mode': 'HTML',
        'disable_web_page_preview': disable_preview
    }
    if photo_url:
        payload['photo'], payload['caption'] = photo_url, text
    else:
        payload['text'] = text

    if inline_buttons:
        keyboard_layout = inline_buttons if (inline_buttons and isinstance(inline_buttons[0], list)) else [[button] for button in inline_buttons]
        payload['reply_markup'] = json.dumps({'inline_keyboard': keyboard_layout})

    return make_request_with_retry('POST', api_url, data=payload, timeout=20)

def send_deletable_telegram_notification(text: str, photo_url: str = None, chat_id=None, inline_buttons: list = None, delay_seconds: int = 60, disable_preview: bool = False):
    async def send_and_delete():
        if not chat_id:
            return

        api_url_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/"
        payload = {
            'chat_id': chat_id,
            'parse_mode': 'HTML',
            'disable_web_page_preview': disable_preview
        }

        if inline_buttons:
            keyboard_layout = inline_buttons if (inline_buttons and isinstance(inline_buttons[0], list)) else [[button] for button in inline_buttons]
            payload['reply_markup'] = json.dumps({'inline_keyboard': keyboard_layout})

        api_url = api_url_base + ('sendPhoto' if photo_url else 'sendMessage')
        if photo_url:
            payload['photo'], payload['caption'] = photo_url, text
        else:
            payload['text'] = text

        print(i18n._("💬 Sending deletable notification to Chat ID {id}, deleting after {seconds} seconds.").format(id=chat_id, seconds=delay_seconds))
        response = make_request_with_retry('POST', api_url, data=payload, timeout=20)

        if not response:
            return

        try:
            sent_message = response.json().get('result', {})
            message_id = sent_message.get('message_id')
        except (json.JSONDecodeError, AttributeError):
            message_id = None

        if not message_id or delay_seconds <= 0:
            return

        await asyncio.sleep(delay_seconds)
        print(i18n._("⏳ Deleting message ID: {id}.").format(id=message_id))
        delete_url = api_url_base + 'deleteMessage'
        delete_payload = {'chat_id': chat_id, 'message_id': message_id}

        del_response = make_request_with_retry('POST', delete_url, data=delete_payload, timeout=10, max_retries=5, retry_delay=5)
        if del_response is None:
            print(i18n._("ℹ️ Delete message {id}: It may no longer exist or permissions are insufficient. Ignored.").format(id=message_id))

    threading.Thread(target=lambda: asyncio.run(send_and_delete())).start()

def send_simple_telegram_message(text: str, chat_id=None, delay_seconds: int = 60):
    target_chat_id = chat_id if chat_id else ADMIN_USER_ID
    if not target_chat_id:
        return
    if not chat_id and isinstance(target_chat_id, list):
        for admin_id in target_chat_id:
            send_deletable_telegram_notification(text, chat_id=admin_id, delay_seconds=delay_seconds)
    else:
        send_deletable_telegram_notification(text, chat_id=target_chat_id, delay_seconds=delay_seconds)

def answer_callback_query(callback_query_id: str, text: str = None, show_alert: bool = False):
    print(i18n._("📞 Answering callback query: {id}").format(id=callback_query_id))
    params = {'callback_query_id': callback_query_id, 'show_alert': show_alert}
    if text:
        params['text'] = text
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    make_request_with_retry('POST', url, params=params, timeout=5)

def edit_telegram_message(chat_id, message_id, text: str, inline_buttons: list = None, disable_preview: bool = False):
    print(i18n._("✏️ Editing message in Chat ID {chat_id}, Message ID {msg_id}.").format(chat_id=chat_id, msg_id=message_id))
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
    payload = {
        'chat_id': chat_id,
        'message_id': message_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': disable_preview
    }
    if inline_buttons is not None:
        payload['reply_markup'] = json.dumps({'inline_keyboard': inline_buttons})

    try:
        resp = make_request_with_retry('POST', url, data=payload, timeout=10)
        return resp
    except Exception as e:
        print(i18n._("❌ Exception in edit_telegram_message call: {error}").format(error=e))
        return None

def edit_telegram_message_caption(chat_id, message_id, caption: str, inline_buttons: list = None):
    print(i18n._("✏️ Editing message caption in Chat ID {chat_id}, Message ID {msg_id}.").format(chat_id=chat_id, msg_id=message_id))
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageCaption"
    payload = {
        'chat_id': chat_id,
        'message_id': message_id,
        'caption': caption,
        'parse_mode': 'HTML',
    }
    if inline_buttons is not None:
        payload['reply_markup'] = json.dumps({'inline_keyboard': inline_buttons})
    make_request_with_retry('POST', url, data=payload, timeout=10)

def delete_telegram_message(chat_id, message_id):
    print(i18n._("🗑️ Deleting message in Chat ID {chat_id}, Message ID {msg_id}.").format(chat_id=chat_id, msg_id=message_id))
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
    payload = {'chat_id': chat_id, 'message_id': message_id}
    make_request_with_retry('POST', url, data=payload, timeout=10)

def delete_user_message_later(chat_id, message_id, delay_seconds=60):
    async def delete_later():
        await asyncio.sleep(delay_seconds)
        delete_telegram_message(chat_id, message_id)

    threading.Thread(target=lambda: asyncio.run(delete_later())).start()

def safe_edit_or_send_message(chat_id, message_id, text: str, buttons: list = None, disable_preview: bool = True, delete_after: int = None):
    resp = None
    message_was_edited = False
    try:
        if message_id:
            resp = edit_telegram_message(chat_id, message_id, text, inline_buttons=buttons, disable_preview=disable_preview)
            if resp and 200 <= resp.status_code < 300:
                message_was_edited = True
    except Exception as e:
        print(i18n._("❌ Exception in edit_telegram_message call: {error}").format(error=e))
        resp = None

    if message_was_edited:
        if delete_after is not None and delete_after > 0:
            delete_user_message_later(chat_id, message_id, delay_seconds=delete_after)
    else:
        if message_id:
            delete_telegram_message(chat_id, message_id)

        if delete_after is None or delete_after <= 0:
            send_telegram_notification(text=text, chat_id=chat_id, inline_buttons=buttons, disable_preview=disable_preview)
        else:
            send_deletable_telegram_notification(text=text, chat_id=chat_id, inline_buttons=buttons, disable_preview=disable_preview, delay_seconds=delete_after)

def is_super_admin(user_id) -> bool:
    from ..core.database import SessionLocal
    from .. import models

    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
        if user and user.role == 'admin':
            return True
        return False
    finally:
        db.close()

def is_user_authorized(user_id) -> bool:
    if is_super_admin(user_id):
        return True

    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
        if user and user.emby_user_id:
            return True
        return False
    finally:
        db.close()

def is_bot_admin(chat_id, user_id) -> bool:
    if is_super_admin(user_id):
        return True
    if chat_id > 0:
        return chat_id == user_id

    now = time.time()
    if chat_id in ADMIN_CACHE and (now - ADMIN_CACHE[chat_id]['timestamp'] < 300):
        return user_id in ADMIN_CACHE[chat_id]['admins']

    admin_ids = []
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getChatAdministrators"
    params = {'chat_id': chat_id}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        try:
            admins = response.json().get('result', [])
            admin_ids = [admin['user']['id'] for admin in admins if 'user' in admin]
            ADMIN_CACHE[chat_id] = {'admins': admin_ids, 'timestamp': now}
            return user_id in admin_ids
        except (json.JSONDecodeError, AttributeError):
            if chat_id in ADMIN_CACHE:
                return user_id in ADMIN_CACHE[chat_id]['admins']
            return False
    else:
        if chat_id in ADMIN_CACHE:
            return user_id in ADMIN_CACHE[chat_id]['admins']
        return False

def is_group_member(user_id: int) -> bool:
    if not GROUP_ID or not isinstance(GROUP_ID, list) or not GROUP_ID[0]:
        print("⚠️ Group member check skipped: GROUP_ID is not configured correctly.")
        return False

    target_group_id = GROUP_ID[0]
    now = time.time()

    if target_group_id in GROUP_MEMBER_CACHE and (now - GROUP_MEMBER_CACHE[target_group_id].get('timestamp', 0) < 300):
        return user_id in GROUP_MEMBER_CACHE[target_group_id].get('members', set())

    print(f"ℹ️ Checking membership for user {user_id} in group {target_group_id} via API...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getChatMember"
    params = {'chat_id': target_group_id, 'user_id': user_id}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        try:
            member_info = response.json().get('result', {})
            status = member_info.get('status')
            if status in ['creator', 'administrator', 'member']:
                if target_group_id not in GROUP_MEMBER_CACHE:
                    GROUP_MEMBER_CACHE[target_group_id] = {'members': set(), 'timestamp': 0}
                GROUP_MEMBER_CACHE[target_group_id]['members'].add(user_id)
                GROUP_MEMBER_CACHE[target_group_id]['timestamp'] = now
                print(f"✅ User {user_id} is a member of group {target_group_id}.")
                return True
        except (json.JSONDecodeError, AttributeError):
            pass

    print(f"❌ User {user_id} is not a member of group {target_group_id}.")
    return False

def send_paginated_message(chat_id: int, user_id: int, full_text: str, photo_url: str = None, buttons: list = None):
    limit = 1024 if photo_url else 4096

    if len(full_text.encode('utf-8')) <= limit:
        send_deletable_telegram_notification(full_text, photo_url, chat_id, buttons)
        return

    print(f"ℹ️ Message for chat {chat_id} is too long ({len(full_text.encode('utf-8'))} bytes > {limit}), paginating.")

    pages = []
    current_page = ""
    for line in full_text.split('\n'):
        if len((current_page + "\n" + line).encode('utf-8')) > limit:
            pages.append(current_page.strip())
            current_page = line
        else:
            if current_page:
                current_page += "\n" + line
            else:
                current_page = line
    pages.append(current_page.strip())

    total_pages = len(pages)

    cache_key = str(uuid.uuid4())
    PAGINATED_MESSAGE_CACHE[cache_key] = {
        'pages': pages,
        'photo_url': photo_url,
        'original_buttons': buttons or [],
        'timestamp': time.time(),
        'initiator_id': user_id
    }

    page_buttons = []
    if total_pages > 1:
        button_text = f"{i18n._('Next Page ▶️')} (1/{total_pages})"
        page_buttons.append({'text': button_text, 'callback_data': f'pagem_{cache_key}_1'})

    final_buttons = (buttons or []) + [page_buttons] if page_buttons else buttons

    text_to_send = pages[0]

    send_deletable_telegram_notification(
        text=text_to_send,
        photo_url=photo_url,
        chat_id=chat_id,
        inline_buttons=final_buttons
    )

def set_telegram_webhook(url: str):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"

    print(i18n._("ℹ️ Setting Telegram webhook to: {url}").format(url=url))
    payload = {'url': url}
    response = make_request_with_retry('POST', api_url, json=payload, timeout=15)

    if response and response.json().get('ok'):
        print(i18n._("✅ Webhook set successfully."))
        return True, response.json().get('description')
    else:
        error_desc = response.json().get('description', 'Unknown error') if response else 'Request failed'
        print(i18n._("❌ Failed to set webhook: {error}").format(error=error_desc))
        return False, error_desc

def remove_telegram_webhook():
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"

    print(i18n._("ℹ️ Removing Telegram webhook..."))
    payload = {'url': ''}
    response = make_request_with_retry('POST', api_url, json=payload, timeout=15)

    if response and response.json().get('ok'):
        print(i18n._("✅ Webhook removed successfully."))
        return True, response.json().get('description')
    else:
        error_desc = response.json().get('description', 'Unknown error') if response else 'Request failed'
        print(i18n._("❌ Failed to remove webhook: {error}").format(error=error_desc))
        return False, error_desc
