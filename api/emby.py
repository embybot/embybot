# -*- coding: utf-8 -*-

import requests
import json
import time

from .. import i18n
from ..core import config
from ..core import cache
from ..core.cache import EMBY_USERS_CACHE
from .base_client import make_request_with_retry
from ..utils import formatters
from ..utils import helpers
from ..utils.formatters import format_stream_details_message

def get_emby_access_token() -> str or None:
    print(i18n._("🔑 Getting Emby Access Token using username/password..."))
    if not all([config.EMBY_SERVER_URL, config.EMBY_USERNAME, config.EMBY_PASSWORD]):
        print(i18n._("❌ Missing Emby username or password configuration required to get a token."))
        return None

    url = f"{config.EMBY_SERVER_URL}/Users/AuthenticateByName"
    headers = {
        'Content-Type': 'application/json',
        'X-Emby-Authorization': 'MediaBrowser Client="Telegram Bot", Device="Script", DeviceId="emby-telegram-bot-backend", Version="1.0.0"'
    }
    payload = {'Username': config.EMBY_USERNAME, 'Pw': config.EMBY_PASSWORD}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            token = response.json().get('AccessToken')
            print(i18n._("✅ Successfully obtained Access Token."))
            return token
        else:
            print(i18n._("❌ Failed to get Access Token. Status Code: {code}, Response: {text}").format(code=response.status_code, text=response.text))
            return None
    except requests.exceptions.RequestException as e:
        print(i18n._("❌ Network error while getting Access Token: {error}").format(error=e))
        return None


def scan_emby_item(item_id: str, item_name: str) -> str:
    print(i18n._("🔎 Requesting scan for Emby item ID: {id}, Name: {name}").format(id=item_id, name=item_name))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        return i18n._("❌ Scan failed: Emby server configuration is incomplete.")

    url = f"{config.EMBY_SERVER_URL}/Items/{item_id}/Refresh"
    params = {
        'api_key': config.EMBY_API_KEY,
        'Recursive': 'true',
        'ReplaceAllMetadata': 'false'
    }

    response = make_request_with_retry('POST', url, params=params, timeout=30)

    if response and response.status_code == 204:
        success_msg = i18n._("✅ Scan request sent to Emby for: \"{item_name}\". The process will run in the background. Please check the results in Emby later.").format(item_name=item_name)
        print(success_msg)
        return success_msg
    else:
        status_code = response.status_code if response else 'N/A'
        response_text = response.text if response else 'No Response'
        error_msg = i18n._('❌ Failed to send scan request for \"{item_name}\" (ID: {item_id}). Status Code: {code}, Server Response: {text}').format(
            item_name=item_name, item_id=item_id, code=status_code, text=response_text
        )
        print(error_msg)
        return error_msg


def scan_all_emby_libraries() -> str:
    print(i18n._("🔎 Requesting to scan all Emby libraries..."))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        return i18n._("❌ Scan failed: Emby server configuration is incomplete.")

    url = f"{config.EMBY_SERVER_URL}/Library/Refresh"
    params = {'api_key': config.EMBY_API_KEY}

    response = make_request_with_retry('POST', url, params=params, timeout=30)

    if response and response.status_code == 204:
        success_msg = i18n._("✅ Request to scan all libraries has been sent to Emby. The task will be executed in the background.")
        print(success_msg)
        return success_msg
    else:
        status_code = response.status_code if response else 'N/A'
        response_text = response.text if response else 'No Response'
        error_msg = i18n._('❌ Failed to send \"Scan All\" request. Status Code: {code}, Response: {text}').format(
            code=status_code, text=response_text
        )
        print(error_msg)
        return error_msg


