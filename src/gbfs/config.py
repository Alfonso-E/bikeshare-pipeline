"""Project paths and config loading."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
QUALITY_DIR = DATA_DIR / "quality"
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"
CONFIG_PATH = PROJECT_ROOT / "config" / "systems.yml"


@dataclass(frozen=True)
class System:
    key: str
    name: str
    timezone: str
    discovery_url: str
    enabled: bool = True


@dataclass(frozen=True)
class Config:
    user_agent: str
    request_timeout_seconds: int
    systems: list[System]

    def enabled_systems(self) -> list[System]:
        return [s for s in self.systems if s.enabled]

    def get(self, key: str) -> System:
        for s in self.systems:
            if s.key == key:
                return s
        raise KeyError(f"unknown system {key!r}; known: {[s.key for s in self.systems]}")


def load_config(path: Path = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Config(
        user_agent=raw.get("user_agent", "bikeshare-pipeline/0.1"),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 30)),
        systems=[System(**s) for s in raw["systems"]],
    )
