"""Weather forecasting via OpenWeatherMap."""

from dataclasses import dataclass
from datetime import date

import httpx

from src.config import settings


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
            async with httpx.AsyncClient() as client:
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
        except Exception as e:
            print(f"Weather API error: {e}")
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
        if precip > 60:
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


def summarize_weekend_recommendation(weather: dict[str, DayForecast]) -> tuple[str, str]:
    """Return (message, tone) for weekend weather guidance.

    tone: success | warning | info
    """
    sat = weather.get("saturday")
    sun = weather.get("sunday")
    if not sat or not sun:
        return ("Weather data unavailable. Plan flexible indoor/outdoor options.", "info")

    days = [sat, sun]
    max_heat = max(d.temp_high_f for d in days)
    min_heat = min(d.temp_high_f for d in days)
    max_rain = max(d.precipitation_pct for d in days)
    avg_rain = sum(d.precipitation_pct for d in days) / len(days)

    if max_rain >= 70:
        return (
            "High rain risk this weekend — prioritize indoor plans with easy parking.",
            "warning",
        )

    if max_heat >= 98:
        return (
            "Very hot weekend — prioritize early-morning outings or indoor plans.",
            "warning",
        )

    if max_heat >= 92 and avg_rain >= 45:
        return (
            "Hot and unsettled weather — mix indoor picks with short outdoor windows.",
            "info",
        )

    if max_heat <= 86 and max_rain <= 35:
        return ("Great weather for outdoor activities this weekend. 🌤️", "success")

    if min_heat >= 88 and max_rain <= 30:
        return (
            "Warm but mostly dry — outdoor plans are good, especially before noon.",
            "info",
        )

    if avg_rain >= 40:
        return (
            "Some rain possible — keep a couple indoor backup options ready.",
            "info",
        )

    return ("Mixed but manageable weather — choose flexible plans and check hourly forecasts.", "info")
