"""Weather forecasting via OpenWeatherMap."""

import logging
from dataclasses import dataclass
from datetime import date

from src.config import settings
from src.http import build_async_client, default_timeout

logger = logging.getLogger("uvicorn.error")


@dataclass
class DayForecast:
    date: date
    temp_high_f: float
    temp_low_f: float
    precipitation_pct: float
    description: str
    icon: str  # emoji
    uv_index: float


class WeatherService:
    """Fetch weekend weather from OpenWeatherMap."""

    async def get_weekend_forecast(self, sat: date, sun: date) -> dict[str, DayForecast]:
        """Returns {"saturday": forecast, "sunday": forecast}."""
        if not settings.weather_api_key:
            return self._default_forecast(sat, sun)

        try:
            async with build_async_client(
                service="weather.openweathermap",
                timeout=default_timeout(),
                headers={"Accept": "application/json"},
            ) as client:
                resp = await client.get(
                    "https://api.openweathermap.org/data/2.5/forecast",
                    params={
                        "lat": settings.weather_lat,
                        "lon": settings.weather_lon,
                        "appid": settings.weather_api_key,
                        "units": "imperial",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            sat_forecast = self._summarize_day(data, sat)
            sun_forecast = self._summarize_day(data, sun)
            return {"saturday": sat_forecast, "sunday": sun_forecast}
        except Exception as exc:
            logger.warning(
                "weather_fetch_failed service=%s url=%s timeout=%ss error=%s",
                "weather.openweathermap",
                "https://api.openweathermap.org/data/2.5/forecast",
                settings.external_http_timeout_seconds,
                exc,
            )
            return self._default_forecast(sat, sun)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _summarize_day(self, data: dict, target: date) -> DayForecast:
        """Aggregate 3-hour forecast blocks into a single daily summary."""
        target_str = target.isoformat()
        temps: list[float] = []
        precip_probs: list[float] = []
        descriptions: list[str] = []

        for item in data.get("list", []):
            dt_txt = item["dt_txt"][:10]
            if dt_txt == target_str:
                temps.append(item["main"]["temp"])
                precip_probs.append(item.get("pop", 0) * 100)
                descriptions.append(item["weather"][0]["description"])

        if not temps:
            return DayForecast(
                date=target,
                temp_high_f=85,
                temp_low_f=70,
                precipitation_pct=10,
                description="partly cloudy",
                icon="\u26c5",
                uv_index=6,
            )

        high = max(temps)
        low = min(temps)
        precip = max(precip_probs)
        desc = descriptions[len(descriptions) // 2]  # midday description
        icon = self._weather_emoji(desc, precip)

        return DayForecast(
            date=target,
            temp_high_f=high,
            temp_low_f=low,
            precipitation_pct=precip,
            description=desc,
            icon=icon,
            uv_index=6,
        )

    @staticmethod
    def _weather_emoji(desc: str, precip: float) -> str:
        if precip > 70:
            return "\U0001f327\ufe0f"  # 🌧️
        if precip > 30:
            return "\U0001f326\ufe0f"  # 🌦️
        if "cloud" in desc:
            return "\u26c5"  # ⛅
        if "clear" in desc or "sun" in desc:
            return "\u2600\ufe0f"  # ☀️
        if "storm" in desc:
            return "\u26c8\ufe0f"  # ⛈️
        return "\U0001f324\ufe0f"  # 🌤️

    @staticmethod
    def _default_forecast(sat: date, sun: date) -> dict[str, DayForecast]:
        return {
            "saturday": DayForecast(
                date=sat,
                temp_high_f=85,
                temp_low_f=72,
                precipitation_pct=20,
                description="partly cloudy",
                icon="\u26c5",
                uv_index=7,
            ),
            "sunday": DayForecast(
                date=sun,
                temp_high_f=87,
                temp_low_f=73,
                precipitation_pct=30,
                description="partly cloudy",
                icon="\U0001f324\ufe0f",
                uv_index=7,
            ),
        }


def summarize_weekend_recommendation(
    weather: dict[str, DayForecast],
) -> tuple[str, str, list[str]]:
    """Return (message, tone, tips) for weekend weather guidance.

    tone: success | warning | info
    """
    sat = weather.get("saturday")
    sun = weather.get("sunday")
    if not sat or not sun:
        return (
            "Weather data unavailable. Plan flexible indoor/outdoor options.",
            "info",
            ["Check day-of forecast before leaving."],
        )

    days = [sat, sun]
    max_heat = max(d.temp_high_f for d in days)
    min_heat = min(d.temp_high_f for d in days)
    max_rain = max(d.precipitation_pct for d in days)
    avg_rain = sum(d.precipitation_pct for d in days) / len(days)

    tips: list[str] = []
    if max_rain > 50:
        tips.append("Rain expected: prioritize indoor picks and bring spare clothes.")
    if max_heat >= 95:
        tips.append("High heat: aim for morning outings, shade, and water-play options.")
    if max_rain < 30 and max_heat < 90:
        tips.append("Great weather: outdoor parks and nature events should be excellent.")

    if max_rain >= 70:
        return (
            "High rain risk this weekend — prioritize indoor plans with easy parking.",
            "warning",
            tips,
        )

    if max_heat >= 98:
        return (
            "Very hot weekend — prioritize early-morning outings or indoor plans.",
            "warning",
            tips,
        )

    if max_heat >= 92 and avg_rain >= 45:
        return (
            "Hot and unsettled weather — mix indoor picks with short outdoor windows.",
            "info",
            tips,
        )

    if max_heat <= 86 and max_rain <= 35:
        return ("Great weather for outdoor activities this weekend. 🌤️", "success", tips)

    if min_heat >= 88 and max_rain <= 30:
        return (
            "Warm but mostly dry — outdoor plans are good, especially before noon.",
            "info",
            tips,
        )

    if avg_rain >= 40:
        return (
            "Some rain possible — keep a couple indoor backup options ready.",
            "info",
            tips,
        )

    return (
        "Mixed but manageable weather — choose flexible plans and check hourly forecasts.",
        "info",
        tips,
    )
