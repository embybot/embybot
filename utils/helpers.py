# -*- coding: utf-8 -*-

import re
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import i18n
from ..core.config import TIMEZONE, MEDIA_BASE_PATH, get_setting

def parse_episode_ranges_from_description(description: str):
    if not description:
        return None, []
    first_line = description.strip().splitlines()[0]
    if not first_line:
        return None, []

    tokens = re.split(r'[，,]\s*|/\s*', first_line)
    
    season_ctx = None
    summary_parts, expanded = [], []

    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        m = re.match(r'(?:(?:S|s)\s*(\d{1,2}))?\s*E?\s*(\d{1,3})(?:\s*-\s*(?:(?:S|s)\s*(\d{1,2}))?\s*E?\s*(\d{1,3}))?$', tok)
        if not m:
            continue
        s1, e1, s2, e2 = m.groups()
        if s1:
            season_ctx = int(s1)
        season = season_ctx if season_ctx is not None else 1
        start_ep = int(e1)

        if e2:
            end_season = int(s2) if s2 else season
            end_ep = int(e2)
            if end_season != season:
                summary_parts.append(f"S{season:02d}E{start_ep:02d}–S{end_season:02d}E{end_ep:02d}")
            else:
                summary_parts.append(f"S{season:02d}E{start_ep:02d}–E{end_ep:02d}")
                for ep in range(start_ep, end_ep + 1):
                    expanded.append(f"S{season:02d}E{ep:02d}")
        else:
            summary_parts.append(f"S{season:02d}E{start_ep:02d}")
            expanded.append(f"S{season:02d}E{start_ep:02d}")

    summary = ", ".join(summary_parts) if summary_parts else None
    return summary, expanded

