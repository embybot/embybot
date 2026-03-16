# -*- coding: utf-8 -*-

import os
import yaml
import operator
from functools import reduce
from zoneinfo import ZoneInfo
from .. import i18n

SUPPORTED_LANGUAGES = {
  'en': {
        'name': '🇺🇸 English',
    },
    'es': {
        'name': '🇪🇸 Español',
    },
    'fr': {
        'name': '🇫🇷 Français',
    },
    'de': {
        'name': '🇩🇪 Deutsch',
    },
    'ja': {
        'name': '🇯🇵 日本語',
    },
    'ko': {
        'name': '🇰🇷 한국어',
    },
    'ru': {
        'name': '🇷🇺 Русский',
    },
    'pt': {
        'name': '🇵🇹 Português',
    },
    'it': {
        'name': '🇮🇹 Italiano',
    },
    'zh_hans': {
        'name': '🇨🇳 简体中文',
    },
    'zh_hant': {
        'name': '🇨🇳 繁體中文',
    }, 
}

CONFIG_DIR = '/config'
CACHE_DIR = os.path.join(CONFIG_DIR, 'cache')
STATIC_DIR = os.path.join(CONFIG_DIR, 'static')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.yaml')

CONFIG: dict = {}
DEFAULT_SETTINGS: dict = {}
TOGGLE_INDEX_TO_KEY: dict = {}
TOGGLE_KEY_TO_INFO: dict = {}
SELECTION_KEY_TO_INFO: dict = {} 

