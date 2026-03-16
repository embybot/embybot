import time
import json as _json
import requests

from .. import i18n
from ..core.config import TELEGRAM_TOKEN, EMBY_SERVER_URL

def make_request_with_retry(method: str, url: str, max_retries: int = 3, retry_delay: int = 1, **kwargs):
    def _extract_payload_for_check(_kwargs):
        if 'json' in _kwargs and isinstance(_kwargs['json'], dict):
            return _kwargs['json']
        if 'data' in _kwargs and isinstance(_kwargs['data'], dict):
            return _kwargs['data']
        return None

    def _check_callback_data_len(_payload):
        try:
            if not _payload:
                return
            rm = _payload.get('reply_markup')
            if not rm:
                return
            if isinstance(rm, str):
                rm_obj = _json.loads(rm)
            elif isinstance(rm, dict):
                rm_obj = rm
            else:
                return
            kb = rm_obj.get('inline_keyboard') or []
            for row in kb:
                for btn in row:
                    cd = btn.get('callback_data')
                    if cd:
                        byte_len = len(cd.encode('utf-8'))
                        if byte_len > 64:
                            print(i18n._("❌ Telegram button callback_data exceeds 64-byte limit: {data} ({length} bytes)").format(data=cd, length=byte_len))
        except Exception as _e:
            print(i18n._("⚠️ Exception occurred during local check of reply_markup: {error}").format(error=_e))

    api_name = i18n._("Unknown API")
    timeout = 15
    if "api.telegram.org" in url:
        api_name = i18n._("Telegram")
        timeout = 30
    elif "api.themoviedb.org" in url:
        api_name = i18n._("TMDB")
    elif any(domain in url for domain in [
        "opendata.baidu.com",
        "api.ip138.com",
        "whois.pconline.com.cn",
        "api.vore.top",
        "ip-api.com"
    ]):
        api_name = i18n._("IP Geolocation")
        timeout = 5
    elif EMBY_SERVER_URL and EMBY_SERVER_URL in url:
        api_name = i18n._("Emby")

    timeout = kwargs.pop('timeout', timeout)

    attempts = 0
    display_url = url.split('?')[0]
    if TELEGRAM_TOKEN and TELEGRAM_TOKEN in display_url:
        display_url = display_url.replace(TELEGRAM_TOKEN, "[REDACTED_TOKEN]")

    while attempts < max_retries:
        try:
            if api_name == i18n._("Telegram"):
                payload_for_check = _extract_payload_for_check(kwargs)
                _check_callback_data_len(payload_for_check)

            if api_name != i18n._("Emby"):
                print(i18n._("🌐 Making {api_name} API request (Attempt {attempt}), URL: {url}, Timeout: {timeout}s").format(
                    api_name=api_name, attempt=attempts + 1, url=display_url, timeout=timeout
                ))

            response = requests.request(method, url, timeout=timeout, **kwargs)

            if 200 <= response.status_code < 300:
                if api_name != i18n._("Emby"):
                    print(i18n._("✅ {api_name} API request successful, Status Code: {code}").format(api_name=api_name, code=response.status_code))
                return response

            try:
                response.encoding = 'utf-8'
                error_text = response.text or ""
            except Exception:
                error_text = str(response)

            lowered = (error_text or "").lower()
            is_edit_or_delete = any(p in url for p in ("/editMessage", "/deleteMessage", "/answerCallbackQuery"))

            if api_name == i18n._("Telegram"):
                harmless_errors = [
                    "message to delete not found",
                    "message can't be deleted",
                    "message to edit not found",
                    "message not found",
                    "message is not modified",
                    "message can't be edited",
                    "query is too old and response timeout expired or query id is invalid",
                ]
                if is_edit_or_delete and any(h in lowered for h in harmless_errors):
                    print(i18n._("ℹ️ Telegram returned an ignorable edit/delete type error, ignoring."))
                    return None

                if response.status_code == 429:
                    try:
                        ra = int(response.headers.get('Retry-After', '1'))
                    except ValueError:
                        ra = 1
                    print(i18n._("⏳ Telegram rate limit (429), retrying after {seconds}s. Error: {error_text}").format(seconds=ra, error_text=error_text))
                    time.sleep(max(ra, retry_delay))
                    attempts += 1
                    continue

            if 500 <= response.status_code < 600:
                print(i18n._("❌ {api_name} server error {code}, will retry. Error: {error_text}").format(
                    api_name=api_name, code=response.status_code, error_text=error_text
                ))
            else:
                print(i18n._("❌ {api_name} API request failed with client error (Attempt {attempt}), Status Code: {code}, Response: {response}").format(
                    api_name=api_name, attempt=attempts + 1, code=response.status_code, response=error_text
                ))
                return response

        except requests.exceptions.RequestException as e:
            error_message = str(e)
            if TELEGRAM_TOKEN:
                error_message = error_message.replace(TELEGRAM_TOKEN, "[REDACTED_TOKEN]")
            print(i18n._("❌ Network error on {api_name} API request (Attempt {attempt}), URL: {url}, Error: {error}").format(
                api_name=api_name, attempt=attempts + 1, url=display_url, error=error_message
            ))

        attempts += 1
        if attempts < max_retries:
            time.sleep(retry_delay)

    print(i18n._("❌ {api_name} API request failed, max retries reached ({max_retries}), URL: {url}").format(
        api_name=api_name, max_retries=max_retries, url=display_url
    ))
    return None
