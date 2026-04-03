"""Skill: 天气查询（wttr.in 免费 API，无需 API Key）"""
from agent_core.tools import tool

try:
    import requests as _req
except ImportError:
    _req = None

# 常见中文城市名 → 英文映射
_CITY_MAP = {
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
    "杭州": "Hangzhou", "成都": "Chengdu", "武汉": "Wuhan", "南京": "Nanjing",
    "重庆": "Chongqing", "西安": "Xian", "苏州": "Suzhou", "天津": "Tianjin",
    "长沙": "Changsha", "郑州": "Zhengzhou", "青岛": "Qingdao", "大连": "Dalian",
    "厦门": "Xiamen", "昆明": "Kunming", "合肥": "Hefei", "福州": "Fuzhou",
    "济南": "Jinan", "哈尔滨": "Harbin", "沈阳": "Shenyang", "长春": "Changchun",
    "南昌": "Nanchang", "贵阳": "Guiyang", "太原": "Taiyuan", "石家庄": "Shijiazhuang",
    "兰州": "Lanzhou", "海口": "Haikou", "拉萨": "Lhasa", "乌鲁木齐": "Urumqi",
}

# 天气描述英→中
_WEATHER_CN = {
    "Sunny": "晴", "Clear": "晴", "Partly cloudy": "多云", "Cloudy": "阴",
    "Overcast": "阴", "Mist": "薄雾", "Fog": "雾", "Light rain": "小雨",
    "Moderate rain": "中雨", "Heavy rain": "大雨", "Light snow": "小雪",
    "Moderate snow": "中雪", "Heavy snow": "大雪", "Thunderstorm": "雷暴",
    "Patchy rain possible": "可能有零星小雨", "Light drizzle": "毛毛雨",
}


def _translate_weather(desc):
    return _WEATHER_CN.get(desc, desc)


@tool("weather", "查询城市天气（实时 + 未来3天预报）", {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "城市名称（中文或英文）"},
    },
    "required": ["city"],
})
def weather(city: str) -> str:
    if _req is None:
        return "需要安装 requests 库: pip install requests"

    # 中文城市名转英文
    query_city = _CITY_MAP.get(city, city)

    try:
        url = "https://wttr.in/{}?format=j1".format(query_city)
        resp = _req.get(url, headers={"User-Agent": "curl/7.0"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return "天气查询失败: {}".format(e)

    # 当前天气
    cur = data.get("current_condition", [{}])[0]
    temp = cur.get("temp_C", "?")
    feels = cur.get("FeelsLikeC", "?")
    desc = _translate_weather(cur.get("weatherDesc", [{}])[0].get("value", "未知"))
    humidity = cur.get("humidity", "?")
    wind_speed = cur.get("windspeedKmph", "?")
    wind_dir = cur.get("winddir16Point", "")
    visibility = cur.get("visibility", "?")

    lines = [
        "📍 {} 当前天气".format(city),
        "  天气: {}".format(desc),
        "  温度: {}°C (体感 {}°C)".format(temp, feels),
        "  湿度: {}%".format(humidity),
        "  风速: {} km/h {}".format(wind_speed, wind_dir),
        "  能见度: {} km".format(visibility),
    ]

    # 未来预报
    forecasts = data.get("weather", [])
    if forecasts:
        lines.append("")
        lines.append("📅 未来预报:")
        for day in forecasts[:3]:
            date = day.get("date", "")
            max_t = day.get("maxtempC", "?")
            min_t = day.get("mintempC", "?")
            hourly = day.get("hourly", [{}])
            # 取中午的天气描述
            mid = hourly[len(hourly) // 2] if hourly else {}
            day_desc = _translate_weather(mid.get("weatherDesc", [{}])[0].get("value", ""))
            lines.append("  {} : {} {}°C ~ {}°C".format(date, day_desc, min_t, max_t))

    return "\n".join(lines)
