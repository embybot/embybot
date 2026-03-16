# -*- coding: utf-8 -*-

from .. import i18n
from ..core.config import SUPPORTED_LANGUAGES, get_setting, is_feature_active
from ..core.cache import LANG_MAP
from ..utils import helpers

def format_stream_details_message(stream_details: dict, is_season_info: bool = False, prefix: str = 'movie') -> list[str]:
    if not is_feature_active('setting_menu_media_spec'):
        return []

    if not stream_details:
        return []

    from ..core.config import SUPPORTED_LANGUAGES, get_setting
    
    current_lang = get_setting('settings.language') or 'en'
    message_parts = []
    indent = "    " if is_season_info else ""
    
    video_info = stream_details.get('video_info')
    if video_info:
        parts = []

        if get_setting('settings.content_settings.media_spec.video.show_codec'):
            parts.append(video_info.get('title'))

        if get_setting('settings.content_settings.media_spec.video.show_resolution') and video_info.get('resolution') != '0x0':
            parts.append(video_info.get('resolution'))

        if get_setting('settings.content_settings.media_spec.video.show_bitrate') and video_info.get('bitrate') and video_info.get('bitrate') != 'unknown':
            parts.append(f"{video_info.get('bitrate')}Mbps")

        show_framerate_mode = get_setting('settings.content_settings.media_spec.video.show_framerate')
        framerate = video_info.get('framerate')
        if framerate and (show_framerate_mode == 'always' or (show_framerate_mode == 'gt30' and framerate > 30)):
            parts.append(f"{round(framerate)}fps")

        show_bit_depth_mode = get_setting('settings.content_settings.media_spec.video.show_bit_depth')
        bit_depth = video_info.get('bit_depth')
        if bit_depth and (show_bit_depth_mode == 'always' or (show_bit_depth_mode == 'gt8' and bit_depth > 8)):
            parts.append(f"{bit_depth}bit")

        show_range_mode = get_setting('settings.content_settings.media_spec.video.show_range')
        video_range = video_info.get('video_range')
        if video_range:

            if show_range_mode == 'always' or (show_range_mode == 'notsdr' and video_range.upper() != 'SDR'):
                profile_str = ""
                if get_setting('settings.content_settings.media_spec.video.show_dolby_profile') and 'DOLBY' in video_range.upper():
                    dv_profile = video_info.get('dv_profile_desc')
                    if dv_profile:
                        profile_str = f" ({dv_profile})"
                parts.append(f"{video_range}{profile_str}")

        if parts:
            video_line = helpers.escape_html(' '.join(filter(None, parts)))
            label = i18n._("Video Specs: ") if prefix in ['new_library_notification', 'playback_action'] else i18n._("Video: ")
            message_parts.append(f"{indent}{label}{video_line}")

    audio_info_list = stream_details.get('audio_info')
    if audio_info_list:
        audio_lines, seen_tracks = [], set()
        for a_info in audio_info_list:
            audio_parts = []

            if get_setting('settings.content_settings.media_spec.audio.show_language'):
                lang_code = a_info.get('language', 'und').strip().lower()
                if lang_code != 'und':
                    lang_info = LANG_MAP.get(lang_code)
                    if lang_info and current_lang in lang_info:
                        lang_display = lang_info[current_lang]
                        if lang_display != i18n._('Unknown'): audio_parts.append(lang_display)
                    else: audio_parts.append(lang_code.capitalize())

            if get_setting('settings.content_settings.media_spec.audio.show_codec'):
                codec = a_info.get('codec', '').upper()
                if codec and codec != 'UNKNOWN': audio_parts.append(codec)
            # Layout
            if get_setting('settings.content_settings.media_spec.audio.show_layout'):
                layout = a_info.get('layout', '')
                if layout: audio_parts.append(layout)
            
            if audio_parts:
                track_str = ' '.join(audio_parts)
                if track_str not in seen_tracks:
                    audio_lines.append(track_str)
                    seen_tracks.add(track_str)
        if audio_lines:
            label = i18n._("Audio Specs: ") if prefix in ['new_library_notification', 'playback_action'] else i18n._("Audio: ")
            message_parts.append(f"{indent}{label}{helpers.escape_html(', '.join(audio_lines))}")
    
    subtitle_info_list = stream_details.get('subtitle_info')
    if subtitle_info_list:
        # A comprehensive map to translate various media language codes to the bot's internal language keys
        media_lang_to_bot_lang = {
            'eng': 'en', 'en': 'en',
            'chi': 'zh_hans', 'zho': 'zh_hans', 'zh-hans': 'zh_hans', 'zh-cn': 'zh_hans', 'zh': 'zh_hans', '简体': 'zh_hans',
            'zh-hant': 'zh_hant', 'zh-tw': 'zh_hant', 'zh-hk': 'zh_hant', '繁體': 'zh_hant',
            'jpn': 'ja', 'ja': 'ja',
            'kor': 'ko', 'ko': 'ko',
            'ger': 'de', 'deu': 'de', 'de': 'de',
            'fre': 'fr', 'fra': 'fr', 'fr': 'fr',
            'spa': 'es', 'es': 'es',
            'ita': 'it', 'it': 'it',
            'rus': 'ru', 'ru': 'ru',
            'por': 'pt', 'pt': 'pt',
        }
        bot_supported_langs = set(SUPPORTED_LANGUAGES.keys())

        def sort_key(subtitle_info):
            media_lang_code = subtitle_info.get('language', 'und').lower()
            bot_lang_code = media_lang_to_bot_lang.get(media_lang_code)
            if bot_lang_code:
                if bot_lang_code == current_lang: return (0, media_lang_code)
                if bot_lang_code in bot_supported_langs: return (1, media_lang_code)
            return (2, media_lang_code)

        sorted_subtitles = sorted(subtitle_info_list, key=sort_key)
        
        subtitle_lines, seen_tracks = [], set()
        total_sub_count = len(sorted_subtitles)
        max_display_subs = 5

        for s_info in sorted_subtitles[:max_display_subs]:
            sub_parts = []

            if get_setting('settings.content_settings.media_spec.subtitle.show_language'):
                lang_code = s_info.get('language', 'und').lower()
                if lang_code != 'und':
                    lang_info = LANG_MAP.get(lang_code)
                    if lang_info and current_lang in lang_info:
                        lang_display = lang_info[current_lang]
                        if lang_display != i18n._('Unknown'): sub_parts.append(lang_display)
                    else: sub_parts.append(lang_code.capitalize())

            if get_setting('settings.content_settings.media_spec.subtitle.show_codec'):
                codec = s_info.get('codec')
                if codec: sub_parts.append(codec)
            
            if sub_parts:
                track_str = ' '.join(sub_parts)
                if track_str not in seen_tracks:
                    subtitle_lines.append(track_str)
                    seen_tracks.add(track_str)

        if subtitle_lines:
            full_subtitle_str = ", ".join(subtitle_lines)
            if total_sub_count > max_display_subs:
                full_subtitle_str += i18n._(" and {count} more...").format(count=total_sub_count - max_display_subs)
            
            label = i18n._("Subtitle Specs: ") if prefix in ['new_library_notification', 'playback_action'] else i18n._("Subtitles: ")
            message_parts.append(f"{indent}{label}{helpers.escape_html(full_subtitle_str)}")

    return message_parts