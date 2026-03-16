# -*- coding: utf-8 -*-

import traceback

from .. import i18n
from ..api import emby as emby_api
from ..api import tmdb as tmdb_api
from ..core.config import EMBY_USER_ID
from ..utils.helpers import escape_html


def get_local_episodes_by_season(series_id: str, user_id: str = None) -> dict[int, set[int]]:
    print(i18n._("ℹ️ Compiling local season/episode numbers for series {id}.").format(id=series_id))
    request_user_id = user_id or EMBY_USER_ID
    if not all([series_id, request_user_id]):
        return {}
        
    episodes = emby_api.get_all_episodes_for_series(series_id, request_user_id)
    if not episodes:
        return {}

    mapping = {}
    for ep in episodes:
        s = ep.get('ParentIndexNumber')
        e = ep.get('IndexNumber')
        if s is None or e is None:
            continue
        s = int(s)
        e = int(e)
        if s == 0:
            continue
        mapping.setdefault(s, set()).add(e)
        
    print(i18n._("✅ Local season/episode count complete ({count} seasons total).").format(count=len(mapping)))
    return mapping


def build_seasonwise_progress_and_missing_lines(series_tmdb_id: str, series_id: str, latest_season_num: int, latest_episode_num: int) -> list[str]:
    lines = []
    if not all([series_tmdb_id, series_id is not None, latest_season_num is not None]):
        return lines

    local_map = get_local_episodes_by_season(series_id, EMBY_USER_ID)
    if not local_map:
        return lines

    local_latest_season = int(latest_season_num)

    tmdb_seasons_nums = [s for s in tmdb_api.get_tmdb_season_numbers(series_tmdb_id) if s <= local_latest_season]
    if not tmdb_seasons_nums:
        tmdb_seasons_nums = sorted([s for s in local_map.keys() if s <= local_latest_season])

    for s_num in tmdb_seasons_nums:
        tmdb_info = tmdb_api.get_tmdb_season_details(series_tmdb_id, s_num)
        if not tmdb_info:
            continue

        tmdb_eps_set = set(tmdb_info.get('episode_numbers', []))
        tmdb_max_ep = tmdb_info.get('max_episode_number', 0)
        local_eps_set = local_map.get(s_num, set())
        local_max_ep = (int(latest_episode_num) if s_num == local_latest_season and latest_episode_num is not None else (max(local_eps_set) if local_eps_set else 0))

        if s_num == local_latest_season:
            remaining = max(0, int(tmdb_max_ep) - int(local_max_ep))
            if remaining == 0:
                status = i18n._("Finished") if tmdb_info.get('is_finale_marked') else i18n._("Finished (may be inaccurate)")
            else:
                status = i18n._("{count} episodes remaining").format(count=remaining)
            lines.append(escape_html(i18n._("Update Progress: {status}").format(status=status)))

            if local_max_ep > 0:
                expected_eps = {n for n in tmdb_eps_set if int(n) <= local_max_ep}
                missing_eps = sorted(list(expected_eps - local_eps_set))
                if missing_eps:
                    head = ", ".join([f"E{int(n):02d}" for n in missing_eps[:10]])
                    suffix = i18n._("…({count} episodes total)").format(count=len(missing_eps)) if len(missing_eps) > 10 else ""
                    lines.append(escape_html(i18n._("Missing: S{s:02d} {head}{suffix}").format(s=int(s_num), head=head, suffix=suffix)))
        else:
            missing_eps = sorted(list(tmdb_eps_set - local_eps_set))
            if missing_eps:
                head = ", ".join([f"E{int(n):02d}" for n in missing_eps[:10]])
                suffix = i18n._("…({count} episodes total)").format(count=len(missing_eps)) if len(missing_eps) > 10 else ""
                lines.append(escape_html(i18n._("Missing: S{s:02d} {head}{suffix}").format(s=int(s_num), head=head, suffix=suffix)))
                
    return lines


def build_progress_lines_for_library_new(item: dict, media_details: dict) -> list[str]:
    try:
        item_type = item.get('Type')
        if item_type not in ('Series', 'Season', 'Episode'):
            return []

        series_id = item.get('Id') if item_type == 'Series' else item.get('SeriesId')
        if not series_id:
            return []

        latest_episode = emby_api._get_latest_episode_info(series_id)
        if not latest_episode:
            return []

        local_s_num = latest_episode.get('ParentIndexNumber')
        local_e_num = latest_episode.get('IndexNumber')
        if local_s_num is None or local_e_num is None:
            return []

        series_tmdb_id = media_details.get('tmdb_id')
        if not series_tmdb_id:
            series_item = emby_api.get_series_item_basic(series_id)
            if series_item:
                series_tmdb_id = (series_item.get('ProviderIds') or {}).get('Tmdb')

        if not series_tmdb_id:
            return []

        return build_seasonwise_progress_and_missing_lines(series_tmdb_id, series_id, local_s_num, local_e_num)
    except Exception as e:
        print(i18n._("⚠️ Exception generating progress/missing info for new library notification: {error}").format(error=e))
        traceback.print_exc()
        return []