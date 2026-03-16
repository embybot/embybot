# -*- coding: utf-8 -*-

import os
import json
import time
from typing import Dict, Any

from .. import i18n
from ..core.config import CACHE_DIR, STATIC_DIR

POSTER_CACHE: Dict[str, Dict[str, str]] = {}
LANG_MAP: Dict[str, Dict[str, str]] = {}
ADMIN_CACHE: Dict[int, Dict[str, Any]] = {}
GROUP_MEMBER_CACHE: Dict[int, Dict[str, Any]] = {}
SEARCH_RESULTS_CACHE: Dict[str, dict] = {}
DELETION_TASK_CACHE: Dict[str, Any] = {}
recent_playback_notifications: Dict[tuple, float] = {}
user_context: Dict[int, Dict[str, Any]] = {}
user_search_state: Dict[int, int] = {}
UPDATE_PATH_CACHE: Dict[str, str] = {}
EMBY_USERS_CACHE: Dict[str, Any] = {}
TMDB_EMBY_ID_MAP: Dict[str, Dict[str, Any]] = {}
PAGINATED_MESSAGE_CACHE: Dict[str, Any] = {}
POLICY_SESSIONS_CACHE: Dict[str, Any] = {}
SESSION_ENFORCEMENT_LOCK = set()

def _load_poster_cache(path: str):

    global POSTER_CACHE
    print(i18n._("🖼️ Attempting to load poster cache: {path}").format(path=path))
    if not os.path.exists(path):
        POSTER_CACHE = {}
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            POSTER_CACHE = json.load(f)
        print(i18n._("✅ Poster cache loaded successfully."))
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(i18n._("❌ Failed to load poster cache: {error}, will use an empty cache.").format(error=e))
        POSTER_CACHE = {}

def save_poster_cache():
    path = os.path.join(CACHE_DIR, 'poster_cache.json')
    print(i18n._("💾 Attempting to save poster cache: {path}").format(path=path))
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(POSTER_CACHE, f, indent=4)
        print(i18n._("✅ Poster cache saved successfully."))
    except Exception as e:
        print(i18n._("❌ Failed to save poster cache: {error}").format(error=e))

def _load_id_map(path: str):
    global TMDB_EMBY_ID_MAP
    print(i18n._("🗺️ Attempting to load ID map cache: {path}").format(path=path))
    if not os.path.exists(path):
        TMDB_EMBY_ID_MAP = {}
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            TMDB_EMBY_ID_MAP = json.load(f)
        print(i18n._("✅ ID map cache loaded successfully."))
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(i18n._("❌ Failed to load ID map cache: {error}, will use an empty cache.").format(error=e))
        TMDB_EMBY_ID_MAP = {}

def update_and_save_id_map(tmdb_id: str, emby_id: str, item_type: str):
    if tmdb_id not in TMDB_EMBY_ID_MAP or TMDB_EMBY_ID_MAP[tmdb_id].get('emby_id') != emby_id:
        print(i18n._("✨ Updating ID map cache: TMDB {tmdb_id} -> Emby {emby_id}").format(tmdb_id=tmdb_id, emby_id=emby_id))
        TMDB_EMBY_ID_MAP[tmdb_id] = {
            'emby_id': emby_id,
            'type': item_type,
            'timestamp': time.time()
        }
        save_id_map()

def save_id_map():
    path = os.path.join(CACHE_DIR, 'id_map.json')
    print(i18n._("💾 Attempting to save ID map cache: {path}").format(path=path))
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(TMDB_EMBY_ID_MAP, f, indent=4)
        print(i18n._("✅ ID map cache saved successfully."))
        with open(path, 'r', encoding='utf-8') as f_verify:
            content = f_verify.read()
    except Exception as e:
        print(i18n._("❌ Failed to save ID map cache: {error}").format(error=e))

def _load_language_map(path: str):
    global LANG_MAP
    fallback_map = {
        'eng': {'en': 'English', 'zh': i18n._('English')}, 'jpn': {'en': 'Japanese', 'zh': i18n._('Japanese')},
        'chi': {'en': 'Chinese', 'zh': i18n._('Chinese')}, 'zho': {'en': 'Chinese', 'zh': i18n._('Chinese')},
        'kor': {'en': 'Korean', 'zh': i18n._('Korean')}, 'und': {'en': 'Undetermined', 'zh': i18n._('Undetermined')},
        'mis': {'en': 'Multiple languages', 'zh': i18n._('Multiple languages')}
    }
    print(i18n._("🌍 Attempting to load language configuration file: {path}").format(path=path))
    LANG_MAP.clear()
    if not os.path.exists(path):
        print(i18n._("⚠️ Language configuration file {path} not found...").format(path=path))
        LANG_MAP.update(fallback_map)
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
            LANG_MAP.update(loaded_data)
        print(i18n._("✅ Language configuration file loaded successfully."))
    except Exception as e:
        print(i18n._("❌ Failed to load language configuration file: {error}...").format(error=e))
        LANG_MAP.update(fallback_map)

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
poster_cache_path = os.path.join(CACHE_DIR, 'poster_cache.json')
lang_map_path = os.path.join(STATIC_DIR, 'language_map.json')
id_map_path = os.path.join(CACHE_DIR, 'id_map.json')

_load_poster_cache(poster_cache_path)
_load_language_map(lang_map_path)
_load_id_map(id_map_path)