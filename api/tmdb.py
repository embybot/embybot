from datetime import datetime, timedelta

from .. import i18n
from ..core.config import TMDB_API_TOKEN, EMBY_SERVER_URL, EMBY_API_KEY, EMBY_USER_ID, POSTER_CACHE_TTL_DAYS
from ..core.cache import POSTER_CACHE, save_poster_cache
from ..utils.helpers import extract_year_from_path
from .base_client import make_request_with_retry

def get_tmdb_details_by_id(tmdb_id: str, preferred_type: str = None):
    print(i18n._("🔍 Querying details for TMDB ID: {id}").format(id=tmdb_id))
    if not TMDB_API_TOKEN:
        return None

    if preferred_type == 'tv':
        search_order = ['tv', 'movie']
    elif preferred_type == 'movie':
        search_order = ['movie', 'tv']
    else:
        search_order = ['tv', 'movie']

    print(f"ℹ️ TMDB search order based on preference ('{preferred_type}'): {search_order}")

    for media_type in search_order:
        url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}"
        params = {'api_key': TMDB_API_TOKEN, 'language': 'zh-CN'}
        response = make_request_with_retry('GET', url, params=params, timeout=10)

        if response and response.status_code == 200:
            details = response.json()
            title = details.get('title') or details.get('name')
            if title:
                print(i18n._("✅ Found a match in TMDB: {title} (Type: {type})").format(title=title, type=media_type))
                details['media_type'] = media_type
                return details

    print(i18n._("❌ Nothing found in TMDB with ID {id}.").format(id=tmdb_id))
    return None

def search_tmdb_multi(title: str, year: str = None) -> list:
    any_year_str = i18n._("Any year")
    print(i18n._("🔍 Performing multi-search on TMDB: {title} ({year})").format(title=title, year=year or any_year_str))
    if not TMDB_API_TOKEN:
        return []

    all_results = []

    for media_type in ['movie', 'tv']:
        params = {'api_key': TMDB_API_TOKEN, 'query': title, 'language': 'zh-CN'}
        if year:
            if media_type == 'tv':
                params['first_air_date_year'] = year
            else:
                params['year'] = year

        url = f"https://api.themoviedb.org/3/search/{media_type}"
        response = make_request_with_retry('GET', url, params=params, timeout=10)

        if response:
            results = response.json().get('results', [])
            for item in results:
                item_title = item.get('title') or item.get('name')
                release_date = item.get('release_date') or item.get('first_air_date')
                item_year = release_date.split('-')[0] if release_date else None
                if item_title:
                    all_results.append({'title': item_title.strip(), 'year': item_year})

    unique_results = []
    seen = set()
    for res in all_results:
        identifier = (res['title'], res['year'])
        if identifier not in seen:
            unique_results.append(res)
            seen.add(identifier)

    print(i18n._("✅ TMDB multi-search found {count} unique results.").format(count=len(unique_results)))
    return unique_results


def search_tmdb_by_title(title: str, year: str = None, media_type: str = 'tv'):
    print(i18n._("🔍 Searching TMDB: {title} ({year})").format(title=title, year=year))
    if not TMDB_API_TOKEN:
        return None

    params = {'api_key': TMDB_API_TOKEN, 'query': title, 'language': 'zh-CN'}
    if year:
        params['first_air_date_year' if media_type == 'tv' else 'year'] = year

    url = f"https://api.themoviedb.org/3/search/{media_type}"
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if response:
        results = response.json().get('results', [])
        if not results:
            print(i18n._("❌ TMDB found no matching results."))
            return None

        exact_match = next((item for item in results if (item.get('name') or item.get('title')) == title), None)
        if exact_match:
            match_title = exact_match.get('name') or exact_match.get('title')
            match_id = exact_match.get('id')
            print(i18n._("✅ Found exact match: {title}, ID: {id}").format(title=match_title, id=match_id))
            return match_id
        else:
            results.sort(key=lambda x: (x.get('popularity', 0)), reverse=True)
            popular_match = results[0]
            match_title = popular_match.get('name') or popular_match.get('title')
            match_id = popular_match.get('id')
            print(i18n._("⚠️ No exact match found, returning the most popular result: {title}, ID: {id}").format(title=match_title, id=match_id))
            return match_id

    print(i18n._("❌ TMDB search failed"))
    return None