def refresh_emby_item(item_id: str, item_name: str) -> str:
    print(i18n._("🔄 Requesting refresh for Emby item ID: {id}, Name: {name}").format(id=item_id, name=item_name))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        return i18n._("❌ Refresh failed: Emby server configuration is incomplete.")

    url = f"{config.EMBY_SERVER_URL}/Items/{item_id}/Refresh"
    params = {
        'api_key': config.EMBY_API_KEY,
        'Recursive': 'true',
        'MetadataRefreshMode': 'FullRefresh',
        'ReplaceAllMetadata': 'true'
    }

    response = make_request_with_retry('POST', url, params=params, timeout=30)

    if response and response.status_code == 204:
        success_msg = i18n._("✅ Refresh request sent to Emby for: \"{item_name}\". The process will run in the background. Please check the results in Emby later.").format(item_name=item_name)
        print(success_msg)
        return success_msg
    else:
        status_code = response.status_code if response else 'N/A'
        response_text = response.text if response else 'No Response'
        error_msg = i18n._('❌ Failed to send refresh request for \"{item_name}\" (ID: {item_id}). Status Code: {code}, Server Response: {text}').format(
            item_name=item_name, item_id=item_id, code=status_code, text=response_text
        )
        print(error_msg)
        return error_msg

def delete_emby_item(item_id: str, item_name: str) -> str:
    print(i18n._("🗑️ Requesting to delete item from Emby. ID: {id}, Name: {name}").format(id=item_id, name=item_name))

    access_token = get_emby_access_token()
    if not access_token:
        return i18n._("❌ Failed to delete \"{item_name}\": Could not obtain a valid user Access Token from the Emby server. Please check the username and password in config.yaml.").format(item_name=item_name)

    url = f"{config.EMBY_SERVER_URL}/Items/{item_id}"

    auth_header_value = (
        f'MediaBrowser Client="MyBot", Device="MyServer", '
        f'DeviceId="emby-telegram-bot-abc123", Version="1.0.0", Token="{access_token}"'
    )
    headers = {
        'X-Emby-Authorization': auth_header_value
    }

    response = make_request_with_retry('DELETE', url, headers=headers, timeout=15)

    if response and response.status_code == 204:
        success_msg = i18n._("✅ The item \"{item_name}\" has been successfully deleted from the Emby library.").format(item_name=item_name)
        print(success_msg)
        return success_msg
    else:
        status_code = response.status_code if response else 'N/A'
        response_text = response.text if response else 'No Response'
        error_msg = i18n._('❌ Failed to delete Emby item \"{item_name}\" (ID: {item_id}). Status Code: {code}, Server Response: {text}').format(
            item_name=item_name, item_id=item_id, code=status_code, text=response_text
        )
        print(error_msg)
        return error_msg


def get_emby_user_by_name(username: str) -> (dict or None, str or None):
    print(i18n._("👤 Querying Emby user by username '{username}'...").format(username=username))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        return None, i18n._("❌ Emby server configuration is incomplete.")

    url = f"{config.EMBY_SERVER_URL}/Users"
    params = {'api_key': config.EMBY_API_KEY}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        try:
            users_data = response.json()
            for user in users_data:
                if user.get('Name', '').lower() == username.lower():
                    print(i18n._("✅ Found user '{username}', ID: {id}").format(username=username, id=user.get('Id')))
                    return user, None
            return None, i18n._("⚠️ Could not find a user with the username '{username}'.").format(username=username)
        except json.JSONDecodeError:
            return None, i18n._("⚠️ Could not parse the user list response from Emby.")
    else:
        return None, i18n._("❌ Failed to get user list from Emby API.")

def get_emby_user_by_id(user_id: str) -> (dict or None, str or None):
    print(i18n._("👤 Querying Emby user by ID '{id}'...").format(id=user_id))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        return None, i18n._("❌ Emby server configuration is incomplete.")

    url = f"{config.EMBY_SERVER_URL}/Users/{user_id}"
    params = {'api_key': config.EMBY_API_KEY}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        try:
            user_data = response.json()
            if user_data and user_data.get('Id') == user_id:
                 print(i18n._("✅ Found user by ID, Name: {name}").format(name=user_data.get('Name')))
                 return user_data, None
            return None, i18n._("⚠️ Could not find a user with the ID '{id}'.").format(id=user_id)
        except json.JSONDecodeError:
            return None, i18n._("⚠️ Could not parse the user response from Emby.")
    else:
        return None, i18n._("❌ Failed to get user from Emby API.")