def escape_html(text: str) -> str:
    """Escapes characters that are special in HTML."""
    if not text:
        return ""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def format_date(date_in):
    try:
        if isinstance(date_in, datetime):
            dt = date_in
        else:
            s = (str(date_in) or "").strip()
            if not s:
                return i18n._("Unknown")

            has_z = s.endswith(('Z', 'z'))
            if has_z:
                s = s[:-1]
            
            if '.' in s:
                main, frac = s.split('.', 1)
                frac_digits = ''.join(ch for ch in frac if ch.isdigit())
                frac6 = (frac_digits + '000000')[:6]
                s = f"{main}.{frac6}"

            dt = datetime.fromisoformat(s)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo('UTC'))
        
        return dt.astimezone(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')

    except Exception:
        return i18n._("Unknown")

def get_event_time_str(payload: dict) -> str:
    try:
        s = (payload or {}).get("Date")
        if s:
            return format_date(s)
    except Exception:
        pass

    try:
        desc = ((payload or {}).get("Description") or "").splitlines()[0].strip()
        if desc:
            m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日.*?(上午|下午)?\s*(\d{1,2}):(\d{2})', desc)
            if m:
                y, mo, d, ampm, hh, mm = m.groups()
                hh = int(hh); mm = int(mm)
                if ampm in ('下午', 'PM', 'pm') and hh < 12:
                    hh += 12
                if ampm in ('上午', 'AM', 'am') and hh == 12:
                    hh = 0
                dt = datetime(int(y), int(mo), int(d), hh, mm, tzinfo=TIMEZONE)
                return dt.strftime('%Y-%m-%d %H:%M:%S')

            cleaned = re.sub(r'^[A-Za-z]+,\s*', '', desc)
            for fmt in ("%B %d, %Y %I:%M %p", "%b %d, %Y %I:%M %p", "%B %d %Y %I:%M %p"):
                try:
                    dt = datetime.strptime(cleaned, fmt).replace(tzinfo=TIMEZONE)
                    return dt.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    pass
    except Exception:
        pass

    return i18n._("Unknown")

def format_ticks_to_hms(ticks: int) -> str:
    if not isinstance(ticks, (int, float)) or ticks <= 0:
        return "00:00:00"
    seconds = ticks / 10_000_000
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

def get_program_type_from_path(path: str) -> str or None:
    if not MEDIA_BASE_PATH or not path or not path.startswith(MEDIA_BASE_PATH):
        return None
    relative_path = path[len(MEDIA_BASE_PATH):].lstrip('/')
    parts = relative_path.split('/')
    if parts and parts[0]:
        return parts[0]
    return None

def extract_year_from_path(path: str) -> str or None:
    if not path:
        return None
    match = re.search(r'\((\d{4})\)', path)
    if match:
        year = match.group(1)
        return year
    return None

def find_nfo_file_in_dir(directory: str) -> str or None:
    try:
        for filename in os.listdir(directory):
            if filename.lower().endswith('.nfo'):
                return os.path.join(directory, filename)
    except OSError as e:
        print(i18n._("❌ Error reading directory {directory}: {error}").format(directory=directory, error=e))
    return None

def parse_tmdbid_from_nfo(nfo_path: str) -> str or None:
    if not nfo_path or not os.path.exists(nfo_path):
        return None
    try:
        with open(nfo_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        try:
            content_no_decl = re.sub(r'<\?xml[^>]*\?>', '', content).strip()
            if content_no_decl:
                root = ET.fromstring(content_no_decl)
                for uniqueid in root.findall('.//uniqueid[@type="tmdb"]'):
                    if uniqueid.get('default') == 'true' and uniqueid.text and uniqueid.text.isdigit():
                        tmdb_id = uniqueid.text.strip()
                        print(i18n._("✅ NFO Parse: Found default <uniqueid type='tmdb'> -> {id}").format(id=tmdb_id))
                        return tmdb_id
                for uniqueid in root.findall('.//uniqueid[@type="tmdb"]'):
                    if uniqueid.text and uniqueid.text.isdigit():
                        tmdb_id = uniqueid.text.strip()
                        print(i18n._("✅ NFO Parse: Found <uniqueid type='tmdb'> -> {id}").format(id=tmdb_id))
                        return tmdb_id
                
                tmdbid_tag = root.find('.//tmdbid')
                if tmdbid_tag is not None and tmdbid_tag.text and tmdbid_tag.text.isdigit():
                    tmdb_id = tmdbid_tag.text.strip()
                    print(i18n._("✅ NFO Parse: Found <tmdbid> -> {id}").format(id=tmdb_id))
                    return tmdb_id
        except ET.ParseError:
            print(i18n._("⚠️ NFO file '{filename}' is not valid XML, falling back to regex matching.").format(filename=os.path.basename(nfo_path)))

        match = re.search(r'themoviedb.org/(?:movie|tv)/(\d+)', content)
        if match:
            tmdb_id = match.group(1)
            print(i18n._("✅ NFO Parse (regex): Found from URL -> {id}").format(id=tmdb_id))
            return tmdb_id
        
        match = re.search(r'<tmdbid>(\d+)</tmdbid>', content, re.IGNORECASE)
        if match:
            tmdb_id = match.group(1)
            print(i18n._("✅ NFO Parse (regex): Found from tag -> {id}").format(id=tmdb_id))
            return tmdb_id
            
    except Exception as e:
        print(i18n._("❌ Error parsing NFO file {path}: {error}").format(path=nfo_path, error=e))
    
    print(i18n._("❌ Failed to find TMDB ID in NFO file '{filename}'.").format(filename=os.path.basename(nfo_path)))
    return None

def parse_season_selection(text: str) -> list[int]:
    if not text:
        return []
    tokens = re.split(r'[,\s，、]+', text.strip())
    seasons = set()
    for t in tokens:
        if not t:
            continue
        m = re.fullmatch(r'(?:S|s)?\s*(\d{1,2})', t.strip())
        if m:
            try:
                n = int(m.group(1))
                if 0 <= n < 200:
                    seasons.add(n)
            except (ValueError, TypeError):
                pass
    return sorted(list(seasons))


def parse_episode_selection(s: str) -> dict[int, set[int]]:
    if not s:
        return {}
    tokens = re.split(r'[,\s，、]+', s.strip())
    ctx_season = None
    mapping = {}
    for tok in tokens:
        tok = tok.strip().upper()
        if not tok:
            continue
        
        m = re.match(r'^(?:S(\d{1,2}))?E(\d{1,3})(?:-E?(\d{1,3}))?$', tok)
        if m:
            s1, e1, e2 = m.groups()
            if s1:
                ctx_season = int(s1)
            if ctx_season is None:
                ctx_season = 1

            e1 = int(e1)
            if e2:
                e2 = int(e2)
                ep_range = range(min(e1, e2), max(e1, e2) + 1)
                mapping.setdefault(ctx_season, set()).update(ep_range)
            else:
                mapping.setdefault(ctx_season, set()).add(e1)
            continue

        m2 = re.match(r'^S(\d{1,2})$', tok)
        if m2:
            ctx_season = int(m2.group(1))
            continue
            
    return mapping

def restart_bot():
    print(i18n._("🤖 Bot is restarting..."))
    try:        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(i18n._("❌ Failed to restart bot: {error}").format(error=e))