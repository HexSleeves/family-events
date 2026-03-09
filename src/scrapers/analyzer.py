"""LLM-powered page analyzer that generates ScrapeRecipes."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Comment
from openai import AsyncOpenAI

from src.config import settings
from src.http import build_async_client, default_timeout

from .recipe import ScrapeRecipe

_STRIP_TAGS = {
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "iframe",
    "noscript",
    "svg",
    "form",
    "button",
    "input",
    "select",
}
_STRIP_CLASSES = {
    "cookie",
    "banner",
    "advertisement",
    "ad-",
    "sidebar",
    "popup",
    "modal",
    "newsletter",
    "social",
    "share",
}
_MAX_CLEAN_CHARS = 24_000  # ~6K tokens


class UnsafeFetchTargetError(ValueError):
    """Raised when a fetch target resolves to a non-public address."""


RECIPE_SCHEMA = """
{
  "strategy": "css",
  "confidence": <float 0-1>,
  "notes": "<brief description of page structure>",
  "css": {
    "event_container": "<CSS selector for each event wrapper>",
    "fields": {
      "title":       {"selector": "<CSS>", "attr": null},
      "description": {"selector": "<CSS>", "attr": null},
      "start_time":  {"selector": "<CSS>", "attr": "<datetime or null>", "format": "<iso or human>"},
      "end_time":    {"selector": "<CSS>", "attr": "<datetime or null>", "format": "<iso or human>"},
      "location":    {"selector": "<CSS>", "attr": null},
      "url":         {"selector": "<CSS>", "attr": "href"},
      "price":       {"selector": "<CSS>", "attr": null, "default": "Free"},
      "image":       {"selector": "<CSS>", "attr": "src"}
    },
    "pagination": {
      "next_selector": "<CSS or null>",
      "max_pages": 3
    }
  }
}
Set selector to null for any field not present on the page.
"""

SYSTEM_PROMPT = f"""You are an expert web scraper. Given HTML from an events page, \
return a JSON scraping recipe with CSS selectors to extract each event.

Return ONLY valid JSON matching this schema:
{RECIPE_SCHEMA}

Rules:
- event_container: the repeating element wrapping each single event
- Use specific CSS selectors (tag.class, tag[attr], etc.)
- For dates: prefer datetime attribute on <time> tags (format: "iso"), \
otherwise use text content (format: "human")
- Set fields to null if not present
- confidence: your confidence that these selectors will extract events correctly"""


def _is_public_ip_address(value: str) -> bool:
    """Return True when the IP is acceptable for outbound scraping."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_public_addresses(hostname: str) -> list[str]:
    """Resolve hostname and reject any private or local answers."""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeFetchTargetError("Could not resolve hostname") from exc

    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.add(address)
            addresses.append(address)
    if not addresses:
        raise UnsafeFetchTargetError("Could not resolve hostname")
    for address in addresses:
        if not _is_public_ip_address(address):
            raise UnsafeFetchTargetError("Private or local network URLs are not allowed")
    return addresses