def get_emby_user_policy(user_id: str) -> (dict or None, str or None):
    print(i18n._("📜 Getting full info for User ID {id} to extract policy...").format(id=user_id))

    url = f"{config.EMBY_SERVER_URL}/Users/{user_id}"
    params = {'api_key': config.EMBY_API_KEY}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        try:
            user_data = response.json()
            policy = user_data.get('Policy')
            if policy:
                return policy, None
            else:
                return None, i18n._("⚠️ Failed to get user policy: Policy object not found in the returned user data.")
        except json.JSONDecodeError:
            return None, i18n._("⚠️ Failed to get user policy: Could not parse user information returned by the Emby server.")

    return None, i18n._("⚠️ Failed to get user policy: Could not get user information from the Emby API (please check if template_user_id is correct).")

def set_emby_user_password(user_id: str, password: str) -> bool:
    print(i18n._("🔑 Setting a new password for User ID {id}...").format(id=user_id))
    url = f"{config.EMBY_SERVER_URL}/Users/{user_id}/Password"
    headers = {'X-Emby-Token': config.EMBY_API_KEY}
    payload = {"Id": user_id, "NewPw": password}
    response = make_request_with_retry('POST', url, headers=headers, json=payload, timeout=10)
    return response is not None and 200 <= response.status_code < 300


def delete_emby_user_by_id(user_id: str) -> bool:
    print(i18n._("🗑️ Deleting User ID: {id}").format(id=user_id))
    url = f"{config.EMBY_SERVER_URL}/Users/{user_id}"
    params = {'api_key': config.EMBY_API_KEY}
    response = make_request_with_retry('DELETE', url, params=params, timeout=10)
    return response is not None and 200 <= response.status_code < 300

def rename_emby_user(user_id: str, new_username: str) -> str:
    print(i18n._("✏️ Renaming username for User ID {id} to '{new_username}'...").format(id=user_id, new_username=new_username))

    existing_user, _error = get_emby_user_by_name(new_username)
    if existing_user:
        return i18n._("❌ Rename failed: Username '{new_username}' is already taken by another user.").format(new_username=new_username)

    get_url = f"{config.EMBY_SERVER_URL}/Users/{user_id}"
    params = {'api_key': config.EMBY_API_KEY}
    get_response = make_request_with_retry('GET', get_url, params=params, timeout=10)
    if not get_response:
        return i18n._("❌ Rename failed: Could not get the details of the current user.")

    try:
        user_data = get_response.json()
    except json.JSONDecodeError:
        return i18n._("❌ Rename failed: Could not parse the current user's information.")

    user_data['Name'] = new_username
    post_url = f"{config.EMBY_SERVER_URL}/Users/{user_id}"
    headers = {'X-Emby-Token': config.EMBY_API_KEY, 'Content-Type': 'application/json'}
    post_response = make_request_with_retry('POST', post_url, headers=headers, json=user_data, timeout=10)

    if post_response and 200 <= post_response.status_code < 300:
        return i18n._("✅ Username has been successfully changed to '{new_username}'.").format(new_username=new_username)
    else:
        return i18n._("❌ Failed to rename username, the server did not respond successfully.")


def create_emby_user(username: str, password: str) -> str:
    if not config.EMBY_TEMPLATE_USER_ID:
        return i18n._("❌ Creation failed: `template_user_id` is not set in the configuration file.")

    existing_user, error_msg = get_emby_user_by_name(username)
    if existing_user:
        return i18n._("❌ Creation failed: Username '{username}' already exists.").format(username=username)

    template_policy, error = get_emby_user_policy(config.EMBY_TEMPLATE_USER_ID)
    if error:
        return i18n._("❌ Creation failed: Could not get the configuration of the template user (ID: {id}). Error: {error}").format(id=config.EMBY_TEMPLATE_USER_ID, error=error)
    print(i18n._("✅ Successfully got the policy for template user {id}.").format(id=config.EMBY_TEMPLATE_USER_ID))

    print(i18n._("➕ Creating new user: {username}...").format(username=username))
    create_url = f"{config.EMBY_SERVER_URL}/Users/New"
    params = {'api_key': config.EMBY_API_KEY}
    create_payload = {'Name': username}
    response = make_request_with_retry('POST', create_url, params=params, json=create_payload, timeout=10)

    if not response:
        return i18n._("❌ Creation failed: Error when calling the Emby API to create a user.")

    try:
        new_user = response.json()
        new_user_id = new_user.get('Id')
        print(i18n._("✅ User '{username}' has been created, new ID: {id}.").format(username=username, id=new_user_id))
    except (json.JSONDecodeError, AttributeError):
        return i18n._("❌ Creation failed: Could not parse new user information from the Emby API response.")

    print(i18n._("📜 Applying template policy for new user {id}...").format(id=new_user_id))
    policy_url = f"{config.EMBY_SERVER_URL}/Users/{new_user_id}/Policy"
    policy_resp = make_request_with_retry('POST', policy_url, params=params, json=template_policy, timeout=10)
    if not policy_resp:
        delete_emby_user_by_id(new_user_id)
        return i18n._("❌ Creation failed: Error applying policy for the new user, operation has been rolled back.")
    print(i18n._("✅ Policy applied successfully."))

    print(i18n._("🔑 Setting password for new user {id}...").format(id=new_user_id))
    if not set_emby_user_password(new_user_id, password):
        delete_emby_user_by_id(new_user_id)
        return i18n._("❌ Creation failed: Error setting password for the new user, operation has been rolled back.")
    print(i18n._("✅ Password set successfully."))

    return i18n._("✅ Successfully created user '{username}', configuration cloned from template user.").format(username=username)


