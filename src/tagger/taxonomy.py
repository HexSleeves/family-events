"""Formal tagging taxonomy and shared constants."""

from __future__ import annotations

TAGGING_VERSION = "v2"

CATEGORY_RULES: dict[str, tuple[str, ...]] = {
    "animals": ("zoo", "animal", "petting", "farm", "wildlife", "aquarium"),
    "arts": ("art", "craft", "paint", "ceramic", "creative", "sensory", "messy"),
    "music": ("music", "concert", "sing", "band", "jazz", "dance-along"),
    "nature": ("nature", "swamp", "trail", "garden", "hike", "park", "outdoors"),
    "learning": ("story", "book", "read", "library", "learn", "museum", "science"),
    "play": (
        "play",
        "playground",
        "bounce",
        "jump",
        "kid",
        "toddler",
        "child",
        "youth",
    ),
    "sports": ("sport", "soccer", "baseball", "basketball", "fit", "gymnastics"),
    "water": ("splash", "swim", "pool", "water", "aqua", "sprinkler", "foam"),
}

POSITIVE_RULES: tuple[tuple[str, int, str], ...] = (
    ("toddler", 18, "explicitly for toddlers"),
    ("preschool", 16, "targets preschoolers"),
    ("story time", 14, "short child-friendly format"),
    ("storytime", 14, "short child-friendly format"),
    ("sensory", 14, "sensory-friendly play"),
    ("playgroup", 14, "peer toddler play"),
    ("petting zoo", 16, "hands-on animal experience"),
    ("splash", 14, "cooling water play"),
    ("playground", 14, "free play opportunity"),
    ("family", 8, "family-oriented framing"),
    ("kids", 10, "kid-oriented framing"),
    ("children", 10, "child-oriented framing"),
    ("music", 8, "music tends to engage toddlers"),
    ("craft", 8, "hands-on creative activity"),
    ("art", 6, "creative exploration"),
    ("free", 5, "low commitment"),
)

CAUTION_RULES: tuple[tuple[str, int, str], ...] = (
    ("festival", -6, "festival scale can be tiring"),
    ("fair", -5, "can be stimulating/crowded"),
    ("market", -5, "often not kid-centered"),
    ("vendor", -4, "adult browsing event"),
    ("lecture", -18, "sit-still expectation"),
    ("workshop", -8, "may skew older/structured"),
    ("evening", -8, "late start for toddlers"),
    ("night", -10, "late timing"),
    ("loud", -10, "noise overload risk"),
    ("crowd", -8, "crowd stress risk"),
    ("downtown", -3, "parking/logistics risk"),
)

EXCLUSION_RULES: tuple[tuple[str, int, str], ...] = (
    ("wine", -35, "adult drinking focus"),
    ("beer", -35, "adult drinking focus"),
    ("cocktail", -35, "adult drinking focus"),
    ("bar", -30, "bar setting"),
    ("brewery", -30, "brewery setting"),
    ("adults only", -45, "explicitly excludes kids"),
    ("21+", -45, "age-gated"),
    ("trivia", -28, "adult attention-focused"),
    ("networking", -28, "adult professional event"),
    ("5k", -24, "not toddler paced"),
    ("marathon", -28, "not toddler paced"),
)

AUDIENCE_META = {
    "toddler_focused": {"label": "Toddler-focused", "icon": "🧸"},
    "family_mixed": {"label": "Family mixed", "icon": "👨‍👩‍👧"},
    "general_public": {"label": "General public", "icon": "📍"},
    "adult_skewed": {"label": "Adult-skewed", "icon": "🚫"},
}
