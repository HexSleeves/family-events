from __future__ import annotations

from src.onboarding import normalize_city_list
from src.tagger.llm import EventTagger


def test_normalize_city_list_includes_home_city_and_dedupes():
    cities = normalize_city_list("Baton Rouge, Lafayette, baton rouge", fallback_home_city="Baton Rouge")
    assert cities == ["Baton Rouge", "Lafayette"]


def test_system_prompt_uses_child_profile():
    prompt = EventTagger()._system_prompt()
    assert "CHILD PROFILE" in prompt
    assert "Home city" in prompt