def get_all_emby_users() -> set:
    now = time.time()

    if 'users' in cache.EMBY_USERS_CACHE and (now - cache.EMBY_USERS_CACHE.get('timestamp', 0) < 60):
        print(i18n._("ℹ️ Getting Emby user list from cache."))
        return cache.EMBY_USERS_CACHE['users']

    print(i18n._("🔑 Getting full user list from Emby API..."))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        print(i18n._("❌ Missing Emby server configuration required to get user list."))
        return set()

    url = f"{config.EMBY_SERVER_URL}/Users"
    params = {'api_key': config.EMBY_API_KEY}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        try:
            users_data = response.json()
            user_names = {user['Name'] for user in users_data if 'Name' in user}

            cache.EMBY_USERS_CACHE['users'] = user_names
            cache.EMBY_USERS_CACHE['timestamp'] = now
            print(i18n._("✅ Successfully retrieved and cached {count} usernames.").format(count=len(user_names)))
            return user_names
        except (json.JSONDecodeError, TypeError):
            print(i18n._("❌ Failed to get Emby user list: Could not parse server response."))
            return set()
    else:
        print(i18n._("❌ Failed to get Emby user list."))
        return set()

def get_active_sessions() -> list:
    print(i18n._("ℹ️ Querying active Emby sessions."))
    if not config.EMBY_SERVER_URL or not config.EMBY_API_KEY:
        print(i18n._("❌ Missing Emby server configuration, cannot query sessions."))
        return []

    url = f"{config.EMBY_SERVER_URL}/Sessions"
    params = {'api_key': config.EMBY_API_KEY, 'activeWithinSeconds': 10}
    response = make_request_with_retry('GET', url, params=params, timeout=15)

    if response:
        try:
            sessions = response.json()
            print(i18n._("✅ Found {count} active sessions.").format(count=len(sessions)))
            return sessions
        except json.JSONDecodeError:
            print(i18n._("❌ Failed to query active sessions: Could not parse server response."))
            return []
    return []


def terminate_emby_session(session_id: str) -> bool:
    print(i18n._("🛑 Attempting to stop playback session: {id}").format(id=session_id))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, session_id]):
        print(i18n._("❌ Failed to stop session: Emby server configuration is incomplete."))
        return False

    url = f"{config.EMBY_SERVER_URL}/Sessions/{session_id}/Playing/Stop"
    params = {'api_key': config.EMBY_API_KEY}
    response = make_request_with_retry('POST', url, params=params, timeout=10)

    if response and 200 <= response.status_code < 300:
        print(i18n._("✅ Playback session {id} has been stopped successfully.").format(id=session_id))
        return True
    else:
        print(i18n._("❌ Failed to stop playback session {id}.").format(id=session_id))
        return False


def send_message_to_emby_session(session_id: str, message: str) -> bool:
    print(i18n._("✉️ Sending message to session {id}.").format(id=session_id))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, session_id]):
        print(i18n._("❌ Failed to send message: Emby server configuration is incomplete."))
        return False

    url = f"{config.EMBY_SERVER_URL}/Sessions/{session_id}/Message"
    params = {'api_key': config.EMBY_API_KEY}
    header = i18n._("ℹ️ Message from administrator")
    payload = {"Text": message, "Header": header, "TimeoutMs": 15000}
    response = make_request_with_retry('POST', url, params=params, json=payload, timeout=10)

    if response and 200 <= response.status_code < 300:
        print(i18n._("✅ Message sent successfully to session {id}.").format(id=session_id))
        return True
    else:
        print(i18n._("❌ Failed to send message to session {id}.").format(id=session_id))
        return False


