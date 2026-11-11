"""Настройки приложения (.env) и конфиг пенсионного модуля (config/pension.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PENSION_CONFIG = ROOT / "config" / "pension.toml"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    backup_dir: Path | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "finance.db"


def load_settings(env_file: Path = ROOT / ".env") -> Settings:
    load_dotenv(env_file)
    data_dir = Path(os.getenv("DATA_DIR") or (ROOT / "data"))
    backup = os.getenv("BACKUP_DIR")
    return Settings(data_dir=data_dir, backup_dir=Path(backup) if backup else None)


@dataclass(frozen=True)
class TrackDef:
    key: str
    label: str
    preset: dict[str, Decimal] | None
    proxies: dict[str, str]


@dataclass(frozen=True)
class PensionConfig:
    usd_ils: str
    proxies: dict[str, str]
    tracks: dict[str, TrackDef]
    columns: dict[str, str] = field(default_factory=dict)

    def proxies_for(self, track_key: str) -> dict[str, str]:
        """Тикеры для трека: общие прокси, переопределённые треком. Пустые тикеры отбрасываются."""
        merged = dict(self.proxies)
        merged.update(self.tracks[track_key].proxies)
        return {k: v for k, v in merged.items() if v}


def load_pension_config(path: Path = DEFAULT_PENSION_CONFIG) -> PensionConfig:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    tracks: dict[str, TrackDef] = {}
    for key, body in raw.get("tracks", {}).items():
        preset_raw = body.get("preset")
        preset = (
            {k: Decimal(str(v)) for k, v in preset_raw.items()} if preset_raw is not None else None
        )
        tracks[key] = TrackDef(
            key=key,
            label=body["label"],
            preset=preset,
            proxies=dict(body.get("proxies", {})),
        )
    return PensionConfig(
        usd_ils=raw["usd_ils"],
        proxies=dict(raw.get("proxies", {})),
        tracks=tracks,
        columns=dict(raw.get("columns", {})),
    )
