# -*- coding: utf-8 -*-

import uuid
import time
import json
import traceback
import base64
import os
import re
import threading
import random
from datetime import datetime, date

from .. import i18n
from ..core.database import SessionLocal
from .. import models
from ..core import config
from ..notifications import telegram_driver
from ..core import cache
from ..logic import media_manager
from ..core.config import get_setting, SETTINGS_MENU_STRUCTURE, TOGGLE_INDEX_TO_KEY, TOGGLE_KEY_TO_INFO, SELECTION_KEY_TO_INFO
from ..core.cache import (
    user_context, user_search_state, DELETION_TASK_CACHE, SEARCH_RESULTS_CACHE, 
    UPDATE_PATH_CACHE, TMDB_EMBY_ID_MAP, POLICY_SESSIONS_CACHE
)
from ..api import emby as emby_api
from ..api import tmdb as tmdb_api
from ..api import geo as geo_api
from ..notifications import manager as notification_manager
from ..notifications import telegram_driver
from ..notifications.telegram_driver import (
    answer_callback_query, delete_telegram_message,
    delete_user_message_later
)
from ..notifications.manager import is_super_admin, is_user_authorized
from ..utils import helpers
from ..utils.helpers import restart_bot
from ..utils import formatters
from ..logic import series_helper

def run_task_in_background(chat_id, user_id, initial_message_id, task_func, is_group_chat, mention):
    def thread_target():
        try:
            result = task_func()
            if result is None:
                return

            if result.get('type') == 'delete_only':
                if initial_message_id:
                    telegram_driver.delete_telegram_message(chat_id, initial_message_id)
                return

            if result.get('type') == 'text':
                final_content = f"{mention}{result.get('content', '')}"
                buttons = result.get('buttons')
                delete_after = 180

                edit_success = False
                if initial_message_id:
                    resp = telegram_driver.edit_telegram_message(
                        chat_id, initial_message_id, final_content, inline_buttons=buttons
                    )
                    if resp and 200 <= resp.status_code < 300:
                        edit_success = True

                if edit_success:
                    if delete_after and delete_after > 0:
                        telegram_driver.delete_user_message_later(chat_id, initial_message_id, delay_seconds=delete_after)
                else:
                    if initial_message_id:
                        telegram_driver.delete_telegram_message(chat_id, initial_message_id)
                    telegram_driver.send_deletable_telegram_notification(
                        text=final_content, chat_id=chat_id, inline_buttons=buttons,
                        delay_seconds=delete_after
                    )

            elif result.get('type') == 'list':
                title = f"{mention}{result.get('title', '')}"
                buttons = result.get('buttons')
                if initial_message_id:
                    telegram_driver.delete_telegram_message(chat_id, initial_message_id)

                notification_manager.send_deletable_notification(text=title, chat_id=chat_id, inline_buttons=buttons, delay_seconds=180)
                
                time.sleep(0.5)

                for item in result.get('content', []):
                    notification_manager.send_deletable_notification(
                        text=item['message'], photo_url=item.get('poster_url'), chat_id=chat_id,
                        inline_buttons=item.get('buttons'), disable_preview=True, delay_seconds=180
                    )
                    time.sleep(0.5)

            elif result.get('type') == 'search_results':
                 send_results_page(
                     chat_id, result.get('search_id'), user_id, page=1, 
                     message_id=initial_message_id, 
                     intro_message_override=result.get('intro_override')
                 )

        except Exception as e:
            error_message = i18n._("❌ An unexpected error occurred in the background task: {error}").format(error=e)
            traceback.print_exc()
            notification_manager.safe_edit_or_send(chat_id, initial_message_id, error_message, delete_after=120)

    thread = threading.Thread(target=thread_target)
    thread.start()

def _start_captcha_flow(chat_id: int, user_id: int, action: str):
    ALL_EMOJIS = [
        "🍎", "🍊", "🍋", "🍉", "🍇", "🍓", "🍒", "🍑", "🥭", "🍍", "🥥", "🥝", "🍅", "🍆", "🥑",
        "🥦", "🥬", "🥒", "🌶️", "🌽", "🥕", "🧄", "🧅", "🥔", "🥐", "🥨", "🥯", "🥞", "🧇", "🧀",
        "🍖", "🍗", "🥩", "🥓", "🍔", "🍟", "🍕", "🚗", "🌭", "🥪", "🌮", "🌯", "🥙", "🧆", "🥚",
        "🍳", "🥘", "🍲", "🥣", "🥗", "🍿", "🧈", "🧂", "🥫", "🍱", "🍘", "🍙", "🍚", "🍛", "🍜"
    ]
    
    NUM_ROWS = 7
    NUM_COLS = 5
    NUM_CORRECT = 5
    TOTAL_OPTIONS = NUM_ROWS * NUM_COLS

    if len(ALL_EMOJIS) < TOTAL_OPTIONS:
        print(i18n._("Insufficient Emoji library, unable to generate verification code!"))
        return

    options = random.sample(ALL_EMOJIS, TOTAL_OPTIONS)
    correct_emojis = random.sample(options, NUM_CORRECT)
    
    prompt_text = i18n._("Please click the following emojis to verify you are human:") + "\n\n" + " ".join(correct_emojis)
    
    buttons = []
    for i in range(NUM_ROWS):
        row = []
        for j in range(NUM_COLS):
            idx = i * NUM_COLS + j
            if idx < len(options):
                emoji = options[idx]
                row.append({'text': emoji, 'callback_data': f"captcha_click_{emoji}_{user_id}"})
        buttons.append(row)
    
    sent_message_response = telegram_driver.send_telegram_notification(
        text=prompt_text, chat_id=chat_id, inline_buttons=buttons
    )

    if sent_message_response and sent_message_response.json().get('ok'):
        message_id = sent_message_response.json().get('result', {}).get('message_id')
        if message_id:
            telegram_driver.delete_user_message_later(chat_id, message_id, delay_seconds=120)

            user_context[chat_id] = {
                'state': 'awaiting_captcha',
                'initiator_id': user_id,
                'message_id': message_id,
                'correct_emojis': set(correct_emojis),
                'selected_emojis': set(),
                'on_success_action': action
            }


def _perform_checkin(chat_id: int, user_id: int, message_id: int, username: str):
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
        today = date.today()

        if not user:
            user = models.User(telegram_user_id=user_id, username=username)
            db.add(user)

        if user.last_checkin_date == today:
            result_text = i18n._("😉 You have already checked in today! Your current points: {points}").format(points=user.points)
            if message_id:
                notification_manager.edit_message(chat_id, message_id, result_text)
            else:
                notification_manager.send_simple_message(result_text, chat_id)
            return

        points_to_add = get_setting('settings.checkin.custom_points') or get_setting('settings.checkin.points_per_checkin')
        user.points = (user.points or 0) + points_to_add
        user.last_checkin_date = today
        db.commit()

        result_text = i18n._("✅ Check-in successful! You earned {points_added} points. Your current points: {points_total}").format(
            points_added=points_to_add, points_total=user.points
        )
        if message_id:
            notification_manager.edit_message(chat_id, message_id, result_text)
        else:
            notification_manager.send_simple_message(result_text, chat_id)

    finally:
        db.close()
        if message_id:
            user_context.pop(chat_id, None)

def _start_redeem_process(chat_id: int, user_id: int, message_id: int):
    user_context[chat_id] = {
        'state': 'awaiting_redemption_code',
        'initiator_id': user_id,
        'message_id': message_id
    }
    prompt = i18n._("✅ Verification successful! Please enter your redemption code:")
    buttons = [[{'text': i18n._('Cancel'), 'callback_data': f'redeem_cancel_{user_id}'}]]
    notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)

def _get_policy_key_map():
    return {
        'remote': ('EnableRemoteAccess', i18n._('Allow remote access to this Emby Server')),
        'play': ('EnableMediaPlayback', i18n._('Allow Media Playback')),
        'tv': ('EnableLiveTvAccess', i18n._('Allow Live TV Access')),
        'allf': ('EnableAllFolders', i18n._('Access All Libraries')),
    }

def _start_bind_process(chat_id: int, user_id: int, message_id: int):
    db = SessionLocal()
    try:
        existing_user = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
        if existing_user and existing_user.emby_user_id:
            emby_user_info, _ = emby_api.get_emby_user_by_id(existing_user.emby_user_id)
            if emby_user_info:
                emby_username = emby_user_info.get('Name')
                message_text = i18n._("ℹ️ Your Telegram account is already bound to the Emby account: {emby_username}").format(emby_username=emby_username)
                buttons = [[
                    {'text': i18n._('Unbind'), 'callback_data': f'bind_unbind_{user_id}'},
                    {'text': i18n._('Rebind'), 'callback_data': f'bind_rebind_{user_id}'}
                ]]
            else:
                existing_user.emby_user_id = None
                db.commit()
                message_text = i18n._("ℹ️ The Emby user you were previously bound to has been deleted. Please rebind or contact customer service!")
                contact_button = [{'text': i18n._('Contact Customer Service'), 'url': f'tg://user?id={config.CUSTOMER_SERVICE_ID}'}] if config.CUSTOMER_SERVICE_ID else []
                buttons = [[{'text': i18n._('Rebind'), 'callback_data': f'bind_rebind_{user_id}'}] + contact_button]
            notification_manager.edit_message(chat_id, message_id, text=message_text, inline_buttons=buttons)
            return
    finally:
        db.close()

    user_context[chat_id] = {'state': 'awaiting_emby_username', 'initiator_id': user_id, 'message_id': message_id}
    notification_manager.edit_message(chat_id, message_id, i18n._("✅ Verification successful!\n✍️ Please enter your Emby username:"))