def validate_public_http_url(url: str) -> None:
    """Validate scheme, hostname, and DNS resolution for an outbound URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeFetchTargetError("URL must start with http:// or https://")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise UnsafeFetchTargetError("URL must include a hostname")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        _resolve_public_addresses(hostname)
    else:
        if not _is_public_ip_address(hostname):
            raise UnsafeFetchTargetError("Private or local network URLs are not allowed")


class _PublicIPOnlyTransport(httpx.AsyncBaseTransport):
    """Wrap httpx transport to block requests that resolve to private/local IPs."""

    def __init__(self, **kwargs: Any) -> None:
        self._transport = httpx.AsyncHTTPTransport(**kwargs)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_public_http_url(str(request.url))
        response = await self._transport.handle_async_request(request)
        if response.is_redirect:
            location = response.headers.get("location")
            if location:
                validate_public_http_url(str(request.url.join(location)))
        return response

    async def aclose(self) -> None:
        await self._transport.aclose()


class PageAnalyzer:
    """Analyzes a URL and generates a ScrapeRecipe."""

    async def analyze(self, url: str) -> ScrapeRecipe:
        """Fetch a URL and generate a scraping recipe.

        Checks for JSON-LD first (free). Falls back to LLM analysis.
        """
        html = await self._fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        # Try JSON-LD first (no LLM needed)
        jsonld_recipe = self._check_jsonld(soup)
        if jsonld_recipe:
            return jsonld_recipe

        # Fall back to LLM analysis
        cleaned = self._clean_html(soup)
        recipe = await self._llm_analyze(url, cleaned)

        # Validate recipe against the HTML
        validated = self._validate(soup, recipe)
        return validated

    # -- JSON-LD detection --------------------------------------------------

    def _check_jsonld(self, soup: BeautifulSoup) -> ScrapeRecipe | None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            events = [i for i in items if i.get("@type") == "Event"]
            if events:
                return ScrapeRecipe(
                    version=1,
                    strategy="jsonld",
                    analyzed_at=datetime.now(tz=UTC),
                    confidence=0.95,
                    notes=f"Found {len(events)} JSON-LD Event(s)",
                    jsonld={"event_type": "Event"},  # type: ignore[arg-type]
                )
        return None

    # -- HTML Cleaning ------------------------------------------------------

    def _clean_html(self, soup: BeautifulSoup) -> str:
        # Remove unwanted tags
        for tag in soup.find_all(_STRIP_TAGS):
            tag.decompose()
        # Remove comments
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()
        # Remove elements with ad/cookie classes
        for el in soup.find_all(class_=True):
            try:
                class_val = el.get("class") or []
                classes = (
                    " ".join(str(c) for c in class_val)
                    if isinstance(class_val, list)
                    else str(class_val)
                )
                if any(c in classes.lower() for c in _STRIP_CLASSES):
                    el.decompose()
            except (AttributeError, TypeError):
                continue
        # Prefer <main> content
        main = soup.find("main")
        root = main if main else soup.body or soup
        html = str(root)
        # Collapse whitespace
        html = re.sub(r"\s+", " ", html)
        # Truncate
        return html[:_MAX_CLEAN_CHARS]

    # -- LLM Analysis -------------------------------------------------------

    async def _llm_analyze(self, url: str, cleaned_html: str) -> ScrapeRecipe:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Page URL: {url}\n\nHTML:\n{cleaned_html}"},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.choices[0].message.content or "{}")
        raw["version"] = 1
        raw["analyzed_at"] = datetime.now(tz=UTC).isoformat()
        # Clean null fields from CSS strategy
        if raw.get("css") and raw["css"].get("fields"):
            fields = raw["css"]["fields"]
            for key in list(fields):
                if fields[key] is None:
                    del fields[key]
        return ScrapeRecipe.model_validate(raw)

    # -- Validation ---------------------------------------------------------

    def _validate(self, soup: BeautifulSoup, recipe: ScrapeRecipe) -> ScrapeRecipe:
        if recipe.strategy != "css" or not recipe.css:
            return recipe
        containers = soup.select(recipe.css.event_container)
        if not containers:
            recipe.confidence = min(recipe.confidence, 0.2)
            recipe.notes += " [WARN: container selector matched 0 elements]"
            return recipe
        # Check that title selector finds content in at least one container
        found_title = False
        for el in containers[:5]:
            title_el = el.select_one(recipe.css.fields.title.selector or "")
            if title_el and title_el.get_text(strip=True):
                found_title = True
                break
        if not found_title:
            recipe.confidence = min(recipe.confidence, 0.3)
            recipe.notes += " [WARN: title selector found no text]"
        return recipe

    # -- Fetch --------------------------------------------------------------

    async def _fetch(self, url: str) -> str:
        validate_public_http_url(url)
        async with build_async_client(
            service="scraper.page_analyzer",
            timeout=default_timeout(),
            transport_factory=lambda: _PublicIPOnlyTransport(retries=0),
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type and "xml" not in content_type:
                raise ValueError("URL did not return HTML content")
            text = resp.text
            if len(text) > 1_000_000:
                raise ValueError("Source page is too large to analyze")
            return text