def get_media_details(item: dict, user_id: str) -> dict:
    details = {'poster_url': None, 'tmdb_link': None, 'year': None, 'tmdb_id': None}
    if not TMDB_API_TOKEN:
        print(i18n._("⚠️ TMDB_API_TOKEN is not configured, skipping fetching media details."))
        return details

    item_type = item.get('Type')
    tmdb_id, api_type = None, None
    details['year'] = item.get('ProductionYear') or extract_year_from_path(item.get('Path'))

    print(i18n._("ℹ️ Getting media details for item {name} ({id}). Type: {type}").format(name=item.get('Name'), id=item.get('Id'), type=item_type))

    if item_type == 'Movie':
        api_type = 'movie'
        tmdb_id = item.get('ProviderIds', {}).get('Tmdb')
        if tmdb_id:
            details['tmdb_link'] = f"https://www.themoviedb.org/movie/{tmdb_id}"

    elif item_type == 'Series':
        api_type = 'tv'
        tmdb_id = item.get('ProviderIds', {}).get('Tmdb')
        if tmdb_id:
            details['tmdb_link'] = f"https://www.themoviedb.org/tv/{tmdb_id}"

    elif item_type == 'Episode':
        api_type = 'tv'
        series_provider_ids = item.get('SeriesProviderIds', {}) or item.get('Series', {}).get('ProviderIds', {})
        tmdb_id = series_provider_ids.get('Tmdb')

        if not tmdb_id and item.get('SeriesId'):
            print(i18n._("⚠️ Cannot get TMDB ID from Episode, trying to get it from SeriesId ({id}).").format(id=item.get('SeriesId')))
            series_id = item.get('SeriesId')
            request_user_id = user_id or EMBY_USER_ID
            url_part = f"/Users/{request_user_id}/Items/{series_id}" if request_user_id else f"/Items/{series_id}"
            url = f"{EMBY_SERVER_URL}{url_part}"
            response = make_request_with_retry('GET', url, params={'api_key': EMBY_API_KEY}, timeout=10)
            if response:
                tmdb_id = response.json().get('ProviderIds', {}).get('Tmdb')

        if not tmdb_id:
            print(i18n._("⚠️ Still no TMDB ID, trying to search TMDB by title."))
            tmdb_id = search_tmdb_by_title(item.get('SeriesName'), details.get('year'), media_type='tv')

        if tmdb_id:
            season_num, episode_num = item.get('ParentIndexNumber'), item.get('IndexNumber')
            if season_num is not None and episode_num is not None:
                details['tmdb_link'] = f"https://www.themoviedb.org/tv/{tmdb_id}/season/{season_num}/episode/{episode_num}"
            else:
                details['tmdb_link'] = f"https://www.themoviedb.org/tv/{tmdb_id}"

    if tmdb_id:
        details['tmdb_id'] = tmdb_id
        if tmdb_id in POSTER_CACHE:
            cached_item = POSTER_CACHE[tmdb_id]
            cached_time = datetime.fromisoformat(cached_item.get('timestamp', '1970-01-01T00:00:00'))
            if (datetime.now() - cached_time < timedelta(days=POSTER_CACHE_TTL_DAYS)) and (cached_item.get('type') == api_type):
                details['poster_url'] = cached_item['url']
                print(i18n._("✅ Got poster link for TMDB ID {id} from cache.").format(id=tmdb_id))
                return details

        url = f"https://api.themoviedb.org/3/{api_type}/{tmdb_id}?api_key={TMDB_API_TOKEN}&language=zh-CN"
        response = make_request_with_retry('GET', url, timeout=10)

        if response:
            poster_path = response.json().get('poster_path')
            if poster_path:
                details['poster_url'] = f"https://image.tmdb.org/t/p/w500{poster_path}"
                POSTER_CACHE[tmdb_id] = {
                    'url': details['poster_url'],
                    'type': api_type,
                    'timestamp': datetime.now().isoformat()
                }
                save_poster_cache()
                print(i18n._("✅ Successfully fetched and cached the poster from TMDB."))

    return details