def get_active_sessions_info(user_id, mention="") -> dict:
    sessions = [s for s in emby_api.get_active_sessions() if s.get('NowPlayingItem')]

    print(i18n._("ℹ️ Number of sessions in a playing state: {count}").format(count=len(sessions)))

    if not sessions:
        return {'type': 'text', 'content': i18n._("✅ No one is currently watching Emby."), 'buttons': None}

    content_mode = get_setting('settings.content_settings.status_feedback.content_mode')

    if content_mode == 'multi_message':
        sessions_data = []
        for session in sessions:
            try:
                item = session.get('NowPlayingItem', {})
                session_user_id, session_id = session.get('UserId'), session.get('Id')

                if not item or not session_id: continue

                media_details = tmdb_api.get_media_details(item, session_user_id)
                tmdb_link, year = media_details.get('tmdb_link'), media_details.get('year')

                raw_user_name = session.get('UserName', i18n._('Unknown User'))
                raw_player = session.get('Client', i18n._('Unknown Player'))
                raw_device = session.get('DeviceName', i18n._('Unknown Device'))

                ip_address = session.get('RemoteEndPoint', '').split(':')[0]
                location = geo_api.get_ip_geolocation(ip_address)

                raw_title = item.get('SeriesName') if item.get('Type') == 'Episode' else item.get('Name', i18n._('Unknown Title'))
                year_str = f" ({year})" if year else ""

                raw_episode_info = ""
                if item.get('Type') == 'Episode':
                    s_num, e_num, e_name_raw = item.get('ParentIndexNumber'), item.get('IndexNumber'), item.get('Name')
                    if s_num is not None and e_num is not None:
                        raw_episode_info = f" S{s_num:02d}E{e_num:02d} {e_name_raw or ''}"
                    else:
                        raw_episode_info = f" {e_name_raw or ''}"

                program_full_title_raw = f"{raw_title}{year_str}{raw_episode_info}"

                session_lines = [
                    f"👤 <b>{i18n._('User')}</b>: {helpers.escape_html(raw_user_name)}",
                    f"<b>{helpers.escape_html('─' * 20)}</b>"
                ]
                if get_setting('settings.content_settings.status_feedback.show_player'):
                    session_lines.append(i18n._("Player: {player}").format(player=helpers.escape_html(raw_player)))
                
                if get_setting('settings.content_settings.status_feedback.show_device'):
                    session_lines.append(i18n._("Device: {device}").format(device=helpers.escape_html(raw_device)))

                if get_setting('settings.content_settings.status_feedback.show_location'):
                    location_line = i18n._("Location: ") + (helpers.escape_html(location) if location == i18n._("LAN") else f"<code>{helpers.escape_html(ip_address)}</code> {helpers.escape_html(location)}")
                    session_lines.append(location_line)

                if get_setting('settings.content_settings.status_feedback.show_media_detail'):
                    program_line_text = i18n._("Program: ")
                    if tmdb_link and get_setting('settings.content_settings.status_feedback.media_detail_has_tmdb_link'):
                        program_line = f'{program_line_text}<a href="{tmdb_link}">{helpers.escape_html(program_full_title_raw)}</a>'
                    else:
                        program_line = f"{program_line_text}{helpers.escape_html(program_full_title_raw)}"
                    session_lines.append(program_line)
                
                if get_setting('settings.content_settings.status_feedback.show_progress'):
                    pos_ticks, run_ticks = session.get('PlayState', {}).get('PositionTicks', 0), item.get('RunTimeTicks')
                    if run_ticks and run_ticks > 0:
                        percent = (pos_ticks / run_ticks) * 100
                        raw_progress_text = f"{percent:.1f}% ({helpers.format_ticks_to_hms(pos_ticks)} / {helpers.format_ticks_to_hms(run_ticks)})"
                        session_lines.append(i18n._("Progress: {progress}").format(progress=helpers.escape_html(raw_progress_text)))

                raw_program_type = helpers.get_program_type_from_path(item.get('Path'))
                if raw_program_type and get_setting('settings.content_settings.status_feedback.show_media_type'):
                    session_lines.append(i18n._("Media Type: {type}").format(type=helpers.escape_html(raw_program_type)))
                if get_setting('settings.content_settings.status_feedback.show_overview'):
                    overview = item.get('Overview', '')
                    if overview:
                        session_lines.append(i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview[:100] + ('...' if len(overview) > 100 else ''))))
                
                if get_setting('settings.content_settings.status_feedback.show_timestamp'):
                    session_lines.append(i18n._("Time: {time}").format(time=helpers.escape_html(datetime.now(config.TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'))))

                buttons, view_button_row, action_button_row = [], [], []
                if config.EMBY_REMOTE_URL and get_setting('settings.content_settings.status_feedback.show_view_on_server_button'):
                    item_id, server_id = item.get('Id'), item.get('ServerId')
                    if item_id and server_id:
                        view_button_row.append({'text': i18n._('➡️ View on Server'), 'url': f"{config.EMBY_REMOTE_URL}/web/index.html#!/item?id={item_id}&serverId={server_id}"})
                if view_button_row: buttons.append(view_button_row)

                if session_id:
                    if get_setting('settings.content_settings.status_feedback.show_terminate_session_button'):
                        action_button_row.append({'text': i18n._('⏹️ Stop Playback'), 'callback_data': f'session_terminate_{session_id}_{user_id}'})
                    if get_setting('settings.content_settings.status_feedback.show_send_message_button'):
                        action_button_row.append({'text': i18n._('✉️ Send Message'), 'callback_data': f'session_message_{session_id}_{user_id}'})
                if action_button_row: buttons.append(action_button_row)

                sessions_data.append({
                    'message': "\n".join(session_lines),
                    'buttons': buttons if buttons else None,
                    'poster_url': media_details.get('poster_url') if get_setting('settings.content_settings.status_feedback.show_poster') else None
                })
            except Exception:
                traceback.print_exc()
                continue
        
        title_message = f"<b>{helpers.escape_html(i18n._('🎬 Current Emby Playback Sessions: {count}').format(count=len(sessions)))}</b>"
        global_buttons = []
        row = []
        if get_setting('settings.content_settings.status_feedback.show_broadcast_button'):
            row.append({'text': i18n._('✉️ Broadcast Message'), 'callback_data': f'session_broadcast_{user_id}'})
        if get_setting('settings.content_settings.status_feedback.show_terminate_all_button'):
            row.append({'text': i18n._('⏹️ Stop All'), 'callback_data': f'session_terminateall_{user_id}'})
        if row: global_buttons.append(row)
        title_message += "\n" + "\n".join([helpers.escape_html(p) for p in formatters.format_stream_details_message({}, prefix='playback_action')])
        
        return {'type': 'list', 'content': sessions_data, 'title': title_message, 'buttons': global_buttons or None}

    elif content_mode == 'single_message':
        message_lines = [f"<b>{helpers.escape_html(i18n._('🎬 Current Emby Playback Sessions: {count}').format(count=len(sessions)))}</b>"]
        for session in sessions:
            try:
                item = session.get('NowPlayingItem', {})
                session_user_id = session.get('UserId')
                if not item: continue

                session_lines = [""]

                raw_user_name = session.get('UserName', i18n._('Unknown User'))
                if get_setting('settings.content_settings.status_feedback.single_mode.show_user'):
                    session_lines.append(f"👤 <b>{i18n._('User')}</b>: {helpers.escape_html(raw_user_name)}")

                if get_setting('settings.content_settings.status_feedback.single_mode.show_player'):
                    session_lines.append(f"{i18n._('Player')}: {helpers.escape_html(session.get('Client', ''))}")
                if get_setting('settings.content_settings.status_feedback.single_mode.show_device'):
                    session_lines.append(f"{i18n._('Device')}: {helpers.escape_html(session.get('DeviceName', ''))}")
                if get_setting('settings.content_settings.status_feedback.single_mode.show_location'):
                    ip_address = session.get('RemoteEndPoint', '').split(':')[0]
                    location = geo_api.get_ip_geolocation(ip_address)
                    location_line = f"{i18n._('Location')}: " + (helpers.escape_html(location) if location == i18n._("LAN") else f"<code>{helpers.escape_html(ip_address)}</code> {helpers.escape_html(location)}")
                    session_lines.append(location_line)

                if get_setting('settings.content_settings.status_feedback.single_mode.show_media_no_link'):
                    media_details = tmdb_api.get_media_details(item, session_user_id)
                    year = media_details.get('year')
                    raw_title = item.get('SeriesName') if item.get('Type') == 'Episode' else item.get('Name', i18n._('Unknown Title'))
                    year_str = f" ({year})" if year else ""
                    raw_episode_info = ""
                    if item.get('Type') == 'Episode':
                        s, e, en = item.get('ParentIndexNumber'), item.get('IndexNumber'), item.get('Name')
                        if s is not None and e is not None:
                            raw_episode_info = f" S{s:02d}E{e:02d} {en or ''}"
                    program_full_title_raw = f"{raw_title}{year_str}{raw_episode_info}"
                    session_lines.append(f"{i18n._('Program')}: {helpers.escape_html(program_full_title_raw)}")

                message_lines.extend(session_lines)
            except Exception:
                traceback.print_exc()
                continue
        
        global_buttons = []
        row = []
        if get_setting('settings.content_settings.status_feedback.show_broadcast_button'):
            row.append({'text': i18n._('✉️ Broadcast Message'), 'callback_data': f'session_broadcast_{user_id}'})
        if get_setting('settings.content_settings.status_feedback.show_terminate_all_button'):
            row.append({'text': i18n._('⏹️ Stop All'), 'callback_data': f'session_terminateall_{user_id}'})
        if row: global_buttons.append(row)
        
        full_content = '\n'.join(message_lines)
        return {'type': 'text', 'content': f"{mention}{full_content}", 'buttons': global_buttons or None}

    return {'type': 'text', 'content': i18n._("❌ Error: Unknown content mode selected.")}

def send_settings_menu(chat_id, user_id, message_id=None, menu_key='root'):
    print(i18n._("⚙️ Sending settings menu to user {id}, menu key: {key}").format(id=user_id, key=menu_key))
    node = SETTINGS_MENU_STRUCTURE.get(menu_key, SETTINGS_MENU_STRUCTURE['root'])

    def get_breadcrumb_path(key):
        path_parts = []
        current_key = key
        while current_key is not None:
            current_node = SETTINGS_MENU_STRUCTURE.get(current_key)
            if current_node:
                path_parts.append(i18n._(current_node['label']))
                current_key = current_node.get('parent')
            else:
                break
        return " >> ".join(reversed(path_parts))

    breadcrumb_title = get_breadcrumb_path(menu_key)
    text_parts = [f"<b>{helpers.escape_html(breadcrumb_title)}</b>"]

    if menu_key == 'root':
        description_text = i18n._("Manage the bot's various features and notifications!")
        text_parts.append(helpers.escape_html(description_text))

    buttons = []

    if menu_key == 'ip_api_selection':
        text_parts.append(helpers.escape_html(i18n._("Please select an IP Geolocation API service.")))
        current_provider = get_setting('settings.ip_api_provider') or 'baidu'
        for child_key in node['children']:
            child_node = SETTINGS_MENU_STRUCTURE[child_key]
            is_selected = (child_node['config_value'] == current_provider)
            status_icon = "✅" if is_selected else " "
            buttons.append([{'text': f"{status_icon} {i18n._(child_node['label'])}", 'callback_data': f"set_ipapi_{child_node['config_value']}_{user_id}"}])

    elif menu_key == 'telegram_mode':
        text_parts.append(helpers.escape_html(i18n._("Please select the bot's working mode.")))
        current_mode = get_setting('settings.telegram_mode') or 'polling'
        for child_key in node['children']:
            child_node = SETTINGS_MENU_STRUCTURE[child_key]
            is_selected = (child_node['config_value'] == current_mode)
            status_icon = "✅" if is_selected else " "
            buttons.append([{'text': f"{status_icon} {i18n._(child_node['label'])}", 'callback_data': f"set_tgmode_{child_node['config_value']}_{user_id}"}])

    elif menu_key == 'language_selection':
        text_parts.append(helpers.escape_html(i18n._("Please select the robot's interface language.")))
        current_lang_code = get_setting('settings.language') or 'en'
        for lang_code, lang_info in config.SUPPORTED_LANGUAGES.items():
            display_name = lang_info.get('name', lang_code)
            is_selected = (lang_code == current_lang_code)
            status_icon = "✅" if is_selected else " "
            buttons.append([{'text': f"{status_icon} {display_name}", 'callback_data': f"set_lang_{lang_code}_{user_id}"}])

    elif node.get('type') == 'selection':
        current_value = get_setting(node['config_path'])
        for value, label in node.get('options', {}).items():
            is_selected = (value == current_value)
            status_icon = "✅" if is_selected else " "
            callback_data = f"sel_{menu_key}_{value}_{user_id}"
            buttons.append([{'text': f"{status_icon} {i18n._(label)}", 'callback_data': callback_data}])

        if 'custom_value_key' in node:
            custom_value_path = node['custom_value_path']
            custom_value = get_setting(custom_value_path)
            
            is_custom_selected = current_value not in node.get('options', {})
            status_icon = "✅" if is_custom_selected else " "

            custom_button_text = i18n._("Custom Points")
            if custom_value and isinstance(custom_value, int) and custom_value > 0:
                custom_button_text += i18n._(": {points} Points").format(points=custom_value)

            callback_data = f"m_custompoints_{node['custom_value_key']}_{user_id}"
            buttons.append([{'text': f"{status_icon} {custom_button_text}", 'callback_data': callback_data}])

        if 'extra_toggles' in node:
            for toggle_key in node['extra_toggles']:
                toggle_node = SETTINGS_MENU_STRUCTURE[toggle_key]
                is_enabled = get_setting(toggle_node['config_path'])
                status_icon = "✅" if is_enabled else "❌"
                item_index = toggle_node.get('index')
                if item_index is not None:
                    callback_data = f"t_{item_index}_{user_id}"
                    buttons.append([{'text': f"{status_icon} {i18n._(toggle_node['label'])}", 'callback_data': callback_data}])

    elif 'children' in node:
        for child_key in node['children']:
            child_node = SETTINGS_MENU_STRUCTURE[child_key]
            button_text = i18n._(child_node['label'])

            if child_key == 'restart_bot':
                buttons.append([{'text': button_text, 'callback_data': f'm_restart_{user_id}'}])
            elif 'children' in child_node or child_node.get('type') == 'selection':
                buttons.append([{'text': f"➡️ {button_text}", 'callback_data': f'n_{child_key}_{user_id}'}])
            elif 'config_path' in child_node:
                is_enabled = get_setting(child_node['config_path'])
                status_icon = "✅" if is_enabled else "❌"
                item_index = child_node.get('index')
                if item_index is not None:
                    callback_data = f"t_{item_index}_{user_id}"
                    buttons.append([{'text': f"{status_icon} {button_text}", 'callback_data': callback_data}])

    nav_buttons = []
    if 'parent' in node and node['parent'] is not None:
        nav_buttons.append({'text': i18n._('🔙 Back to previous step'), 'callback_data': f'n_{node["parent"]}_{user_id}'})
    nav_buttons.append({'text': i18n._('☑️ Done'), 'callback_data': f'c_menu_{user_id}'})
    buttons.append(nav_buttons)

    message_text = "\n".join(text_parts)
    if message_id:
        notification_manager.edit_message(chat_id, message_id, message_text, inline_buttons=buttons)
    else:
        notification_manager.send_notification(text=message_text, chat_id=chat_id, inline_buttons=buttons)

def _send_search_and_format(query, chat_id, user_id, is_group_chat, mention, is_manage_mode=False):
    
    notification_manager.send_deletable_notification(
        text=f"{mention}{i18n._('🔍 Searching for “{query}”, please wait...').format(query=helpers.escape_html(query))}",
        chat_id=chat_id,
        delay_seconds=10
    )
    initial_message_id = None

    def search_task():
        print(i18n._("🗃️ User {id} initiated a search, query: {query}").format(id=user_id, query=query))
        original_query = query.strip()
        request_user_id = config.EMBY_USER_ID
        if not request_user_id:
            return {'type': 'text', 'content': i18n._("❌ Error: The bot administrator has not set the Emby `user_id` in the configuration file.")}

        results = []
        intro_override = None

        if original_query.isdigit():
            tmdb_id_to_search = original_query
            if tmdb_id_to_search in TMDB_EMBY_ID_MAP:
                cached_info = TMDB_EMBY_ID_MAP[tmdb_id_to_search]
                emby_id = cached_info.get('emby_id')
                print(i18n._("✅ Cache hit! TMDB ID {tmdb_id} corresponds to Emby ID {emby_id}.").format(tmdb_id=tmdb_id_to_search, emby_id=emby_id))
                item_info = emby_api.get_series_item_basic(emby_id)
                if item_info:
                    results = [item_info]
            
            if not results:
                print(i18n._("ℹ️ Cache miss or invalid, performing TMDB API polling search..."))
                name_year_combos = tmdb_api.get_all_titles_and_year_by_id(tmdb_id_to_search)
                if not name_year_combos:
                    return {'type': 'text', 'content': i18n._("ℹ️ Could not find a program with ID <code>{id}</code> in TMDB.").format(id=helpers.escape_html(tmdb_id_to_search))}
                
                all_emby_results = []
                found_emby_ids = set()
                for name, year in name_year_combos:
                    print(i18n._("ℹ️ Searching in Emby using combination: (Name: {name}, Year: {year})").format(name=name, year=year or 'N/A'))
                    url = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items"
                    params = {'api_key': config.EMBY_API_KEY, 'SearchTerm': name, 'IncludeItemTypes': 'Movie,Series','ExcludeItemTypes': 'Season,Episode,BoxSet,Collection', 'Recursive': 'true','Fields': 'ProviderIds,Path,ProductionYear,Name,Type'}
                    if year: params['Years'] = year
                    
                    response = emby_api.make_request_with_retry('GET', url, params=params, timeout=15)
                    if response:
                        current_results = response.json().get('Items', [])
                        for item in current_results:
                            item_id = item.get('Id')
                            if item_id and item_id not in found_emby_ids:
                                provider_ids = item.get('ProviderIds', {})
                                if provider_ids.get('Tmdb') == tmdb_id_to_search:
                                    all_emby_results.append(item)
                                    found_emby_ids.add(item_id)
                results = all_emby_results
                if len(results) == 1:
                    item = results[0]
                    emby_id = item.get('Id')
                    item_type = item.get('Type')
                    if emby_id and item_type in ['Movie', 'Series']:
                        cache.update_and_save_id_map(tmdb_id=tmdb_id_to_search, emby_id=emby_id, item_type=item_type)
        else:
            url = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items"
            match = re.search(r'(\d{4})$', original_query)
            year_for_filter = match.group(1) if match else None
            search_term = original_query[:match.start()].strip() if match else original_query
            if not search_term:
                return {'type': 'text', 'content': i18n._("ℹ️ Invalid keyword!")}
            params = {'api_key': config.EMBY_API_KEY, 'SearchTerm': search_term, 'IncludeItemTypes': 'Movie,Series','ExcludeItemTypes': 'Season,Episode,BoxSet,Collection', 'Recursive': 'true','Fields': 'ProviderIds,Path,ProductionYear,Name,Type'}
            if year_for_filter: params['Years'] = year_for_filter
            response = emby_api.make_request_with_retry('GET', url, params=params, timeout=20)
            results = response.json().get('Items', []) if response else []

            if not results and not original_query.isdigit():
                print(i18n._("ℹ️ Emby did not directly find '{query}', trying TMDB fallback search.").format(query=original_query))
                tmdb_alternatives = tmdb_api.search_tmdb_multi(search_term, year_for_filter)
                
                alternative_results = []
                found_emby_ids = set()

                if tmdb_alternatives:
                    for alt in tmdb_alternatives:
                        alt_title = alt['title']
                        alt_params = {
                            'api_key': config.EMBY_API_KEY, 
                            'SearchTerm': alt_title, 
                            'IncludeItemTypes': 'Movie,Series',
                            'ExcludeItemTypes': 'Season,Episode,BoxSet,Collection',
                            'Recursive': 'true', 
                            'Fields': 'ProviderIds,Path,ProductionYear,Name,Type'
                        }
                        if year_for_filter:
                            alt_params['Years'] = year_for_filter

                        alt_response = emby_api.make_request_with_retry('GET', url, params=alt_params, timeout=10)
                        
                        if alt_response:
                            emby_items = alt_response.json().get('Items', [])
                            for item in emby_items:
                                if item.get('Name') and alt_title and item.get('Name').lower() == alt_title.lower() and item.get('Id') not in found_emby_ids:
                                    alternative_results.append(item)
                                    found_emby_ids.add(item.get('Id'))
                
                if alternative_results:
                    results = alternative_results
                    intro_override = i18n._("ℹ️ Emby did not find a program with the same name, but found programs with the alias “{query}”:").format(query=helpers.escape_html(search_term))

        if not results:
            message_template = i18n._("ℹ️ Could not find anything related to “{query}” in Emby.")
            raw_message = message_template.format(query=original_query)
            
            buttons = None
            if is_manage_mode:
                buttons = [
                    [{'text': i18n._('🔄 Restart Search'), 'callback_data': f'm_searchshow_dummy_{user_id}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                ]
            else:
                buttons = [
                    [{'text': i18n._('🔄 Restart Search'), 'callback_data': f's_search_again_{user_id}'}],
                    [{'text': i18n._('☑️ Done'), 'callback_data': f'close_dummy_{user_id}'}]
                ]
            
            return {'type': 'text', 'content': raw_message, 'buttons': buttons}

        search_id = str(uuid.uuid4())
        SEARCH_RESULTS_CACHE[search_id] = {'results': results, 'is_manage': is_manage_mode}
        print(i18n._("✅ Search successful, found {count} results, cache ID: {id}").format(count=len(results), id=search_id))
        
        return {
            'type': 'search_results',
            'search_id': search_id,
            'intro_override': intro_override
        }

    run_task_in_background(chat_id, user_id, initial_message_id, search_task, is_group_chat, mention)

def send_results_page(chat_id, search_id, user_id, page=1, message_id=None, intro_message_override=None):
    print(i18n._("📄 Sending results page {page}, cache ID: {id}").format(page=page, id=search_id))
    cache_entry = SEARCH_RESULTS_CACHE.get(search_id)
    if not cache_entry:
        error_msg = i18n._("⚠️ Sorry, this search result has expired. Please start a new search.")
        if message_id:
            notification_manager.edit_message(chat_id, message_id, error_msg)
        else:
            notification_manager.send_deletable_notification(error_msg, chat_id=chat_id)
        return
        
    results, is_manage = cache_entry['results'], cache_entry['is_manage']
    items_per_page = 10
    start_index = (page - 1) * items_per_page
    end_index = start_index + items_per_page
    page_items = results[start_index:end_index]
    
    if intro_message_override:
        message_text = helpers.escape_html(intro_message_override)
    else:
        raw_text = i18n._("Please select the program you want to manage:") if is_manage else i18n._("Found the following programs, click on a name to see details:")
        message_text = helpers.escape_html(raw_text)

    buttons, page_prefix, detail_prefix = [], 'm_page' if is_manage else 's_page', 'm_detail' if is_manage else 's_detail'
    for i, item in enumerate(page_items):
        raw_title = item.get('Name', i18n._('Unknown Title'))
        final_year = helpers.extract_year_from_path(item.get('Path')) or item.get('ProductionYear') or ''
        title_with_year = f"{raw_title} ({final_year})" if final_year else raw_title
        button_text = f"{i + 1 + start_index}. {title_with_year}"
        if get_setting('settings.content_settings.search_display.show_media_type_in_list'):
            raw_program_type = helpers.get_program_type_from_path(item.get('Path'))
            if raw_program_type: button_text += f" | {raw_program_type}"
        buttons.append([{'text': button_text, 'callback_data': f'{detail_prefix}_{search_id}_{start_index + i}_{user_id}'}])
    
    page_buttons = []
    if page > 1: page_buttons.append({'text': i18n._('◀️ Previous Page'), 'callback_data': f'{page_prefix}_{search_id}_{page-1}_{user_id}'})
    if end_index < len(results): page_buttons.append({'text': i18n._('Next Page ▶️'), 'callback_data': f'{page_prefix}_{search_id}_{page+1}_{user_id}'})
    if page_buttons: buttons.append(page_buttons)

    nav_buttons = []
    if is_manage:
        nav_buttons.append({'text': i18n._('🔄 Restart Search'), 'callback_data': f'm_searchshow_dummy_{user_id}'})
        nav_buttons.append({'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'})
    else:
        nav_buttons.append({'text': i18n._('🔄 Restart Search'), 'callback_data': f's_search_again_{user_id}'})
        nav_buttons.append({'text': i18n._('☑️ Done'), 'callback_data': f'close_dummy_{user_id}'})
    buttons.append(nav_buttons)

    if message_id:
        notification_manager.edit_message(chat_id, message_id, message_text, inline_buttons=buttons)
    else:
        notification_manager.send_deletable_notification(message_text, chat_id=chat_id, inline_buttons=buttons, delay_seconds=90)
        
def send_search_detail(chat_id, search_id, item_index, user_id, message_id):
    def task():
        print(i18n._("ℹ️ Sending search result details, cache ID: {id}, index: {index}").format(id=search_id, index=item_index))
        cache_entry = SEARCH_RESULTS_CACHE.get(search_id)
        if not cache_entry or item_index >= len(cache_entry.get('results', [])):
            return {'type': 'text', 'content': i18n._("⚠️ Sorry, this search result has expired or is invalid.")}
        
        item_from_cache = cache_entry['results'][item_index]
        item_id = item_from_cache.get('Id')
        request_user_id = config.EMBY_USER_ID
        if not request_user_id:
            return {'type': 'text', 'content': i18n._("❌ Error: The bot administrator has not set the Emby `user_id`.")}

        full_item_url = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items/{item_id}"
        params = {'api_key': config.EMBY_API_KEY, 'Fields': 'ProviderIds,Path,Overview,ProductionYear,ServerId,DateCreated'}
        response = emby_api.make_request_with_retry('GET', full_item_url, params=params, timeout=10)
        if not response:
            return {'type': 'text', 'content': i18n._("⚠️ Failed to get detailed information.")}
            
        item = response.json()
        item_type = item.get('Type')
        raw_title = item.get('Name', i18n._('Unknown Title'))
        raw_overview = item.get('Overview', i18n._('No overview available.'))
        final_year = helpers.extract_year_from_path(item.get('Path')) or item.get('ProductionYear') or ''
        media_details = tmdb_api.get_media_details(item, request_user_id)
        poster_url, tmdb_link = media_details.get('poster_url'), media_details.get('tmdb_link', '')
        
        message_parts = []
        prefix = 'movie' if item_type == 'Movie' else 'series'
        title_with_year = f"{raw_title} ({final_year})" if final_year else raw_title
        
        if tmdb_link and get_setting(f'settings.content_settings.search_display.{prefix}.title_has_tmdb_link'):
            message_parts.append(i18n._("Name: <a href=\"{link}\">{title}</a>").format(title=helpers.escape_html(title_with_year), link=tmdb_link))
        else:
            message_parts.append(i18n._("Name: <b>{title}</b>").format(title=helpers.escape_html(title_with_year)))
        
        if get_setting(f'settings.content_settings.search_display.{prefix}.show_type'):
            item_type_str = i18n._("Movie") if item_type == 'Movie' else i18n._("Series")
            message_parts.append(i18n._("Type: {type}").format(type=helpers.escape_html(item_type_str)))
            
        raw_program_type = helpers.get_program_type_from_path(item.get('Path'))
        if raw_program_type and get_setting(f'settings.content_settings.search_display.{prefix}.show_category'):
            message_parts.append(i18n._("Category: {category}").format(category=helpers.escape_html(raw_program_type)))
            
        if raw_overview and get_setting(f'settings.content_settings.search_display.{prefix}.show_overview'):
            overview_text = raw_overview[:150] + "..." if len(raw_overview) > 150 else raw_overview
            message_parts.append(i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview_text)))

        if item_type == 'Movie':
            stream_details = emby_api.get_media_stream_details(item_id, request_user_id)
            formatted_parts = formatters.format_stream_details_message(stream_details, prefix='movie')
            if formatted_parts:
                message_parts.extend(formatted_parts)
            if get_setting('settings.content_settings.search_display.movie.show_added_time'):
                date_created_str = item.get('DateCreated')
                message_parts.append(i18n._("Date Added: {time}").format(time=helpers.escape_html(helpers.format_date(date_created_str))))
        
        elif item_type == 'Series':
            season_info_list_raw = emby_api.get_series_season_media_info(item_id)
            if season_info_list_raw:
                formatted_lines = []
                for season_data in season_info_list_raw:
                    season_num = season_data['season_number']
                    stream_details = season_data['stream_details']
                    season_line = f"S{season_num:02d}:\n    {i18n._('Specs unknown')}"
                    if stream_details:
                        formatted_parts = formatters.format_stream_details_message(stream_details, is_season_info=True, prefix='series')
                        if formatted_parts:
                            season_line = f"S{season_num:02d}:\n" + "\n".join(formatted_parts)
                    formatted_lines.append(season_line)

                if formatted_lines:
                    message_parts.append(i18n._("Season Specs:") + "\n" + "\n".join(formatted_lines))        

            latest_episode = emby_api._get_latest_episode_info(item_id)
            if latest_episode:
                message_parts.append("\u200b")
                if get_setting('settings.content_settings.search_display.series.update_progress.show_latest_episode'):
                    s_num, e_num = latest_episode.get('ParentIndexNumber'), latest_episode.get('IndexNumber')
                    update_info_raw = i18n._("Season {s} Episode {e}").format(s=s_num, e=e_num) if s_num is not None and e_num is not None else i18n._("Incomplete information")
                    episode_media_details = tmdb_api.get_media_details(latest_episode, config.EMBY_USER_ID)
                    episode_tmdb_link = episode_media_details.get('tmdb_link')
                    if episode_tmdb_link and get_setting('settings.content_settings.search_display.series.update_progress.latest_episode_has_tmdb_link'):
                        message_parts.append(i18n._("Updated to: <a href=\"{link}\">{info}</a>").format(info=helpers.escape_html(update_info_raw), link=episode_tmdb_link))
                    else:
                        message_parts.append(i18n._("Updated to: {info}").format(info=helpers.escape_html(update_info_raw)))
                
                if get_setting('settings.content_settings.search_display.series.update_progress.show_overview'):
                    episode_overview = latest_episode.get('Overview')
                    if episode_overview:
                        overview_text = episode_overview[:100] + "..." if len(episode_overview) > 100 else episode_overview
                        message_parts.append(i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview_text)))
                
                if get_setting('settings.content_settings.search_display.series.update_progress.show_added_time'):
                    message_parts.append(i18n._("Date Added: {time}").format(time=helpers.escape_html(helpers.format_date(latest_episode.get('DateCreated')))))

                if get_setting('settings.content_settings.search_display.series.update_progress.show_progress_status'):
                    series_tmdb_id = media_details.get('tmdb_id')
                    local_s_num = latest_episode.get('ParentIndexNumber')
                    local_e_num = latest_episode.get('IndexNumber')
                    lines = series_helper.build_seasonwise_progress_and_missing_lines(series_tmdb_id, item_id, local_s_num, local_e_num)
                    if lines:
                        message_parts.extend(lines)

        final_message = "\n".join(filter(None, message_parts))
        final_poster_url = poster_url if poster_url and get_setting(f'settings.content_settings.search_display.{prefix}.show_poster') else None
        
        buttons = []
        if get_setting(f'settings.content_settings.search_display.{prefix}.show_view_on_server_button') and config.EMBY_REMOTE_URL:
            server_id = item.get('ServerId')
            if item_id and server_id:
                item_url = f"{config.EMBY_REMOTE_URL}/web/index.html#!/item?id={item_id}&serverId={server_id}"
                buttons.append([{'text': i18n._('➡️ View on Server'), 'url': item_url}])

        telegram_driver.send_paginated_message(
            chat_id=chat_id,
            user_id=user_id,
            full_text=final_message,
            photo_url=final_poster_url,
            buttons=buttons
        )
        return {'type': 'delete_only'}

    if message_id:
        delete_telegram_message(chat_id, message_id)
    run_task_in_background(chat_id, user_id, None, task, chat_id < 0, "")

def send_manage_detail(chat_id, search_id, item_index, user_id, message_id):
    def task():
        print(i18n._("ℹ️ Sending manage details, cache ID: {id}, index: {index}").format(id=search_id, index=item_index))
        cache_entry = SEARCH_RESULTS_CACHE.get(search_id)
        if not cache_entry or item_index >= len(cache_entry.get('results', [])):
            return {'type': 'text', 'content': i18n._("⚠️ Sorry, this search result has expired or is invalid.")}

        item_from_cache = cache_entry['results'][item_index]
        item_id = item_from_cache.get('Id')
        request_user_id = config.EMBY_USER_ID
        if not request_user_id:
            return {'type': 'text', 'content': i18n._("❌ Error: The bot administrator has not set the Emby `user_id`.")}

        full_item_url = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items/{item_id}"
        params = {'api_key': config.EMBY_API_KEY, 'Fields': 'ProviderIds,Path,Overview,ProductionYear,ServerId,DateCreated'}
        response = emby_api.make_request_with_retry('GET', full_item_url, params=params, timeout=10)
        if not response:
            return {'type': 'text', 'content': i18n._("⚠️ Failed to get detailed information.")}
            
        item = response.json()
        item_type = item.get('Type')
        raw_title = item.get('Name', i18n._('Unknown Title'))
        raw_overview = item.get('Overview', i18n._('No overview available.'))
        final_year = helpers.extract_year_from_path(item.get('Path')) or item.get('ProductionYear') or ''
        media_details = tmdb_api.get_media_details(item, request_user_id)
        poster_url, tmdb_link = media_details.get('poster_url'), media_details.get('tmdb_link', '')
        
        message_parts = []
        prefix = 'movie' if item_type == 'Movie' else 'series'
        title_with_year = f"{raw_title} ({final_year})" if final_year else raw_title
        
        if tmdb_link and get_setting(f'settings.content_settings.search_display.{prefix}.title_has_tmdb_link'):
            message_parts.append(i18n._("Name: <a href=\"{link}\">{title}</a>").format(title=helpers.escape_html(title_with_year), link=tmdb_link))
        else:
            message_parts.append(i18n._("Name: <b>{title}</b>").format(title=helpers.escape_html(title_with_year)))
        
        if get_setting(f'settings.content_settings.search_display.{prefix}.show_type'):
            item_type_str = i18n._("Movie") if item_type == 'Movie' else i18n._("Series")
            message_parts.append(i18n._("Type: {type}").format(type=helpers.escape_html(item_type_str)))
            
        raw_program_type = helpers.get_program_type_from_path(item.get('Path'))
        if raw_program_type and get_setting(f'settings.content_settings.search_display.{prefix}.show_category'):
            message_parts.append(i18n._("Category: {category}").format(category=helpers.escape_html(raw_program_type)))
            
        if raw_overview and get_setting(f'settings.content_settings.search_display.{prefix}.show_overview'):
            overview_text = raw_overview[:150] + "..." if len(raw_overview) > 150 else raw_overview
            message_parts.append(i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview_text)))

        if item_type == 'Movie':
            stream_details = emby_api.get_media_stream_details(item_id, request_user_id)
            formatted_parts = formatters.format_stream_details_message(stream_details, prefix='movie')
            if formatted_parts:
                message_parts.extend([helpers.escape_html(part) for part in formatted_parts])
            if get_setting('settings.content_settings.search_display.movie.show_added_time'):
                date_created_str = item.get('DateCreated')
                message_parts.append(i18n._("Date Added: {time}").format(time=helpers.escape_html(helpers.format_date(date_created_str))))
        
        elif item_type == 'Series':
            season_info_list_raw = emby_api.get_series_season_media_info(item_id)
            if season_info_list_raw:
                formatted_lines = []
                for season_data in season_info_list_raw:
                    season_num = season_data['season_number']
                    stream_details = season_data['stream_details']
                    season_line = f"S{season_num:02d}:\n    {i18n._('Specs unknown')}"
                    if stream_details:
                        formatted_parts = formatters.format_stream_details_message(stream_details, is_season_info=True, prefix='series')
                        if formatted_parts:
                            escaped_parts = [helpers.escape_html(part) for part in formatted_parts]
                            season_line = f"S{season_num:02d}:\n" + "\n".join(escaped_parts)
                    formatted_lines.append(season_line)

                if formatted_lines:
                    message_parts.append(i18n._("Season Specs:") + "\n" + "\n".join(formatted_lines))

            
            latest_episode = emby_api._get_latest_episode_info(item_id)
            if latest_episode:
                message_parts.append("\u200b")
                if get_setting('settings.content_settings.search_display.series.update_progress.show_latest_episode'):
                    s_num, e_num = latest_episode.get('ParentIndexNumber'), latest_episode.get('IndexNumber')
                    update_info_raw = i18n._("Season {s} Episode {e}").format(s=s_num, e=e_num) if s_num is not None and e_num is not None else i18n._("Incomplete information")
                    episode_media_details = tmdb_api.get_media_details(latest_episode, config.EMBY_USER_ID)
                    episode_tmdb_link = episode_media_details.get('tmdb_link')
                    if episode_tmdb_link and get_setting('settings.content_settings.search_display.series.update_progress.latest_episode_has_tmdb_link'):
                        message_parts.append(i18n._("Updated to: <a href=\"{link}\">{info}</a>").format(info=helpers.escape_html(update_info_raw), link=episode_tmdb_link))
                    else:
                        message_parts.append(i18n._("Updated to: {info}").format(info=helpers.escape_html(update_info_raw)))
                
                if get_setting('settings.content_settings.search_display.series.update_progress.show_overview'):
                    episode_overview = latest_episode.get('Overview')
                    if episode_overview:
                        overview_text = episode_overview[:100] + "..." if len(episode_overview) > 100 else episode_overview
                        message_parts.append(i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview_text)))
                
                if get_setting('settings.content_settings.search_display.series.update_progress.show_added_time'):
                    message_parts.append(i18n._("Date Added: {time}").format(time=helpers.escape_html(helpers.format_date(latest_episode.get('DateCreated')))))

                if get_setting('settings.content_settings.search_display.series.update_progress.show_progress_status'):
                    series_tmdb_id = media_details.get('tmdb_id')
                    local_s_num = latest_episode.get('ParentIndexNumber')
                    local_e_num = latest_episode.get('IndexNumber')
                    lines = series_helper.build_seasonwise_progress_and_missing_lines(series_tmdb_id, item_id, local_s_num, local_e_num)
                    if lines:
                        message_parts.extend(lines)

        final_message = "\n".join(filter(None, message_parts))
        final_poster_url = poster_url if poster_url and get_setting(f'settings.content_settings.search_display.{prefix}.show_poster') else None
        
        buttons = []
        if get_setting(f'settings.content_settings.search_display.{prefix}.show_view_on_server_button') and config.EMBY_REMOTE_URL:
            server_id = item.get('ServerId')
            if item_id and server_id:
                item_url = f"{config.EMBY_REMOTE_URL}/web/index.html#!/item?id={item_id}&serverId={server_id}"
                buttons.append([{'text': i18n._('➡️ View on Server'), 'url': item_url}])
                
        buttons.append([{'text': i18n._('🔄 Manage this program'), 'callback_data': f'm_files_{item_id}_{user_id}'}])
        buttons.append([{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}])

        telegram_driver.send_paginated_message(
            chat_id=chat_id,
            user_id=user_id,
            full_text=final_message,
            photo_url=final_poster_url,
            buttons=buttons
        )
        return {'type': 'delete_only'}

    if message_id:
        delete_telegram_message(chat_id, message_id)
    run_task_in_background(chat_id, user_id, None, task, chat_id < 0, "")

