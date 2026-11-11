from decimal import Decimal as D

from app.config import load_pension_config


def test_config_loads_tracks_and_presets():
    cfg = load_pension_config()
    assert set(cfg.tracks) >= {"sp500", "stocks", "general", "age_under_50", "age_50_60", "age_60_plus", "other"}
    assert cfg.tracks["sp500"].preset["foreign_equity"] == D(100)
    assert cfg.tracks["stocks"].preset is None
    assert "israel_bonds" not in cfg.proxies_for("general")  # пустой тикер отброшен
    assert cfg.proxies_for("sp500")["foreign_equity"] == "^GSPC"  # трек переопределяет общий прокси