def get_resolution_for_item(item_id: str, user_id: str = None) -> str:
    print(i18n._("ℹ️ Getting resolution for item {id}.").format(id=item_id))
    request_user_id = user_id or config.EMBY_USER_ID
    if not request_user_id:
        url = f"{config.EMBY_SERVER_URL}/Items/{item_id}"
    else:
        url = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items/{item_id}"

    params = {'api_key': config.EMBY_API_KEY, 'Fields': 'MediaSources'}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if not response:
        print(i18n._("❌ Failed to get media source info for item {id}.").format(id=item_id))
        return i18n._("Unknown resolution")

    try:
        media_sources = response.json().get('MediaSources', [])
        if not media_sources:
            print(i18n._("❌ Media source for item {id} is empty.").format(id=item_id))
            return i18n._("Unknown resolution")

        for stream in media_sources[0].get('MediaStreams', []):
            if stream.get('Type') == 'Video':
                width, height = stream.get('Width', 0), stream.get('Height', 0)
                if width and height:
                    print(i18n._("✅ Got resolution for item {id}: {w}x{h}").format(id=item_id, w=width, h=height))
                    return f"{width}x{height}"

        print(i18n._("⚠️ No video stream found in item {id}.").format(id=item_id))
        return i18n._("Unknown resolution")
    except (json.JSONDecodeError, IndexError, KeyError):
        print(i18n._("❌ Error parsing media source info for item {id}.").format(id=item_id))
        return i18n._("Unknown resolution")

def get_emby_libraries() -> (list or None, str or None):
    print(i18n._("🗂️ Getting Emby library list..."))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        return None, i18n._("❌ Emby server configuration is incomplete.")

    url = f"{config.EMBY_SERVER_URL}/Library/VirtualFolders"
    params = {'api_key': config.EMBY_API_KEY}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        try:
            libraries = response.json()
            lib_info = [{'name': lib.get('Name'), 'id': lib.get('ItemId')}
                        for lib in libraries if lib.get('Name') and lib.get('ItemId')]

            if not lib_info:
                return None, i18n._("⚠️ Could not find any media libraries.")

            print(i18n._("✅ Successfully retrieved {count} media libraries.").format(count=len(lib_info)))
            return lib_info, None
        except (json.JSONDecodeError, KeyError) as e:
            return None, i18n._("⚠️ Failed to parse media library list: {error}").format(error=e)

    return None, i18n._("⚠️ Failed to get media library list from Emby API.")

def get_series_item_basic(series_id: str) -> dict or None:
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, config.EMBY_USER_ID]):
        return None
    try:
        url = f"{config.EMBY_SERVER_URL}/Users/{config.EMBY_USER_ID}/Items/{series_id}"
        params = {'api_key': config.EMBY_API_KEY, 'Fields': 'Path,Name,ProductionYear,ProviderIds,Type'}
        resp = make_request_with_retry('GET', url, params=params, timeout=10)
        return resp.json() if resp else None
    except Exception:
        return None

def get_series_season_id_map(series_id: str) -> dict:
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, config.EMBY_USER_ID]):
        return {}
    url = f"{config.EMBY_SERVER_URL}/Users/{config.EMBY_USER_ID}/Items"
    params = {'api_key': config.EMBY_API_KEY, 'ParentId': series_id, 'IncludeItemTypes': 'Season'}
    resp = make_request_with_retry('GET', url, params=params, timeout=15)
    if not resp:
        return {}
    mapping = {}
    try:
        for s in resp.json().get('Items', []):
            sn = s.get('IndexNumber')
            sid = s.get('Id')
            if sn is None or sid is None:
                continue
            sn = int(sn)
            if sn == 0:
                continue
            mapping[sn] = sid
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return mapping


