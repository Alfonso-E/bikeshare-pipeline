"""Resolve a GBFS discovery document into concrete feed URLs.

GBFS systems publish a `gbfs.json` that maps feed names to URLs. The layout
differs between v1/v2 (language-keyed) and v3 (flat), and some operators nest
the feed list under a language code even in v3. This module normalises that.
"""
from __future__ import annotations

import requests

from .config import Config, System

_PREFERRED_LANGUAGES = ("en", "en-US")


def _extract_feed_list(payload: dict) -> list[dict]:
    data = payload.get("data", {})

    # GBFS v3: {"data": {"feeds": [...]}}
    if isinstance(data.get("feeds"), list):
        return data["feeds"]

    # GBFS v1/v2: {"data": {"en": {"feeds": [...]}, "fr": {...}}}
    for lang in _PREFERRED_LANGUAGES:
        if lang in data and isinstance(data[lang], dict):
            return data[lang].get("feeds", [])

    # Fall back to whichever language the operator listed first.
    for value in data.values():
        if isinstance(value, dict) and isinstance(value.get("feeds"), list):
            return value["feeds"]

    raise ValueError("could not locate a feed list in the discovery document")


def resolve_feeds(system: System, config: Config) -> dict[str, str]:
    """Return {feed_name: url} for one system."""
    response = requests.get(
        system.discovery_url,
        timeout=config.request_timeout_seconds,
        headers={"User-Agent": config.user_agent},
    )
    response.raise_for_status()
    feeds = _extract_feed_list(response.json())
    return {f["name"]: f["url"] for f in feeds if "name" in f and "url" in f}


def fetch_feed(url: str, config: Config) -> dict:
    """Fetch and parse a single GBFS feed document."""
    response = requests.get(
        url,
        timeout=config.request_timeout_seconds,
        headers={"User-Agent": config.user_agent},
    )
    response.raise_for_status()
    return response.json()