SETTINGS_MENU_STRUCTURE = {
    'root': {'label': '⚙️ Main Menu', 'children': ['notification_message_settings', 'session_control_settings', 'points_checkin_settings', 'system_settings']},
    'notification_message_settings': {'label': 'Notification Message Settings', 'parent': 'root', 'children': ['content_settings', 'notification_management', 'auto_delete_settings']},
    'content_settings': {'label': 'Content Settings', 'parent': 'notification_message_settings', 'children': ['status_feedback', 'playback_action', 'library_deleted_content', 'new_library_content_settings', 'search_display', 'media_spec_settings']},
    'new_library_content_settings': {'label': 'New Content Notification Settings', 'parent': 'content_settings', 'children': ['new_library_show_poster', 'new_library_show_media_detail', 'new_library_media_detail_has_tmdb_link', 'new_library_show_overview', 'new_library_show_media_type', 'new_library_show_video_spec', 'new_library_show_audio_spec', 'new_library_show_subtitle_spec', 'new_library_show_progress_status', 'new_library_show_timestamp', 'new_library_show_view_on_server_button']},
    'new_library_show_progress_status': {'label': 'Show Update Progress/Missing', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_progress_status', 'default': False},
    'new_library_show_poster': {'label': 'Show Poster', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_poster', 'default': False},
    'new_library_show_media_detail': {'label': 'Show Media Details', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_media_detail', 'default': False},
    'new_library_media_detail_has_tmdb_link': {'label': 'Add TMDB Link to Details', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.media_detail_has_tmdb_link', 'default': False},
    'new_library_show_overview': {'label': 'Show Overview', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_overview', 'default': False},
    'new_library_show_media_type': {'label': 'Show Media Type', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_media_type', 'default': False},
    'new_library_show_video_spec': {'label': 'Show Video Specs', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_video_spec', 'default': False},
    'new_library_show_audio_spec': {'label': 'Show Audio Specs', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_audio_spec', 'default': False},
    'new_library_show_subtitle_spec': {'label': 'Show Subtitle Specs', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_subtitle_spec', 'default': False},
    'new_library_show_timestamp': {'label': 'Show Timestamp', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_timestamp', 'default': False},
    'new_library_show_view_on_server_button': {'label': 'Show "View on Server" Button', 'parent': 'new_library_content_settings', 'config_path': 'settings.content_settings.new_library_notification.show_view_on_server_button', 'default': False},
    'status_feedback': {'label': 'Playback Status Feedback Settings', 'parent': 'content_settings', 'children': ['status_content_mode', 'status_display_content_settings']},
    'status_show_poster': {'label': 'Show Poster', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_poster', 'default': False},
    'status_show_player': {'label': 'Show Player', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_player', 'default': False},
    'status_show_device': {'label': 'Show Device', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_device', 'default': False},
    'status_show_location': {'label': 'Show Location Info', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_location', 'default': False},
    'status_show_media_detail': {'label': 'Show Media Details', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_media_detail', 'default': False},
    'status_media_detail_has_tmdb_link': {'label': 'Add TMDB Link to Details', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.media_detail_has_tmdb_link', 'default': False},
    'status_show_media_type': {'label': 'Show Media Type', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_media_type', 'default': False},
    'status_show_overview': {'label': 'Show Overview', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_overview', 'default': False},
    'status_show_progress': {'label': 'Show Playback Progress', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_progress', 'default': False},
    'status_show_timestamp': {'label': 'Show Timestamp', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_timestamp', 'default': False},
    'status_show_view_on_server_button': {'label': 'Show "View on Server" Button', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_view_on_server_button', 'default': False},
    'status_show_terminate_session_button': {'label': 'Show "Terminate Session" Button', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_terminate_session_button', 'default': False},
    'status_show_send_message_button': {'label': 'Show "Send Message" Button', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_send_message_button', 'default': False},
    'status_show_broadcast_button': {'label': 'Show "Broadcast Message" Button', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_broadcast_button', 'default': False},
    'status_show_terminate_all_button': {'label': 'Show "Terminate All" Button', 'parent': 'status_feedback', 'config_path': 'settings.content_settings.status_feedback.show_terminate_all_button', 'default': False},
    'playback_action': {'label': 'Playback Action Notification Settings', 'parent': 'content_settings', 'children': ['playback_show_poster', 'playback_show_media_detail', 'playback_media_detail_has_tmdb_link', 'playback_show_user', 'playback_show_player', 'playback_show_device', 'playback_show_location', 'playback_show_progress', 'playback_show_video_spec', 'playback_show_audio_spec', 'playback_show_subtitle_spec', 'playback_show_media_type', 'playback_show_overview', 'playback_show_timestamp', 'playback_show_view_on_server_button']},
    'playback_show_poster': {'label': 'Show Poster', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_poster', 'default': False},
    'playback_show_media_detail': {'label': 'Show Media Details', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_media_detail', 'default': False},
    'playback_media_detail_has_tmdb_link': {'label': 'Add TMDB Link to Details', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.media_detail_has_tmdb_link', 'default': False},
    'playback_show_user': {'label': 'Show Username', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_user', 'default': False},
    'playback_show_player': {'label': 'Show Player', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_player', 'default': False},
    'playback_show_device': {'label': 'Show Device', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_device', 'default': False},
    'playback_show_location': {'label': 'Show Location Info', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_location', 'default': False},
    'playback_show_progress': {'label': 'Show Playback Progress', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_progress', 'default': False},
    'playback_show_video_spec': {'label': 'Show Video Specs', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_video_spec', 'default': False},
    'playback_show_audio_spec': {'label': 'Show Audio Specs', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_audio_spec', 'default': False},
    'playback_show_subtitle_spec': {'label': 'Show Subtitle Specs', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_subtitle_spec', 'default': False},
    'playback_show_media_type': {'label': 'Show Media Type', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_media_type', 'default': False},
    'playback_show_overview': {'label': 'Show Overview', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_overview', 'default': False},
    'playback_show_timestamp': {'label': 'Show Timestamp', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_timestamp', 'default': False},
    'playback_show_view_on_server_button': {'label': 'Show "View on Server" Button', 'parent': 'playback_action', 'config_path': 'settings.content_settings.playback_action.show_view_on_server_button', 'default': False},
    'library_deleted_content': {'label': 'Deleted Content Notification Settings', 'parent': 'content_settings', 'children': ['deleted_show_poster', 'deleted_show_media_detail', 'deleted_media_detail_has_tmdb_link', 'deleted_show_overview', 'deleted_show_media_type', 'deleted_show_timestamp']},
    'deleted_show_poster': {'label': 'Show Poster', 'parent': 'library_deleted_content', 'config_path': 'settings.content_settings.library_deleted_notification.show_poster', 'default': False},
    'deleted_show_media_detail': {'label': 'Show Media Details', 'parent': 'library_deleted_content', 'config_path': 'settings.content_settings.library_deleted_notification.show_media_detail', 'default': False},
    'deleted_media_detail_has_tmdb_link': {'label': 'Add TMDB Link to Details', 'parent': 'library_deleted_content', 'config_path': 'settings.content_settings.library_deleted_notification.media_detail_has_tmdb_link', 'default': False},
    'deleted_show_overview': {'label': 'Show Overview', 'parent': 'library_deleted_content', 'config_path': 'settings.content_settings.library_deleted_notification.show_overview', 'default': False},
    'deleted_show_media_type': {'label': 'Show Media Type', 'parent': 'library_deleted_content', 'config_path': 'settings.content_settings.library_deleted_notification.show_media_type', 'default': False},
    'deleted_show_timestamp': {'label': 'Show Deletion Time', 'parent': 'library_deleted_content', 'config_path': 'settings.content_settings.library_deleted_notification.show_timestamp', 'default': False},
    'search_display': {'label': 'Search Results Display Settings', 'parent': 'content_settings', 'children': ['search_show_media_type_in_list', 'search_movie', 'search_series']},
    'search_show_media_type_in_list': {'label': 'Show Media Type in Search List', 'parent': 'search_display', 'config_path': 'settings.content_settings.search_display.show_media_type_in_list', 'default': False},
    'search_movie': {'label': 'Movie Display Settings', 'parent': 'search_display', 'children': ['movie_show_poster', 'movie_title_has_tmdb_link', 'movie_show_type', 'movie_show_category', 'movie_show_overview', 'movie_show_video_spec', 'movie_show_audio_spec', 'movie_show_subtitle_spec', 'movie_show_added_time', 'movie_show_view_on_server_button']},
    'search_series': {'label': 'Series Display Settings', 'parent': 'search_display', 'children': ['series_show_poster', 'series_title_has_tmdb_link', 'series_show_type', 'series_show_category', 'series_show_overview', 'series_season_specs', 'series_update_progress', 'series_show_view_on_server_button']},
    'movie_show_poster': {'label': 'Show Poster', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_poster', 'default': False},
    'movie_title_has_tmdb_link': {'label': 'Add TMDB Link to Movie Title', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.title_has_tmdb_link', 'default': False},
    'movie_show_type': {'label': 'Show Type', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_type', 'default': False},
    'movie_show_category': {'label': 'Show Category', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_category', 'default': False},
    'movie_show_overview': {'label': 'Show Overview', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_overview', 'default': False},
    'movie_show_video_spec': {'label': 'Show Video Specs', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_video_spec', 'default': False},
    'movie_show_audio_spec': {'label': 'Show Audio Specs', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_audio_spec', 'default': False},
    'movie_show_subtitle_spec': {'label': 'Show Subtitle Specs', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_subtitle_spec', 'default': False},
    'movie_show_added_time': {'label': 'Show Added Time', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_added_time', 'default': False},
    'movie_show_view_on_server_button': {'label': 'Show "View on Server" Button', 'parent': 'search_movie', 'config_path': 'settings.content_settings.search_display.movie.show_view_on_server_button', 'default': False},
    'series_show_poster': {'label': 'Show Poster', 'parent': 'search_series', 'config_path': 'settings.content_settings.search_display.series.show_poster', 'default': False},
    'series_title_has_tmdb_link': {'label': 'Add TMDB Link to Series Title', 'parent': 'search_series', 'config_path': 'settings.content_settings.search_display.series.title_has_tmdb_link', 'default': False},
    'series_show_type': {'label': 'Show Type', 'parent': 'search_series', 'config_path': 'settings.content_settings.search_display.series.show_type', 'default': False},
    'series_show_category': {'label': 'Show Category', 'parent': 'search_series', 'config_path': 'settings.content_settings.search_display.series.show_category', 'default': False},
    'series_show_overview': {'label': 'Show Overview', 'parent': 'search_series', 'config_path': 'settings.content_settings.search_display.series.show_overview', 'default': False},
    'series_show_view_on_server_button': {'label': 'Show "View on Server" Button', 'parent': 'search_series', 'config_path': 'settings.content_settings.search_display.series.show_view_on_server_button', 'default': False},
    'series_season_specs': {'label': 'Season Specs', 'parent': 'search_series', 'children': ['series_season_show_video_spec', 'series_season_show_audio_spec', 'series_season_show_subtitle_spec']},
    'series_season_show_video_spec': {'label': 'Show Season Video Specs', 'parent': 'series_season_specs', 'config_path': 'settings.content_settings.search_display.series.season_specs.show_video_spec', 'default': False},
    'series_season_show_audio_spec': {'label': 'Show Season Audio Specs', 'parent': 'series_season_specs', 'config_path': 'settings.content_settings.search_display.series.season_specs.show_audio_spec', 'default': False},
    'series_season_show_subtitle_spec': {'label': 'Show Season Subtitle Specs', 'parent': 'series_season_specs', 'config_path': 'settings.content_settings.search_display.series.season_specs.show_subtitle_spec', 'default': False},
    'series_update_progress': {'label': 'Update Progress', 'parent': 'search_series', 'children': ['series_progress_show_latest_episode', 'series_progress_latest_episode_has_tmdb_link', 'series_progress_show_overview', 'series_progress_show_added_time', 'series_progress_show_progress_status']},
    'series_progress_show_latest_episode': {'label': 'Show "Updated to"', 'parent': 'series_update_progress', 'config_path': 'settings.content_settings.search_display.series.update_progress.show_latest_episode', 'default': False},
    'series_progress_latest_episode_has_tmdb_link': {'label': 'Add TMDB Link to "Updated to"', 'parent': 'series_update_progress', 'config_path': 'settings.content_settings.search_display.series.update_progress.latest_episode_has_tmdb_link', 'default': False},
    'series_progress_show_overview': {'label': 'Show Overview', 'parent': 'series_update_progress', 'config_path': 'settings.content_settings.search_display.series.update_progress.show_overview', 'default': False},
    'series_progress_show_added_time': {'label': 'Show Added Time', 'parent': 'series_update_progress', 'config_path': 'settings.content_settings.search_display.series.update_progress.show_added_time', 'default': False},
    'series_progress_show_progress_status': {'label': 'Show Update Progress', 'parent': 'series_update_progress', 'config_path': 'settings.content_settings.search_display.series.update_progress.show_progress_status', 'default': False},
    'notification_management': {'label': 'Notification Management', 'parent': 'notification_message_settings', 'children': ['notify_library_events', 'notify_playback_events', 'notification_management_advanced']},
    'notify_library_events': {'label': 'New/Deleted Content Notifications', 'parent': 'notification_management', 'children': ['notify_library_new', 'notify_library_deleted']},
    'notify_library_new': {'label': 'New Content Notifications', 'parent': 'notify_library_events', 'children': ['new_to_group', 'new_to_channel', 'new_to_private']},
    'notify_library_deleted': {'label': 'Deleted Content Notifications', 'parent': 'notify_library_events', 'config_path': 'settings.notification_management.library_deleted', 'default': False},
    'notify_playback_events': {'label': 'Playback Action Notifications', 'parent': 'notification_management', 'children': ['notify_playback_start', 'notify_playback_pause', 'notify_playback_stop']},
    'notify_playback_start': {'label': 'Playback Start/Resume Notifications', 'parent': 'notify_playback_events', 'config_path': 'settings.notification_management.playback_start', 'default': False},
    'notify_playback_pause': {'label': 'Playback Pause Notifications', 'parent': 'notify_playback_events', 'config_path': 'settings.notification_management.playback_pause', 'default': False},
    'notify_playback_stop': {'label': 'Playback Stop Notifications', 'parent': 'notify_playback_events', 'config_path': 'settings.notification_management.playback_stop', 'default': False},
    'new_to_group': {'label': 'Send to Group', 'parent': 'notify_library_new', 'config_path': 'settings.notification_management.library_new.to_group', 'default': False},
    'new_to_channel': {'label': 'Send to Channel', 'parent': 'notify_library_new', 'config_path': 'settings.notification_management.library_new.to_channel', 'default': False},
    'new_to_private': {'label': 'Send to Admin', 'parent': 'notify_library_new', 'config_path': 'settings.notification_management.library_new.to_private', 'default': False},
    'notification_management_advanced': {'label': 'Advanced Notification Management', 'parent': 'notification_management', 'children': ['notify_user_login_success', 'notify_user_login_failure', 'notify_user_creation_deletion', 'notify_user_updates', 'notify_server_restart_required']},
    'notify_user_login_success': {'label': 'User Login Success', 'parent': 'notification_management_advanced', 'config_path': 'settings.notification_management.advanced.user_login_success', 'default': False},
    'notify_user_login_failure': {'label': 'User Login Failure', 'parent': 'notification_management_advanced', 'config_path': 'settings.notification_management.advanced.user_login_failure', 'default': False},
    'notify_user_creation_deletion': {'label': 'User Creation/Deletion', 'parent': 'notification_management_advanced', 'config_path': 'settings.notification_management.advanced.user_creation_deletion', 'default': False},
    'notify_user_updates': {'label': 'User Policy/Password Updates', 'parent': 'notification_management_advanced', 'config_path': 'settings.notification_management.advanced.user_updates', 'default': False},
    'notify_server_restart_required': {'label': 'Server Restart Required', 'parent': 'notification_management_advanced', 'config_path': 'settings.notification_management.advanced.server_restart_required', 'default': False},
    'auto_delete_settings': {'label': 'Auto-Delete Message Settings', 'parent': 'notification_message_settings', 'children': ['delete_library_events', 'delete_playback_status', 'delete_advanced_notifications']},
    'delete_library_events': {'label': 'New/Deleted Content Notifications', 'parent': 'auto_delete_settings', 'children': ['delete_new_library', 'delete_library_deleted']},
    'delete_new_library': {'label': 'New Content Notifications', 'parent': 'delete_library_events', 'children': ['delete_new_library_group', 'delete_new_library_channel', 'delete_new_library_private']},
    'delete_library_deleted': {'label': 'Deleted Content Notifications', 'parent': 'delete_library_events', 'config_path': 'settings.auto_delete_settings.library_deleted', 'default': False},
    'delete_new_library_group': {'label': 'Auto-delete messages sent to Group', 'parent': 'delete_new_library', 'config_path': 'settings.auto_delete_settings.new_library.to_group', 'default': False},
    'delete_new_library_channel': {'label': 'Auto-delete messages sent to Channel', 'parent': 'delete_new_library', 'config_path': 'settings.auto_delete_settings.new_library.to_channel', 'default': False},
    'delete_new_library_private': {'label': 'Auto-delete messages sent to Admin', 'parent': 'delete_new_library', 'config_path': 'settings.auto_delete_settings.new_library.to_private', 'default': False},
    'delete_playback_status': {'label': 'Playback Action Notifications', 'parent': 'auto_delete_settings', 'children': ['delete_playback_start', 'delete_playback_pause', 'delete_playback_stop']},
    'delete_playback_start': {'label': 'Playback Start/Resume Notifications', 'parent': 'delete_playback_status', 'config_path': 'settings.auto_delete_settings.playback_start', 'default': False},
    'delete_playback_pause': {'label': 'Playback Pause Notifications', 'parent': 'delete_playback_status', 'config_path': 'settings.auto_delete_settings.playback_pause', 'default': False},
    'delete_playback_stop': {'label': 'Playback Stop Notifications', 'parent': 'delete_playback_status', 'config_path': 'settings.auto_delete_settings.playback_stop', 'default': False},
    'delete_advanced_notifications': {'label': 'Advanced Notifications', 'parent': 'auto_delete_settings', 'children': ['delete_user_login', 'delete_user_management', 'delete_server_events']},
    'delete_user_login': {'label': 'User Login Success/Failure', 'parent': 'delete_advanced_notifications', 'config_path': 'settings.auto_delete_settings.advanced.user_login', 'default': False},
    'delete_user_management': {'label': 'User Create/Delete/Update', 'parent': 'delete_advanced_notifications', 'config_path': 'settings.auto_delete_settings.advanced.user_management', 'default': False},
    'delete_server_events': {'label': 'Server Events', 'parent': 'delete_advanced_notifications', 'config_path': 'settings.auto_delete_settings.advanced.server_events', 'default': False},
    'system_settings': {'label': 'System Settings', 'parent': 'root', 'children': ['ip_api_selection', 'language_selection', 'telegram_mode', 'restart_bot']},
    'language_selection': {'label': 'Language Settings', 'parent': 'system_settings', 'children': []},
    'session_control_settings': {'label': 'Playback Session Control', 'parent': 'root', 'children': ['session_control_enabled', 'session_control_limit_settings']},
    'session_control_enabled': {'label': 'Enable Playback Session Control', 'parent': 'session_control_settings', 'config_path': 'settings.session_control.enabled', 'default': False},
    'session_control_limit_settings': {'label': 'Session Limit Settings', 'parent': 'session_control_settings', 'type': 'selection', 'children': [], 'config_path': 'settings.session_control.max_sessions', 'default': 3, 'options': {1: '1 Session', 2: '2 Sessions', 3: '3 Sessions', 4: '4 Sessions', 5: '5 Sessions'}},
    'points_checkin_settings': {'label': 'Points & Check-in Settings', 'parent': 'root', 'children': ['points_settings', 'checkin_settings']},
    'points_settings': {'label': 'Points Settings', 'parent': 'points_checkin_settings', 'children': ['points_enabled', 'points_transfer_enabled', 'group_chat_points_settings']},
    'points_enabled': {'label': 'Enable Points Functionality', 'parent': 'points_settings', 'config_path': 'settings.points.enabled', 'default': False},
    'points_transfer_enabled': {'label': 'Allow Points Transfer', 'parent': 'points_settings', 'config_path': 'settings.points.transfer_enabled', 'default': False},
    'group_chat_points_settings': {'label': 'Group Chat Points Settings', 'parent': 'points_settings', 'type': 'selection', 'config_path': 'settings.points.group_chat.points_per_message', 'default': 1, 'options': {1: '1 Point', 2: '2 Points', 5: '5 Points', 10: '10 Points', 100: '100 Points'}, 'custom_value_key': 'group_chat_custom', 'custom_value_path': 'settings.points.group_chat.custom_points', 'extra_toggles': ['group_chat_points_enabled']},
    'group_chat_points_enabled': {'label': 'Enable Group Chat Points', 'parent': 'group_chat_points_settings', 'config_path': 'settings.points.group_chat.enabled', 'default': False},
    'checkin_settings': {'label': 'Check-in Settings', 'parent': 'points_checkin_settings', 'children': ['checkin_enabled', 'checkin_method_settings', 'checkin_points_settings', 'checkin_captcha_settings']},
    'checkin_enabled': {'label': 'Enable Check-in Functionality', 'parent': 'checkin_settings', 'config_path': 'settings.checkin.enabled', 'default': False},
    'checkin_method_settings': {'label': 'Check-in Method Settings', 'parent': 'checkin_settings', 'children': ['checkin_method_group_command', 'checkin_method_group_text', 'checkin_method_private_command', 'checkin_method_private_text']},
    'checkin_method_group_command': {'label': 'Group: /checkin command', 'parent': 'checkin_method_settings', 'config_path': 'settings.checkin.methods.group_command', 'default': True},
    'checkin_method_group_text': {'label': 'Group: "Check-in" text', 'parent': 'checkin_method_settings', 'config_path': 'settings.checkin.methods.group_text', 'default': True},
    'checkin_method_private_command': {'label': 'Private: /checkin command', 'parent': 'checkin_method_settings', 'config_path': 'settings.checkin.methods.private_command', 'default': True},
    'checkin_method_private_text': {'label': 'Private: "Check-in" text', 'parent': 'checkin_method_settings', 'config_path': 'settings.checkin.methods.private_text', 'default': True},
    'checkin_points_settings': {'label': 'Check-in Points Settings', 'parent': 'checkin_settings', 'type': 'selection', 'config_path': 'settings.checkin.points_per_checkin', 'default': 5, 'options': {1: '1 Point', 2: '2 Points', 5: '5 Points', 10: '10 Points', 100: '100 Points'}, 'custom_value_key': 'checkin_custom', 'custom_value_path': 'settings.checkin.custom_points'},
    'checkin_captcha_settings': {'label': 'Captcha Settings', 'parent': 'checkin_settings', 'children': ['checkin_captcha_group', 'checkin_captcha_private']},
    'checkin_captcha_group': {'label': 'Group Check-in Captcha', 'parent': 'checkin_captcha_settings', 'config_path': 'settings.checkin.captcha.group_enabled', 'default': False},
    'checkin_captcha_private': {'label': 'Private Chat Check-in Captcha', 'parent': 'checkin_captcha_settings', 'config_path': 'settings.checkin.captcha.private_enabled', 'default': False},
    'group_chat_points_custom_value_storage': {'config_path': 'settings.points.group_chat.custom_points', 'default': None},
    'checkin_points_custom_value_storage': {'config_path': 'settings.checkin.custom_points', 'default': None},
    'restart_bot': {'label': '🤖 Restart Bot', 'parent': 'system_settings'},
    'ip_api_selection': {'label': 'IP Geolocation API Settings', 'parent': 'system_settings', 'children': ['api_baidu', 'api_ip138', 'api_pconline', 'api_vore', 'api_ipapi']},
    'telegram_mode': {'label': 'Telegram Mode', 'parent': 'system_settings', 'children': ['mode_polling', 'mode_webhook']},
    'mode_polling': {'label': 'Long Polling', 'parent': 'telegram_mode', 'config_value': 'polling'},
    'mode_webhook': {'label': 'Webhook', 'parent': 'telegram_mode', 'config_value': 'webhook'},
    'api_baidu': {'label': 'Baidu API', 'parent': 'ip_api_selection', 'config_value': 'baidu'},
    'api_ip138': {'label': 'IP138 API (Token required)', 'parent': 'ip_api_selection', 'config_value': 'ip138'},
    'api_pconline': {'label': 'PConline API', 'parent': 'ip_api_selection', 'config_value': 'pconline'},
    'api_vore': {'label': 'Vore API', 'parent': 'ip_api_selection', 'config_value': 'vore'},
    'api_ipapi': {'label': 'IP-API.com', 'parent': 'ip_api_selection', 'config_value': 'ipapi'},
    'media_spec_settings': {'label': 'Media Spec Info Display Settings', 'parent': 'content_settings', 'children': ['video_spec_settings', 'audio_spec_settings', 'subtitle_spec_settings']},
    'video_spec_settings': {'label': 'Video Spec Display Settings', 'parent': 'media_spec_settings', 'children': ['video_show_codec', 'video_show_resolution', 'video_show_bitrate', 'video_framerate_settings', 'video_range_settings', 'video_bitdepth_settings']},
    'audio_spec_settings': {'label': 'Audio Spec Display Settings', 'parent': 'media_spec_settings', 'children': ['audio_show_language', 'audio_show_codec', 'audio_show_layout']},
    'subtitle_spec_settings': {'label': 'Subtitle Spec Display Settings', 'parent': 'media_spec_settings', 'children': ['subtitle_show_language', 'subtitle_show_codec']},
    'video_show_codec': {'label': 'Show Codec', 'parent': 'video_spec_settings', 'config_path': 'settings.content_settings.media_spec.video.show_codec', 'default': False},
    'video_show_resolution': {'label': 'Show Resolution', 'parent': 'video_spec_settings', 'config_path': 'settings.content_settings.media_spec.video.show_resolution', 'default': False},
    'video_show_bitrate': {'label': 'Show Bitrate', 'parent': 'video_spec_settings', 'config_path': 'settings.content_settings.media_spec.video.show_bitrate', 'default': False},
    'audio_show_language': {'label': 'Show Language', 'parent': 'audio_spec_settings', 'config_path': 'settings.content_settings.media_spec.audio.show_language', 'default': False},
    'audio_show_codec': {'label': 'Show Codec', 'parent': 'audio_spec_settings', 'config_path': 'settings.content_settings.media_spec.audio.show_codec', 'default': False},
    'audio_show_layout': {'label': 'Show Layout', 'parent': 'audio_spec_settings', 'config_path': 'settings.content_settings.media_spec.audio.show_layout', 'default': False},
    'subtitle_show_language': {'label': 'Show Language', 'parent': 'subtitle_spec_settings', 'config_path': 'settings.content_settings.media_spec.subtitle.show_language', 'default': False},
    'subtitle_show_codec': {'label': 'Show Codec', 'parent': 'subtitle_spec_settings', 'config_path': 'settings.content_settings.media_spec.subtitle.show_codec', 'default': False},
    'video_show_dolby_profile': {'label': 'Show Dolby Profile', 'parent': 'video_range_settings', 'config_path': 'settings.content_settings.media_spec.video.show_dolby_profile', 'default': False},
    'video_framerate_settings': {'label': 'Show Frame Rate', 'parent': 'video_spec_settings', 'type': 'selection', 'children': [], 'config_path': 'settings.content_settings.media_spec.video.show_framerate', 'default': 'none', 'options': {'none': 'Do not show', 'gt30': 'Show only if >30fps', 'always': 'Always show'}},
    'video_range_settings': {'label': 'Show Video Range', 'parent': 'video_spec_settings', 'type': 'selection', 'children': [], 'config_path': 'settings.content_settings.media_spec.video.show_range', 'default': 'always', 'options': {'none': 'Do not show', 'notsdr': 'Do not show for SDR', 'always': 'Always show'}, 'extra_toggles': ['video_show_dolby_profile']},
    'video_bitdepth_settings': {'label': 'Show Bit Depth', 'parent': 'video_spec_settings', 'type': 'selection', 'children': [], 'config_path': 'settings.content_settings.media_spec.video.show_bit_depth', 'default': 'none', 'options': {'none': 'Do not show', 'gt8': 'Show only if >8bit', 'always': 'Always show'}},
    'status_content_mode': {'label': 'Content Mode Switching', 'parent': 'status_feedback', 'type': 'selection', 'children': [], 'config_path': 'settings.content_settings.status_feedback.content_mode', 'default': 'multi_message', 'options': {'single_message': 'Single-message List Mode', 'multi_message': 'Multi-message Card Mode'}},
    'status_display_content_settings': {'label': 'Display Content Settings', 'parent': 'status_feedback', 'children': ['status_single_message_settings', 'status_multi_message_settings']},
    'status_single_message_settings': {'label': 'Single-message List Mode', 'parent': 'status_display_content_settings', 'children': ['status_single_show_user', 'status_single_show_player', 'status_single_show_device', 'status_single_show_location', 'status_single_show_media_no_link']},
    'status_multi_message_settings': {'label': 'Multi-message Card Mode', 'parent': 'status_display_content_settings', 'children': ['status_show_poster', 'status_show_player', 'status_show_device', 'status_show_location', 'status_show_media_detail', 'status_media_detail_has_tmdb_link', 'status_show_media_type', 'status_show_overview', 'status_show_progress', 'status_show_timestamp', 'status_show_view_on_server_button', 'status_show_terminate_session_button', 'status_show_send_message_button', 'status_show_broadcast_button', 'status_show_terminate_all_button']},
    'status_single_show_user': {'label': 'Show Username', 'parent': 'status_single_message_settings', 'config_path': 'settings.content_settings.status_feedback.single_mode.show_user', 'default': False},
    'status_single_show_player': {'label': 'Show Player', 'parent': 'status_single_message_settings', 'config_path': 'settings.content_settings.status_feedback.single_mode.show_player', 'default': False},
    'status_single_show_device': {'label': 'Show Device', 'parent': 'status_single_message_settings', 'config_path': 'settings.content_settings.status_feedback.single_mode.show_device', 'default': False},
    'status_single_show_location': {'label': 'Show Location Info', 'parent': 'status_single_message_settings', 'config_path': 'settings.content_settings.status_feedback.single_mode.show_location', 'default': False},
    'status_single_show_media_no_link': {'label': 'Show Media Details', 'parent': 'status_single_message_settings', 'config_path': 'settings.content_settings.status_feedback.single_mode.show_media_no_link', 'default': False}
}

SETTING_PATH_TO_FEATURE_KEY = {
    'settings.telegram_mode': 'setting_system_telegram_mode',
    'settings.ip_api_provider': 'setting_system_ip_geolocation',
    'settings.language': 'setting_system_language',
    'settings.notification_management.library_new.to_group': 'setting_notify_library_new',
    'settings.notification_management.library_new.to_channel': 'setting_notify_library_new',
    'settings.notification_management.library_new.to_private': 'setting_notify_library_new',
    'settings.notification_management.library_deleted': 'setting_notify_library_deleted',
    'settings.notification_management.playback_start': 'setting_notify_playback_start',
    'settings.notification_management.playback_pause': 'setting_notify_playback_pause',
    'settings.notification_management.playback_stop': 'setting_notify_playback_stop',
    'settings.notification_management.advanced.user_login_success': 'setting_notify_advanced_login_success',
    'settings.notification_management.advanced.user_login_failure': 'setting_notify_advanced_login_failure',
    'settings.notification_management.advanced.user_creation_deletion': 'setting_notify_advanced_user_management',
    'settings.notification_management.advanced.user_updates': 'setting_notify_advanced_user_updates',
    'settings.notification_management.advanced.server_restart_required': 'setting_notify_advanced_server_restart',
    'settings.auto_delete_settings.new_library.to_group': 'setting_autodelete_new_library',
    'settings.auto_delete_settings.new_library.to_channel': 'setting_autodelete_new_library',
    'settings.auto_delete_settings.new_library.to_private': 'setting_autodelete_new_library',
    'settings.auto_delete_settings.library_deleted': 'setting_autodelete_library_deleted',
    'settings.auto_delete_settings.playback_start': 'setting_autodelete_playback_start',
    'settings.auto_delete_settings.playback_pause': 'setting_autodelete_playback_pause',
    'settings.auto_delete_settings.playback_stop': 'setting_autodelete_playback_stop',
    'settings.auto_delete_settings.advanced.user_login': 'setting_autodelete_advanced_menu',
    'settings.auto_delete_settings.advanced.user_management': 'setting_autodelete_advanced_menu',
    'settings.auto_delete_settings.advanced.server_events': 'setting_autodelete_advanced_menu',
    'settings.content_settings.new_library_notification.show_poster': 'setting_content_new_library_show_poster',
    'settings.content_settings.new_library_notification.show_media_detail': 'setting_content_new_library_show_media_detail',
    'settings.content_settings.new_library_notification.media_detail_has_tmdb_link': 'setting_content_new_library_media_detail_has_tmdb_link',
    'settings.content_settings.new_library_notification.show_overview': 'setting_content_new_library_show_overview',
    'settings.content_settings.new_library_notification.show_media_type': 'setting_content_new_library_show_media_type',
    'settings.content_settings.new_library_notification.show_progress_status': 'setting_content_new_library_show_progress_status',
    'settings.content_settings.new_library_notification.show_timestamp': 'setting_content_new_library_show_timestamp',
    'settings.content_settings.new_library_notification.show_view_on_server_button': 'setting_content_new_library_show_view_on_server_button',
    'settings.content_settings.status_feedback.content_mode': 'setting_content_status_switch_mode',
    'settings.content_settings.status_feedback.show_poster': 'setting_content_status_show_poster',
    'settings.content_settings.status_feedback.show_player': 'setting_content_status_show_player',
    'settings.content_settings.status_feedback.show_device': 'setting_content_status_show_device',
    'settings.content_settings.status_feedback.show_location': 'setting_content_status_show_location',
    'settings.content_settings.status_feedback.show_media_detail': 'setting_content_status_show_media_detail',
    'settings.content_settings.status_feedback.media_detail_has_tmdb_link': 'setting_content_status_media_detail_has_tmdb_link',
    'settings.content_settings.status_feedback.show_media_type': 'setting_content_status_show_media_type',
    'settings.content_settings.status_feedback.show_overview': 'setting_content_status_show_overview',
    'settings.content_settings.status_feedback.show_progress': 'setting_content_status_show_progress',
    'settings.content_settings.status_feedback.show_timestamp': 'setting_content_status_show_timestamp',
    'settings.content_settings.status_feedback.show_view_on_server_button': 'setting_content_status_show_view_on_server_button',
    'settings.content_settings.status_feedback.show_terminate_session_button': 'setting_content_status_show_terminate_session_button',
    'settings.content_settings.status_feedback.show_send_message_button': 'setting_content_status_show_send_message_button',
    'settings.content_settings.status_feedback.show_broadcast_button': 'setting_content_status_show_broadcast_button',
    'settings.content_settings.status_feedback.show_terminate_all_button': 'setting_content_status_show_terminate_all_button',
    'settings.content_settings.playback_action.show_poster': 'setting_content_playback_show_poster',
    'settings.content_settings.playback_action.show_media_detail': 'setting_content_playback_show_media_detail',
    'settings.content_settings.playback_action.media_detail_has_tmdb_link': 'setting_content_playback_media_detail_has_tmdb_link',
    'settings.content_settings.playback_action.show_user': 'setting_content_playback_show_user',
    'settings.content_settings.playback_action.show_player': 'setting_content_playback_show_player',
    'settings.content_settings.playback_action.show_device': 'setting_content_playback_show_device',
    'settings.content_settings.playback_action.show_location': 'setting_content_playback_show_location',
    'settings.content_settings.playback_action.show_progress': 'setting_content_playback_show_progress',
    'settings.content_settings.playback_action.show_video_spec': 'setting_content_playback_show_video_spec',
    'settings.content_settings.playback_action.show_audio_spec': 'setting_content_playback_show_audio_spec',
    'settings.content_settings.playback_action.show_subtitle_spec': 'setting_content_playback_show_subtitle_spec',
    'settings.content_settings.playback_action.show_media_type': 'setting_content_playback_show_media_type',
    'settings.content_settings.playback_action.show_overview': 'setting_content_playback_show_overview',
    'settings.content_settings.playback_action.show_timestamp': 'setting_content_playback_show_timestamp',
    'settings.content_settings.playback_action.show_view_on_server_button': 'setting_content_playback_show_view_on_server_button',
    'settings.content_settings.library_deleted_notification.show_poster': 'setting_content_deleted_show_poster',
    'settings.content_settings.library_deleted_notification.show_media_detail': 'setting_content_deleted_show_media_detail',
    'settings.content_settings.library_deleted_notification.media_detail_has_tmdb_link': 'setting_content_deleted_media_detail_has_tmdb_link',
    'settings.content_settings.library_deleted_notification.show_overview': 'setting_content_deleted_show_overview',
    'settings.content_settings.library_deleted_notification.show_media_type': 'setting_content_deleted_show_media_type',
    'settings.content_settings.library_deleted_notification.show_timestamp': 'setting_content_deleted_show_timestamp',
    'settings.content_settings.search_display.show_media_type_in_list': 'setting_content_search_show_media_type_in_list',
    'settings.content_settings.search_display.movie.show_poster': 'setting_content_search_movie_show_poster',
    'settings.content_settings.search_display.series.update_progress.show_progress_status': 'setting_content_search_series_show_update_progress',
    'settings.content_settings.search_display.series.season_specs.show_video_spec': 'setting_content_search_series_show_season_specs',
    'settings.content_settings.media_spec.video.show_codec': 'setting_media_spec_video_show_codec',
    'settings.content_settings.media_spec.video.show_resolution': 'setting_media_spec_video_show_resolution',
    'settings.content_settings.media_spec.video.show_bitrate': 'setting_media_spec_video_show_bitrate',
    'settings.content_settings.media_spec.video.show_framerate': 'setting_media_spec_video_show_framerate',
    'settings.content_settings.media_spec.video.show_range': 'setting_media_spec_video_show_range',
    'settings.content_settings.media_spec.video.show_dolby_profile': 'setting_media_spec_video_show_dolby_profile',
    'settings.content_settings.media_spec.video.show_bit_depth': 'setting_media_spec_video_show_bit_depth',
    'settings.content_settings.media_spec.audio.show_language': 'setting_media_spec_audio_show_language',
    'settings.content_settings.media_spec.audio.show_codec': 'setting_media_spec_audio_show_codec',
    'settings.content_settings.media_spec.audio.show_layout': 'setting_media_spec_audio_show_layout',
    'settings.content_settings.media_spec.subtitle.show_language': 'setting_media_spec_subtitle_show_language',
    'settings.content_settings.media_spec.subtitle.show_codec': 'setting_media_spec_subtitle_show_codec',
    'settings.session_control.enabled': 'setting_session_control',
    'settings.session_control.max_sessions': 'setting_session_control'
}

def is_feature_active(feature_key: str) -> bool:
    return True

def build_toggle_maps():
    index = 0
    for key, node in SETTINGS_MENU_STRUCTURE.items():
        if 'config_path' in node and 'parent' in node:
            TOGGLE_INDEX_TO_KEY[index] = key
            TOGGLE_KEY_TO_INFO[key] = {
                'config_path': node['config_path'],
                'parent': node['parent']
            }
            SETTINGS_MENU_STRUCTURE[key]['index'] = index
            index += 1
    print(i18n._("⚙️ Settings menu key map built."))

def build_selection_maps():
    for key, node in SETTINGS_MENU_STRUCTURE.items():
        if node.get('type') == 'selection':
            SELECTION_KEY_TO_INFO[key] = {
                'config_path': node['config_path'],
                'parent': node['parent']
            }
    print(i18n._("⚙️ Settings selection menu key map built."))

def _build_default_settings():
    defaults = {}
    for node in SETTINGS_MENU_STRUCTURE.values():
        if 'config_path' in node:
            path = node['config_path']
            value = node['default']
            keys = path.split('.')
            d = defaults
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            d[keys[-1]] = value
    return defaults

def get_setting(path_str: str):
    try:
        return reduce(operator.getitem, path_str.split('.'), CONFIG)
    except (KeyError, TypeError):
        try:
            return reduce(operator.getitem, path_str.split('.'), DEFAULT_SETTINGS)
        except (KeyError, TypeError):
            print(i18n._("⚠️ WARNING: Key not found in user config or defaults: {path_str}").format(path_str=path_str))
            return None

def set_setting(path_str: str, value):
    keys = path_str.split('.')
    d = CONFIG
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value

def merge_configs(user_config: dict, default_config: dict) -> dict:
    if isinstance(user_config, dict) and isinstance(default_config, dict):
        merged = default_config.copy()
        for key, value in user_config.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = merge_configs(value, merged[key])
            else:
                merged[key] = value
        return merged
    return user_config

def load_config():
    global CONFIG
    print(i18n._("📝 Attempting to load configuration file: {config_path}").format(config_path=CONFIG_PATH))
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            user_config = yaml.safe_load(f) or {}
        CONFIG = merge_configs(user_config, DEFAULT_SETTINGS)
        print(i18n._("✅ Configuration loaded successfully."))
    except FileNotFoundError:
        print(i18n._("⚠️ WARNING: Configuration file {config_path} not found. Using built-in defaults.").format(config_path=CONFIG_PATH))
        CONFIG = DEFAULT_SETTINGS
    except Exception as e:
        print(i18n._("❌ ERROR: Failed to read or parse configuration file: {error}").format(error=e))
        exit(1)

def save_config():
    print(i18n._("💾 Attempting to save configuration file: {config_path}").format(config_path=CONFIG_PATH))
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(CONFIG, f, allow_unicode=True, sort_keys=False)
        print(i18n._("✅ Configuration saved successfully."))
    except Exception as e:
        print(i18n._("❌ Failed to save configuration: {error}").format(error=e))

TELEGRAM_TOKEN = None
ADMIN_USER_ID = None
GROUP_ID = None
CHANNEL_ID = None
ALLOWED_GROUP_ID = None
TMDB_API_TOKEN = None
TIMEZONE_STR = None
TIMEZONE = None
PLAYBACK_DEBOUNCE_SECONDS = None
MEDIA_BASE_PATH = None
MEDIA_CLOUD_PATH = None
POSTER_CACHE_TTL_DAYS = None
EMBY_SERVER_URL = None
EMBY_API_KEY = None
EMBY_USER_ID = None
EMBY_USERNAME = None
EMBY_PASSWORD = None
EMBY_REMOTE_URL = None
APP_SCHEME = None
EMBY_TEMPLATE_USER_ID = None
BOT_NAME = None
CUSTOMER_SERVICE_ID = None

DEFAULT_SETTINGS = _build_default_settings()
build_toggle_maps()
build_selection_maps()
load_config()

g = globals()

def _ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    
    str_value = str(value)
    return [item.strip() for item in str_value.split(',') if item.strip()]

g['TELEGRAM_TOKEN'] = CONFIG.get('telegram', {}).get('token')
g['ADMIN_USER_ID'] = _ensure_list(CONFIG.get('telegram', {}).get('admin_user_id'))
g['GROUP_ID'] = _ensure_list(CONFIG.get('telegram', {}).get('group_id'))
g['CHANNEL_ID'] = _ensure_list(CONFIG.get('telegram', {}).get('channel_id'))
g['ALLOWED_GROUP_ID'] = g['GROUP_ID'][0] if g['GROUP_ID'] else None

g['TMDB_API_TOKEN'] = CONFIG.get('tmdb', {}).get('api_token')
g['TIMEZONE_STR'] = get_setting('settings.timezone') or 'UTC'

try:
    g['TIMEZONE'] = ZoneInfo(g['TIMEZONE_STR'])
except Exception as e:
    print(i18n._("⚠️ WARNING: Could not parse timezone '{tz_str}', falling back to UTC. Error: {error}").format(tz_str=g['TIMEZONE_STR'], error=e))
    g['TIMEZONE_STR'] = 'UTC'
    g['TIMEZONE'] = ZoneInfo('UTC')

g['PLAYBACK_DEBOUNCE_SECONDS'] = get_setting('settings.debounce_seconds') or 10
g['MEDIA_BASE_PATH'] = get_setting('settings.media_base_path')
g['MEDIA_CLOUD_PATH'] = get_setting('settings.media_cloud_path')
g['POSTER_CACHE_TTL_DAYS'] = get_setting('settings.poster_cache_ttl_days') or 30

g['EMBY_SERVER_URL'] = CONFIG.get('emby', {}).get('server_url')
g['EMBY_API_KEY'] = CONFIG.get('emby', {}).get('api_key')
g['EMBY_USER_ID'] = CONFIG.get('emby', {}).get('user_id')
g['EMBY_USERNAME'] = CONFIG.get('emby', {}).get('username')
g['EMBY_PASSWORD'] = CONFIG.get('emby', {}).get('password')
g['EMBY_REMOTE_URL'] = CONFIG.get('emby', {}).get('remote_url')
g['APP_SCHEME'] = CONFIG.get('emby', {}).get('app_scheme')
g['EMBY_TEMPLATE_USER_ID'] = CONFIG.get('emby', {}).get('template_user_id')
g['BOT_NAME'] = get_setting('settings.bot_name') or 'EmbyBot'
g['CUSTOMER_SERVICE_ID'] = CONFIG.get('telegram', {}).get('customer_service_id')
g['TELEGRAM_WEBHOOK_URL'] = CONFIG.get('telegram', {}).get('webhook_url')
g['TELEGRAM_MODE'] = get_setting('settings.telegram_mode') or 'polling'

if not g['TELEGRAM_TOKEN'] or not g['ADMIN_USER_ID']:
    print(i18n._("❌ ERROR: TELEGRAM_TOKEN or ADMIN_USER_ID is not set correctly in config.yaml"))
    exit(1)
if not g['EMBY_TEMPLATE_USER_ID']:
    print(i18n._("⚠️ WARNING: 'template_user_id' is not configured in config.yaml, user creation will be unavailable."))

print(i18n._("🚀 Configuration initialization complete."))