def _get_latest_episode_info(series_id: str) -> dict:
    print(i18n._("ℹ️ Getting latest episode information for series {id}.").format(id=series_id))
    request_user_id = config.EMBY_USER_ID
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, series_id, request_user_id]):
        return {}

    api_endpoint = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items"
    params = {
        'api_key': config.EMBY_API_KEY, 'ParentId': series_id, 'IncludeItemTypes': 'Episode', 'Recursive': 'true',
        'SortBy': 'ParentIndexNumber,IndexNumber', 'SortOrder': 'Descending', 'Limit': 1,
        'Fields': 'ProviderIds,Path,ServerId,DateCreated,ParentIndexNumber,IndexNumber,SeriesName,SeriesProviderIds,Overview'
    }
    response = make_request_with_retry('GET', api_endpoint, params=params, timeout=15)

    try:
        latest_episode = response.json()['Items'][0] if response and response.json().get('Items') else {}
        if latest_episode:
            s_num = latest_episode.get('ParentIndexNumber')
            e_num = latest_episode.get('IndexNumber')
            print(i18n._("✅ Got latest episode: S{s}E{e}").format(s=s_num, e=e_num))
        return latest_episode
    except (json.JSONDecodeError, IndexError, KeyError):
        return {}


def get_episode_item_by_number(series_id: str, season_number: int, episode_number: int) -> dict or None:
    print(i18n._("ℹ️ Querying for exact item info of series {id} S{s:02d}E{e:02d}.").format(id=series_id, s=season_number, e=episode_number))
    request_user_id = config.EMBY_USER_ID
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, series_id, request_user_id]):
        return None

    api_endpoint = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items"
    params = {
        'api_key': config.EMBY_API_KEY,
        'ParentId': series_id,
        'IncludeItemTypes': 'Episode',
        'Recursive': 'true',
        'Fields': 'Id,ParentIndexNumber,IndexNumber',
        'ParentIndexNumber': season_number,
        'IndexNumber': episode_number
    }
    response = make_request_with_retry('GET', api_endpoint, params=params, timeout=15)

    if response:
        try:
            items = response.json().get('Items', [])
            if items:
                episode_item = items[0]
                print(i18n._("✅ Exact match successful, Item ID: {id}").format(id=episode_item.get('Id')))
                return episode_item
        except (json.JSONDecodeError, IndexError, KeyError):
            pass

    print(i18n._("❌ Emby API could not find S{s:02d}E{e:02d}.").format(s=season_number, e=episode_number))
    return None


def get_any_episode_from_season(series_id: str, season_number: int) -> dict or None:
    print(i18n._("ℹ️ Looking for any episode in season {num} as a spec reference...").format(num=season_number))
    request_user_id = config.EMBY_USER_ID
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, series_id, request_user_id]):
        return None

    api_endpoint = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items"
    params = {
        'api_key': config.EMBY_API_KEY,
        'ParentId': series_id,
        'IncludeItemTypes': 'Episode',
        'Recursive': 'true',
        'Fields': 'Id,ParentIndexNumber',
        'ParentIndexNumber': season_number,
        'Limit': 1
    }
    response = make_request_with_retry('GET', api_endpoint, params=params, timeout=15)

    if response:
        try:
            items = response.json().get('Items', [])
            if items:
                episode_item = items[0]
                print(i18n._("✅ Found reference episode in season {num}, ID: {id}").format(num=season_number, id=episode_item.get('Id')))
                return episode_item
        except (json.JSONDecodeError, IndexError, KeyError):
            pass

    print(i18n._("❌ Could not find any episodes in season {num}.").format(num=season_number))
    return None