def get_tmdb_season_numbers(series_tmdb_id: str) -> list[int]:
    print(i18n._("ℹ️ Querying season list for TMDB series {id}.").format(id=series_tmdb_id))
    if not TMDB_API_TOKEN or not series_tmdb_id:
        return []

    url = f"https://api.themoviedb.org/3/tv/{series_tmdb_id}"
    params = {'api_key': TMDB_API_TOKEN, 'language': 'zh-CN'}
    resp = make_request_with_retry('GET', url, params=params, timeout=10)

    if not resp:
        return []

    data = resp.json()
    seasons = data.get('seasons', []) or []
    nums = []
    for s in seasons:
        n = s.get('season_number')
        try:
            if n is not None and int(n) != 0:
                nums.append(int(n))
        except (ValueError, TypeError):
            pass

    nums = sorted(set(nums))
    print(i18n._("✅ TMDB Season List: {nums}").format(nums=nums))
    return nums


def get_tmdb_season_details(series_tmdb_id: str, season_number: int) -> dict or None:
    print(i18n._("ℹ️ Querying details for season {num} of TMDB series {id}.").format(id=series_tmdb_id, num=season_number))
    if not all([TMDB_API_TOKEN, series_tmdb_id, season_number is not None]):
        return None

    url = f"https://api.themoviedb.org/3/tv/{series_tmdb_id}/season/{season_number}"
    params = {'api_key': TMDB_API_TOKEN, 'language': 'zh-CN'}
    response = make_request_with_retry('GET', url, params=params, timeout=10)

    if not response:
        return None

    data = response.json()
    episodes = data.get('episodes', [])
    if not episodes:
        print(i18n._("❌ TMDB could not find the episode list for season {num}.").format(num=season_number))
        return None

    nums = []
    for ep in episodes:
        n = ep.get('episode_number')
        try:
            if n is not None:
                nums.append(int(n))
        except (ValueError, TypeError):
            pass

    max_ep = max(nums) if nums else 0
    is_finale = episodes[-1].get('episode_type') == 'finale' if episodes else False

    print(i18n._("✅ TMDB Season {num}: {count} total entries, max episode number E{max_ep:02d}.").format(num=season_number, count=len(episodes), max_ep=max_ep))
    return {
        'total_episodes': len(episodes),
        'max_episode_number': max_ep,
        'episode_numbers': sorted(set(nums)),
        'is_finale_marked': is_finale
    }

def get_all_titles_and_year_by_id(tmdb_id: str) -> list[tuple[str, str]]:
    if not TMDB_API_TOKEN:
        return []

    print(i18n._("ℹ️ Getting all aliases and years for ID {id} from TMDB...").format(id=tmdb_id))
    all_name_year_combos = set()

    for media_type in ['movie', 'tv']:
        details_url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}"
        params_details = {'api_key': TMDB_API_TOKEN, 'language': 'zh-CN'}
        details_resp = make_request_with_retry('GET', details_url, params=params_details)

        if details_resp:
            try:
                details = details_resp.json()
                title = details.get('title') or details.get('name')
                original_title = details.get('original_title') or details.get('original_name')
                release_date = details.get('release_date') or details.get('first_air_date')
                year = release_date.split('-')[0] if release_date else None

                if title: all_name_year_combos.add((title.strip(), year))
                if original_title: all_name_year_combos.add((original_title.strip(), year))

                alt_titles_url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/alternative_titles"
                params_alt = {'api_key': TMDB_API_TOKEN}
                alt_titles_resp = make_request_with_retry('GET', alt_titles_url, params=params_alt)
                if alt_titles_resp:
                    alt_data = alt_titles_resp.json()
                    titles_list = alt_data.get('titles', alt_data.get('results', []))
                    for t in titles_list:
                        if t.get('title'):
                            all_name_year_combos.add((t['title'].strip(), year))

                translations_url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/translations"
                params_trans = {'api_key': TMDB_API_TOKEN}
                translations_resp = make_request_with_retry('GET', translations_url, params=params_trans)
                if translations_resp:
                    trans_data = translations_resp.json().get('translations', [])
                    for t in trans_data:
                        trans_title = t.get('data', {}).get('title') or t.get('data', {}).get('name')
                        if trans_title:
                            all_name_year_combos.add((trans_title.strip(), year))
            except Exception as e:
                print(f"Error processing TMDB data for {media_type} ID {tmdb_id}: {e}")

    result = list(all_name_year_combos)
    print(i18n._("✅ Found {count} unique name/year combinations for ID {id}.").format(id=tmdb_id, count=len(result)))
    return result
