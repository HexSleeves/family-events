from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.db.models import Event


def format_console_message(
    events_with_scores: list[tuple[Event, float]],
    weather: dict,
    child_name: str = "Your Little One",
) -> str:
    """Format the weekend plans message for console/text output."""
    # Weather line
    sat_wx = weather.get("saturday")
    sun_wx = weather.get("sunday")

    weather_line = (
        f"Weather: {sat_wx.icon} Sat {sat_wx.temp_high_f:.0f}°F"
        f" / {sun_wx.icon} Sun {sun_wx.temp_high_f:.0f}°F"
    )

    medals = ["🥇 TOP PICK", "🥈", "🥉"]
    lines = [
        f"🌟 Weekend Plans for {child_name}! 🌟",
        "",
        weather_line,
        "",
    ]

    for i, (event, _score) in enumerate(events_with_scores[:3]):
        medal = medals[i] if i < 3 else f"#{i + 1}"
        tags = event.tags

        # Day and time
        day = event.start_time.strftime("%a")
        time_str = event.start_time.strftime("%-I:%M%p").lower()

        # Price
        price = "Free" if event.is_free else f"${event.price_min or '?'}"

        # Features
        features: list[str] = []
        if tags:
            if tags.categories:
                features.extend(tags.categories[:2])
            features.append(tags.indoor_outdoor)
            if tags.stroller_friendly:
                features.append("stroller-friendly")
        feature_str = ", ".join(features)

        lines.append(f"{medal}: {event.title}")
        lines.append(f"   📍 {event.location_city} | 🕐 {day} {time_str} | 💵 {price}")
        if feature_str:
            lines.append(f"   ✨ {feature_str}")
        lines.append("")

    # If there are more events, mention it
    total = len(events_with_scores)
    if total > 3:
        lines.append(f"... and {total - 3} more options available!")
        lines.append("")

    lines.append("Have a great weekend! 🎈")
    return "\n".join(lines)