def send_points_menu(chat_id, user_id, message_id=None):
    if not get_setting('settings.points.enabled'):
        text = i18n._("ℹ️ The points feature is currently disabled.")
        if message_id:
            notification_manager.edit_message(chat_id, message_id, text, inline_buttons=[])
        else:
            notification_manager.send_simple_message(text, chat_id)
        return

    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
        current_points = user.points if user else 0
        
        text = i18n._("💰 Your current points: {points}").format(points=current_points)
        buttons = [
            [
                {'text': i18n._('Transfer'), 'callback_data': f'points_transfer_{user_id}'},
                {'text': i18n._('Redeem'), 'callback_data': f'points_redeem_{user_id}'}
            ]
        ]
        if message_id:
            notification_manager.edit_message(chat_id, message_id, text, inline_buttons=buttons)
        else:
            notification_manager.send_deletable_notification(text, chat_id=chat_id, inline_buttons=buttons)
    finally:
        db.close()

def handle_telegram_command(message: dict):
    global user_search_state, user_context, DELETION_TASK_CACHE

    msg_text = (message.get('text') or '').strip()
    chat_id = message['chat']['id']

    if 'from' not in message:
        return
    user_id = message['from']['id']

    print(i18n._("➡️ Received command from user {user_id} in Chat ID {chat_id}: {text}").format(
        user_id=user_id, chat_id=chat_id, text=msg_text
    ))

    is_group_chat = chat_id < 0
    is_reply = 'reply_to_message' in message
    mention = f"@{message['from'].get('username')} " if is_group_chat and message['from'].get('username') else ""

    if is_group_chat and not msg_text.startswith('/'):
        if get_setting('settings.points.enabled') and get_setting('settings.points.group_chat.enabled'):
            if str(chat_id) in config.GROUP_ID:
                if (re.search("[\u4e00-\u9FFF]", msg_text) and len(msg_text) > 10) or \
                   (not re.search("[\u4e00-\u9FFF]", msg_text) and len(msg_text.split()) > 10):
                    db = SessionLocal()
                    try:
                        user = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
                        if not user:
                            user = models.User(telegram_user_id=user_id, username=message['from'].get('username'))
                            db.add(user)
                        points_to_add = get_setting('settings.points.group_chat.custom_points') or get_setting('settings.points.group_chat.points_per_message')
                        user.points = (user.points or 0) + points_to_add
                        db.commit()
                        print(i18n._("✅ User {user_id} earned {points} points for group message.").format(user_id=user_id, points=points_to_add))
                    finally:
                        db.close()

    if chat_id in user_context:
        if msg_text.startswith('/'):
            user_context.pop(chat_id, None)
        else:
            ctx = user_context.get(chat_id)
            if not ctx: return

            if ctx.get('initiator_id') and ctx['initiator_id'] != user_id:
                return

            state = ctx.get('state')
            original_message_id = ctx.get('message_id')

            if state == 'awaiting_emby_username':
                emby_username_to_check = msg_text.strip()
                emby_user_obj, error_msg = emby_api.get_emby_user_by_name(emby_username_to_check)
                if not emby_user_obj:
                    notification_manager.send_simple_message(i18n._("❌ This Emby user was not found on the server. Please check the spelling and try again, or use /bind to restart."), chat_id)
                    user_context.pop(chat_id, None)
                    return
                emby_user_id_to_check = emby_user_obj.get('Id')
                db = SessionLocal()
                try:
                    existing_binding = db.query(models.User).filter(models.User.emby_user_id == emby_user_id_to_check).first()
                    if existing_binding:
                        message_text = i18n._("❌ This Emby user is already bound, please bind another Emby user, or contact the administrator!")
                        contact_button = [{'text': i18n._('Contact Customer Service'), 'url': f'tg://user?id={config.CUSTOMER_SERVICE_ID}'}] if config.CUSTOMER_SERVICE_ID else []
                        buttons = [[{'text': i18n._('Re-enter'), 'callback_data': f'bind_reenter_{user_id}'}] + contact_button]
                        notification_manager.send_deletable_notification(text=message_text, chat_id=chat_id, inline_buttons=buttons)
                        user_context.pop(chat_id, None)
                        return
                finally:
                    db.close()
                user_context[chat_id] = {'state': 'awaiting_emby_password', 'initiator_id': user_id, 'emby_username': emby_username_to_check}
                notification_manager.send_deletable_notification(i18n._("✍️ Now, please enter your Emby password:"), chat_id=chat_id, delay_seconds=60)
                return

            if state == 'awaiting_emby_password':
                emby_username = ctx.get('emby_username')
                emby_password = msg_text.strip()
                telegram_driver.delete_telegram_message(chat_id, message['message_id'])
                notification_manager.send_deletable_notification(
                    text=i18n._("🔄 Verifying your credentials, please wait..."),
                    chat_id=chat_id,
                    delay_seconds=10
                )
                initial_message_id = None
                def task():
                    emby_user_data = emby_api.authenticate_and_get_emby_user(emby_username, emby_password)
                    if not emby_user_data:
                        return {'type': 'text', 'content': i18n._("❌ Binding failed. Please check your username and password and try /bind again.")}
                    emby_user_id = emby_user_data.get('Id')
                    db = SessionLocal()
                    try:
                        existing_binding = db.query(models.User).filter(models.User.emby_user_id == emby_user_id).first()
                        if existing_binding:
                            return {'type': 'text', 'content': i18n._("❌ Binding failed. This Emby account is already bound to another Telegram account.")}
                        current_user_record = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
                        if not current_user_record:
                            current_user_record = models.User(telegram_user_id=user_id, username=message['from'].get('username'))
                            db.add(current_user_record)
                        current_user_record.emby_user_id = emby_user_id
                        db.commit()
                        return {'type': 'text', 'content': i18n._("✅ Binding successful! Your Telegram account is now linked to Emby account: {emby_username}").format(emby_username=emby_username)}
                    finally:
                        db.close()
                user_context.pop(chat_id, None)
                run_task_in_background(chat_id, user_id, initial_message_id, task, is_group_chat, mention)
                return

            if state == 'awaiting_transfer_target_id':
                if not msg_text.isdigit():
                    prompt = i18n._("❌ Invalid format. Please enter the recipient's numeric Telegram ID.")
                    buttons = [[
                        {'text': i18n._('🔙 Back to previous step'), 'callback_data': f'points_backtomenu_{user_id}'},
                        {'text': i18n._('Cancel'), 'callback_data': f'points_cancel_{user_id}'}
                    ]]
                    notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                    return
                
                target_id = int(msg_text)
                db = SessionLocal()
                try:
                    target_user = db.query(models.User).filter(models.User.telegram_user_id == target_id).first()
                    if not target_user:
                        prompt = i18n._("❌ User not found in the database. Please check the ID and re-enter.")
                        buttons = [[
                            {'text': i18n._('🔙 Back to previous step'), 'callback_data': f'points_backtomenu_{user_id}'},
                            {'text': i18n._('Cancel'), 'callback_data': f'points_cancel_{user_id}'}
                        ]]
                        notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                        return
                    
                    user_context[chat_id]['state'] = 'awaiting_transfer_amount'
                    user_context[chat_id]['target_id'] = target_id
                    user_context[chat_id]['target_name'] = target_user.username or str(target_id)
                    prompt = i18n._("✍️ Please enter the amount of points to transfer to {target_name}:").format(target_name=user_context[chat_id]['target_name'])
                    buttons = [[
                        {'text': i18n._('🔙 Back to previous step'), 'callback_data': f'points_backtomenu_{user_id}'},
                        {'text': i18n._('Cancel'), 'callback_data': f'points_cancel_{user_id}'}
                    ]]
                    notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                finally:
                    db.close()
                return

            if state == 'awaiting_transfer_amount':
                if not msg_text.isdigit() or int(msg_text) <= 0:
                    prompt = i18n._("❌ Invalid amount. Please enter a positive integer.")
                    buttons = [[{'text': i18n._('Cancel'), 'callback_data': f'points_cancel_{user_id}'}]]
                    notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                    return
                amount = int(msg_text)
                db = SessionLocal()
                try:
                    sender = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
                    if not sender or sender.points < amount:
                        prompt = i18n._("❌ Insufficient points. You currently have {points} points.").format(points=sender.points if sender else 0)
                        buttons = [[{'text': i18n._('Cancel'), 'callback_data': f'points_cancel_{user_id}'}]]
                        notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                        return
                    user_context[chat_id]['state'] = 'awaiting_transfer_confirmation'
                    user_context[chat_id]['amount'] = amount
                    target_name = user_context[chat_id]['target_name']
                    prompt = i18n._("❓ Please confirm the transfer:\n\nRecipient: {target_name}\nAmount: {amount} points\n\nYour balance after transfer: {new_balance} points").format(target_name=target_name, amount=amount, new_balance=sender.points - amount)
                    buttons = [
                        [{'text': i18n._('✅ Confirm'), 'callback_data': f'points_confirm_transfer_{user_id}'}],
                        [{'text': i18n._('Cancel'), 'callback_data': f'points_cancel_{user_id}'}]
                    ]
                    notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                finally:
                    db.close()
                return

            if state == 'awaiting_redemption_code':
                code_to_check = msg_text.strip()
                db = SessionLocal()
                try:
                    duration_code = db.query(models.DurationCode).filter(models.DurationCode.code == code_to_check).first()
                    if duration_code:
                        if not duration_code.is_valid:
                            notification_manager.edit_message(chat_id, original_message_id, i18n._("❌ This code has been disabled."))
                            user_context.pop(chat_id, None)
                            return
                        if duration_code.is_used:
                            notification_manager.edit_message(chat_id, original_message_id, i18n._("❌ This code has already been used."))
                            user_context.pop(chat_id, None)
                            return
                        
                        user = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
                        if not user or not user.emby_user_id:
                            prompt = i18n._("ℹ️ You must bind an Emby account before redeeming a duration code. Please use /bind first.")
                            notification_manager.edit_message(chat_id, original_message_id, prompt)
                            user_context.pop(chat_id, None)
                            return
                        
                        prompt = i18n._("✅ Duration code found!\n\nDuration: {days} days\n\nAre you sure you want to redeem it?").format(days=duration_code.duration_days)
                        buttons = [
                            [{'text': i18n._('Redeem Now'), 'callback_data': f'redeem_confirm_duration_{code_to_check}_{user_id}'}],
                            [{'text': i18n._('Cancel'), 'callback_data': f'redeem_cancel_{user_id}'}]
                        ]
                        notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                        return

                    invite_code = db.query(models.InvitationCode).filter(models.InvitationCode.code == code_to_check).first()
                    if invite_code:
                        if not invite_code.is_valid:
                            notification_manager.edit_message(chat_id, original_message_id, i18n._("❌ This code has been disabled."))
                            user_context.pop(chat_id, None)
                            return
                        if invite_code.is_used:
                            notification_manager.edit_message(chat_id, original_message_id, i18n._("❌ This code has already been used."))
                            user_context.pop(chat_id, None)
                            return
                        
                        prompt = i18n._("✅ Invitation code found!\n\nThis will create a new Emby user account for you. Continue?")
                        buttons = [
                            [{'text': i18n._('Continue'), 'callback_data': f'redeem_confirm_invite_{code_to_check}_{user_id}'}],
                            [{'text': i18n._('Cancel'), 'callback_data': f'redeem_cancel_{user_id}'}]
                        ]
                        notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                        return

                    prompt = i18n._("❌ Redemption code not found. Please check your input and use /redeem to try again.")
                    notification_manager.edit_message(chat_id, original_message_id, prompt)
                    user_context.pop(chat_id, None)

                finally:
                    db.close()
                return

            if state == 'awaiting_invite_credentials':
                parts = msg_text.split()
                if len(parts) > 2 or (len(parts) > 0 and ' ' in parts[0]):
                    error_msg = i18n._("❌ Format error. Usernames cannot contain spaces, and only one space should separate the username and password.")
                    notification_manager.safe_edit_or_send(chat_id, original_message_id, helpers.escape_html(error_msg), delete_after=60)
                    return
                
                username = parts[0] if len(parts) > 0 else ""
                password = parts[1] if len(parts) > 1 else ""
                if not username:
                    error_msg = i18n._("❌ Username cannot be empty.")
                    notification_manager.safe_edit_or_send(chat_id, original_message_id, helpers.escape_html(error_msg), delete_after=60)
                    return
                
                invite_code = ctx.get('invite_code')
                
                notification_manager.edit_message(chat_id, original_message_id, i18n._("✅ Request received, creating user in the background..."))
                
                def task():
                    result_message = emby_api.create_emby_user(username, password)
                    if not result_message.startswith("✅"):
                        return {'type': 'text', 'content': result_message}
                    
                    from datetime import datetime, timedelta
                    db = SessionLocal()
                    try:
                        new_emby_user, _ = emby_api.get_emby_user_by_name(username)
                        if not new_emby_user:
                            return {'type': 'text', 'content': i18n._("❌ Critical error: New Emby user could not be found after creation.")}
                        new_emby_id = new_emby_user.get('Id')

                        tg_user = db.query(models.User).filter(models.User.telegram_user_id == user_id).first()
                        if not tg_user:
                            tg_user = models.User(telegram_user_id=user_id, username=message['from'].get('username'))
                            db.add(tg_user)
                        
                        tg_user.emby_user_id = new_emby_id
                        expiry_date = datetime.now() + timedelta(days=2)
                        tg_user.subscription_expires_at = expiry_date

                        i_code = db.query(models.InvitationCode).filter(models.InvitationCode.code == invite_code).first()
                        if i_code:
                            i_code.is_used = True
                            i_code.used_by_telegram_id = user_id
                            i_code.used_by_emby_id = new_emby_id
                            i_code.used_at = datetime.now()

                        db.commit()

                        final_text = i18n._("✅ User '{username}' created successfully!\n\nYour account is valid until: {date}.\nPlease renew your subscription in time to avoid service interruption.").format(
                            username=username,
                            date=expiry_date.strftime('%Y-%m-%d %H:%M:%S')
                        )
                        buttons = [[{'text': i18n._('Done'), 'callback_data': f'redeem_done_{user_id}'}]]
                        return {'type': 'text', 'content': final_text, 'buttons': buttons}

                    finally:
                        db.close()

                run_task_in_background(chat_id, user_id, original_message_id, task, is_group_chat, mention)
                user_context.pop(chat_id, None)
                return

            if state == 'awaiting_new_duration_codes':
                parts = msg_text.split()
                if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                    error_msg = i18n._("❌ Invalid format. Please enter two numbers separated by a space (e.g., 5 90).")
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_durationcodemain_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    notification_manager.edit_message(chat_id, original_message_id, error_msg, inline_buttons=buttons)
                    return
                
                count, days = int(parts[0]), int(parts[1])
                if not (0 < count <= 50 and days > 0):
                    error_msg = i18n._("❌ Invalid numbers. The number of codes must be between 1 and 50, and the duration must be greater than 0.")
                    notification_manager.edit_message(chat_id, original_message_id, error_msg)
                    return

                notification_manager.edit_message(chat_id, original_message_id, i18n._("⏳ Generating codes, please wait..."))
                
                new_codes = []
                db = SessionLocal()
                try:
                    for _ in range(count):
                        while True:
                            code_val = str(uuid.uuid4()).upper().replace('-', '')[:16]
                            code_val = '-'.join(code_val[i:i+4] for i in range(0, len(code_val), 4))
                            
                            exists = db.query(models.DurationCode).filter(models.DurationCode.code == code_val).first() or \
                                     db.query(models.InvitationCode).filter(models.InvitationCode.code == code_val).first()
                            if not exists:
                                new_codes.append(code_val)
                                break
                    
                    for code_val in new_codes:
                        new_code_obj = models.DurationCode(
                            code=code_val,
                            owner_telegram_id=user_id,
                            duration_days=days,
                            is_valid=True,
                            is_used=False
                        )
                        db.add(new_code_obj)
                    db.commit()

                    codes_str = "\n".join([f"<code>{code}</code>" for code in new_codes])
                    success_msg = i18n._("✅ Successfully generated {count} duration codes for {days} days:\n\n{codes}").format(
                        count=count, days=days, codes=codes_str
                    )
                    
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_durationcodemain_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    notification_manager.edit_message(chat_id, original_message_id, success_msg, inline_buttons=buttons)

                except Exception as e:
                    db.rollback()
                    notification_manager.edit_message(chat_id, original_message_id, f"❌ An error occurred: {e}")
                finally:
                    db.close()
                
                user_context.pop(chat_id, None)
                return

            if state in ['awaiting_duration_code_to_disable', 'awaiting_duration_code_to_enable']:
                code_to_manage = msg_text.strip()
                is_disable = state == 'awaiting_duration_code_to_disable'
                verb_past = i18n._('disabled') if is_disable else i18n._('enabled')
                new_is_valid_status = not is_disable

                db = SessionLocal()
                try:
                    code_obj = db.query(models.DurationCode).filter(models.DurationCode.code == code_to_manage).first()
                    if not code_obj:
                        error_msg = i18n._("❌ Code not found. Please check the input and try again.")
                        notification_manager.edit_message(chat_id, original_message_id, error_msg)
                        return

                    code_obj.is_valid = new_is_valid_status
                    db.commit()

                    success_msg = i18n._("✅ Code <code>{code}</code> has been successfully {verb}.").format(code=helpers.escape_html(code_to_manage), verb=verb_past)
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_managedurationcodes_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    notification_manager.edit_message(chat_id, original_message_id, success_msg, inline_buttons=buttons)

                finally:
                    db.close()
                
                user_context.pop(chat_id, None)
                return

            if state == 'awaiting_new_invite_codes':
                if not msg_text.isdigit():
                    error_msg = i18n._("❌ Invalid format. Please enter a single number (e.g., 5).")
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_invitecodemain_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    notification_manager.edit_message(chat_id, original_message_id, error_msg, inline_buttons=buttons)
                    return
                
                count = int(msg_text)
                if not (0 < count <= 50):
                    error_msg = i18n._("❌ Invalid number. The number of codes must be between 1 and 50.")
                    notification_manager.edit_message(chat_id, original_message_id, error_msg)
                    return

                notification_manager.edit_message(chat_id, original_message_id, i18n._("⏳ Generating codes, please wait..."))
                
                new_codes = []
                db = SessionLocal()
                try:
                    for _ in range(count):
                        while True:
                            code_val = str(uuid.uuid4()).upper().replace('-', '')[:16]
                            code_val = '-'.join(code_val[i:i+4] for i in range(0, len(code_val), 4))
                            
                            exists = db.query(models.DurationCode).filter(models.DurationCode.code == code_val).first() or \
                                     db.query(models.InvitationCode).filter(models.InvitationCode.code == code_val).first()
                            if not exists:
                                new_codes.append(code_val)
                                break
                    
                    for code_val in new_codes:
                        new_code_obj = models.InvitationCode(
                            code=code_val,
                            owner_telegram_id=user_id,
                            is_valid=True,
                            is_used=False
                        )
                        db.add(new_code_obj)
                    db.commit()

                    codes_str = "\n".join([f"<code>{code}</code>" for code in new_codes])
                    success_msg = i18n._("✅ Successfully generated {count} invitation codes:\n\n{codes}").format(
                        count=count, codes=codes_str
                    )
                    
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_invitecodemain_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    notification_manager.edit_message(chat_id, original_message_id, success_msg, inline_buttons=buttons)

                except Exception as e:
                    db.rollback()
                    notification_manager.edit_message(chat_id, original_message_id, f"❌ An error occurred: {e}")
                finally:
                    db.close()
                
                user_context.pop(chat_id, None)
                return

            if state in ['awaiting_invite_code_to_disable', 'awaiting_invite_code_to_enable']:
                code_to_manage = msg_text.strip()
                is_disable = state == 'awaiting_invite_code_to_disable'
                verb_past = i18n._('disabled') if is_disable else i18n._('enabled')
                new_is_valid_status = not is_disable

                db = SessionLocal()
                try:
                    code_obj = db.query(models.InvitationCode).filter(models.InvitationCode.code == code_to_manage).first()
                    if not code_obj:
                        error_msg = i18n._("❌ Code not found. Please check the input and try again.")
                        notification_manager.edit_message(chat_id, original_message_id, error_msg)
                        return

                    code_obj.is_valid = new_is_valid_status
                    db.commit()

                    success_msg = i18n._("✅ Code <code>{code}</code> has been successfully {verb}.").format(code=helpers.escape_html(code_to_manage), verb=verb_past)
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_manageinvitecodes_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    notification_manager.edit_message(chat_id, original_message_id, success_msg, inline_buttons=buttons)

                finally:
                    db.close()
                
                user_context.pop(chat_id, None)
                return
 
            if state == 'awaiting_code_to_query':
                code_to_query = msg_text.strip()
                result_text = i18n._("❌ Code not found in the database.")

                db = SessionLocal()
                try:
                    code_obj = db.query(models.DurationCode).filter(models.DurationCode.code == code_to_query).first()
                    if not code_obj:
                        code_obj = db.query(models.InvitationCode).filter(models.InvitationCode.code == code_to_query).first()
                    
                    if code_obj:
                        result_text = _format_code_details(code_obj)
                finally:
                    db.close()
                
                buttons = [
                    [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_querycodemain_{user_id}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                ]
                notification_manager.edit_message(chat_id, original_message_id, result_text, inline_buttons=buttons)
                user_context.pop(chat_id, None)
                return
           
            if state == 'awaiting_custom_points':
                custom_key = ctx.get('custom_key')
                menu_key_to_refresh = ctx.get('menu_key')
                if not msg_text.isdigit() or int(msg_text) <= 0:
                    error_text = i18n._("❌ Invalid input, please re-enter.\nPlease enter an integer greater than 0.")
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'n_{menu_key_to_refresh}_{user_id}'}],
                        [{'text': i18n._('Cancel'), 'callback_data': f'm_cancel_state_{user_id}'}]
                    ]
                    notification_manager.edit_message(chat_id, original_message_id, error_text, inline_buttons=buttons)
                    return
                points_value = int(msg_text)
                target_node = next((node for key, node in SETTINGS_MENU_STRUCTURE.items() if node.get('custom_value_key') == custom_key), None)
                if target_node:
                    config.set_setting(target_node['custom_value_path'], points_value)
                    config.set_setting(target_node['config_path'], points_value)
                    config.save_config()
                    user_context.pop(chat_id, None)
                    send_settings_menu(chat_id, user_id, message_id=original_message_id, menu_key=menu_key_to_refresh)
                else:
                    user_context.pop(chat_id, None)
                    notification_manager.edit_message(chat_id, original_message_id, "Error: Could not find target setting node.")
                return
            
            if state:
                user_context.pop(chat_id, None)

                if state == 'awaiting_botuser_query':
                    query = msg_text.strip()
                    target_user = None
                    db = SessionLocal()
                    try:
                        if query.isdigit():
                            target_user = db.query(models.User).filter(models.User.telegram_user_id == int(query)).first()
                        else:
                            target_user = db.query(models.User).filter(models.User.emby_user_id == query).first()
                    finally:
                        db.close()

                    user_context.pop(chat_id, None)
                    if target_user:
                        send_bot_user_details_menu(chat_id, user_id, original_message_id, target_user.telegram_user_id)
                    else:
                        buttons = [
                            [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_botusermain_{user_id}'}],
                            [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                        ]
                        notification_manager.edit_message(chat_id, original_message_id, i18n._("❌ User not found based on your input."), inline_buttons=buttons)
                    return

                if state == 'awaiting_botuser_newpoints':
                    if not msg_text.isdigit():
                        target_tg_id = ctx['target_tg_id']
                        error_msg = i18n._("❌ Invalid format, only integers are allowed. Please return to the previous step and try again.")
                        buttons = [
                            [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_detail_{target_tg_id}_{user_id}'}],
                            [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                        ]
                        notification_manager.edit_message(chat_id, original_message_id, error_msg, inline_buttons=buttons)
                        return

                    target_tg_id = ctx['target_tg_id']
                    new_points = int(msg_text)
                    db = SessionLocal()
                    try:
                        current_user = db.query(models.User).filter(models.User.telegram_user_id == target_tg_id).first()
                        current_points = current_user.points if current_user else 0
                    finally:
                        db.close()
                    prompt = (f"{i18n._('Current points')}: {current_points}\n"
                              f"{i18n._('New points')}: {new_points}\n\n"
                              f"❓ {i18n._('Are you sure you want to make this change?')}")
                    buttons = [
                        [{'text': i18n._('✅ Confirm'), 'callback_data': f'm_botuser_pointsconfirm_{target_tg_id}_{new_points}_{user_id}'}],
                        [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_detail_{target_tg_id}_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    user_context.pop(chat_id, None)
                    notification_manager.edit_message(chat_id, original_message_id, prompt, inline_buttons=buttons)
                    return

                if state == 'awaiting_botuser_giftdc':
                    parts = msg_text.split()
                    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit() or int(parts[0]) <= 0 or int(parts[1]) <= 0:
                        target_tg_id = ctx['target_tg_id']
                        error_msg = i18n._("❌ Invalid format, requires quantity and days (e.g., 5 90). Please return to the previous step and try again.")
                        buttons = [
                            [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_codes_{target_tg_id}_{user_id}'}],
                            [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                        ]
                        notification_manager.edit_message(chat_id, original_message_id, error_msg, inline_buttons=buttons)
                        return

                    count, days = int(parts[0]), int(parts[1])
                    target_tg_id = ctx['target_tg_id']
                    new_codes = []
                    db = SessionLocal()
                    try:
                        for _ in range(count):
                            while True:
                                code_val = str(uuid.uuid4()).upper().replace('-', '')[:16]
                                code_val = '-'.join(code_val[i:i+4] for i in range(0, len(code_val), 4))
                                if not db.query(models.DurationCode).filter(models.DurationCode.code == code_val).first():
                                    new_codes.append(code_val)
                                    break
                        for code in new_codes:
                            db.add(models.DurationCode(code=code, owner_telegram_id=target_tg_id, duration_days=days))
                        db.commit()
                        codes_str = "\n".join([f"<code>{c}</code>" for c in new_codes])
                        msg = i18n._("✅ Successfully generated and gifted {count} duration codes for {days} days to user {tg_id}:\n\n{codes}").format(count=count, days=days, tg_id=target_tg_id, codes=codes_str)
                    finally:
                        db.close()
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_codes_{target_tg_id}_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    user_context.pop(chat_id, None)
                    notification_manager.edit_message(chat_id, original_message_id, msg, inline_buttons=buttons)
                    return

                if state == 'awaiting_botuser_giftic':
                    if not msg_text.isdigit() or int(msg_text) <= 0:
                        target_tg_id = ctx['target_tg_id']
                        error_msg = i18n._("❌ Invalid format, only positive integers are allowed. Please return to the previous step and try again.")
                        buttons = [
                            [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_codes_{target_tg_id}_{user_id}'}],
                            [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                        ]
                        notification_manager.edit_message(chat_id, original_message_id, error_msg, inline_buttons=buttons)
                        return

                    count = int(msg_text)
                    target_tg_id = ctx['target_tg_id']
                    new_codes = []
                    db = SessionLocal()
                    try:
                        for _ in range(count):
                            while True:
                                code_val = str(uuid.uuid4()).upper().replace('-', '')[:16]
                                code_val = '-'.join(code_val[i:i+4] for i in range(0, len(code_val), 4))
                                if not db.query(models.InvitationCode).filter(models.InvitationCode.code == code_val).first():
                                    new_codes.append(code_val)
                                    break
                        for code in new_codes:
                            db.add(models.InvitationCode(code=code, owner_telegram_id=target_tg_id))
                        db.commit()
                        codes_str = "\n".join([f"<code>{c}</code>" for c in new_codes])
                        msg = i18n._("✅ Successfully generated and gifted {count} invitation codes to user {tg_id}:\n\n{codes}").format(count=count, tg_id=target_tg_id, codes=codes_str)
                    finally:
                        db.close()
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_codes_{target_tg_id}_{user_id}'}],
                        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
                    ]
                    user_context.pop(chat_id, None)
                    notification_manager.edit_message(chat_id, original_message_id, msg, inline_buttons=buttons)
                    return

                if state == 'awaiting_manage_query':
                    if original_message_id:
                        delete_telegram_message(chat_id, original_message_id)
                    _send_search_and_format(msg_text, chat_id, user_id, is_group_chat, mention, is_manage_mode=True)
                    return
                
                if state == 'awaiting_new_show_info':
                    parts = msg_text.split()
                    if len(parts) < 3 or not parts[-2].isdigit() or len(parts[-2]) != 4:
                        error_text = i18n._("❌ Incorrect input format. Please ensure it includes a name, a four-digit year, and a type, separated by spaces.")
                        buttons = [[{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]]
                        notification_manager.safe_edit_or_send(chat_id, original_message_id, helpers.escape_html(error_text), buttons=buttons, delete_after=60)
                        return
                    name, year, media_type_folder = " ".join(parts[:-2]), parts[-2], parts[-1]
                    feedback_msgid = "ℹ️ Searching for \"{name} ({year})\" in the \"{folder}\" directory of the cloud drive. Results will be shown shortly."
                    feedback_text_raw = i18n._(feedback_msgid).format(name=name, year=year, folder=media_type_folder)
                    notification_manager.send_deletable_notification(text=helpers.escape_html(feedback_text_raw), chat_id=chat_id, delay_seconds=20)                    
                    media_cloud_path = get_setting('settings.media_cloud_path')
                    media_base_path = get_setting('settings.media_base_path')
                    if not media_cloud_path or not media_base_path:
                        notification_manager.safe_edit_or_send(chat_id, original_message_id, helpers.escape_html(i18n._("❌ Configuration Error: `media_cloud_path` or `media_base_path` is not set.")), delete_after=60)
                        return
                    source_category_dir = os.path.join(media_cloud_path, media_type_folder)
                    if not os.path.isdir(source_category_dir):
                        notification_manager.safe_edit_or_send(chat_id, original_message_id, i18n._("❌ Cloud category directory not found: <code>{folder}</code>").format(folder=helpers.escape_html(media_type_folder)), delete_after=60)
                        return
                    best_match_dir = next((d for d in os.listdir(source_category_dir) if name in d and year in d), None)
                    if not best_match_dir:
                        notification_manager.safe_edit_or_send(chat_id, original_message_id, helpers.escape_html(i18n._("❌ No matching program directory found for “{name} ({year})” under the `{folder}` category.").format(folder=media_type_folder, name=name, year=year)), delete_after=60)
                        return
                    full_cloud_path = os.path.join(source_category_dir, best_match_dir)
                    nfo_file = os.path.join(full_cloud_path, 'tvshow.nfo') if os.path.isfile(os.path.join(full_cloud_path, 'tvshow.nfo')) else helpers.find_nfo_file_in_dir(full_cloud_path)
                    if not nfo_file:
                        error_text = i18n._("❌ No .nfo file found in the directory <code>/{path}</code>.").format(path=helpers.escape_html(os.path.join(media_type_folder, best_match_dir)))
                        buttons = [[{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_filesmain_{user_id}'}],[{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]]
                        notification_manager.safe_edit_or_send(chat_id, original_message_id, error_text, buttons=buttons, delete_after=120)
                        return
                    tmdb_id = helpers.parse_tmdbid_from_nfo(nfo_file)
                    if not tmdb_id:
                        error_text = i18n._("❌ Could not parse a valid TMDB ID from the file <code>{filename}</code>.").format(filename=helpers.escape_html(os.path.basename(nfo_file)))
                        buttons = [[{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_filesmain_{user_id}'}],[{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]]
                        notification_manager.safe_edit_or_send(chat_id, original_message_id, error_text, buttons=buttons, delete_after=120)
                        return
                    is_tv_show_nfo = 'tvshow' in os.path.basename(nfo_file).lower()
                    preferred_media_type = 'tv' if is_tv_show_nfo else 'movie'
                    tmdb_details = tmdb_api.get_tmdb_details_by_id(tmdb_id, preferred_type=preferred_media_type)
                    if not tmdb_details:
                        error_text = i18n._("❌ Failed to query information using TMDB ID <code>{id}</code>.").format(id=tmdb_id)
                        buttons = [[{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_filesmain_{user_id}'}],[{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]]
                        notification_manager.safe_edit_or_send(chat_id, original_message_id, helpers.escape_html(error_text), buttons=buttons, delete_after=120)
                        return
                    title = tmdb_details.get('title') or tmdb_details.get('name')
                    determined_media_type = tmdb_details.get('media_type')
                    item_type_for_poster = "Series" if determined_media_type == 'tv' else "Movie"
                    mock_item_for_poster = {"ProviderIds": {"Tmdb": tmdb_id}, "Type": item_type_for_poster, "Name": title, "Id": f"TMDB ID: {tmdb_id}"}
                    poster_details = tmdb_api.get_media_details(mock_item_for_poster, user_id)
                    poster_url = poster_details.get('poster_url')
                    overview = tmdb_details.get('overview', i18n._('No overview available.'))
                    tmdb_link = f"https://www.themoviedb.org/{determined_media_type}/{tmdb_id}"
                    message_parts = [i18n._("ℹ️ Please confirm if you want to sync the following program from the cloud drive:"), i18n._("\nName: <a href=\"{link}\">{title}</a>").format(title=helpers.escape_html(f'{title} ({year})'), link=tmdb_link), i18n._("Category: {folder}").format(folder=helpers.escape_html(media_type_folder)), i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview[:150] + '...' if len(overview) > 150 else overview))]
                    message_text = "\n".join(message_parts)
                    update_uuid = str(uuid.uuid4())
                    target_path_for_emby = os.path.join(media_base_path, media_type_folder, best_match_dir)
                    UPDATE_PATH_CACHE[update_uuid] = target_path_for_emby
                    buttons = [[{'text': i18n._('⬇️ Confirm and Start Sync'), 'callback_data': f'm_doupdate_{update_uuid}_{user_id}'}], [{'text': i18n._('🔄 Restart Search'), 'callback_data': f'm_addfromcloud_dummy_{user_id}'}], [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]]
                    if original_message_id:
                        delete_telegram_message(chat_id, original_message_id)
                    notification_manager.send_deletable_notification(message_text, photo_url=poster_url, chat_id=chat_id, inline_buttons=buttons, delay_seconds=300, disable_preview=True)
                    return
            return

    if chat_id in user_search_state:
        if msg_text.startswith('/'):
            user_search_state.pop(chat_id, None)
            print(i18n._("ℹ️ User {id} entered a new command, canceling previous awaiting state.").format(id=user_id))
        else:
            if is_group_chat and not is_reply:
                return
            original_user_id = user_search_state.pop(chat_id, None)
            if original_user_id is None or original_user_id != user_id:
                return
            if not is_user_authorized(user_id):
                print(i18n._("🚫 Ignoring message from unauthorized user."))
                notification_manager.send_simple_message(i18n._("⚠️ You are not authorized to use this bot. Please contact an administrator."), chat_id)
                return
            _send_search_and_format(msg_text, chat_id, user_id, is_group_chat, mention, is_manage_mode=False)
            return

    if '@' in msg_text:
        msg_text = msg_text.split('@')[0]
    if not msg_text.startswith('/') and msg_text not in [i18n._('Check-in'), i18n._('Points')]:
        return

    command = msg_text.split()[0]
    
    if msg_text == i18n._('Check-in') or command == '/checkin':
        if not (is_super_admin(user_id) or telegram_driver.is_group_member(user_id)):
            notification_manager.send_simple_message(i18n._("ℹ️ This command is only available to administrators and group members."), chat_id)
            return

        if not get_setting('settings.checkin.enabled'):
            notification_manager.send_simple_message(i18n._("ℹ️ The check-in feature is currently disabled."), chat_id)
            return

        method_allowed = False
        if is_group_chat:
            if command == '/checkin' and get_setting('settings.checkin.methods.group_command'): method_allowed = True
            if msg_text == i18n._('Check-in') and get_setting('settings.checkin.methods.group_text'): method_allowed = True
        else:
            if command == '/checkin' and get_setting('settings.checkin.methods.private_command'): method_allowed = True
            if msg_text == i18n._('Check-in') and get_setting('settings.checkin.methods.private_text'): method_allowed = True

        if not method_allowed:
            notification_manager.send_simple_message(i18n._("ℹ️ This check-in method is currently disabled."), chat_id)
            return

        captcha_needed = False
        if is_group_chat and get_setting('settings.checkin.captcha.group_enabled'):
            captcha_needed = True
        elif not is_group_chat and get_setting('settings.checkin.captcha.private_enabled'):
            captcha_needed = True

        if captcha_needed:
            _start_captcha_flow(chat_id, user_id, action='checkin')
        else:
            username = message['from'].get('username')
            _perform_checkin(chat_id, user_id, None, username)
        return

    if msg_text == i18n._('Points') or command == '/points':
        if not (is_super_admin(user_id) or telegram_driver.is_group_member(user_id)):
            notification_manager.send_simple_message(i18n._("ℹ️ This command is only available to administrators and group members."), chat_id)
            return

        _start_captcha_flow(chat_id, user_id, action='points')
        return

    if command == '/start':
        welcome_msgid = "👋 Welcome to {bot_name}!\n\n"
        welcome_text_part1 = i18n._(welcome_msgid).format(bot_name=config.BOT_NAME)
        example1 = f"<code>/search {helpers.escape_html(i18n._('Kung Fu Panda').strip())}</code>\n"
        example2 = f"<code>/search {helpers.escape_html(i18n._('The Big Bang Theory 2007').strip())}</code>\n"
        example3 = f"<code>/search 299534</code>"
        welcome_text = (
            helpers.escape_html(welcome_text_part1) +
            helpers.escape_html(i18n._("This bot helps you interact with your Emby server.\n\n")) +
            "   <code>/bind</code>" + helpers.escape_html(i18n._(" - Bind your Telegram account to an Emby account to get started.\n\n")) +
            helpers.escape_html(i18n._("Here are the commands you can use after binding:\n\n")) +
            "🔍 <code>/search</code>" + helpers.escape_html(i18n._(" - Search for movies or series in your Emby library. Example: \n")) +
            "      " + example1 +
            "      " + example2 +
            "      " + example3 + helpers.escape_html(i18n._(" (search a TMDB ID)\n\n")) +
            "📊 <code>/status</code>" + helpers.escape_html(i18n._(" - Check the current playback status on the Emby server (server administrators only).\n\n")) +
            "⚙️ <code>/settings</code>" + helpers.escape_html(i18n._(" - Access an interactive menu to configure bot notifications and features (server administrators only).\n\n")) +
            "🗃️ <code>/manage</code>" + helpers.escape_html(i18n._(" - Manage Emby programs, media files, and users (server administrators only).\n\n"))
        )
        notification_manager.send_notification(text=welcome_text, chat_id=chat_id, disable_preview=True)
        return

    if command == '/bind':
        if not is_super_admin(user_id):
            db = SessionLocal()
            try:
                if db.query(models.BannedUser).filter(models.BannedUser.telegram_user_id == user_id).first():
                    notification_manager.send_simple_message(i18n._("❌ You are banned from using this feature."), chat_id)
                    return
            finally:
                db.close()

        if is_group_chat:
            notification_manager.send_simple_message(
                i18n._("ℹ️ For security reasons, please send the /bind command in a private chat with me."),
                chat_id
            )
            return
        _start_captcha_flow(chat_id, user_id, action='bind')
        return

    if not is_user_authorized(user_id):
        print(i18n._("🚫 Ignoring command from unauthorized user."))
        notification_manager.send_simple_message(i18n._("ℹ️ You are not authorized to use this command. Please use /bind to link your account."), chat_id)
        return
        
    if command in ['/status', '/settings', '/manage']:

        if not is_super_admin(user_id):
            notification_manager.send_simple_message(i18n._("ℹ️ Insufficient permissions: This command is for super administrators only."), chat_id)
            print(i18n._("🚫 Denied user {id} from executing admin command {command}").format(id=user_id, command=command))
            return

        if command == '/status':
            notification_manager.send_deletable_notification(
                text=f"{mention}{i18n._('📊 Getting current server status, please wait...')}", 
                chat_id=chat_id,
                delay_seconds=10
            )
            initial_message_id = None 

            run_task_in_background(
                chat_id, user_id, initial_message_id,
                task_func=lambda: get_active_sessions_info(user_id, mention),
                is_group_chat=is_group_chat,
                mention=mention
            )
            return

        if command == '/settings':
            send_settings_menu(chat_id, user_id)
            return

        if command == '/manage':
            search_term = msg_text[len('/manage'):].strip()
            if search_term:
                _send_search_and_format(search_term, chat_id, user_id, is_group_chat, mention, is_manage_mode=True)
            else:
                send_manage_main_menu(chat_id, user_id)
            return

    if command == '/search':
        search_term = msg_text[len('/search'):].strip()
        if search_term:
            _send_search_and_format(search_term, chat_id, user_id, is_group_chat, mention, is_manage_mode=False)
        else:
            user_search_state[chat_id] = user_id
            prompt_message = i18n._("✍️ Please provide the program name (year optional) or TMDB ID you want to search for.\nFor example: Kung Fu Panda or The Big Bang Theory 2007 or 299534")
            if is_group_chat:
                translated_prompt = i18n._('Please reply to this message with the program name (year optional) or TMDB ID you want to search for.\nFor example: Kung Fu Panda or The Big Bang Theory 2007 or 299534')
                prompt_message = f"{mention}{translated_prompt}"
            
            notification_manager.send_deletable_notification(
                helpers.escape_html(prompt_message), 
                chat_id=chat_id, 
                delay_seconds=60
            )

    if command == '/redeem':
        if not is_super_admin(user_id):
            db = SessionLocal()
            try:
                if db.query(models.BannedUser).filter(models.BannedUser.telegram_user_id == user_id).first():
                    notification_manager.send_simple_message(i18n._("❌ You are banned from using this feature."), chat_id)
                    return
            finally:
                db.close()
        _start_captcha_flow(chat_id, user_id, action='redeem')
        return
            
def send_manage_main_menu(chat_id, user_id, message_id=None):
    prompt_message = i18n._("Please select a management category:")
    buttons = [
        [{'text': i18n._('🎦 Emby Program Management'), 'callback_data': f'm_filesmain_{user_id}'}],
        [{'text': i18n._('👤 Emby User Management'), 'callback_data': f'm_usermain_{user_id}'}],
        [{'text': i18n._('🥷🏽 Bot User Management'), 'callback_data': f'm_botusermain_{user_id}'}],
        [{'text': i18n._('🔑 Redemption Code Management'), 'callback_data': f'm_codemain_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    if message_id:
        notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)
    else:
        notification_manager.send_deletable_notification(helpers.escape_html(prompt_message), chat_id=chat_id, inline_buttons=buttons, delay_seconds=180)

def send_code_management_menu(chat_id, user_id, message_id):
    prompt_message = i18n._("Please select a redemption code operation:")
    buttons = [
        [{'text': i18n._('⏳ Duration Code Management'), 'callback_data': f'm_durationcodemain_{user_id}'}],
        [{'text': i18n._('✉️ Invitation Code Management'), 'callback_data': f'm_invitecodemain_{user_id}'}],
        [{'text': i18n._('🔍 Query Redemption Code'), 'callback_data': f'm_querycodemain_{user_id}'}],
        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_backtomain_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)

def send_duration_code_menu(chat_id, user_id, message_id):
    prompt_message = i18n._("Please select a duration code operation:")
    buttons = [
        [{'text': i18n._('➕ Add Duration Codes'), 'callback_data': f'm_adddurationcode_{user_id}'}],
        [{'text': i18n._('🔧 Manage Existing Duration Codes'), 'callback_data': f'm_managedurationcodes_{user_id}'}],
        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_codemain_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)

def send_manage_duration_codes_menu(chat_id, user_id, message_id):
    prompt_message = i18n._("Please select an operation for existing duration codes:")
    buttons = [
        [{'text': i18n._('🚫 Disable Duration Code(s)'), 'callback_data': f'm_disabledurationcode_{user_id}'}],
        [{'text': i18n._('✅ Enable Duration Code(s)'), 'callback_data': f'm_enabledurationcode_{user_id}'}],
        [{'text': i18n._('🗑️ Clear All Unused Duration Codes'), 'callback_data': f'm_cleardurationcodes_{user_id}'}],
        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_durationcodemain_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)

def send_invite_code_menu(chat_id, user_id, message_id):
    prompt_message = i18n._("Please select an invitation code operation:")
    buttons = [
        [{'text': i18n._('➕ Add Invitation Codes'), 'callback_data': f'm_addinvitecode_{user_id}'}],
        [{'text': i18n._('🔧 Manage Existing Invitation Codes'), 'callback_data': f'm_manageinvitecodes_{user_id}'}],
        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_codemain_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)

def send_manage_invite_codes_menu(chat_id, user_id, message_id):
    prompt_message = i18n._("Please select an operation for existing invitation codes:")
    buttons = [
        [{'text': i18n._('🚫 Disable Invitation Code(s)'), 'callback_data': f'm_disableinvitecode_{user_id}'}],
        [{'text': i18n._('✅ Enable Invitation Code(s)'), 'callback_data': f'm_enableinvitecode_{user_id}'}],
        [{'text': i18n._('🗑️ Clear All Unused Invitation Codes'), 'callback_data': f'm_clearinvitecodes_{user_id}'}],
        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_invitecodemain_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)

def _format_code_details(code_obj):
    lines = []
    if isinstance(code_obj, models.DurationCode):
        lines.append(f"<b>{i18n._('Type: Duration Code')}</b>")
        lines.append(f"<b>{i18n._('Code')}:</b> <code>{helpers.escape_html(code_obj.code)}</code>")
        lines.append(f"<b>{i18n._('Duration')}:</b> {code_obj.duration_days} {i18n._('days')}")
    elif isinstance(code_obj, models.InvitationCode):
        lines.append(f"<b>{i18n._('Type: Invitation Code')}</b>")
        lines.append(f"<b>{i18n._('Code')}:</b> <code>{helpers.escape_html(code_obj.code)}</code>")

    lines.append(f"<b>{i18n._('Owner TG ID')}:</b> <code>{code_obj.owner_telegram_id}</code>")
    
    status = i18n._('Enabled') if code_obj.is_valid else i18n._('Disabled')
    lines.append(f"<b>{i18n._('Status')}:</b> {status}")

    usage = i18n._('Used') if code_obj.is_used else i18n._('Unused')
    lines.append(f"<b>{i18n._('Usage')}:</b> {usage}")

    if code_obj.is_used:
        lines.append(f"<b>{i18n._('Used by TG ID')}:</b> <code>{code_obj.used_by_telegram_id}</code>")
        lines.append(f"<b>{i18n._('Used by Emby ID')}:</b> <code>{code_obj.used_by_emby_id}</code>")
        used_time = code_obj.used_at.strftime('%Y-%m-%d %H:%M:%S') if code_obj.used_at else i18n._('Unknown')
        lines.append(f"<b>{i18n._('Used at')}:</b> {used_time}")
        
    return "\n".join(lines)


def send_query_code_menu(chat_id, user_id, message_id):
    prompt_message = i18n._("Please select a query operation:")
    buttons = [
        [{'text': i18n._('Query a specific code'), 'callback_data': f'm_queryspecificcode_{user_id}'}],
        [{'text': i18n._('List all unused duration codes'), 'callback_data': f'm_list_d_unused_{user_id}'}],
        [{'text': i18n._('List all used duration codes'), 'callback_data': f'm_list_d_used_{user_id}'}],
        [{'text': i18n._('List all unused invitation codes'), 'callback_data': f'm_list_i_unused_{user_id}'}],
        [{'text': i18n._('List all used invitation codes'), 'callback_data': f'm_list_i_used_{user_id}'}],
        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_codemain_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)

def post_update_result_to_telegram(*, chat_id: int, message_id: int, callback_message: dict, escaped_result: str, delete_after: int = 180):
    used_original = False
    is_photo_card = 'photo' in (callback_message or {})

    try:
        if len(escaped_result) < 900:
            if is_photo_card:
                url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/editMessageCaption"
                payload = {
                    'chat_id': chat_id,
                    'message_id': message_id,
                    'caption': escaped_result,
                    'parse_mode': 'HTML',
                    'reply_markup': json.dumps({'inline_keyboard': []})
                }
                resp = emby_api.make_request_with_retry('POST', url, json=payload, timeout=10)
                used_original = bool(resp)
            else:
                resp = notification_manager.edit_message(chat_id, message_id, escaped_result, inline_buttons=[])
                used_original = bool(resp)
        else:
            summary_message = i18n._("✅ Update successful!\nSee the new message below for detailed logs.")
            if is_photo_card:
                url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/editMessageCaption"
                payload = {
                    'chat_id': chat_id, 'message_id': message_id,
                    'caption': helpers.escape_html(summary_message),
                    'parse_mode': 'HTML',
                    'reply_markup': json.dumps({'inline_keyboard': []})
                }
                emby_api.make_request_with_retry('POST', url, json=payload, timeout=10)
            else:
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(summary_message), inline_buttons=[])
            
            notification_manager.send_deletable_notification(text=escaped_result, chat_id=chat_id, delay_seconds=delete_after)
            used_original = True
    except Exception as e:
        print(i18n._("⚠️ Exception occurred when posting update results, falling back to a separate text message: {error}").format(error=e))

    if not used_original:
        notification_manager.send_deletable_notification(text=escaped_result, chat_id=chat_id, delay_seconds=delete_after)

    if message_id:
        delete_user_message_later(chat_id, message_id, delete_after)

def handle_callback_query(callback_query: dict):
    global DELETION_TASK_CACHE, user_context, UPDATE_PATH_CACHE

    query_id = callback_query['id']
    data = callback_query.get('data')
    
    if not data:
        answer_callback_query(query_id)
        return

    print(i18n._("📞 Received callback query. ID: {id}, Data: {data}").format(id=query_id, data=data))
    
    message = callback_query.get('message', {}) or {}
    clicker_id = callback_query['from']['id']
    chat_id = message.get('chat', {}).get('id')
    message_id = message.get('message_id')

    if data.startswith('checkin_start_process_'):
        parts = data.split('_')
        initiator_id_str = parts[-1]
        
        if str(clicker_id) != initiator_id_str:
            answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
            return

        answer_callback_query(query_id)
        username = callback_query.get('from', {}).get('username')
        _perform_checkin(chat_id, clicker_id, message_id, username)
        return

    if data.startswith('captcha_click_'):
        ctx = user_context.get(chat_id)
        parts = data.split('_')
        initiator_id_str = parts[-1]

        if not ctx or str(ctx.get('initiator_id')) != initiator_id_str or str(clicker_id) != initiator_id_str:
            answer_callback_query(query_id, text=i18n._("ℹ️ This is not for you or has expired."), show_alert=True)
            return

        answer_callback_query(query_id)
        selected_emoji = parts[2]

        if len(ctx['selected_emojis']) >= len(ctx['correct_emojis']) or selected_emoji in ctx['selected_emojis']:
            return

        ctx['selected_emojis'].add(selected_emoji)

        all_buttons_flat = [btn for row in message['reply_markup']['inline_keyboard'] for btn in row]
        new_buttons_flat = []
        for button in all_buttons_flat:
            emoji = button['text']
            if ' ' in emoji:
                emoji = emoji.split(' ')[1]

            if emoji in ctx['selected_emojis']:
                if emoji in ctx['correct_emojis']:
                    new_buttons_flat.append({'text': f"✅ {emoji}", 'callback_data': button['callback_data']})
                else:
                    new_buttons_flat.append({'text': f"❌ {emoji}", 'callback_data': button['callback_data']})
            else:
                new_buttons_flat.append(button)

        new_button_grid = [new_buttons_flat[i:i+5] for i in range(0, len(new_buttons_flat), 5)]
        notification_manager.edit_message(chat_id, message_id, message['text'], inline_buttons=new_button_grid)

        if len(ctx['selected_emojis']) >= len(ctx['correct_emojis']):
            time.sleep(0.5)

            correct_selections = ctx['correct_emojis'].intersection(ctx['selected_emojis'])

            if len(correct_selections) == len(ctx['correct_emojis']):
                action = ctx.get('on_success_action')
                if action == 'checkin':
                    username = callback_query.get('from', {}).get('username')
                    _perform_checkin(chat_id, clicker_id, message_id, username)
                elif action == 'redeem':
                    _start_redeem_process(chat_id, clicker_id, message_id)
                elif action == 'bind':
                    _start_bind_process(chat_id, clicker_id, message_id)
                elif action == 'points':
                    send_points_menu(chat_id, clicker_id, message_id=message_id)
                else:
                    notification_manager.edit_message(chat_id, message_id, i18n._("❌ Internal error: Unknown success action."))

            else:
                action = ctx.get('on_success_action')
                failure_message = i18n._("❌ Verification failed. Please try again.")

                if action in ['bind', 'redeem'] and not is_super_admin(clicker_id):
                    failure_message = i18n._("❌ Verification failed. You have been banned from using this feature.")
                    db = SessionLocal()
                    try:
                        existing_ban = db.query(models.BannedUser).filter(models.BannedUser.telegram_user_id == clicker_id).first()
                        if not existing_ban:
                            banned_user = models.BannedUser(telegram_user_id=clicker_id, ban_reason="CAPTCHA Failed")
                            db.add(banned_user)
                            db.commit()
                            print(f"User {clicker_id} has been banned for failing CAPTCHA.")
                        else:
                            print(f"User {clicker_id} failed CAPTCHA but was already banned.")
                    finally:
                        db.close()

                user_context.pop(chat_id, None)
                notification_manager.edit_message(chat_id, message_id, failure_message)

        return

    if data.startswith('points_'):
        parts = data.split('_')
        command = parts[1]
        initiator_id_str = parts[-1]

        if str(clicker_id) != initiator_id_str:
            answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
            return

        answer_callback_query(query_id)

        if command == 'backtomenu':
            user_context.pop(chat_id, None)
            send_points_menu(chat_id, clicker_id, message_id=message_id)
            return

        if command == 'redeem':
            notification_manager.edit_message(chat_id, message_id, i18n._("ℹ️ This feature is not yet available."))
            return

        if command == 'cancel':
            user_context.pop(chat_id, None)
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Operation cancelled."))
            return

        if command == 'transfer':
            if not get_setting('settings.points.transfer_enabled'):
                notification_manager.edit_message(chat_id, message_id, i18n._("ℹ️ Points transfer is currently disabled by the administrator."))
                return
            if chat_id > 0:
                user_context[chat_id] = {'state': 'awaiting_transfer_target_id', 'initiator_id': clicker_id, 'message_id': message_id}
                prompt = i18n._("✍️ Please enter the recipient's numeric Telegram ID:")
                buttons = [[{'text': i18n._('Cancel'), 'callback_data': f'points_cancel_{clicker_id}'}]]
                notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            else:
                prompt = i18n._("ℹ️ For security, please initiate points transfer in a private chat with me.")
                notification_manager.edit_message(chat_id, message_id, prompt)
            return

        if command == 'confirm' and parts[2] == 'transfer':
            ctx = user_context.get(chat_id)
            if not ctx or ctx.get('state') != 'awaiting_transfer_confirmation':
                notification_manager.edit_message(chat_id, message_id, i18n._("❌ Action expired. Please start over."))
                return

            sender_id = clicker_id
            target_id = ctx['target_id']
            amount = ctx['amount']

            db = SessionLocal()
            try:
                sender = db.query(models.User).filter(models.User.telegram_user_id == sender_id).one()
                target = db.query(models.User).filter(models.User.telegram_user_id == target_id).one()

                if sender.points < amount:
                    notification_manager.edit_message(chat_id, message_id, i18n._("❌ Transfer failed: Insufficient points."))
                    return

                sender.points -= amount
                target.points += amount
                db.commit()

                final_text = i18n._("✅ Transfer successful! Your current balance: {points} points.").format(points=sender.points)
                notification_manager.edit_message(chat_id, message_id, final_text)

            except Exception as e:
                db.rollback()
                notification_manager.edit_message(chat_id, message_id, i18n._("❌ An error occurred during the transfer."))
                print(f"Error during points transfer: {e}")
            finally:
                db.close()
                user_context.pop(chat_id, None)
            return

        return

    setting_key_to_feature_map = {
        "setting_menu_system": "system_settings",
        "setting_system_telegram_mode": "telegram_mode",
        "setting_system_ip_geolocation": "ip_api_selection",
        "setting_system_language": "language_selection",
        "setting_menu_notification_management": "notification_management",
        "setting_notify_library_new": "notify_library_new",
        "setting_notify_library_deleted": "notify_library_deleted",
        "setting_notify_playback_start": "notify_playback_start",
        "setting_notify_playback_pause": "notify_playback_pause",
        "setting_notify_playback_stop": "notify_playback_stop",
        "setting_notify_advanced_menu": "notification_management_advanced",
        "setting_notify_advanced_login_success": "notify_user_login_success",
        "setting_notify_advanced_login_failure": "notify_user_login_failure",
        "setting_notify_advanced_user_management": "notify_user_creation_deletion",
        "setting_notify_advanced_user_updates": "notify_user_updates",
        "setting_notify_advanced_server_restart": "notify_server_restart_required",
        "setting_menu_autodelete": "auto_delete_settings",
        "setting_autodelete_new_library": "delete_new_library",
        "setting_autodelete_library_deleted": "delete_library_deleted",
        "setting_autodelete_playback_start": "delete_playback_start",
        "setting_autodelete_playback_pause": "delete_playback_pause",
        "setting_autodelete_playback_stop": "delete_playback_stop",
        "setting_autodelete_advanced_menu": "delete_advanced_notifications",
        "setting_menu_content": "content_settings",
        "setting_content_new_library_show_poster": "new_library_show_poster",
        "setting_content_new_library_show_media_detail": "new_library_show_media_detail",
        "setting_content_new_library_media_detail_has_tmdb_link": "new_library_media_detail_has_tmdb_link",
        "setting_content_new_library_show_overview": "new_library_show_overview",
        "setting_content_new_library_show_media_type": "new_library_show_media_type",
        "setting_content_new_library_show_progress_status": "new_library_show_progress_status",
        "setting_content_new_library_show_timestamp": "new_library_show_timestamp",
        "setting_content_new_library_show_view_on_server_button": "new_library_show_view_on_server_button",
        "setting_content_status_switch_mode": "status_content_mode",
        "setting_content_status_show_poster": "status_show_poster",
        "setting_content_status_show_player": "status_show_player",
        "setting_content_status_show_device": "status_show_device",
        "setting_content_status_show_location": "status_show_location",
        "setting_content_status_show_media_detail": "status_show_media_detail",
        "setting_content_status_media_detail_has_tmdb_link": "status_media_detail_has_tmdb_link",
        "setting_content_status_show_media_type": "status_show_media_type",
        "setting_content_status_show_overview": "status_show_overview",
        "setting_content_status_show_timestamp": "status_show_timestamp",
        "setting_content_status_show_view_on_server_button": "status_show_view_on_server_button",
        "setting_content_status_show_terminate_session_button": "status_show_terminate_session_button",
        "setting_content_status_show_send_message_button": "status_show_send_message_button",
        "setting_content_status_show_broadcast_button": "status_show_broadcast_button",
        "setting_content_status_show_terminate_all_button": "status_show_terminate_all_button",
        "setting_content_playback_show_poster": "playback_show_poster",
        "setting_content_playback_show_media_detail": "playback_show_media_detail",
        "setting_content_playback_media_detail_has_tmdb_link": "playback_media_detail_has_tmdb_link",
        "setting_content_playback_show_user": "playback_show_user",
        "setting_content_playback_show_player": "playback_show_player",
        "setting_content_playback_show_device": "playback_show_device",
        "setting_content_playback_show_location": "playback_show_location",
        "setting_content_playback_show_progress": "playback_show_progress",
        "setting_content_playback_show_media_type": "playback_show_media_type",
        "setting_content_playback_show_overview": "playback_show_overview",
        "setting_content_playback_show_timestamp": "playback_show_timestamp",
        "setting_content_playback_show_view_on_server_button": "playback_show_view_on_server_button",
        "setting_content_deleted_show_poster": "deleted_show_poster",
        "setting_content_deleted_show_media_detail": "deleted_show_media_detail",
        "setting_content_deleted_media_detail_has_tmdb_link": "deleted_media_detail_has_tmdb_link",
        "setting_content_deleted_show_overview": "deleted_show_overview",
        "setting_content_deleted_show_media_type": "deleted_show_media_type",
        "setting_content_deleted_show_timestamp": "deleted_show_timestamp",
        "setting_content_search_show_media_type_in_list": "search_show_media_type_in_list",
        "setting_content_search_movie_show_poster": "movie_show_poster",
        "setting_content_search_movie_title_has_tmdb_link": "movie_title_has_tmdb_link",
        "setting_content_search_movie_show_type": "movie_show_type",
        "setting_content_search_movie_show_category": "movie_show_category",
        "setting_content_search_movie_show_overview": "movie_show_overview",
        "setting_content_search_movie_show_added_time": "movie_show_added_time",
        "setting_content_search_movie_show_view_on_server_button": "movie_show_view_on_server_button",
        "setting_content_search_series_show_poster": "series_show_poster",
        "setting_content_search_series_title_has_tmdb_link": "series_title_has_tmdb_link",
        "setting_content_search_series_show_type": "series_show_type",
        "setting_content_search_series_show_category": "series_show_category",
        "setting_content_search_series_show_overview": "series_show_overview",
        "setting_content_search_series_show_view_on_server_button": "series_show_view_on_server_button",
        "setting_content_search_series_show_update_progress": "series_update_progress",
        "setting_content_search_series_show_season_specs": "series_season_specs",
        "setting_menu_media_spec": "media_spec_settings",
        "setting_media_spec_video_show_codec": "video_show_codec",
        "setting_media_spec_video_show_resolution": "video_show_resolution",
        "setting_media_spec_video_show_bitrate": "video_show_bitrate",
        "setting_media_spec_video_show_framerate": "video_framerate_settings",
        "setting_media_spec_video_show_range": "video_range_settings",
        "setting_media_spec_video_show_dolby_profile": "video_show_dolby_profile",
        "setting_media_spec_video_show_bit_depth": "video_bitdepth_settings",
        "setting_media_spec_audio_show_language": "audio_show_language",
        "setting_media_spec_audio_show_codec": "audio_show_codec",
        "setting_media_spec_audio_show_layout": "audio_show_layout",
        "setting_media_spec_subtitle_show_language": "subtitle_show_language",
        "setting_media_spec_subtitle_show_codec": "subtitle_show_codec"
    }

    if data.startswith(('n_', 't_', 'sel_', 'set_ipapi_', 'set_tgmode_')):
        if not is_super_admin(clicker_id):
            answer_callback_query(query_id, text=i18n._("ℹ️ Sorry, this action is only available to super administrators."), show_alert=True)
            return

        feature_key_to_check = None
        menu_key_to_feature_map = {v: k for k, v in setting_key_to_feature_map.items()}

        try:
            payload_with_id = data.split('_', 1)[1]
            payload, initiator_id_str = payload_with_id.rsplit('_', 1)

            if data.startswith('n_'):
                menu_key = payload
                feature_key_to_check = menu_key_to_feature_map.get(menu_key)
            
            elif data.startswith('t_'):
                item_index = int(payload)
                node_key = TOGGLE_INDEX_TO_KEY.get(item_index)
                if node_key:
                    parent_menu_key = TOGGLE_KEY_TO_INFO.get(node_key, {}).get('parent')
                    feature_key_to_check = menu_key_to_feature_map.get(node_key) or menu_key_to_feature_map.get(parent_menu_key)
            
            elif data.startswith('sel_'):
                menu_key = None
                for key in SELECTION_KEY_TO_INFO.keys():
                    if payload.startswith(key + '_'):
                        menu_key = key
                        break
                if menu_key:
                    feature_key_to_check = menu_key_to_feature_map.get(menu_key)

            elif data.startswith('set_ipapi_'):
                feature_key_to_check = 'setting_system_ip_geolocation'
            elif data.startswith('set_tgmode_'):
                feature_key_to_check = 'setting_system_telegram_mode'

        except (ValueError, IndexError):
            pass
        
    if data.startswith('sel_'):
        try:
            initiator_id_str = data.rsplit('_', 1)[-1]
            data_body = data[4:-len(initiator_id_str)-1]

            found_menu_key = None
            found_value = None
    
            for key in SELECTION_KEY_TO_INFO.keys():
                if data_body.startswith(key + '_'):
                    potential_value_str = data_body[len(key)+1:]
                    options = SETTINGS_MENU_STRUCTURE[key].get('options', {})
                    
                    if potential_value_str in options:
                        found_menu_key = key
                        found_value = potential_value_str
                        break

                    try:
                        potential_value_int = int(potential_value_str)
                        if potential_value_int in options:
                            found_menu_key = key
                            found_value = potential_value_int
                            break
                    except (ValueError, TypeError):
                        continue

            if not found_menu_key or found_value is None:
                raise ValueError(f"Could not parse menu_key and value from data: {data}")

            menu_key = found_menu_key
            value = found_value

            if str(clicker_id) != initiator_id_str:
                answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return

            node_info = SELECTION_KEY_TO_INFO.get(menu_key)
            if not node_info:
                raise KeyError(f"Menu key '{menu_key}' not found in SELECTION_KEY_TO_INFO map.")

            config_path = node_info['config_path']
            config.set_setting(config_path, value)
            config.save_config()

            status_icon = "✅" if value != 'none' else "❌"
            answer_callback_query(query_id, text=i18n._("Setting updated: {status}").format(status=status_icon))

            menu_to_refresh = menu_key
            if menu_to_refresh:
                send_settings_menu(chat_id, int(initiator_id_str), message_id, menu_key=menu_to_refresh)

        except (ValueError, KeyError) as e:
            answer_callback_query(query_id, text=f"❌ Callback parameter error: {e}", show_alert=True)
        return

    if data.startswith('set_ipapi_'):
        try:
            params_str = data[len('set_ipapi_'):]
            provider, initiator_id_str = params_str.split('_', 1)

            if str(clicker_id) != initiator_id_str:
                answer_callback_query(query_id, text=i18n._("This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return

            config.set_setting('settings.ip_api_provider', provider)
            config.save_config()
            
            provider_map = {
                'baidu': i18n._('Baidu API'), 'ip138': i18n._('IP138 API (Token required)'), 
                'pconline': i18n._('PCOnline API'), 'vore': i18n._('Vore API'), 'ipapi': i18n._('IP-API.com')
            }
            provider_name = provider_map.get(provider, provider)
            
            answer_callback_query(query_id, text=i18n._("API has been switched to: {name}").format(name=provider_name))
            send_settings_menu(chat_id, int(initiator_id_str), message_id, menu_key='ip_api_selection')
        except ValueError:
            answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
        return

    if data.startswith('set_tgmode_'):
        try:
            params_str = data[len('set_tgmode_'):]
            mode, initiator_id_str = params_str.split('_', 1)

            if str(clicker_id) != initiator_id_str:
                answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return

            current_mode = get_setting('settings.telegram_mode') or 'polling'
            if current_mode == mode:
                answer_callback_query(query_id, text=i18n._("ℹ️ No changes made."))
                return

            answer_callback_query(query_id)

            if mode == 'webhook' and not config.TELEGRAM_WEBHOOK_URL:
                error_text = i18n._("❌ Configuration Error: To use Webhook mode, you must set `webhook_url` in the `telegram` section of your config.yaml file.")
                buttons = [[{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'n_telegram_mode_{initiator_id_str}'}]]
                notification_manager.edit_message(chat_id, message_id, error_text, inline_buttons=buttons)
                return

            mode_name = i18n._('Webhook') if mode == 'webhook' else i18n._('Long Polling')
            prompt_text = i18n._("❓ Switching to {mode_name} mode requires a restart. Are you sure you want to continue?").format(mode_name=mode_name)
            buttons = [
                [{'text': i18n._('✅ Confirm & Restart'), 'callback_data': f'm_switchandrestart_{mode}_{initiator_id_str}'}],
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'n_telegram_mode_{initiator_id_str}'}, {'text': i18n._('☑️ Done'), 'callback_data': f'c_menu_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt_text, inline_buttons=buttons)

        except ValueError:
            answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
        return

    try:
        command, rest_of_data = data.split('_', 1)
    except (ValueError, AttributeError):
        answer_callback_query(query_id)
        return
        
    if command == 'pagem':
        cache_key, page_index_str = rest_of_data.rsplit('_', 1)
        page_index = int(page_index_str)

        if not (cached_data := cache.PAGINATED_MESSAGE_CACHE.get(cache_key)):
            answer_callback_query(query_id, text=i18n._("ℹ️ This message has expired."), show_alert=True)
            return
        
        if clicker_id != cached_data.get('initiator_id'):
            answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
            return

        pages = cached_data['pages']
        total_pages = len(pages)
        
        if not (0 <= page_index < total_pages):
            answer_callback_query(query_id, text=i18n._("❌ Error: Page index out of bounds."), show_alert=True)
            return

        new_text = pages[page_index]
        page_buttons = []

        current_page_num = page_index + 1     
        if page_index > 0:
            button_text = f"◀️ {i18n._('Previous Page')} ({current_page_num}/{total_pages})"
            page_buttons.append({'text': button_text, 'callback_data': f'pagem_{cache_key}_{page_index-1}'})
        if page_index < total_pages - 1:
            button_text = f"{i18n._('Next Page ▶️')} ({current_page_num}/{total_pages})"
            page_buttons.append({'text': button_text, 'callback_data': f'pagem_{cache_key}_{page_index+1}'})
        
        final_buttons = (cached_data.get('original_buttons') or []) + [page_buttons]

        if cached_data.get('photo_url'):
            telegram_driver.edit_telegram_message_caption(chat_id, message_id, new_text, inline_buttons=final_buttons)
        else:
            notification_manager.edit_message(chat_id, message_id, new_text, inline_buttons=final_buttons)
        
        answer_callback_query(query_id)
        return

    if data.startswith('set_lang_'):
        try:
            params_str = data[len('set_lang_'):]
            lang_code, initiator_id_str = params_str.rsplit('_', 1)

            if str(clicker_id) != initiator_id_str:
                answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return

            config.set_setting('settings.language', lang_code)
            config.save_config()

            i18n.set_language(lang_code)

            lang_info = config.SUPPORTED_LANGUAGES.get(lang_code, {})
            lang_name = lang_info.get('name', lang_code)

            answer_callback_query(query_id, text=i18n._("✅ Language switched to: {lang}!").format(lang=lang_name), show_alert=True)
            config.load_config()
            config.build_toggle_maps()
            send_settings_menu(chat_id, int(initiator_id_str), message_id, menu_key='language_selection')
        except ValueError:
            answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
        return
        
    if command == 'n':
        main_data, initiator_id_str = rest_of_data.rsplit('_', 1)
        answer_callback_query(query_id)
        send_settings_menu(chat_id, int(initiator_id_str), message_id, main_data)
        return

    if command == 't':
        item_index_str, initiator_id_str = rest_of_data.split('_', 1)
        item_index = int(item_index_str)
        node_key = TOGGLE_INDEX_TO_KEY.get(item_index)
        if not node_key:
            answer_callback_query(query_id, text=i18n._("⚠️ Invalid setting item."), show_alert=True)
            return
            
        node_info = TOGGLE_KEY_TO_INFO.get(node_key)
        config_path, menu_key_to_refresh = node_info['config_path'], node_info['parent']
        
        current_value = get_setting(config_path)
        new_value = not current_value
        config.set_setting(config_path, new_value)
        config.save_config()
        
        status_icon = "✅" if new_value else "❌"
        answer_callback_query(query_id, text=i18n._("Setting updated: {status}").format(status=status_icon))
        send_settings_menu(chat_id, int(initiator_id_str), message_id, menu_key=menu_key_to_refresh)
        return

    if command == 'close':
        initiator_id_str = rest_of_data.split('_')[-1]
        
        if str(clicker_id) != initiator_id_str and not is_super_admin(clicker_id):
            answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
            return

        answer_callback_query(query_id)
        delete_telegram_message(chat_id, message_id)
        return

    if command == 'c' and rest_of_data == f"menu_{clicker_id}":
        answer_callback_query(query_id)
        delete_telegram_message(chat_id, message_id)
        raw_text = i18n._("✅ Settings menu closed.")
        escaped_text = helpers.escape_html(raw_text)
        notification_manager.send_simple_message(escaped_text, chat_id=chat_id)
        return

    if command == 'bind':
        action, initiator_id_str = rest_of_data.split('_', 1)
        
        if str(clicker_id) != initiator_id_str:
            answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
            return

        initiator_id = int(initiator_id_str)
        
        if action == 'reenter':
            answer_callback_query(query_id)
            user_context[chat_id] = {'state': 'awaiting_emby_username', 'initiator_id': initiator_id}
            notification_manager.edit_message(
                chat_id, 
                message_id, 
                i18n._("✍️ Please enter your Emby username:")
            )
            return
        
        if action == 'unbind':
            db = SessionLocal()
            try:
                user_to_unbind = db.query(models.User).filter(models.User.telegram_user_id == initiator_id).first()
                if user_to_unbind:
                    user_to_unbind.emby_user_id = None
                    db.commit()
                    answer_callback_query(query_id, text=i18n._("✅ Unbind successful."), show_alert=True)
                    notification_manager.edit_message(chat_id, message_id, i18n._("✅ Your Emby account has been unbound."))
                else:
                    answer_callback_query(query_id, text=i18n._("⚠️ User not found."), show_alert=True)
            finally:
                db.close()
            return
            
        if action == 'rebind':
            db = SessionLocal()
            try:
                user_to_rebind = db.query(models.User).filter(models.User.telegram_user_id == initiator_id).first()
                if user_to_rebind:
                    user_to_rebind.emby_user_id = None
                    db.commit()
            finally:
                db.close()

            answer_callback_query(query_id)
            user_context[chat_id] = {'state': 'awaiting_emby_username', 'initiator_id': initiator_id}
            notification_manager.edit_message(chat_id, message_id, i18n._("✍️ Your previous binding has been removed. Please enter your new Emby username:"))
            return

    if command == 'mdc':
        try:
            _prefix, sub, shortid = data.split('_', 2)
        except ValueError:
            answer_callback_query(query_id, text=i18n._("ℹ️ Invalid action."), show_alert=True)
            return

        task = DELETION_TASK_CACHE.get(shortid)
        if not task:
            answer_callback_query(query_id, text=i18n._("ℹ️ This action has expired, please select again."), show_alert=True)
            return
        if task.get('initiator_id') != clicker_id:
            answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
            return

        answer_callback_query(query_id, text=i18n._("ℹ️ Executing deletion..."), show_alert=False)

        def _delete_emby_items_for_task(_task):
            t = _task['type']
            series_id = _task['series_id']
            if t == 'seasons':
                return emby_api.delete_emby_seasons(series_id, _task['seasons'])
            if t == 'episodes':
                return emby_api.delete_emby_episodes(series_id, _task['mapping'])
            return i18n._("❌ Unknown task type.")

        def _delete_files_for_task(_task, do_local, do_cloud):
            series_info = emby_api.get_series_item_basic(_task['series_id'])
            if not series_info or not series_info.get('Path'):
                return i18n._("❌ Failed to get program path, cannot delete files.")
            
            series_path = series_info['Path']
            if _task['type'] == 'seasons':
                return media_manager.delete_local_cloud_seasons(series_path, _task['seasons'], delete_local=do_local, delete_cloud=do_cloud)
            if _task['type'] == 'episodes':
                return media_manager.delete_local_cloud_episodes(series_path, _task['mapping'], delete_local=do_local, delete_cloud=do_cloud)
            return i18n._("❌ Unknown task type.")
        
        result_log = ""
        if sub == 'e':
            result_log = _delete_emby_items_for_task(task)
        elif sub == 'l':
            result_log = _delete_files_for_task(task, do_local=True, do_cloud=False)
        elif sub == 'c':
            result_log = _delete_files_for_task(task, do_local=False, do_cloud=True)
        elif sub == 'b':
            result_log = _delete_files_for_task(task, do_local=True, do_cloud=True)
        else:
            answer_callback_query(query_id, text=i18n._("⚠️ Invalid deletion method."), show_alert=True)
            return

        DELETION_TASK_CACHE.pop(shortid, None)
        result_text = helpers.escape_html(result_log or i18n._("✅ Operation complete."))
        
        post_update_result_to_telegram(
            chat_id=chat_id, message_id=message_id, callback_message=message,
            escaped_result=result_text, delete_after=120
        )
        return

    if command == 's':
        try:
            action, rest_params = rest_of_data.split('_', 1)
            initiator_id_str = rest_params.rsplit('_', 1)[-1]
            if not initiator_id_str.isdigit() or (not is_super_admin(clicker_id) and str(clicker_id) != initiator_id_str):
                answer_callback_query(query_id, text=i18n._("This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return
        except ValueError:
            answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
            return

        if action == 'page':
            try:
                search_id, page_str, initiator_id_str = rest_params.split('_')
                answer_callback_query(query_id)
                send_results_page(chat_id, search_id, int(initiator_id_str), int(page_str), message_id)
            except ValueError:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
            return
            
        elif action == 'detail':
            try:
                search_id, item_index_str, initiator_id_str = rest_params.split('_')
                answer_callback_query(query_id, text=i18n._("ℹ️ Getting detailed information..."))
                send_search_detail(chat_id, search_id, int(item_index_str), int(initiator_id_str), message_id)
            except ValueError:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
            return

        elif action == 'search':
            if rest_params.endswith(f'again_{clicker_id}'):
                answer_callback_query(query_id)
                delete_telegram_message(chat_id, message_id)
                
                user_search_state[chat_id] = clicker_id
                prompt_message = i18n._("✍️ Please provide the program name (year optional) or TMDB ID you want to search for.\nFor example: Kung Fu Panda or The Big Bang Theory 2007 or 299534")
                if chat_id < 0:
                    mention = ""
                    translated_prompt = i18n._('Please reply to this message with the program name (year optional) or TMDB ID you want to search for.\nFor example: Kung Fu Panda or The Big Bang Theory 2007 or 299534')
                    prompt_message = f"{mention}{translated_prompt}"
                
                notification_manager.send_deletable_notification(
                    helpers.escape_html(prompt_message), 
                    chat_id=chat_id, 
                    delay_seconds=60
                )
                return

    try:
        command, rest_of_data = data.split('_', 1)
    except (ValueError, AttributeError):
        answer_callback_query(query_id)
        return

    if command == 'm':

        if rest_of_data.startswith('botuser'):
            try:
                initiator_id_str = rest_of_data.rsplit('_', 1)[-1]
                if str(clicker_id) != initiator_id_str:
                    answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                    return
                
                answer_callback_query(query_id)
                
                if rest_of_data.startswith('botusermain_'):
                    send_bot_user_management_menu(chat_id, clicker_id, message_id)

                elif rest_of_data.startswith('botuserquery_'):
                    user_context[chat_id] = {'state': 'awaiting_botuser_query', 'initiator_id': clicker_id, 'message_id': message_id}
                    prompt = i18n._("✍️ Please enter the Telegram ID or Emby User ID of the user you want to manage:")
                    buttons = [[{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botusermain_{clicker_id}'}]]
                    notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)

                elif rest_of_data.startswith('botuserbanlist_'):
                    db = SessionLocal()
                    try:
                        banned_users = db.query(models.BannedUser).all()
        
                        nav_buttons = [
                            {'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botusermain_{clicker_id}'},
                            {'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{clicker_id}'}
                        ]

                        if not banned_users:
                            text = i18n._("✅ No users are currently banned.")
                            notification_manager.edit_message(chat_id, message_id, text, inline_buttons=[nav_buttons])
                        else:
                            count = len(banned_users)
                            title = i18n._("🚫 Banned User List (Total: {count}):\n\n").format(count=count)
                            user_list = "\n".join([f"<code>{u.telegram_user_id}</code>" for u in banned_users])
                            text = title + user_list
            
                            notification_manager.delete_message(chat_id, message_id)
                            telegram_driver.send_paginated_message(chat_id, clicker_id, text, buttons=[nav_buttons])
                    finally:
                        db.close()

                elif rest_of_data.startswith('botuser_detail_'):
                    target_tg_id = int(rest_of_data.split('_')[2])
                    send_bot_user_details_menu(chat_id, clicker_id, message_id, target_tg_id)

                elif rest_of_data.startswith('botuser_ban_') or rest_of_data.startswith('botuser_unban_'):
                    parts = rest_of_data.split('_')
                    sub_action = parts[1]
                    target_tg_id = int(parts[2])
                    db = SessionLocal()
                    try:
                        if sub_action == 'ban':
                            if not db.query(models.BannedUser).filter(models.BannedUser.telegram_user_id == target_tg_id).first():
                                db.add(models.BannedUser(telegram_user_id=target_tg_id, ban_reason="Banned by admin"))
                        else:
                            db.query(models.BannedUser).filter(models.BannedUser.telegram_user_id == target_tg_id).delete()
                        db.commit()
                    finally:
                        db.close()
                    send_bot_user_details_menu(chat_id, clicker_id, message_id, target_tg_id)

                elif rest_of_data.startswith('botuser_points_'):
                    target_tg_id = int(rest_of_data.split('_')[2])
                    user_context[chat_id] = {'state': 'awaiting_botuser_newpoints', 'initiator_id': clicker_id, 'message_id': message_id, 'target_tg_id': target_tg_id}
                    prompt = i18n._("✍️ Please enter the new points balance for this user:")
                    buttons = [[{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_detail_{target_tg_id}_{clicker_id}'}]]
                    notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)

                elif rest_of_data.startswith('botuser_pointsconfirm_'):
                    parts = rest_of_data.split('_')
                    target_tg_id = int(parts[2])
                    new_points = int(parts[3])
                    db = SessionLocal()
                    try:
                        user_to_update = db.query(models.User).filter(models.User.telegram_user_id == target_tg_id).first()
                        if user_to_update:
                            user_to_update.points = new_points
                            db.commit()
                    finally:
                        db.close()
                    send_bot_user_details_menu(chat_id, clicker_id, message_id, target_tg_id)

                elif rest_of_data.startswith('botuser_codes_'):
                    target_tg_id = int(rest_of_data.split('_')[2])
                    send_bot_user_code_menu(chat_id, clicker_id, message_id, target_tg_id)

                elif rest_of_data.startswith('botuser_viewcodes_'):
                    target_tg_id = int(rest_of_data.split('_')[2])
                    db = SessionLocal()
                    try:
                        d_codes = db.query(models.DurationCode).filter(models.DurationCode.owner_telegram_id == target_tg_id).all()
                        i_codes = db.query(models.InvitationCode).filter(models.InvitationCode.owner_telegram_id == target_tg_id).all()
                        all_codes = d_codes + i_codes

                        nav_buttons = [
                            {'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_codes_{target_tg_id}_{clicker_id}'},
                            {'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{clicker_id}'}
                        ]

                        if not all_codes:
                            text = i18n._("This user does not own any redemption codes.")
                            notification_manager.edit_message(chat_id, message_id, text, inline_buttons=[nav_buttons])
                        else:
                            details = "\n\n".join([_format_owned_code_details(c) for c in all_codes])
                            text = f"<b>{i18n._('Owned Redemption Codes')} ({len(all_codes)}):</b>\n\n{details}"
                            notification_manager.delete_message(chat_id, message_id)
                            telegram_driver.send_paginated_message(chat_id, clicker_id, text, buttons=[nav_buttons])
                    finally:
                        db.close()

                elif rest_of_data.startswith('botuser_giftdc_'):
                    target_tg_id = int(rest_of_data.split('_')[2])
                    user_context[chat_id] = {'state': 'awaiting_botuser_giftdc', 'initiator_id': clicker_id, 'message_id': message_id, 'target_tg_id': target_tg_id}
                    prompt = i18n._("✍️ Please enter the number of codes and duration in days (e.g., 5 90):")
                    buttons = [[{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_codes_{target_tg_id}_{clicker_id}'}]]
                    notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)

                elif rest_of_data.startswith('botuser_giftic_'):
                    target_tg_id = int(rest_of_data.split('_')[2])
                    user_context[chat_id] = {'state': 'awaiting_botuser_giftic', 'initiator_id': clicker_id, 'message_id': message_id, 'target_tg_id': target_tg_id}
                    prompt = i18n._("✍️ Please enter the number of invitation codes to generate:")
                    buttons = [[{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_botuser_codes_{target_tg_id}_{clicker_id}'}]]
                    notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)

            except Exception as e:
                traceback.print_exc()
                answer_callback_query(query_id, text=f"Error: {e}", show_alert=True)
            return

        if rest_of_data.startswith('cancel_state_'):
            initiator_id_str = rest_of_data.split('_')[-1]
            if str(clicker_id) != initiator_id_str:
                answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return
            
            answer_callback_query(query_id)
            user_context.pop(chat_id, None)
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Operation cancelled."))
            return

        if rest_of_data.startswith('restart_'):
            initiator_id_str = rest_of_data.split('_')[-1]
            if str(clicker_id) != initiator_id_str:
                answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return

            answer_callback_query(query_id)
            prompt_text = i18n._("❓ Are you sure you want to restart the bot?")
            buttons = [
                [{'text': i18n._('✅ Yes, restart now'), 'callback_data': f'm_restartconfirm_{initiator_id_str}'}],
                [{'text': i18n._('   Back to previous step'), 'callback_data': f'n_system_settings_{initiator_id_str}'}, {'text': i18n._('☑️ Done'), 'callback_data': f'c_menu_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt_text, inline_buttons=buttons)
            return

        if rest_of_data.startswith('restartconfirm_'):
            initiator_id_str = rest_of_data.split('_')[-1]
            if str(clicker_id) != initiator_id_str:
                answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return

            answer_callback_query(query_id, text=i18n._("🤖 Restarting..."), show_alert=False)
            notification_manager.edit_message(chat_id, message_id, i18n._("🤖 The bot is restarting, please wait a moment..."), inline_buttons=[])
            helpers.restart_bot()
            return

        if rest_of_data.startswith('custompoints_'):
            try:
                parts = rest_of_data.split('_')
                initiator_id_str = parts[-1]
                custom_key = '_'.join(parts[1:-1])

                if str(clicker_id) != initiator_id_str:
                    answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                    return

                target_node = None
                menu_key_to_refresh = None
                for key, node in SETTINGS_MENU_STRUCTURE.items():
                    if node.get('custom_value_key') == custom_key:
                        target_node = node
                        menu_key_to_refresh = key
                        break
                
                if target_node:
                    answer_callback_query(query_id)
                    prompt = i18n._("✍️ Please enter an integer greater than 0 for the custom points value.")
                    buttons = [
                        [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'n_{menu_key_to_refresh}_{initiator_id_str}'}],
                        [{'text': i18n._('☑️ Done'), 'callback_data': f'c_menu_{initiator_id_str}'}]
                    ]
                    user_context[chat_id] = {
                        'state': 'awaiting_custom_points', 
                        'initiator_id': clicker_id, 
                        'message_id': message_id,
                        'custom_key': custom_key,
                        'menu_key': menu_key_to_refresh
                    }
                    notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)

            except ValueError:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
            return

        if rest_of_data.startswith('switchandrestart_'):
            try:
                _, mode, initiator_id_str = rest_of_data.split('_')
                if str(clicker_id) != initiator_id_str:
                    answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                    return

                answer_callback_query(query_id, text=i18n._("ℹ️ Switching mode..."), show_alert=False)
                notification_manager.edit_message(chat_id, message_id, i18n._("ℹ️ Switching mode and preparing to restart..."), inline_buttons=[])

                success = False
                error_message = "Unknown error"
                if mode == 'webhook':
                    webhook_url_with_path = f"{config.TELEGRAM_WEBHOOK_URL}/telegram_webhook"
                    success, error_message = telegram_driver.set_telegram_webhook(webhook_url_with_path)
                else:
                    success, error_message = telegram_driver.remove_telegram_webhook()

                if success:
                    config.set_setting('settings.telegram_mode', mode)
                    config.save_config()
                    notification_manager.edit_message(chat_id, message_id, i18n._("✅ Mode switched successfully! Restarting now..."))
                    helpers.restart_bot()
                else:
                    error_text = (
                        i18n._("❌ Mode switch failed! The bot will not be restarted.\n\nReason: {error}\n\nSuggestion: Please check if your `webhook_url` in `config.yaml` is correct and accessible from the internet.\n\nAction: If you have just corrected the `webhook_url`, you can restart the bot now and then try switching again.").format(error=error_message)
                    )
                    buttons = [
                        [{'text': i18n._('🤖 Restart bot now'), 'callback_data': f'm_restartconfirm_{initiator_id_str}'}],
                        [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'n_telegram_mode_{initiator_id_str}'}, {'text': i18n._('☑️ Done'), 'callback_data': f'c_menu_{initiator_id_str}'}]
                    ]
                    notification_manager.edit_message(chat_id, message_id, error_text, inline_buttons=buttons)
                return
            except ValueError:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
                return

        parts = rest_of_data.split('_')
        action = parts[0]
        
        action_to_feature_map = {
            'filesmain': 'manage_programs',
            'usermain': 'manage_users',
            'searchshow': 'manage_programs_search',
            'scanlibrary': 'manage_programs_scan',
            'addfromcloud': 'manage_programs_sync_from_cloud',
            'scanitem': 'manage_programs_action_scan',
            'refresh': 'manage_programs_action_refresh',
            'delete': 'manage_programs_action_delete',
            'update': 'manage_programs_action_update_from_cloud',
            'usercreate': 'manage_users_create',
            'userrename': 'manage_users_rename',
            'userpass': 'manage_users_change_password',
            'userpolicy': 'manage_users_permissions',
            'userdelete': 'manage_users_delete'
        }
        
        if len(parts) > 1:
            if parts[0] in ['scanitem', 'scanlibrary', 'scanall', 'userdelete', 'deleteemby', 'deletelocal', 'deletecloud', 'deleteboth', 'refresh'] and parts[1] == 'confirm':
                action = f"{parts[0]}confirm"
            elif parts[0] in ['scanlibrary', 'scanall'] and parts[1] == 'execute':
                action = f"{parts[0]}execute"

        rest_params = rest_of_data[len(action)+1:] if rest_of_data.startswith(action + '_') else ''
        if not rest_params and len(parts) > 1 and action == parts[0]:
            rest_params = '_'.join(parts[1:])

        try:
            initiator_id_str = rest_params.rsplit('_', 1)[-1]
            if initiator_id_str.isdigit():
                if not is_super_admin(clicker_id) and str(clicker_id) != initiator_id_str:
                    answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                    return
        except (ValueError, IndexError):
            pass

        if action == 'filesmain':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt_message = i18n._("Please select a program and file management operation:")
            buttons = [
                [{'text': i18n._('🎦 Manage a single program'), 'callback_data': f'm_searchshow_dummy_{initiator_id_str}'}],
                [{'text': i18n._('🔎 Scan media library'), 'callback_data': f'm_scanlibrary_{initiator_id_str}'}],
                [{'text': i18n._('⬇️ Sync new program from cloud drive'), 'callback_data': f'm_addfromcloud_dummy_{initiator_id_str}'}],
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_backtomain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)
            return

        if action == 'backtomain':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            send_manage_main_menu(chat_id, int(initiator_id_str), message_id)
            return

        if action == 'codemain':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            send_code_management_menu(chat_id, int(initiator_id_str), message_id)
            return

        if action == 'durationcodemain':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            send_duration_code_menu(chat_id, int(initiator_id_str), message_id)
            return

        if action == 'adddurationcode':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt = i18n._("✍️ Please enter the <b>number of codes</b> to generate and the <b>duration in days</b> for each, separated by a space.\n\nFor example: <code>5 90</code> (generates 5 codes, each for 90 days)")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_durationcodemain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_new_duration_codes', 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action == 'managedurationcodes':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            send_manage_duration_codes_menu(chat_id, int(initiator_id_str), message_id)
            return

        if action in ['disabledurationcode', 'enabledurationcode']:
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            
            is_disable = action == 'disabledurationcode'
            verb = i18n._('disable') if is_disable else i18n._('enable')
            bulk_button_text = i18n._('🚫 Disable All Unused') if is_disable else i18n._('✅ Enable All Unused')
            bulk_callback = f'm_disablealldurationcodes_{initiator_id_str}' if is_disable else f'm_enablealldurationcodes_{initiator_id_str}'

            prompt = i18n._("✍️ Please enter the specific duration code you want to {verb}, or use the button below for bulk operation.").format(verb=verb)
            
            buttons = [
                [{'text': bulk_button_text, 'callback_data': bulk_callback}],
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_managedurationcodes_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]

            state = 'awaiting_duration_code_to_disable' if is_disable else 'awaiting_duration_code_to_enable'
            user_context[chat_id] = {'state': state, 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action in ['disablealldurationcodes', 'enablealldurationcodes']:
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            
            is_disable = action == 'disablealldurationcodes'
            verb = i18n._('disable') if is_disable else i18n._('enable')
            verb_past = i18n._('disabled') if is_disable else i18n._('enabled')

            prompt = i18n._("❓ Are you sure you want to {verb} <b>all unused</b> duration codes?").format(verb=verb)
            confirm_callback = f'm_confirm_{action}_{initiator_id_str}'

            buttons = [
                [{'text': f"⚠️ {i18n._('Yes, confirm')}", 'callback_data': confirm_callback}],
                [{'text': i18n._('Cancel'), 'callback_data': f'm_managedurationcodes_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action.startswith('confirm_disableall') or action.startswith('confirm_enableall'):
            initiator_id_str = rest_params
            answer_callback_query(query_id, text=i18n._("Processing..."))

            is_disable = action.startswith('confirm_disableall')
            new_is_valid_status = not is_disable
            verb_past = i18n._('disabled') if is_disable else i18n._('enabled')

            db = SessionLocal()
            try:
                updated_count = db.query(models.DurationCode).filter(models.DurationCode.is_used == False).update({'is_valid': new_is_valid_status})
                db.commit()
                
                success_msg = i18n._("✅ Operation complete. {count} unused duration codes have been {verb}.").format(count=updated_count, verb=verb_past)
                buttons = [
                    [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_managedurationcodes_{initiator_id_str}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
                ]
                notification_manager.edit_message(chat_id, message_id, success_msg, inline_buttons=buttons)

            except Exception as e:
                db.rollback()
                notification_manager.edit_message(chat_id, message_id, f"❌ An error occurred: {e}")
            finally:
                db.close()
            return

        if action == 'cleardurationcodes':
            initiator_id_str = rest_params
            answer_callback_query(query_id)

            prompt = i18n._("❓ <b>WARNING:</b> Are you sure you want to permanently delete <b>all unused</b> duration codes? This action cannot be undone.")
            confirm_callback = f'm_cleardurationcodesconfirm_{initiator_id_str}'

            buttons = [
                [{'text': f"‼️ {i18n._('Yes, delete all')}", 'callback_data': confirm_callback}],
                [{'text': i18n._('Cancel'), 'callback_data': f'm_managedurationcodes_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action == 'cleardurationcodesconfirm':
            initiator_id_str = rest_params
            answer_callback_query(query_id, text=i18n._("Processing..."))

            db = SessionLocal()
            try:
                deleted_count = db.query(models.DurationCode).filter(models.DurationCode.is_used == False).delete(synchronize_session=False)
                db.commit()
                
                success_msg = i18n._("✅ Operation complete. {count} unused duration codes have been deleted.").format(count=deleted_count)
                buttons = [
                    [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_managedurationcodes_{initiator_id_str}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
                ]
                notification_manager.edit_message(chat_id, message_id, success_msg, inline_buttons=buttons)

            except Exception as e:
                db.rollback()
                notification_manager.edit_message(chat_id, message_id, f"❌ An error occurred: {e}")
            finally:
                db.close()
            return

        if action == 'invitecodemain':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            send_invite_code_menu(chat_id, int(initiator_id_str), message_id)
            return

        if action == 'manageinvitecodes':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            send_manage_invite_codes_menu(chat_id, int(initiator_id_str), message_id)
            return

        if action == 'addinvitecode':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt = i18n._("✍️ Please enter the <b>number of invitation codes</b> to generate.\n\nFor example: <code>5</code>")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_invitecodemain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_new_invite_codes', 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action in ['disableinvitecode', 'enableinvitecode']:
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            
            is_disable = action == 'disableinvitecode'
            verb = i18n._('disable') if is_disable else i18n._('enable')
            bulk_button_text = i18n._('🚫 Disable All Unused') if is_disable else i18n._('✅ Enable All Unused')
            bulk_callback = f'm_disableallinvitecodes_{initiator_id_str}' if is_disable else f'm_enableallinvitecodes_{initiator_id_str}'

            prompt = i18n._("✍️ Please enter the specific invitation code you want to {verb}, or use the button below for bulk operation.").format(verb=verb)
            
            buttons = [
                [{'text': bulk_button_text, 'callback_data': bulk_callback}],
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_manageinvitecodes_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]

            state = 'awaiting_invite_code_to_disable' if is_disable else 'awaiting_invite_code_to_enable'
            user_context[chat_id] = {'state': state, 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action in ['disableallinvitecodes', 'enableallinvitecodes']:
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            
            is_disable = action == 'disableallinvitecodes'
            verb = i18n._('disable') if is_disable else i18n._('enable')

            prompt = i18n._("❓ Are you sure you want to {verb} <b>all unused</b> invitation codes?").format(verb=verb)
            confirm_callback = f'm_confirm_{action}_{initiator_id_str}'

            buttons = [
                [{'text': f"⚠️ {i18n._('Yes, confirm')}", 'callback_data': confirm_callback}],
                [{'text': i18n._('Cancel'), 'callback_data': f'm_manageinvitecodes_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action.startswith('confirm_disableallinvitecodes') or action.startswith('confirm_enableallinvitecodes'):
            initiator_id_str = rest_params
            answer_callback_query(query_id, text=i18n._("Processing..."))

            is_disable = action.startswith('confirm_disableallinvitecodes')
            new_is_valid_status = not is_disable
            verb_past = i18n._('disabled') if is_disable else i18n._('enabled')

            db = SessionLocal()
            try:
                updated_count = db.query(models.InvitationCode).filter(models.InvitationCode.is_used == False).update({'is_valid': new_is_valid_status})
                db.commit()
                
                success_msg = i18n._("✅ Operation complete. {count} unused invitation codes have been {verb}.").format(count=updated_count, verb=verb_past)
                buttons = [
                    [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_manageinvitecodes_{initiator_id_str}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
                ]
                notification_manager.edit_message(chat_id, message_id, success_msg, inline_buttons=buttons)

            except Exception as e:
                db.rollback()
                notification_manager.edit_message(chat_id, message_id, f"❌ An error occurred: {e}")
            finally:
                db.close()
            return

        if action == 'clearinvitecodes':
            initiator_id_str = rest_params
            answer_callback_query(query_id)

            prompt = i18n._("❓ <b>WARNING:</b> Are you sure you want to permanently delete <b>all unused</b> invitation codes? This action cannot be undone.")
            confirm_callback = f'm_clearinvitecodesconfirm_{initiator_id_str}'

            buttons = [
                [{'text': f"‼️ {i18n._('Yes, delete all')}", 'callback_data': confirm_callback}],
                [{'text': i18n._('Cancel'), 'callback_data': f'm_manageinvitecodes_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action == 'clearinvitecodesconfirm':
            initiator_id_str = rest_params
            answer_callback_query(query_id, text=i18n._("Processing..."))

            db = SessionLocal()
            try:
                deleted_count = db.query(models.InvitationCode).filter(models.InvitationCode.is_used == False).delete(synchronize_session=False)
                db.commit()
                
                success_msg = i18n._("✅ Operation complete. {count} unused invitation codes have been deleted.").format(count=deleted_count)
                buttons = [
                    [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_manageinvitecodes_{initiator_id_str}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
                ]
                notification_manager.edit_message(chat_id, message_id, success_msg, inline_buttons=buttons)

            except Exception as e:
                db.rollback()
                notification_manager.edit_message(chat_id, message_id, f"❌ An error occurred: {e}")
            finally:
                db.close()
            return

        if action == 'querycodemain':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            send_query_code_menu(chat_id, int(initiator_id_str), message_id)
            return

        if action == 'queryspecificcode':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt = i18n._("✍️ Please enter the redemption code you want to query:")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_querycodemain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_code_to_query', 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action == 'list':
            answer_callback_query(query_id, text=i18n._("Querying, please wait..."))
    
            try:
                code_type, usage_status, initiator_id_str = rest_params.split('_')
        
                if str(clicker_id) != initiator_id_str:
                    notification_manager.edit_message(chat_id, message_id, i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"))
                    return
            except ValueError:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
                return

            is_used = usage_status == 'used'
            db = SessionLocal()
            try:
                if code_type == 'd':
                    model = models.DurationCode
                    title = i18n._("Used Duration Codes") if is_used else i18n._("Unused Duration Codes")
                else:
                    model = models.InvitationCode
                    title = i18n._("Used Invitation Codes") if is_used else i18n._("Unused Invitation Codes")
        
                codes = db.query(model).filter(model.is_used == is_used).all()

                nav_buttons = [
                    {'text': i18n._('🔙 Back to query menu'), 'callback_data': f'm_querycodemain_{initiator_id_str}'},
                    {'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}
                ]

                if not codes:
                    result_text = f"<b>{title}:</b>\n\n{i18n._('No matching codes found.')}"
                    notification_manager.edit_message(chat_id, message_id, result_text, inline_buttons=[nav_buttons])
                else:
                    details_list = [_format_code_details(code) for code in codes]
                    result_text = f"<b>{title} ({len(details_list)}):</b>\n\n" + "\n\n".join(details_list)
            
                    notification_manager.delete_message(chat_id, message_id)
                    telegram_driver.send_paginated_message(chat_id, clicker_id, result_text, buttons=[nav_buttons])

            finally:
                db.close()
            return

        if action == 'usermain':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            buttons = [
                [{'text': i18n._('➕ Create User'), 'callback_data': f'm_usercreate_{initiator_id_str}'}],
                [{'text': i18n._('✏️ Rename User'), 'callback_data': f'm_userrename_{initiator_id_str}'}],
                [{'text': i18n._('🔑 Change Password'), 'callback_data': f'm_userpass_{initiator_id_str}'}],
                [{'text': i18n._('🛡️ Permission Management'), 'callback_data': f'm_userpolicy_{initiator_id_str}'}],
                [{'text': i18n._('🗑️ Delete User'), 'callback_data': f'm_userdelete_{initiator_id_str}'}],
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_backtomain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, i18n._("Please select a user management operation:"), inline_buttons=buttons)
            return

        if action == 'exit':
            answer_callback_query(query_id)
            delete_telegram_message(chat_id, message_id)
            notification_manager.send_simple_message(i18n._("✅ Management exited."), chat_id=chat_id, delay_seconds=15)
            return

        if action == 'usercreate':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            if not config.EMBY_TEMPLATE_USER_ID:
                error_msg = i18n._("❌ Operation failed: `template_user_id` is not configured in config.yaml.")
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(error_msg), inline_buttons=[])
                return
            prompt = i18n._("✍️ Please enter a <b>username</b> and an optional <b>initial password</b>, separated by a space.\n\n<i>Note:</i>\n<i>1. Neither the username nor the password may contain spaces.\n2. If only a username is entered, the password will be set to empty.</i>")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_usermain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_new_user_credentials', 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return
            
        if action == 'userrename':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt = i18n._("✍️ Please enter the user's <b>old username</b> and <b>new username</b>, separated by a space.\n\n<i>Note: Neither the old nor the new username may contain spaces.</i>")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_usermain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_rename_info', 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return
            
        if action == 'userpass':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt = i18n._("✍️ Please enter the <b>username</b> for the password change and the optional <b>new password</b>, separated by a space.\n\n<i>Note:</i>\n<i>1. Neither the username nor the password may contain spaces.\n2. If only a username is entered, the password will be set to empty.</i>")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_usermain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_password_change_info', 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action == 'userpolicy':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt = i18n._("✍️ Please enter the <b>exact</b> username of the Emby user whose permissions you wish to manage.")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_usermain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_user_for_policy', 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action == 'togglepolicy':
            try:
                session_key, short_key = rest_params.split('_')
            except (ValueError, IndexError):
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
                return

            session_context = POLICY_SESSIONS_CACHE.get(session_key)
            if not session_context or (time.time() - session_context.get("timestamp", 0) > 3600):
                answer_callback_query(query_id, text=i18n._("ℹ️ This session has expired. Please start over."), show_alert=True)
                return

            initiator_id = session_context['initiator_id']
            if clicker_id != initiator_id:
                answer_callback_query(query_id, text=i18n._("ℹ️ This interaction was initiated by another user, you cannot operate it!"), show_alert=True)
                return

            answer_callback_query(query_id)

            user_id_to_manage = session_context['user_id_to_manage']
            user_obj = {'Id': user_id_to_manage, 'Name': session_context['user_name_to_manage']}

            POLICY_KEY_MAP = _get_policy_key_map()
            if short_key not in POLICY_KEY_MAP:
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(i18n._("❌ Invalid permission key.")))
                return
            
            full_policy_key = POLICY_KEY_MAP[short_key][0]

            policy, error = emby_api.get_emby_user_policy(user_id_to_manage)
            if error:
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(f"❌ {i18n._('Failed to get permissions')}: {error}"))
                return
            
            current_value = policy.get(full_policy_key, False)
            policy[full_policy_key] = not current_value
            
            if emby_api.update_emby_user_policy(user_id_to_manage, policy):
                send_user_policy_menu(chat_id, message_id, user_obj, initiator_id, session_key=session_key)
            else:
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(i18n._("❌ Failed to update permissions. Please try again.")))
            return
        
        if action == 'userdelete':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt = i18n._("✍️ Please enter the <b>exact</b> username of the Emby user you wish to delete.")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_usermain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_user_to_delete', 'initiator_id': int(initiator_id_str), 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return
            
        if action == 'searchshow':
            initiator_id_str = rest_of_data.split('_')[-1]
            answer_callback_query(query_id)
            prompt_text = i18n._("✍️ Please enter the name of the program to manage (year optional) or a TMDB ID.")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_filesmain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_manage_query', 'initiator_id': clicker_id, 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_text), inline_buttons=buttons)
            return

        if action == 'addfromcloud':
            initiator_id_str = rest_of_data.split('_')[-1]
            answer_callback_query(query_id)
            
            delete_telegram_message(chat_id, message_id)

            prompt_text = i18n._("✍️ Please enter the program name, year, and media type (e.g., Kung Fu Panda 2008 Movies):")
            buttons = [
                [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_filesmain_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            
            new_message = notification_manager.send_deletable_notification(prompt_text, chat_id=chat_id, inline_buttons=buttons, delay_seconds=180)
            
            if new_message and new_message.json().get('ok'):
                new_message_id = new_message.json().get('result', {}).get('message_id')
                user_context[chat_id] = {'state': 'awaiting_new_show_info', 'initiator_id': clicker_id, 'message_id': new_message_id}

            return

        if action == 'scanitem':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id)
            item_info = emby_api.get_series_item_basic(item_id)
            item_name = item_info.get('Name', f"ID: {item_id}") if item_info else f"ID: {item_id}"
            prompt_text = i18n._("❓ Are you sure you want to scan the folder containing <b>{item_name}</b>?\n\nThis action will look for new or changed files (e.g., new episodes).").format(item_name=helpers.escape_html(item_name))
            buttons = [
                [{'text': i18n._('⚠️ Yes, scan'), 'callback_data': f'm_scanitemconfirm_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_files_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt_text, inline_buttons=buttons)
            return

        if action == 'refresh':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id)
            item_info = emby_api.get_series_item_basic(item_id)
            item_name = item_info.get('Name', f"ID: {item_id}") if item_info else f"ID: {item_id}"
            prompt_text = i18n._("❓ Are you sure you want to refresh the metadata for <b>{item_name}</b>?\n\nThis action will rescan all related files and fetch information from the internet.").format(item_name=helpers.escape_html(item_name))
            buttons = [
                [{'text': i18n._('⚠️ Yes, refresh'), 'callback_data': f'm_refreshconfirm_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_files_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt_text, inline_buttons=buttons)
            return

        if action == 'delete':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id, text=i18n._("ℹ️ Getting program type..."))
            item_info = emby_api.get_series_item_basic(item_id)
            if not item_info:
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(i18n._("❌ Failed to get program type, please try again.")), inline_buttons=[])
                return

            item_type = item_info.get('Type')
            if item_type == 'Movie':
                buttons = [
                    [{'text': i18n._('⏏️ Delete program from Emby'), 'callback_data': f'm_deleteemby_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('🗑️ Delete local files'), 'callback_data': f'm_deletelocal_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('☁️ Delete cloud files'), 'callback_data': f'm_deletecloud_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('💥 Delete local and cloud files'), 'callback_data': f'm_deleteboth_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_files_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
                ]
                notification_manager.edit_message(chat_id, message_id, i18n._("ℹ️ Detected this program is a <b>Movie</b>. Please select a deletion method:"), inline_buttons=buttons)
            elif item_type == 'Series':
                buttons = [
                    [{'text': i18n._('❌ Delete entire series'), 'callback_data': f'm_deleteall_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('❌ Delete seasons'), 'callback_data': f'm_deleteseasons_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('❌ Delete episodes'), 'callback_data': f'm_deleteepisodes_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_files_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
                ]
                notification_manager.edit_message(chat_id, message_id, i18n._("ℹ️ Detected this program is a <b>Series</b>. Please select what you want to delete:"), inline_buttons=buttons)
            else:
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(i18n._("❌ Unsupported program type: {type}, cannot perform deletion.").format(type=item_type)), inline_buttons=[])
            return

        if action == 'deleteall':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id)
            buttons = [
                [{'text': i18n._('⏏️ Delete program from Emby'), 'callback_data': f'm_deleteemby_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('🗑️ Delete local files'), 'callback_data': f'm_deletelocal_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('☁️ Delete cloud files'), 'callback_data': f'm_deletecloud_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('💥 Delete local and cloud files'), 'callback_data': f'm_deleteboth_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_delete_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, helpers.escape_html(i18n._("Please select a deletion method:")), inline_buttons=buttons)
            return

        if action == 'delseasonconfirm' or action == 'delepisodeconfirm':
            answer_callback_query(query_id)
            
            try:
                del_type, shortid, initiator_id_str = rest_params.split('_', 2)
            except ValueError:
                return

            task = DELETION_TASK_CACHE.get(shortid)
            if not task:
                answer_callback_query(query_id, text=i18n._("ℹ️ This action has expired, please select again."), show_alert=True)
                return

            action_map = {
                'e': {'text': i18n._("from Emby"), 'confirm_cb': f'mdc_e_{shortid}'},
                'l': {'text': i18n._("local files"), 'confirm_cb': f'mdc_l_{shortid}'},
                'c': {'text': i18n._("cloud files"), 'confirm_cb': f'mdc_c_{shortid}'},
                'b': {'text': i18n._("local and cloud files"), 'confirm_cb': f'mdc_b_{shortid}'}
            }
            
            target_info = action_map.get(del_type)
            if not target_info: return

            if action == 'delseasonconfirm':
                preview = ", ".join([f"S{n:02d}" for n in task.get('seasons', [])])
                back_callback_data = f'm_deleteseasons_{task.get("series_id")}_{initiator_id_str}'
            else:
                mapping = task.get('mapping', {})
                groups = [f"S{int(s):02d}({','.join([f'E{int(e):02d}' for e in sorted(list(eps))])})" for s, eps in sorted(mapping.items())]
                preview = "; ".join(groups)
                back_callback_data = f'm_deleteepisodes_{task.get("series_id")}_{initiator_id_str}'

            prompt_text = (
                i18n._("❓ Are you sure you want to delete these items?\n<b>{preview}</b>\nTarget: <b>{target}</b>\nThis action cannot be undone!").format(
                    preview=helpers.escape_html(preview),
                    target=helpers.escape_html(target_info['text'])
                )
            )

            buttons = [
                [{'text': i18n._('⚠️ Yes, delete'), 'callback_data': target_info['confirm_cb']}],
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': back_callback_data}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            
            notification_manager.edit_message(chat_id, message_id, prompt_text, inline_buttons=buttons)
            return

        if action == 'deleteseasons':
            series_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id)
            prompt = (
                i18n._("✍️ Please enter the season numbers to delete, separated by spaces (S prefix or pure numbers are supported):\n") +
                i18n._("For example: S01 S03 S05 or 1 3 5")
            )
            buttons = [
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_delete_{series_id}_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_season_selection', 'initiator_id': int(initiator_id_str), 'series_id': series_id, 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return

        if action == 'deleteepisodes':
            series_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id)
            prompt = (
                i18n._("✍️ Please enter the season and episode numbers to delete. Ranges and season-less notations are supported; separate multiple entries with spaces/commas:\n") +
                i18n._("For example: S01E03 E11 S02E03-E06 E10")
            )
            buttons = [
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_delete_{series_id}_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            user_context[chat_id] = {'state': 'awaiting_episode_selection', 'initiator_id': int(initiator_id_str), 'series_id': series_id, 'message_id': message_id}
            notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
            return
                   
        if action == 'page':
            try:
                search_id, page_str, initiator_id_str = rest_params.split('_')
                answer_callback_query(query_id)
                send_results_page(chat_id, search_id, int(initiator_id_str), int(page_str), message_id)
            except ValueError:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
            return

        if action == 'detail':
            try:
                search_id, item_index_str, initiator_id_str = rest_params.split('_')
                answer_callback_query(query_id, text=i18n._("ℹ️ Getting detailed information..."))
                send_manage_detail(chat_id, search_id, int(item_index_str), int(initiator_id_str), message_id)
            except ValueError:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error."), show_alert=True)
            return

        if action == 'files':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id)
            delete_telegram_message(chat_id, message_id)
            buttons = [
                [{'text': i18n._('🗂️ Scan folder'), 'callback_data': f'm_scanitem_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('🔄 Refresh metadata'), 'callback_data': f'm_refresh_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('❌ Delete this program'), 'callback_data': f'm_delete_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('🔄 Update this program from cloud'), 'callback_data': f'm_update_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            notification_manager.send_deletable_notification(
                text=i18n._("Please select a file operation to perform on this program:"),
                chat_id=chat_id,
                inline_buttons=buttons,
                delay_seconds=120
            )
            return
        
        if action == 'doupdate':
            update_uuid, initiator_id_str = rest_params.rsplit('_', 1)
            feedback_msgid = "ℹ️ Syncing files from cloud... Details will be listed shortly."
            feedback_text_raw = i18n._(feedback_msgid)
            notification_manager.send_deletable_notification(
                text=helpers.escape_html(feedback_text_raw),
                chat_id=chat_id,
                delay_seconds=180
            )
            answer_callback_query(query_id)

            base_path = UPDATE_PATH_CACHE.pop(update_uuid, None)
            if not base_path:
                error_text = i18n._("❌ Action expired or invalid. Please try again.")
                post_update_result_to_telegram(
                    chat_id=chat_id, message_id=message_id, callback_message=message,
                    escaped_result=helpers.escape_html(error_text), delete_after=60
                )
                return
            
            result_message = media_manager.update_media_files(base_path)
            escaped_result = helpers.escape_html(result_message)

            post_update_result_to_telegram(
                chat_id=chat_id, message_id=message_id, callback_message=message,
                escaped_result=escaped_result, delete_after=120
            )
            return

        if action == 'userdeleteconfirm':
            user_id_to_delete, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id, i18n._("ℹ️ Executing deletion..."), show_alert=False)
            
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Deletion request sent. The process will run in the background."), inline_buttons=[])

            def task():
                success = emby_api.delete_emby_user_by_id(user_id_to_delete)
                if success:
                    db = SessionLocal()
                    try:
                        bound_user = db.query(models.User).filter(models.User.emby_user_id == user_id_to_delete).first()
                        if bound_user:
                            bound_user.emby_user_id = None
                            db.commit()
                    finally:
                        db.close()
                    result_message = i18n._("✅ User (ID: {user_id}) has been successfully deleted.").format(user_id=user_id_to_delete)
                else:
                    result_message = i18n._("❌ Failed to delete user (ID: {user_id}).").format(user_id=user_id_to_delete)
                return {'type': 'text', 'content': result_message}
            
            run_task_in_background(chat_id, clicker_id, message_id, task, chat_id < 0, "")
            return
            
        if action == 'scanitemconfirm':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id, i18n._("ℹ️ Sending scan request..."), show_alert=False)
            
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Scan request sent. The process will run in the background."), inline_buttons=[])

            def task():
                item_info = emby_api.get_series_item_basic(item_id)
                item_name = item_info.get('Name', f"ID: {item_id}") if item_info else f"ID: {item_id}"
                result_log = emby_api.scan_emby_item(item_id, item_name)
                return {'type': 'text', 'content': result_log}

            run_task_in_background(chat_id, clicker_id, message_id, task, chat_id < 0, "")
            return

        if action == 'refreshconfirm':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id, i18n._("ℹ️ Sending refresh request..."), show_alert=False)

            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Refresh request sent. The process will run in the background."), inline_buttons=[])
            
            def task():
                item_info = emby_api.get_series_item_basic(item_id)
                item_name = item_info.get('Name', f"ID: {item_id}") if item_info else f"ID: {item_id}"
                result_log = emby_api.refresh_emby_item(item_id, item_name)
                return {'type': 'text', 'content': result_log}

            run_task_in_background(chat_id, clicker_id, message_id, task, chat_id < 0, "")
            return

        if action == 'scanlibrary':
            initiator_id_str = rest_params
            answer_callback_query(query_id, text=i18n._("ℹ️ Getting library list..."))
            libraries, error = emby_api.get_emby_libraries()
            if error:
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(i18n._("❌ Failed to get media libraries: {error}").format(error=error)), inline_buttons=[])
                return

            buttons = []
            buttons.append([{'text': i18n._('💥 Scan all libraries'), 'callback_data': f"m_scanallconfirm_{initiator_id_str}"}])
            for lib in libraries:
                lib_name_b64 = base64.b64encode(lib['name'].encode('utf-8')).decode('utf-8')
                buttons.append([{'text': f"🗂️ {lib['name']}", 'callback_data': f"m_scanlibraryconfirm_{lib['id']}_{lib_name_b64}_{initiator_id_str}"}])
            
            buttons.append([{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_filesmain_{initiator_id_str}'}])
            buttons.append([{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}])
            notification_manager.edit_message(chat_id, message_id, helpers.escape_html(i18n._("Please select a library to scan:")), inline_buttons=buttons)
            return

        if action == 'scanallconfirm':
            initiator_id_str = rest_params
            answer_callback_query(query_id)
            prompt_text = i18n._("❓ Are you sure you want to scan <b>all</b> media libraries?\n\nThis operation can be resource-intensive and may take some time.")
            buttons = [
                [{'text': i18n._('⚠️ Yes, scan all'), 'callback_data': f'm_scanallexecute_{initiator_id_str}'}],
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_scanlibrary_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt_text, inline_buttons=buttons)
            return

        if action == 'scanlibraryconfirm':
            try:
                lib_id, lib_name_b64, initiator_id_str = rest_params.split('_', 2)
                lib_name = base64.b64decode(lib_name_b64).decode('utf-8')
            except Exception:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error"), show_alert=True)
                return
            answer_callback_query(query_id)
            prompt_text = i18n._("❓ Are you sure you want to scan the library <b>{library_name}</b>?\n\nThis may take some time.").format(library_name=helpers.escape_html(lib_name))
            buttons = [
                [{'text': i18n._('⚠️ Yes, scan'), 'callback_data': f"m_scanlibraryexecute_{lib_id}_{lib_name_b64}_{initiator_id_str}"}],
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_scanlibrary_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            notification_manager.edit_message(chat_id, message_id, prompt_text, inline_buttons=buttons)
            return
        
        if action == 'scanallexecute':
            initiator_id_str = rest_params
            answer_callback_query(query_id, i18n._("ℹ️ Sending global scan request..."), show_alert=False)
            
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Scan all libraries request sent. The process will run in the background."), inline_buttons=[])

            task_func = lambda: {'type': 'text', 'content': emby_api.scan_all_emby_libraries()}
            run_task_in_background(chat_id, clicker_id, message_id, task_func, chat_id < 0, "")
            return

        if action == 'scanlibraryexecute':
            try:
                lib_id, lib_name_b64, initiator_id_str = rest_params.split('_', 2)
                lib_name = base64.b64decode(lib_name_b64).decode('utf-8')
            except Exception:
                answer_callback_query(query_id, text=i18n._("❌ Callback parameter error"), show_alert=True)
                return
            answer_callback_query(query_id, i18n._("ℹ️ Sending scan request..."), show_alert=False)
            
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Scan request for library '{library_name}' sent. The process will run in the background.").format(library_name=lib_name), inline_buttons=[])

            task_func = lambda: {'type': 'text', 'content': emby_api.scan_emby_item(lib_id, lib_name)}
            run_task_in_background(chat_id, clicker_id, message_id, task_func, chat_id < 0, "")
            return
            
        if action == 'deleteembyconfirm':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id, i18n._("ℹ️ Executing Emby deletion..."), show_alert=False)
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Deletion request sent. The process will run in the background."), inline_buttons=[])

            def task():
                item_info = emby_api.get_series_item_basic(item_id)
                item_name = item_info.get('Name', f"ID: {item_id}") if item_info else f"ID: {item_id}"
                result_message = emby_api.delete_emby_item(item_id, item_name)
                return {'type': 'text', 'content': result_message}

            run_task_in_background(chat_id, clicker_id, message_id, task, chat_id < 0, "")
            return

        if action in ['deletelocalconfirm', 'deletecloudconfirm', 'deletebothconfirm']:
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id, i18n._("ℹ️ Deleting files..."), show_alert=False)
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ File deletion started in the background."), inline_buttons=[])

            def task():
                item_info = emby_api.get_series_item_basic(item_id)
                if not item_info or not item_info.get('Path'):
                    return {'type': 'text', 'content': i18n._("❌ Failed to get item path, cannot delete files.")}
                
                do_local = action in ['deletelocalconfirm', 'deletebothconfirm']
                do_cloud = action in ['deletecloudconfirm', 'deletebothconfirm']
                result_message = media_manager.delete_media_files(item_info['Path'], delete_local=do_local, delete_cloud=do_cloud)
                return {'type': 'text', 'content': result_message}

            run_task_in_background(chat_id, clicker_id, message_id, task, chat_id < 0, "")
            return

        if action == 'update':
            item_id, initiator_id_str = rest_params.split('_')
            answer_callback_query(query_id)
            
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ The update task has started executing in the background and you will be notified when it is complete."), inline_buttons=[])

            def task():
                item_info = emby_api.get_series_item_basic(item_id)
                if not item_info or not item_info.get('Path'):
                    return {'type': 'text', 'content': i18n._("❌ Failed to get item path, cannot update.")}

                item_path = item_info['Path']
                if os.path.splitext(item_path)[1]:
                    item_path = os.path.dirname(item_path)
                
                result_log = media_manager.update_media_files(item_path)
                return {'type': 'text', 'content': result_log}

            run_task_in_background(chat_id, clicker_id, message_id, task, chat_id < 0, "")
            return

        if action in ['deleteemby', 'deletelocal', 'deletecloud', 'deleteboth']:
            item_id, initiator_id_str = rest_params.split('_')
            
            if action in ['deletecloud', 'deleteboth'] and not get_setting('settings.media_cloud_path'):
                answer_callback_query(query_id)
                buttons = [
                    [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_delete_{item_id}_{initiator_id_str}'}],
                    [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
                ]
                notification_manager.edit_message(chat_id, message_id, helpers.escape_html(i18n._("❌ Operation failed: Cloud drive path (media_cloud_path) is not set in the configuration.")), inline_buttons=buttons)
                return

            item_info = emby_api.get_series_item_basic(item_id)
            if not item_info:
                answer_callback_query(query_id, i18n._("❌ Failed to get item information!"), show_alert=True)
                return
            
            item_name = item_info.get('Name', i18n._('⚠️ Unknown program'))
            year = item_info.get('ProductionYear')
            full_item_name = f"{item_name} ({year})" if item_name and year else item_name

            action_map = {
                'deleteemby': {'text': i18n._("<b>{name}</b> in Emby media library").format(name=full_item_name), 'confirm_cb': f'm_deleteembyconfirm_{item_id}_{initiator_id_str}'},
                'deletelocal': {'text': i18n._('local files'), 'confirm_cb': f'm_deletelocalconfirm_{item_id}_{initiator_id_str}'},
                'deletecloud': {'text': i18n._('cloud files'), 'confirm_cb': f'm_deletecloudconfirm_{item_id}_{initiator_id_str}'},
                'deleteboth': {'text': i18n._('local and cloud files'), 'confirm_cb': f'm_deletebothconfirm_{item_id}_{initiator_id_str}'}
            }
            prompt_target = action_map[action]['text']
            confirm_callback = action_map[action]['confirm_cb']

            prompt_text = i18n._("❓ Are you sure you want to delete `{target}`?\n\nThis action cannot be undone!").format(target=prompt_target)
            buttons = [
                [{'text': i18n._('⚠️ Yes, delete'), 'callback_data': confirm_callback}],
                [{'text': i18n._('🔙 Back to previous step'), 'callback_data': f'm_delete_{item_id}_{initiator_id_str}'}],
                [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id_str}'}]
            ]
            answer_callback_query(query_id)
            notification_manager.edit_message(chat_id, message_id, prompt_text, inline_buttons=buttons)
            return
            
        if action == 'exit':
            answer_callback_query(query_id)
            delete_telegram_message(chat_id, message_id)
            notification_manager.send_simple_message(i18n._("✅ Management exited."), chat_id=chat_id, delay_seconds=15)
            return

    if command == 'session':
        if rest_of_data.startswith('terminateall'):
            if rest_of_data == f"terminateall_{clicker_id}":
                answer_callback_query(query_id)
                confirmation_buttons = [[
                    {'text': i18n._('⚠️ Yes, stop all'), 'callback_data': f'session_terminateall_confirm_{clicker_id}'},
                    {'text': i18n._('Cancel'), 'callback_data': f'session_action_cancel_{clicker_id}'}
                ]]
                notification_manager.edit_message(chat_id, message_id, i18n._("❓ Are you sure you want to stop <b>all</b> playback sessions? This action cannot be undone."), inline_buttons=confirmation_buttons)
                return

            if rest_of_data == f"terminateall_confirm_{clicker_id}":
                answer_callback_query(query_id, text=i18n._("ℹ️ Stopping all sessions..."), show_alert=False)
                sessions_to_terminate = [s for s in emby_api.get_active_sessions() if s.get('NowPlayingItem')]
                count = 0
                if not sessions_to_terminate:
                    notification_manager.edit_message(chat_id, message_id, i18n._("✅ No active sessions to stop."), inline_buttons=[])
                else:
                    for session in sessions_to_terminate:
                        if session_id := session.get('Id'):
                            if emby_api.terminate_emby_session(session_id):
                                count += 1
                    notification_manager.edit_message(chat_id, message_id, i18n._("✅ Operation complete, {count} playback sessions were stopped.").format(count=count), inline_buttons=[])
                delete_user_message_later(chat_id, message_id, 60)
                return

        if rest_of_data == f"broadcast_{clicker_id}":
            answer_callback_query(query_id)
            user_context[chat_id] = {'state': 'awaiting_broadcast_message', 'initiator_id': clicker_id}
            prompt_text = i18n._("✍️ Please enter the message you want to <b>broadcast</b> to all users:")
            if chat_id < 0:
                prompt_text = i18n._("✍️ <b>Please reply to this message</b> with the content you want to <b>broadcast</b> to all users:")
            notification_manager.send_deletable_notification(prompt_text, chat_id=chat_id, delay_seconds=60)
            return

        if rest_of_data == f"action_cancel_{clicker_id}":
            answer_callback_query(query_id)
            original_text = message.get('text', i18n._('ℹ️ Operation cancelled'))
            notification_manager.edit_message(chat_id, message_id, f"<s>{original_text}</s>\n\n✅ {i18n._('ℹ️ Operation cancelled')}.", inline_buttons=[])
            delete_user_message_later(chat_id, message_id, 60)
            return

        try:
            action, session_id, initiator_id_str = rest_of_data.split('_')
            initiator_id = int(initiator_id_str)
        except ValueError:
            answer_callback_query(query_id)
            return

        if action == 'terminate':
            answer_callback_query(query_id)
            def task():
                if emby_api.terminate_emby_session(session_id):
                    notification_manager.send_simple_message(i18n._("✅ Playback stopped successfully."), chat_id)
                else:
                    notification_manager.send_simple_message(i18n._("❌ Failed to stop playback."), chat_id)
                return {'type': 'delete_only'}
            
            run_task_in_background(chat_id, clicker_id, None, task, chat_id < 0, "")
        
        elif action == 'message':
            answer_callback_query(query_id)
            user_context[chat_id] = {
                'state': 'awaiting_message_for_session', 
                'initiator_id': clicker_id,
                'session_id': session_id
            }
            prompt_text = i18n._("✍️ Please enter the message you want to send to this user:")
            if chat_id < 0:
                prompt_text = i18n._("✍️ <b>Please reply to this message</b> with the content you want to send to this user:")
            
            notification_manager.send_deletable_notification(prompt_text, chat_id=chat_id, delay_seconds=60)
        return

    if data.startswith('redeem_'):
        parts = data.split('_')
        action = parts[1]
        initiator_id_str = parts[-1]

        ctx = user_context.get(chat_id)
        if not ctx or str(ctx.get('initiator_id')) != initiator_id_str or str(clicker_id) != initiator_id_str:
            if not data.startswith('redeem_start_process_'):
                answer_callback_query(query_id, text=i18n._("ℹ️ This is not for you or has expired."), show_alert=True)
                return

        if action == 'start' and parts[2] == 'process':
            answer_callback_query(query_id)
            _start_redeem_process(chat_id, clicker_id, message_id)
            return
    
        if action == 'confirm':
            sub_action = parts[2]
            code = parts[3]
            
            if sub_action == 'duration':
                answer_callback_query(query_id, text=i18n._("Processing..."))
                db = SessionLocal()
                try:
                    user = db.query(models.User).filter(models.User.telegram_user_id == clicker_id).first()
                    d_code = db.query(models.DurationCode).filter(models.DurationCode.code == code).first()

                    if not user or not user.emby_user_id or not d_code or d_code.is_used or not d_code.is_valid:
                        notification_manager.edit_message(chat_id, message_id, i18n._("❌ Redemption failed: User or code is invalid or already used."))
                        return

                    from datetime import datetime, timedelta
                    start_date = user.subscription_expires_at if user.subscription_expires_at and user.subscription_expires_at > datetime.now() else datetime.now()
                    new_expiry_date = start_date + timedelta(days=d_code.duration_days)
                    user.subscription_expires_at = new_expiry_date

                    d_code.is_used = True
                    d_code.used_by_telegram_id = clicker_id
                    d_code.used_by_emby_id = user.emby_user_id
                    d_code.used_at = datetime.now()
                    
                    db.commit()

                    emby_api.update_user_access(user.emby_user_id, enabled=True)

                    final_text = i18n._("✅ Redemption successful!\n\nAdded: {days} days\nNew expiry date: {date}").format(
                        days=d_code.duration_days,
                        date=new_expiry_date.strftime('%Y-%m-%d %H:%M:%S')
                    )
                    buttons = [[{'text': i18n._('Done'), 'callback_data': f'redeem_done_{clicker_id}'}]]
                    notification_manager.edit_message(chat_id, message_id, final_text, inline_buttons=buttons)

                finally:
                    db.close()
                return

            if sub_action == 'invite':
                answer_callback_query(query_id)
                
                user_context[chat_id] = {
                    'state': 'awaiting_invite_credentials',
                    'initiator_id': clicker_id,
                    'message_id': message_id,
                    'invite_code': code
                }

                prompt = i18n._("✍️ Please enter a <b>username</b> and an optional <b>initial password</b> for your new Emby account, separated by a space.\n\n<i>Note: Usernames cannot contain spaces.</i>")
                buttons = [[{'text': i18n._('Cancel'), 'callback_data': f'redeem_cancel_{clicker_id}'}]]
                notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)
                return

        if action == 'done':
            answer_callback_query(query_id)
            user_context.pop(chat_id, None)
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Done!"))
            delete_user_message_later(chat_id, message_id, 10)
            return

        if action == 'cancel':
            answer_callback_query(query_id)
            user_context.pop(chat_id, None)
            notification_manager.edit_message(chat_id, message_id, i18n._("✅ Operation cancelled."))
            delete_user_message_later(chat_id, message_id, 10)
            return

    answer_callback_query(query_id)

def send_user_policy_menu(chat_id: int, message_id: int, user_obj: dict, initiator_id: int, session_key: str = None):
    POLICY_KEY_MAP = _get_policy_key_map()

    user_id = user_obj.get('Id')
    user_name = user_obj.get('Name')

    if not session_key:
        session_key = uuid.uuid4().hex[:8]
        POLICY_SESSIONS_CACHE[session_key] = {
            "user_id_to_manage": user_id,
            "user_name_to_manage": user_name,
            "initiator_id": initiator_id,
            "timestamp": time.time()
        }

    policy, error = emby_api.get_emby_user_policy(user_id)
    if error:
        notification_manager.edit_message(chat_id, message_id, helpers.escape_html(f"❌ {i18n._('Failed to get permissions')}: {error}"))
        return

    message_text = i18n._("🛡️ Managing permissions for user: <b>{user_name}</b>").format(user_name=helpers.escape_html(user_name))
    buttons = []

    for short_key, (full_key, label) in POLICY_KEY_MAP.items():
        is_enabled = policy.get(full_key, False)
        status_icon = "✅" if is_enabled else "❌"
        callback_data = f"m_togglepolicy_{session_key}_{short_key}"
        buttons.append([{'text': f"{status_icon} {label}", 'callback_data': callback_data}])
    
    buttons.append([{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_usermain_{initiator_id}'}])
    buttons.append([{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{initiator_id}'}])
    
    notification_manager.edit_message(chat_id, message_id, message_text, inline_buttons=buttons)

def send_bot_user_management_menu(chat_id, user_id, message_id):
    prompt_message = i18n._("Please select a Bot user management operation:")
    buttons = [
        [{'text': i18n._('🔍 Query and Manage User'), 'callback_data': f'm_botuserquery_{user_id}'}],
        [{'text': i18n._('🚫 Banned User List'), 'callback_data': f'm_botuserbanlist_{user_id}'}],
        [{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_backtomain_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    notification_manager.edit_message(chat_id, message_id, helpers.escape_html(prompt_message), inline_buttons=buttons)

def send_bot_user_details_menu(chat_id, user_id, message_id, target_tg_id: int):
    db = SessionLocal()
    try:
        target_user = db.query(models.User).filter(models.User.telegram_user_id == target_tg_id).first()
        if not target_user:
            notification_manager.edit_message(chat_id, message_id, i18n._("❌ User not found in the database."))
            return

        is_banned = db.query(models.BannedUser).filter(models.BannedUser.telegram_user_id == target_tg_id).first() is not None
        
        info_lines = []
        info_lines.append(f"<b>{i18n._('🥷🏽 User Details')}</b>")
        info_lines.append(f"<b>TG ID:</b> <code>{target_user.telegram_user_id}</code>")
        if target_user.username:
            info_lines.append(f"<b>TG Username:</b> @{target_user.username}")
        
        ban_status = i18n._('Banned') if is_banned else i18n._('Not Banned')
        info_lines.append(f"<b>{i18n._('Ban Status')}:</b> {ban_status}")

        if target_user.emby_user_id:
            info_lines.append(f"<b>Emby ID:</b> <code>{target_user.emby_user_id}</code>")
            emby_user_info, _ = emby_api.get_emby_user_by_id(target_user.emby_user_id)
            if emby_user_info and emby_user_info.get('Name'):
                info_lines.append(f"<b>{i18n._('Emby Username')}:</b> {helpers.escape_html(emby_user_info.get('Name'))}")
            if target_user.subscription_expires_at:
                expires_str = target_user.subscription_expires_at.strftime('%Y-%m-%d %H:%M:%S')
                info_lines.append(f"<b>{i18n._('Emby Expires At')}:</b> {expires_str}")
        else:
            info_lines.append(f"<b>Emby ID:</b> {i18n._('Not Bound')}")

        info_lines.append(f"<b>{i18n._('Points Balance')}:</b> {target_user.points or 0}")

        message_text = "\n".join(info_lines)
        
        buttons = []
        if is_banned:
            buttons.append([{'text': i18n._('✅ Unban User'), 'callback_data': f'm_botuser_unban_{target_tg_id}_{user_id}'}])
        else:
            buttons.append([{'text': i18n._('🚫 Ban User'), 'callback_data': f'm_botuser_ban_{target_tg_id}_{user_id}'}])
        
        buttons.append([
            {'text': i18n._('✏️ Modify Points'), 'callback_data': f'm_botuser_points_{target_tg_id}_{user_id}'},
            {'text': i18n._('🔑 Modify Redemption Codes'), 'callback_data': f'm_botuser_codes_{target_tg_id}_{user_id}'}
        ])
        buttons.append([{'text': i18n._('🔙 Back to previous menu'), 'callback_data': f'm_botusermain_{user_id}'}])
        buttons.append([{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}])

        notification_manager.edit_message(chat_id, message_id, message_text, inline_buttons=buttons)

    finally:
        db.close()

def send_bot_user_code_menu(chat_id, user_id, message_id, target_tg_id: int):
    """显示用户的兑换码管理菜单"""
    prompt = i18n._("Please select an operation for this user's redemption codes:")
    buttons = [
        [{'text': i18n._("View Owned Codes"), 'callback_data': f'm_botuser_viewcodes_{target_tg_id}_{user_id}'}],
        [{'text': i18n._("Gift Duration Code"), 'callback_data': f'm_botuser_giftdc_{target_tg_id}_{user_id}'}],
        [{'text': i18n._("Gift Invitation Code"), 'callback_data': f'm_botuser_giftic_{target_tg_id}_{user_id}'}],
        [{'text': i18n._('🔙 Back to user details'), 'callback_data': f'm_botuser_detail_{target_tg_id}_{user_id}'}],
        [{'text': i18n._('↩️ Exit Management'), 'callback_data': f'm_exit_dummy_{user_id}'}]
    ]
    notification_manager.edit_message(chat_id, message_id, prompt, inline_buttons=buttons)

def _format_owned_code_details(code_obj):
    """辅助函数，格式化拥有的兑换码信息"""
    lines = []
    code_type = i18n._("Duration") if isinstance(code_obj, models.DurationCode) else i18n._("Invitation")
    status = i18n._("Valid") if code_obj.is_valid else i18n._("Invalid")
    usage = i18n._("Used") if code_obj.is_used else i18n._("Unused")
    
    details = f"<b>{i18n._('Type')}:</b> {code_type} | <b>{i18n._('Status')}:</b> {status} | <b>{i18n._('Usage')}:</b> {usage}"
    if isinstance(code_obj, models.DurationCode):
        details += f" | <b>{i18n._('Duration')}:</b> {code_obj.duration_days} {i18n._('days')}"

    lines.append(f"<code>{helpers.escape_html(code_obj.code)}</code>")
    lines.append(details)
    if code_obj.is_used:
        lines.append(f"<b>{i18n._('Used by TG ID')}:</b> <code>{code_obj.used_by_telegram_id}</code>")

    return "\n".join(lines)
