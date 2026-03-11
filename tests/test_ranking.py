from datetime import date, datetime

from src.db.models import Event, EventTags, InterestProfile
from src.ranker.scoring import rank_events, score_event_breakdown
from src.ranker.weather import DayForecast


def _weather() -> dict[str, DayForecast]:
    return {
        "saturday": DayForecast(
            date=date(2026, 3, 7),
            temp_high_f=82,
            temp_low_f=68,
            precipitation_pct=10,
            description="clear",
            icon="☀️",
            uv_index=6,
        ),
        "sunday": DayForecast(
            date=date(2026, 3, 8),
            temp_high_f=82,
            temp_low_f=68,
            precipitation_pct=10,
            description="clear",
            icon="☀️",
            uv_index=6,
        ),
    }


def _event(
    title: str, *, city: str, tags: EventTags, hour: int = 10, price_min: float | None = None
) -> Event:
    return Event(
        title=title,
        description="Test event",
        start_time=datetime(2026, 3, 8, hour, 0),
        end_time=None,
        source="test",
        source_url=f"https://example.com/{title}",
        source_id=title,
        location_name="Venue",
        location_address="123 Main St",
        location_city=city,
        is_free=price_min is None,
        price_min=price_min,
        price_max=price_min,
        tags=tags,
        raw_data={},
    )


def test_ranker_prefers_toddler_focused_event_over_adult_event():
    profile = InterestProfile()
    toddler_event = _event(
        "Story Time",
        city="Lafayette",
        tags=EventTags(
            toddler_score=9,
            raw_rule_score=88,
            audience="toddler_focused",
            categories=["learning", "music"],
            noise_level="quiet",
            crowd_level="small",
            meltdown_risk="low",
            nap_compatible=True,
            confidence_score=0.8,
        ),
    )
    adult_event = _event(
        "Brewery Trivia",
        city="Lafayette",
        hour=20,
        tags=EventTags(
            toddler_score=2,
            raw_rule_score=18,
            audience="adult_skewed",
            categories=["music"],
            noise_level="loud",
            crowd_level="large",
            meltdown_risk="high",
            nap_compatible=False,
            confidence_score=0.8,
            exclusion_signals=["adult drinking focus"],
        ),
    )

    ranked = rank_events([adult_event, toddler_event], profile, _weather())

    assert ranked[0][0].title == "Story Time"
    assert ranked[0][1] > ranked[1][1]


def test_score_breakdown_applies_budget_penalty_instead_of_zeroing():
    profile = InterestProfile()
    profile.constraints.budget_per_event = 30.0
    pricey = _event(
        "Museum Day",
        city="Lafayette",
        price_min=50.0,
        tags=EventTags(
            toddler_score=8,
            raw_rule_score=75,
            audience="family_mixed",
            categories=["learning"],
            confidence_score=0.7,
        ),
    )

    breakdown = score_event_breakdown(pricey, profile, _weather())

    assert breakdown.final > 0
    assert breakdown.budget_penalty > 0


def test_score_breakdown_can_be_persisted_on_event_model():
    event = _event(
        "Persisted breakdown",
        city="Lafayette",
        tags=EventTags(
            toddler_score=8,
            raw_rule_score=80,
            audience="family_mixed",
            categories=["play"],
            confidence_score=0.6,
        ),
    )
    breakdown = score_event_breakdown(event, InterestProfile(), _weather())
    event.score_breakdown = {"final": breakdown.final, "intrinsic": breakdown.intrinsic}

    assert event.score_breakdown == {"final": breakdown.final, "intrinsic": breakdown.intrinsic}


def test_weather_service_uses_shared_http_client(monkeypatch):
    import asyncio

    from src.ranker.weather import WeatherService

    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "list": [
                    {
                        "dt_txt": "2026-03-07 12:00:00",
                        "main": {"temp": 80},
                        "pop": 0.1,
                        "weather": [{"description": "clear sky"}],
                    },
                    {
                        "dt_txt": "2026-03-08 12:00:00",
                        "main": {"temp": 82},
                        "pop": 0.2,
                        "weather": [{"description": "partly cloudy"}],
                    },
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url: str, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs["params"]
            return FakeResponse()

    def fake_build_async_client(**kwargs):
        captured["client_kwargs"] = kwargs
        return FakeClient()

    monkeypatch.setattr("src.ranker.weather.build_async_client", fake_build_async_client)
    monkeypatch.setattr("src.ranker.weather.settings.weather_api_key", "weather-key")

    async def scenario() -> None:
        forecast = await WeatherService().get_weekend_forecast(date(2026, 3, 7), date(2026, 3, 8))
        assert forecast["saturday"].temp_high_f == 80
        assert forecast["sunday"].temp_high_f == 82

    asyncio.run(scenario())

    assert captured["client_kwargs"]["service"] == "weather.openweathermap"
    assert captured["url"] == "https://api.openweathermap.org/data/2.5/forecast"
