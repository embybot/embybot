# -*- coding: utf-8 -*-

import json
import time
import re
import traceback
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote
from datetime import datetime, date

from .. import i18n
from ..core import config
from ..core.config import (
    get_setting, ADMIN_USER_ID, GROUP_ID, CHANNEL_ID, EMBY_REMOTE_URL, 
    PLAYBACK_DEBOUNCE_SECONDS, EMBY_USER_ID
)
from ..core.cache import recent_playback_notifications, SESSION_ENFORCEMENT_LOCK
from ..api import emby as emby_api
from ..api import tmdb as tmdb_api
from ..api import geo as geo_api
from ..notifications import manager as notification_manager
from ..utils import helpers
from ..utils import formatters
from ..logic import series_helper
from ..handlers import telegram_handler
from ..notifications import telegram_driver
from ..core.database import SessionLocal
from .. import models

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data_bytes = self.rfile.read(content_length)

            if self.path == '/telegram_webhook':
                self.send_response(200)
                self.end_headers()

                if not post_data_bytes:
                    return

                update_data = json.loads(post_data_bytes.decode('utf-8'))
                
                if 'message' in update_data:
                    message = update_data['message']
                    chat_id = message['chat']['id']
                    message_id = message['message_id']
                    
                    bot_id = None
                    if config.TELEGRAM_TOKEN:
                        try:
                            bot_id = int(config.TELEGRAM_TOKEN.split(':')[0])
                        except (ValueError, IndexError):
                            pass
                    
                    is_group_chat = chat_id < 0
                    should_delete = False
                    
                    if not is_group_chat:
                        should_delete = True
                    else:
                        msg_text = message.get('text', '')
                        if msg_text and msg_text.startswith('/'):
                            should_delete = True
                        elif 'reply_to_message' in message and bot_id:
                            if message['reply_to_message']['from']['id'] == bot_id:
                                should_delete = True
                    
                    if should_delete:
                        telegram_driver.delete_user_message_later(chat_id, message_id, delay_seconds=60)
                    
                    telegram_handler.handle_telegram_command(message)

                elif 'callback_query' in update_data:
                    telegram_handler.handle_callback_query(update_data['callback_query'])
                
                return

            print(i18n._("🔔 Webhook request received."))
            content_type = self.headers.get('Content-Type', '').lower()
            json_string = None

            if 'application/json' in content_type:
                json_string = post_data_bytes.decode('utf-8')
            elif 'application/x-www-form-urlencoded' in content_type:
                parsed_form = parse_qs(post_data_bytes.decode('utf-8'))
                json_string = parsed_form.get('data', [None])[0]
            else:
                print(i18n._("❌ Unsupported Content-Type: {type}").format(type=content_type))
                self.send_response(400)
                self.end_headers()
                return

            if not json_string:
                print(i18n._("❌ No data in webhook request."))
                self.send_response(400)
                self.end_headers()
                return
            
            self.send_response(200)
            self.end_headers()

            event_data = json.loads(unquote(json_string))
        #   print(i18n._("\n--- Emby Webhook Payload Start ---\n"))
        #   print(json.dumps(event_data, indent=2, ensure_ascii=False))
        #   print(i18n._("\n--- Emby Webhook Payload End ---\n"))

            event_type = event_data.get('Event')
            print(i18n._("ℹ️ Emby event detected: {event}").format(event=event_type))
            
            if event_type == "library.new":
                self._handle_library_new(event_data)
            elif event_type == "library.deleted":
                self._handle_library_deleted(event_data)
            elif event_type in ["playback.start", "playback.unpause", "playback.stop", "playback.pause"]:
                self._handle_playback_event(event_data)
            elif event_type and (event_type.startswith("user.") or event_type.startswith("system.")):
                 self._handle_system_event(event_data)
            else:
                print(i18n._("ℹ️ Unhandled event type: {event}").format(event=event_type))
                return

        except Exception as e:
            print(i18n._("❌ Error processing webhook: {error}").format(error=e))
            traceback.print_exc()
            try:
                if not hasattr(self, 'headers_sent') or not self.headers_sent:
                    self.send_response(500)
                    self.end_headers()
            except Exception as send_err:
                print(f"Failed to send error response to client: {send_err}")

    def _check_and_enforce_session_limit(self, user: dict):
        if not get_setting('settings.session_control.enabled'):
            return

        user_id = user.get('Id')
        user_name = user.get('Name')
        if not user_id:
            return

        if user_id in SESSION_ENFORCEMENT_LOCK:
            print(i18n._("ℹ️ User {user} is already in the session enforcement process, skipping check.").format(user=user_name))
            return

        print(i18n._("🛡️ Checking session limits for user: {user} ({id})").format(user=user_name, id=user_id))
        
        max_sessions = get_setting('settings.session_control.max_sessions') or 3
        all_active_sessions = emby_api.get_active_sessions()

        user_playing_sessions = [
            s for s in all_active_sessions 
            if s.get('UserId') == user_id and s.get('NowPlayingItem')
        ]
        
        current_session_count = len(user_playing_sessions)
        print(i18n._("ℹ️ User {user} has {count} active playback sessions. Limit is {limit}.").format(
            user=user_name, count=current_session_count, limit=max_sessions
        ))

        if current_session_count > max_sessions:

            SESSION_ENFORCEMENT_LOCK.add(user_id)
            
            def enforcement_task():
                try:
                    session_ids_to_terminate = [s['Id'] for s in user_playing_sessions if s.get('Id')]

                    for i in range(3, 0, -1):
                        remaining_seconds = i * 5
                        warning_message = i18n._(
                            "⚠️ Only {limit} devices are allowed to play simultaneously. You have exceeded the limit, playback will be interrupted in {sec} seconds.").format(limit=max_sessions, sec=remaining_seconds)

                        print(i18n._("⚠️ User {user} exceeded session limit. Sending warning ({i}/3)...").format(user=user_name, i=4-i))
                        for session_id in session_ids_to_terminate:
                            emby_api.send_message_to_emby_session(session_id, warning_message)

                        if i > 1:
                            time.sleep(5)

                    time.sleep(5)

                    print(i18n._("🛑 Terminating {count} sessions for user {user}...").format(
                        count=len(session_ids_to_terminate), user=user_name
                    ))
                    terminated_count = 0
                    for session_id in session_ids_to_terminate:
                        if emby_api.terminate_emby_session(session_id):
                            terminated_count += 1

                    print(i18n._("✅ Session termination task completed. {count} sessions stopped.").format(count=terminated_count))

                finally:
                    SESSION_ENFORCEMENT_LOCK.remove(user_id)
                    print(i18n._("✅ User {user} unlocked from session enforcement.").format(user=user_name))

            thread = threading.Thread(target=enforcement_task)
            thread.start()

    def _handle_library_new(self, event_data: dict):
        if not any([
            get_setting('settings.notification_management.library_new.to_group'),
            get_setting('settings.notification_management.library_new.to_channel'),
            get_setting('settings.notification_management.library_new.to_private')
        ]):
            print(i18n._("⚠️ New item notifications are disabled, skipping."))
            return

        item = event_data.get('Item', {}) or {}
        stream_details = None

        if item.get('Id') and EMBY_USER_ID:
            print(i18n._("ℹ️ Supplementing metadata for item {id} using Emby API.").format(id=item.get('Id')))
            full_item_info = emby_api.get_series_item_basic(item.get('Id'))
            if full_item_info:
                item = full_item_info
        
        media_details = tmdb_api.get_media_details(item, event_data.get('User', {}).get('Id'))
        added_summary, added_list = helpers.parse_episode_ranges_from_description(event_data.get('Description', ''))

        if item.get('Type') == 'Series':
            if added_list:
                s_key = lambda s: (int(m.group(1)) if (m := re.match(r'S(\d+)', s, re.I)) else 0, int(m.group(1)) if (m := re.search(r'E(\d+)', s, re.I)) else 0)
                sorted_added = sorted(added_list, key=s_key)
                for ep_str in sorted_added:
                    s_num, e_num = s_key(ep_str)
                    if s_num > 0 and e_num > 0:
                        ep_item = emby_api.get_episode_item_by_number(item.get('Id'), s_num, e_num)
                        if ep_item:
                            stream_details = emby_api.get_media_stream_details(ep_item.get('Id'), EMBY_USER_ID)
                            if stream_details:
                                print(i18n._("✅ Specs found successfully (Strategy 1): Using specs from {ep}.").format(ep=ep_str))
                                break
        else:
            print(i18n._("ℹ️ New item is a movie/episode, waiting 30s for Emby to analyze media source..."))
            time.sleep(30)
            stream_details = emby_api.get_media_stream_details(item.get('Id'), EMBY_USER_ID)

        if not stream_details and item.get('Type') == 'Episode':
            season_num = item.get('ParentIndexNumber')
            series_id = item.get('SeriesId')
            if season_num is not None and series_id:
                print(i18n._("🔍 Specs not found, trying fallback: finding another episode in S{num:02d} for reference...").format(num=season_num))
                ref_episode_item = emby_api.get_any_episode_from_season(series_id, season_num)
                if ref_episode_item:
                    stream_details = emby_api.get_media_stream_details(ref_episode_item.get('Id'), EMBY_USER_ID)
                    if stream_details:
                        print(i18n._("✅ Fallback successful: using specs from reference episode (ID: {id}).").format(id=ref_episode_item.get('Id')))

        if not stream_details:
             print(i18n._("❌ All spec-finding strategies failed, sending notification without specs."))
        
        parts = []
        raw_episode_info = ""
        item_type = item.get('Type')
        if item_type == 'Episode':
            s, e, en = item.get('ParentIndexNumber'), item.get('IndexNumber'), item.get('Name')
            raw_episode_info = f" S{s:02d}E{e:02d} {en or ''}" if s is not None and e is not None else f" {en or ''}"

        raw_title = item.get('SeriesName') or item.get('Name', i18n._('Unknown Title'))
        title_with_year = f"{raw_title} ({media_details.get('year')})" if media_details.get('year') else raw_title
        full_title_raw = title_with_year + raw_episode_info

        action_text = i18n._("✅ New")
        item_type_str = i18n._("Series") if item_type in ['Episode', 'Series', 'Season'] else (i18n._("Movie") if item_type == 'Movie' else "")

        if get_setting('settings.content_settings.new_library_notification.show_media_detail'):
            if get_setting('settings.content_settings.new_library_notification.media_detail_has_tmdb_link') and media_details.get('tmdb_link'):
                full_title_line = f'<a href="{media_details.get("tmdb_link")}">{helpers.escape_html(full_title_raw)}</a>'
            else:
                full_title_line = helpers.escape_html(full_title_raw)
            parts.append(f"{action_text}{item_type_str} {full_title_line}")
        else:
            parts.append(f"{action_text}{item_type_str}")

        if added_summary:
            count_match = re.search(r'(\d+)\s*(items|项目)', (event_data.get('Title') or ''), re.I)
            count_str = i18n._(" (Total {count} episodes)").format(count=count_match.group(1)) if count_match else ""
            parts.append(i18n._("Newly added: {summary}{count}").format(summary=helpers.escape_html(added_summary), count=helpers.escape_html(count_str)))
        
        if get_setting('settings.content_settings.new_library_notification.show_media_type'):
            program_type = helpers.get_program_type_from_path(item.get('Path'))
            if program_type:
                parts.append(i18n._("Media Type: {type}").format(type=helpers.escape_html(program_type)))

        if get_setting('settings.content_settings.new_library_notification.show_overview'):
            overview = (item.get('Overview') or i18n._('No overview available.'))[:150]
            if len(item.get('Overview', '')) > 150: overview += "..."
            parts.append(i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview)))

        if get_setting('settings.content_settings.new_library_notification.show_progress_status'):
            progress_lines = series_helper.build_progress_lines_for_library_new(item, media_details)
            if progress_lines:
                parts.extend(progress_lines)

        if stream_details:
            formatted_specs = formatters.format_stream_details_message(stream_details, prefix='new_library_notification')
            parts.extend([helpers.escape_html(p) for p in formatted_specs])

        if get_setting('settings.content_settings.new_library_notification.show_timestamp'):
            parts.append(i18n._("Date Added: {time}").format(time=helpers.escape_html(datetime.now(config.TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'))))

        message = "\n".join(parts)
        photo_url = media_details.get('poster_url') if get_setting('settings.content_settings.new_library_notification.show_poster') else None

        buttons = []
        if get_setting('settings.content_settings.new_library_notification.show_view_on_server_button') and EMBY_REMOTE_URL:
            item_id_, server_id = item.get('Id'), item.get('ServerId')
            if item_id_ and server_id:
                item_url = f"{EMBY_REMOTE_URL}/web/index.html#!/item?id={item_id_}&serverId={server_id}"
                buttons.append([{'text': i18n._('➡️ View on Server'), 'url': item_url}])

        common_args = {'text': message, 'photo_url': photo_url, 'inline_buttons': buttons if buttons else None}
        
        if get_setting('settings.notification_management.library_new.to_group') and config.GROUP_ID:
            print(i18n._("✉️ Sending new item notification to groups: {ids}.").format(ids=config.GROUP_ID))
            is_deletable = get_setting('settings.auto_delete_settings.new_library.to_group')
            notification_manager.send_to_targets(config.GROUP_ID, is_deletable, **common_args)

        if get_setting('settings.notification_management.library_new.to_channel') and config.CHANNEL_ID:
            print(i18n._("✉️ Sending new item notification to channels: {ids}.").format(ids=config.CHANNEL_ID))
            is_deletable = get_setting('settings.auto_delete_settings.new_library.to_channel')
            notification_manager.send_to_targets(config.CHANNEL_ID, is_deletable, **common_args)

        if get_setting('settings.notification_management.library_new.to_private') and config.ADMIN_USER_ID:
            print(i18n._("✉️ Sending new item notification to admins: {ids}.").format(ids=config.ADMIN_USER_ID))
            is_deletable = get_setting('settings.auto_delete_settings.new_library.to_private')
            notification_manager.send_to_targets(config.ADMIN_USER_ID, is_deletable, **common_args)

    def _handle_library_deleted(self, event_data: dict):
        if not get_setting('settings.notification_management.library_deleted'):
            print(i18n._("⚠️ Deleted item notifications are disabled, skipping."))
            return

        item = event_data.get('Item', {}) or {}
        item_type = item.get('Type')
        
        if item_type not in ['Movie', 'Series', 'Season', 'Episode']:
            print(i18n._("⚠️ Ignoring unsupported delete event type: {type}").format(type=item_type))
            return

        if item_type in ['Episode', 'Season'] and item.get('SeriesId'):
            series_id = item.get('SeriesId')
            series_stub = emby_api.get_series_item_basic(series_id) or {}
            media_details = tmdb_api.get_media_details(series_stub or item, EMBY_USER_ID)
            display_title = series_stub.get('Name') or item.get('SeriesName') or item.get('Name', i18n._('Unknown Title'))
            year = series_stub.get('ProductionYear') or helpers.extract_year_from_path((series_stub.get('Path') or item.get('Path') or ''))
        else:
            media_details = tmdb_api.get_media_details(item, EMBY_USER_ID)
            display_title = item.get('Name', i18n._('Unknown Title'))
            year = item.get('ProductionYear') or helpers.extract_year_from_path(item.get('Path'))

        episode_info = ""
        if item_type == 'Episode':
            s, e, en = item.get('ParentIndexNumber'), item.get('IndexNumber'), (item.get('Name') or '')
            episode_info = f" S{s:02d}E{e:02d} {en or ''}" if s is not None and e is not None else f" {en or ''}"
        elif item_type == 'Season':
            s = item.get('IndexNumber')
            episode_info = i18n._(" Season {s}").format(s=s) if s is not None else ""

        title_full = f"{display_title} ({year})" if year else display_title
        title_full += episode_info
        
        parts = []
        action_text = i18n._("🗑️ Deleted")
        item_type_str = i18n._("Series") if item_type in ['Series', 'Season', 'Episode'] else i18n._("Movie")

        if get_setting('settings.content_settings.library_deleted_notification.show_media_detail'):
            if get_setting('settings.content_settings.library_deleted_notification.media_detail_has_tmdb_link') and media_details.get('tmdb_link'):
                parts.append(f'{action_text}{item_type_str} <a href="{media_details.get("tmdb_link")}">{helpers.escape_html(title_full)}</a>')
            else:
                parts.append(f"{action_text}{item_type_str} {helpers.escape_html(title_full)}")
        else:
            parts.append(f"{action_text}{item_type_str}")

        if get_setting('settings.content_settings.library_deleted_notification.show_media_type'):
            program_type = helpers.get_program_type_from_path(item.get('Path') or '')
            if program_type:
                parts.append(i18n._("Media Type: {type}").format(type=helpers.escape_html(program_type)))

        deleted_summary, _ = helpers.parse_episode_ranges_from_description(event_data.get('Description', ''))
        if deleted_summary:
            parts.append(i18n._("Deleted: {summary}").format(summary=helpers.escape_html(deleted_summary)))

        if get_setting('settings.content_settings.library_deleted_notification.show_overview'):
            ov = (item.get('Overview') or '')
            if ov:
                overview_text = ov[:150] + ('...' if len(ov) > 150 else '')
                parts.append(i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview_text)))

        if get_setting('settings.content_settings.library_deleted_notification.show_timestamp'):
            now_str = datetime.now(config.TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
            parts.append(i18n._("Deletion Time: {time}").format(time=helpers.escape_html(now_str)))

        message = "\n".join(parts)
        photo_url = media_details.get('poster_url') if get_setting('settings.content_settings.library_deleted_notification.show_poster') else None

        if config.ADMIN_USER_ID:
            print(i18n._("✉️ Sending deletion notification to admins: {ids}.").format(ids=config.ADMIN_USER_ID))
            is_deletable = get_setting('settings.auto_delete_settings.library_deleted')
            notification_manager.send_to_targets(
                config.ADMIN_USER_ID,
                is_deletable,
                text=message,
                photo_url=photo_url,
                delay_seconds=60
            )
        else:
            print(i18n._("⚠️ Deletion notification skipped: ADMIN_USER_ID not configured."))

    def _handle_playback_event(self, event_data: dict):
        event_type = event_data.get('Event')
        item = event_data.get('Item', {}) or {}
        user = event_data.get('User', {}) or {}
        session = event_data.get('Session', {}) or {}
        playback_info = event_data.get('PlaybackInfo', {}) or {}

        if event_type in ["playback.start", "playback.unpause"]:
            self._check_and_enforce_session_limit(user)

        event_key_map = {
            'playback.start': 'playback_start',
            'playback.unpause': 'playback_start',
            'playback.stop': 'playback_stop',
            'playback.pause': 'playback_pause'
        }
        notification_type = event_key_map.get(event_type)
        if not notification_type or not get_setting(f'settings.notification_management.{notification_type}'):
            print(i18n._("⚠️ Notifications for {event} are disabled, skipping.").format(event=event_type))
            return

        if not config.ADMIN_USER_ID:
            print(i18n._("⚠️ ADMIN_USER_ID not configured, skipping playback notification."))
            return

        if event_type in ["playback.start", "playback.unpause"]:
            now = time.time()
            event_key = (user.get('Id'), item.get('Id'))
            if now - recent_playback_notifications.get(event_key, 0) < PLAYBACK_DEBOUNCE_SECONDS:
                print(i18n._("⏳ Ignoring {event} event due to debounce time ({seconds}s).").format(event=event_type, seconds=PLAYBACK_DEBOUNCE_SECONDS))
                return
            recent_playback_notifications[event_key] = now
        
        media_details = tmdb_api.get_media_details(item, user.get('Id'))
        stream_details = emby_api.get_media_stream_details(item.get('Id'), user.get('Id'))

        raw_title = item.get('SeriesName') if item.get('Type') == 'Episode' else item.get('Name', i18n._('Unknown Title'))
        raw_episode_info = ""
        if item.get('Type') == 'Episode':
            s, e, en = item.get('ParentIndexNumber'), item.get('IndexNumber'), item.get('Name')
            raw_episode_info = f" S{s:02d}E{e:02d} {en or ''}" if s is not None and e is not None else f" {en or ''}"

        title_full_raw = f"{raw_title} ({media_details.get('year')})" if media_details.get('year') else raw_title
        title_full_raw += raw_episode_info

        action_text_map = {
            "playback.start": i18n._("▶️ Playback Started"),
            "playback.unpause": i18n._("▶️ Playback Resumed"),
            "playback.stop": i18n._("⏹️ Playback Stopped"),
            "playback.pause": i18n._("⏸️ Playback Paused")
        }
        action_text = action_text_map.get(event_type, "")
        item_type_str = i18n._("Series") if item.get('Type') in ['Episode', 'Series'] else (i18n._("Movie") if item.get('Type') == 'Movie' else "")

        parts = []
        if get_setting('settings.content_settings.playback_action.show_media_detail'):
            if get_setting('settings.content_settings.playback_action.media_detail_has_tmdb_link') and media_details.get('tmdb_link'):
                full_title_line = f'<a href="{media_details.get("tmdb_link")}">{helpers.escape_html(title_full_raw)}</a>'
            else:
                full_title_line = helpers.escape_html(title_full_raw)
            parts.append(f"{action_text}{item_type_str} {full_title_line}")
        else:
            parts.append(f"{action_text}{item_type_str}")

        if get_setting('settings.content_settings.playback_action.show_user'):
            parts.append(i18n._("User: {user}").format(user=helpers.escape_html(user.get('Name', i18n._('Unknown User')))))
        if get_setting('settings.content_settings.playback_action.show_player'):
            parts.append(i18n._("Player: {player}").format(player=helpers.escape_html(session.get('Client', ''))))
        if get_setting('settings.content_settings.playback_action.show_device'):
            parts.append(i18n._("Device: {device}").format(device=helpers.escape_html(session.get('DeviceName', ''))))

        if get_setting('settings.content_settings.playback_action.show_location'):
            ip = session.get('RemoteEndPoint', '').split(':')[0]
            loc = geo_api.get_ip_geolocation(ip)
            if loc == i18n._("LAN"):
                parts.append(i18n._("Location: {location}").format(location=helpers.escape_html(loc)))
            else:
                parts.append(i18n._("Location: <code>{ip}</code> {location}").format(ip=helpers.escape_html(ip), location=helpers.escape_html(loc)))
        
        if get_setting('settings.content_settings.playback_action.show_progress'):
            pos_ticks, run_ticks = playback_info.get('PositionTicks'), item.get('RunTimeTicks')
            if pos_ticks is not None and run_ticks and run_ticks > 0:
                percent = (pos_ticks / run_ticks) * 100
                progress_text = i18n._("Progress: Watched {percent:.1f}%").format(percent=percent) if event_type == "playback.stop" \
                    else i18n._("Progress: {percent:.1f}% ({played} / {total})").format(
                        percent=percent, 
                        played=helpers.format_ticks_to_hms(pos_ticks), 
                        total=helpers.format_ticks_to_hms(run_ticks)
                    )
                parts.append(helpers.escape_html(progress_text))

        if stream_details:
            formatted_specs = formatters.format_stream_details_message(stream_details, prefix='playback_action')
            parts.extend([helpers.escape_html(p) for p in formatted_specs])

        if get_setting('settings.content_settings.playback_action.show_media_type'):
            program_type = helpers.get_program_type_from_path(item.get('Path'))
            if program_type:
                parts.append(i18n._("Media Type: {type}").format(type=helpers.escape_html(program_type)))

        if get_setting('settings.content_settings.playback_action.show_overview'):
            overview_raw = item.get('Overview')
            if overview_raw:
                overview = overview_raw[:150] + '...' if len(overview_raw) > 150 else overview_raw
                parts.append(i18n._("Overview: {overview}").format(overview=helpers.escape_html(overview)))

        if get_setting('settings.content_settings.playback_action.show_timestamp'):
            parts.append(i18n._("Time: {time}").format(time=helpers.escape_html(datetime.now(config.TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'))))

        message = "\n".join(parts)
        print(i18n._("✉️ Sending playback notification to admins: {ids}.").format(ids=config.ADMIN_USER_ID))

        buttons = []
        if EMBY_REMOTE_URL and get_setting('settings.content_settings.playback_action.show_view_on_server_button'):
            item_id, server_id = item.get('Id'), item.get('ServerId') or (event_data.get('Server', {}).get('Id'))
            if item_id and server_id:
                buttons.append([{'text': i18n._('➡️ View on Server'), 'url': f"{EMBY_REMOTE_URL}/web/index.html#!/item?id={item_id}&serverId={server_id}"}])

        auto_delete_path_map = {
            'playback.start': 'settings.auto_delete_settings.playback_start',
            'playback.unpause': 'settings.auto_delete_settings.playback_start',
            'playback.pause': 'settings.auto_delete_settings.playback_pause',
            'playback.stop': 'settings.auto_delete_settings.playback_stop'
        }
        auto_delete_path = auto_delete_path_map.get(event_type)
        is_deletable = auto_delete_path and get_setting(auto_delete_path)

        common_args = {
            'text': message,
            'photo_url': media_details.get('poster_url') if get_setting('settings.content_settings.playback_action.show_poster') else None,
            'inline_buttons': buttons if buttons else None,
            'delay_seconds': 60
        }

        notification_manager.send_to_targets(
            config.ADMIN_USER_ID,
            is_deletable,
            **common_args
        )

    def _handle_system_event(self, event_data: dict):
        event_type = event_data.get('Event')
        user = event_data.get('User', {}) or {}

        config_map = {
            "user.authenticated": 'settings.notification_management.advanced.user_login_success',
            "user.authenticationfailed": 'settings.notification_management.advanced.user_login_failure',
            "user.created": 'settings.notification_management.advanced.user_creation_deletion',
            "user.deleted": 'settings.notification_management.advanced.user_creation_deletion',
            "user.policyupdated": 'settings.notification_management.advanced.user_updates',
            "user.passwordchanged": 'settings.notification_management.advanced.user_updates',
            "system.serverrestartrequired": 'settings.notification_management.advanced.server_restart_required',
        }
        
        config_path = config_map.get(event_type)
        feature_key = config.SETTING_PATH_TO_FEATURE_KEY.get(config_path) if config_path else None
        if not config_path or not get_setting(config_path) or (feature_key and not config.is_feature_active(feature_key)):
            print(i18n._("⚠️ Notifications for {event} event are disabled, skipping.").format(event=event_type))
            return

        if not config.ADMIN_USER_ID:
            print(i18n._("⚠️ ADMIN_USER_ID not configured, cannot send user/system event notifications."))
            return
        
        time_str = helpers.get_event_time_str(event_data)
        parts = []
        icon = "ℹ️"
        custom_title = ""
        username = user.get('Name')

        if event_type == "user.authenticated":
            icon = "✅"
            custom_title = i18n._("User {username} successfully logged in").format(username=username)
            session_info = event_data.get("Session", {})
            ip_address = session_info.get('RemoteEndPoint', '')
            location = geo_api.get_ip_geolocation(ip_address)
            parts.append(i18n._("Client: {client}").format(client=helpers.escape_html(session_info.get('Client'))))
            parts.append(i18n._("Device: {device}").format(device=helpers.escape_html(session_info.get('DeviceName'))))
            parts.append(i18n._("Location: <code>{ip}</code> {location}").format(ip=helpers.escape_html(ip_address), location=helpers.escape_html(location)))
        
        elif event_type == "user.authenticationfailed":
            icon = "⚠️"
            original_title = event_data.get("Title", "")
            username_match = re.search(r'(?:来自|from)\s+(.+?)\s+(?:的登录|on)', original_title, re.I)
            username_to_check = username_match.group(1).strip() if username_match else i18n._("Unknown")
            custom_title = i18n._("User {username} login failed").format(username=username_to_check)
            
            desc_text = event_data.get("Description", "")
            ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', desc_text)
            ip_address = ip_match.group(1) if ip_match else i18n._("Unknown IP")
            location = geo_api.get_ip_geolocation(ip_address)
            device_info = event_data.get("DeviceInfo", {})
            parts.append(i18n._("Client: {client}").format(client=helpers.escape_html(device_info.get('AppName'))))
            parts.append(i18n._("Device: {device}").format(device=helpers.escape_html(device_info.get('Name'))))
            parts.append(i18n._("Location: <code>{ip}</code> {location}").format(ip=helpers.escape_html(ip_address), location=helpers.escape_html(location)))
            
            if username_to_check != i18n._("Unknown"):
                all_users = emby_api.get_all_emby_users()
                if username_to_check in all_users:
                    failure_reason = i18n._("Reason for failure: Incorrect password")
                else:
                    failure_reason = i18n._("Reason for failure: User does not exist")
                parts.append(helpers.escape_html(failure_reason))

        elif event_type == "user.created":
            icon = "➕"
            custom_title = i18n._("User {username} successfully created").format(username=username)
            
        elif event_type == "user.deleted":
            icon = "➖"
            custom_title = i18n._("User {username} has been deleted").format(username=username)
            deleted_emby_id = user.get('Id')
            
            unbound_info_parts = []
            if deleted_emby_id:
                db = SessionLocal()
                try:
                    bound_user = db.query(models.User).filter(models.User.emby_user_id == deleted_emby_id).first()
                    if bound_user:
                        unbound_info_parts.append(f"\n\n{i18n._('ℹ️ Unbinding complete:')}")
                        if bound_user.telegram_user_id:
                            unbound_info_parts.append(f"Telegram: <code>{bound_user.telegram_user_id}</code>")
                        if bound_user.wecom_user_id:
                            unbound_info_parts.append(f"{i18n._('WeCom')}: <code>{bound_user.wecom_user_id}</code>")
                        
                        bound_user.emby_user_id = None
                        db.commit()
                finally:
                    db.close()
            
            if unbound_info_parts:
                parts.append("\n".join(unbound_info_parts))

        elif event_type == "user.policyupdated":
            icon = "🔧"
            custom_title = i18n._("Policy for user {username} has been updated").format(username=username)
            
        elif event_type == "user.passwordchanged":
            icon = "🔧"
            custom_title = i18n._("Password for user {username} has been changed").format(username=username)
            
        elif event_type == "system.serverrestartrequired":
            icon = "🔄"
            custom_title = i18n._("Server restart required")
            server_name = event_data.get("Server", {}).get("Name", "")
            restart_line_raw = i18n._("Please Restart Emby Server: {server_name}").format(server_name=server_name)
            parts.append(helpers.escape_html(restart_line_raw))
        
        message_parts = [f"{icon} <b>{helpers.escape_html(custom_title)}</b>"]
        message_parts.extend(parts)
        if time_str != i18n._("Unknown"):
            message_parts.append(i18n._("Time: {time}").format(time=helpers.escape_html(time_str)))
            
        message = "\n".join(message_parts)

        autodelete_config_map = {
            "user.authenticated": 'settings.auto_delete_settings.advanced.user_login',
            "user.authenticationfailed": 'settings.auto_delete_settings.advanced.user_login',
            "user.created": 'settings.auto_delete_settings.advanced.user_management',
            "user.deleted": 'settings.auto_delete_settings.advanced.user_management',
            "user.policyupdated": 'settings.auto_delete_settings.advanced.user_management',
            "user.passwordchanged": 'settings.auto_delete_settings.advanced.user_management',
            "system.serverrestartrequired": 'settings.auto_delete_settings.advanced.server_events',
        }
        autodelete_path = autodelete_config_map.get(event_type)
        is_deletable = autodelete_path and get_setting(autodelete_path)

        print(i18n._("✉️ Sending system event notification to admins: {ids}.").format(ids=config.ADMIN_USER_ID))
        notification_manager.send_to_targets(
            config.ADMIN_USER_ID,
            is_deletable,
            text=message,
            delay_seconds=180
        )


class QuietWebhookHandler(WebhookHandler):
    def log_message(self, format, *args):
        pass