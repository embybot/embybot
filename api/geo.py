# -*- coding: utf-8 -*-

import json

from .. import i18n
from ..core.config import CONFIG, get_setting, is_feature_active
from .base_client import make_request_with_retry


def _get_geo_baidu(ip: str) -> str:
    url = f"https://opendata.baidu.com/api.php?co=&resource_id=6006&oe=utf8&query={ip}"
    response = make_request_with_retry('GET', url, timeout=5)
    if not response:
        return i18n._("Unknown location")
    try:
        data = response.json()
        if data.get('status') == '0' and data.get('data'):
            location_info = data['data'][0].get('location')
            if location_info:
                print(i18n._("✅ Successfully got geolocation for IP ({ip}) from Baidu API: {location}").format(ip=ip, location=location_info))
                return location_info
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        print(i18n._("❌ Error parsing Baidu API response. IP: {ip}, Error: {error}").format(ip=ip, error=e))
    return i18n._("Unknown location")

def _get_geo_ip138(ip: str) -> str:
    token = CONFIG.get('settings', {}).get('ip_api_token_ip138')
    if not token:
        print(i18n._("❌ IP138 API Error: 'ip_api_token_ip138' is not configured in config.yaml."))
        return i18n._("Unknown location (Token missing)")
    
    url = f"https://api.ip138.com/ipdata/?ip={ip}&datatype=jsonp&token={token}"
    response = make_request_with_retry('GET', url, timeout=5)
    if not response:
        return i18n._("Unknown location")
    try:
        content = response.text
        if content.startswith('jsonp_'):
            content = content[content.find('{') : content.rfind('}')+1]
        
        data = json.loads(content)
        
        if data.get('ret') == 'ok':
            geo_data = data.get('data', [])
            country, province, city, district, isp = geo_data[0], geo_data[1], geo_data[2], geo_data[3], geo_data[4]
            
            location = ""
            if country == i18n._("China"):
                loc_parts = []
                if province:
                    loc_parts.append(province)
                if city and city != province:
                    loc_parts.append(city)
                if district:
                    loc_parts.append(district)
                location = ''.join(p for p in loc_parts if p)
            else:
                loc_parts = [p for p in [country, province, city] if p]
                location = ''.join(loc_parts)

            return f"{location} {isp}".strip()

    except (json.JSONDecodeError, IndexError, KeyError) as e:
        print(i18n._("❌ Error parsing IP138 API response. IP: {ip}, Error: {error}").format(ip=ip, error=e))
    return i18n._("Unknown location")

def _get_geo_pconline(ip: str) -> str:
    url = f"https://whois.pconline.com.cn/ipJson.jsp?ip={ip}&json=true"
    response = make_request_with_retry('GET', url, timeout=5)
    if not response:
        return i18n._("Unknown location")
    try:
        response.encoding = response.apparent_encoding
        data = response.json()
        addr = data.get('addr')
        if addr:
            return addr.replace(ip, '').strip()
    except (json.JSONDecodeError, KeyError) as e:
        print(i18n._("❌ Error parsing PCOnline API response. IP: {ip}, Error: {error}").format(ip=ip, error=e))
    return i18n._("Unknown location")

def _get_geo_vore(ip: str) -> str:
    url = f"https://api.vore.top/api/IPdata?ip={ip}"
    response = make_request_with_retry('GET', url, timeout=5)
    if not response:
        return i18n._("Unknown location")
    try:
        data = response.json()
        if data.get('code') == 200 and data.get('adcode', {}).get('o'):
            return data['adcode']['o'].replace(' - ', ' ')
    except (json.JSONDecodeError, KeyError) as e:
        print(i18n._("❌ Error parsing Vore API response. IP: {ip}, Error: {error}").format(ip=ip, error=e))
    return i18n._("Unknown location")

def _get_geo_ipapi(ip: str) -> str:
    url = f"http://ip-api.com/json/{ip}?fields=status,message,country,regionName,city,isp&lang=zh-CN"
    response = make_request_with_retry('GET', url, timeout=5)
    if not response:
        return i18n._("Unknown location")
    try:
        data = response.json()
        if data.get('status') == 'success':
            isp_map = {
                'Chinanet': i18n._("Telecom"), 'China Telecom': i18n._("Telecom"),
                'China Unicom': i18n._("Unicom"), 'CHINA169': i18n._("Unicom"),
                'CNC Group': i18n._("Unicom"), 'China Netcom': i18n._("Unicom"),
                'China Mobile': i18n._("Mobile"), 'China Broadcasting': i18n._("Broadcasting")
            }
            isp_en = data.get('isp', '')
            isp = isp_en
            for keyword, name in isp_map.items():
                if keyword.lower() in isp_en.lower():
                    isp = name
                    break
            
            region = data.get('regionName', '')
            city = data.get('city', '')
            
            if region and city and region in city:
                city = ''
            
            geo_parts = [p for p in [region, city] if p]
            location_part = ''.join(geo_parts)
            
            full_location = f"{location_part} {isp}".strip()
            return full_location if full_location else i18n._("Unknown location")

    except (json.JSONDecodeError, KeyError) as e:
        print(i18n._("❌ Error parsing IP-API.com response. IP: {ip}, Error: {error}").format(ip=ip, error=e))
    return i18n._("Unknown location")


def get_ip_geolocation(ip: str) -> str:
    if not ip or ip.startswith(('192.168.', '10.', '172.')):
        return i18n._("LAN")

    provider = get_setting('settings.ip_api_provider') or 'baidu'
    if not provider:
         return ""
    print(i18n._("🌍 Querying IP: {ip} using {provider} API").format(provider=provider.upper(), ip=ip))
    
    location = i18n._("Unknown location")
    if provider == 'ip138':
        location = _get_geo_ip138(ip)
    elif provider == 'pconline':
        location = _get_geo_pconline(ip)
    elif provider == 'vore':
        location = _get_geo_vore(ip)
    elif provider == 'ipapi':
        location = _get_geo_ipapi(ip)
    else:
        location = _get_geo_baidu(ip)

    return location