def get_media_stream_details(item_id: str, user_id: str = None) -> dict or None:
    print(i18n._("ℹ️ Getting media stream info for item {id}.").format(id=item_id))
    request_user_id = user_id or config.EMBY_USER_ID
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, request_user_id]):
        return None

    url = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items/{item_id}"
    params = {'api_key': config.EMBY_API_KEY, 'Fields': 'MediaSources'}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if not response:
        return None

    try:
        item_data = response.json()
        media_sources = item_data.get('MediaSources', [])
        if not media_sources:
            return None
        print(i18n._("✅ Got media stream info for item {id}.").format(id=item_id))

        video_info, audio_info_list, subtitle_info_list = {}, [], []
        for stream in media_sources[0].get('MediaStreams', []):
            if stream.get('Type') == 'Video' and not video_info:
                bitrate_mbps = stream.get('BitRate', 0) / 1_000_000
                video_info = {
                    'title': stream.get('Codec', 'unknown').upper(),
                    'resolution': f"{stream.get('Width', 0)}x{stream.get('Height', 0)}",
                    'bitrate': f"{bitrate_mbps:.1f}" if bitrate_mbps > 0 else "unknown",
                    'video_range': stream.get('VideoRange', ''),
                    'framerate': stream.get('AverageFrameRate') or stream.get('RealFrameRate'),
                    'bit_depth': stream.get('BitDepth'),
                    'profile': stream.get('Profile'),
                    'dv_profile_desc': stream.get('ExtendedVideoSubTypeDescription')
                }

            elif stream.get('Type') == 'Audio':
                audio_info_list.append({
                    'language': stream.get('Language', 'und'), 'codec': stream.get('Codec', 'unknown'),
                    'layout': stream.get('ChannelLayout', '')
                })
            elif stream.get('Type') == 'Subtitle':
                subtitle_info_list.append({
                    'language': stream.get('Language', 'und'),
                    'codec': stream.get('Codec', 'unknown').upper()
                })

        if video_info or audio_info_list or subtitle_info_list:
            return {'video_info': video_info, 'audio_info': audio_info_list, 'subtitle_info': subtitle_info_list}
        return None

    except (json.JSONDecodeError, IndexError, KeyError):
        return None

def get_series_season_media_info(series_id: str) -> list[dict] or None:
    print(i18n._("ℹ️ Getting season specs for series {id}.").format(id=series_id))
    request_user_id = config.EMBY_USER_ID
    if not request_user_id:
        print(i18n._("❌ Error: This feature requires an Emby User ID to be configured"))
        return None

    seasons_url = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items"
    seasons_params = {'api_key': config.EMBY_API_KEY, 'ParentId': series_id, 'IncludeItemTypes': 'Season'}
    seasons_response = make_request_with_retry('GET', seasons_url, params=seasons_params, timeout=10)

    if not seasons_response:
        print(i18n._("⚠️ Failed to query season list"))
        return None

    try:
        seasons = seasons_response.json().get('Items', [])
        if not seasons:
            print(i18n._("⚠️ No seasons found"))
            return []

        season_info_list = []
        for season in sorted(seasons, key=lambda s: s.get('IndexNumber', 0)):
            season_num, season_id = season.get('IndexNumber'), season.get('Id')
            if season_num is None or season_id is None:
                continue

            print(i18n._("ℹ️ Querying episodes for season {num} ({id}).").format(num=season_num, id=season_id))
            first_episode = get_any_episode_from_season(series_id, season_num)

            stream_details = None
            if first_episode:
                stream_details = get_media_stream_details(first_episode.get('Id'), request_user_id)

            season_info_list.append({
                'season_number': season_num,
                'stream_details': stream_details
            })

        return season_info_list
    except (json.JSONDecodeError, IndexError, KeyError):
        print(i18n._("⚠️ Error parsing season information"))
        return None

def delete_emby_seasons(series_id: str, seasons: list[int]) -> str:
    season_map = get_series_season_id_map(series_id)
    logs = []
    for sn in seasons:
        sid = season_map.get(sn)
        if not sid:
            logs.append(i18n._("🟡 Emby season S{num:02d} not found").format(num=sn))
            continue
        msg = delete_emby_item(sid, f"S{sn:02d}")
        logs.append(msg)
    return "\n".join(logs) if logs else i18n._("ℹ️ No seasons were deleted.")


def delete_emby_episodes(series_id: str, season_to_eps: dict[int, list[int]]) -> str:
    logs = []
    season_map = get_series_season_id_map(series_id)
    for sn, eps in sorted(season_to_eps.items()):
        sid = season_map.get(sn)
        if not sid:
            logs.append(i18n._("🟡 Emby season S{num:02d} not found").format(num=sn))
            continue

        url = f"{config.EMBY_SERVER_URL}/Users/{config.EMBY_USER_ID}/Items"
        params = {'api_key': config.EMBY_API_KEY, 'ParentId': sid, 'IncludeItemTypes': 'Episode', 'Fields': 'IndexNumber,Name'}
        resp = make_request_with_retry('GET', url, params=params, timeout=15)

        if not resp:
            logs.append(i18n._("❌ Failed to fetch episode list for Emby season S{num:02d}").format(num=sn))
            continue

        ep_map = {}
        try:
            for ep in resp.json().get('Items', []):
                n = ep.get('IndexNumber')
                if n is not None:
                    ep_map[int(n)] = ep.get('Id')
        except (json.JSONDecodeError, ValueError, TypeError):
            logs.append(i18n._("❌ Failed to parse episode list for Emby season S{num:02d}").format(num=sn))
            continue

        for e in eps:
            eid = ep_map.get(e)
            if not eid:
                logs.append(i18n._("🟡 Emby item S{s:02d}E{e:02d} not found").format(s=sn, e=e))
                continue
            msg = delete_emby_item(eid, f"S{sn:02d}E{e:02d}")
            logs.append(msg)

    return "\n".join(logs) if logs else i18n._("ℹ️ No episodes were deleted.")

def get_all_episodes_for_series(series_id: str, user_id: str = None) -> list or None:
    request_user_id = user_id or config.EMBY_USER_ID
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY, request_user_id, series_id]):
        return None

    url = f"{config.EMBY_SERVER_URL}/Users/{request_user_id}/Items"
    params = {
        'api_key': config.EMBY_API_KEY,
        'ParentId': series_id,
        'IncludeItemTypes': 'Episode',
        'Recursive': 'true',
        'Fields': 'ParentIndexNumber,IndexNumber'
    }

    resp = make_request_with_retry('GET', url, params=params, timeout=20)

    if not resp:
        return None

    try:
        return resp.json().get('Items', [])
    except json.JSONDecodeError:
        return None

def update_emby_user_policy(user_id: str, policy: dict) -> bool:
    print(i18n._("📜 Updating policy for User ID {id}...").format(id=user_id))
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        print(i18n._("❌ Policy update failed: Emby server configuration is incomplete."))
        return False

    url = f"{config.EMBY_SERVER_URL}/Users/{user_id}/Policy"
    params = {'api_key': config.EMBY_API_KEY}
    headers = {'Content-Type': 'application/json'}

    response = make_request_with_retry('POST', url, params=params, headers=headers, json=policy, timeout=15)

    if response and 200 <= response.status_code < 300:
        print(i18n._("✅ Policy for User ID {id} updated successfully.").format(id=user_id))
        return True
    else:
        status_code = response.status_code if response else 'N/A'
        response_text = response.text if response else 'No Response'
        print(i18n._("❌ Failed to update policy for User ID {id}. Status: {code}, Response: {text}").format(
            id=user_id, code=status_code, text=response_text
        ))
        return False

def authenticate_and_get_emby_user(username: str, password: str) -> dict or None:
    print(i18n._("👤 Authenticating Emby user: {username}").format(username=username))
    if not all([config.EMBY_SERVER_URL, username]):
        print(i18n._("⚠️ Missing server URL or username for authentication."))
        return None

    url = f"{config.EMBY_SERVER_URL}/Users/AuthenticateByName"

    headers = {
        'Content-Type': 'application/json',
        'X-Emby-Authorization': 'MediaBrowser Client="EmbyBot", Device="Binding", DeviceId="emby-telegram-bot-binder", Version="1.0.0"'
    }

    payload = {
        'Username': username,
        'Pw': password
    }

    response = make_request_with_retry('POST', url, headers=headers, json=payload, timeout=15, max_retries=1)

    if response and response.status_code == 200:
        try:
            user_data = response.json()
            emby_id = user_data.get('User', {}).get('Id')
            if emby_id:
                print(i18n._("✅ Authentication successful for user '{username}', Emby ID: {id}").format(username=username, id=emby_id))
                return user_data.get('User')
        except Exception as e:
            print(i18n._("❌ Failed to parse successful authentication response: {error}").format(error=e))
            return None

    print(i18n._("❌ Authentication failed for user '{username}'.").format(username=username))
    return None

def get_emby_server_info() -> dict or None:
    if not all([config.EMBY_SERVER_URL, config.EMBY_API_KEY]):
        return None

    url = f"{config.EMBY_SERVER_URL}/System/Info"
    params = {'api_key': config.EMBY_API_KEY}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        try:
            return response.json()
        except json.JSONDecodeError:
            return None
    return None