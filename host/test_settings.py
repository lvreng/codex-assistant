import pytest

from settings import Settings, validate, validate_expiry


def test_validate_accepts_pixel_theme():
    assert validate({"theme": "pixel"})["theme"] == "pixel"


def test_rotation_mode_defaults_to_active_and_persists(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    assert settings.poll()["rotation_mode"] == "active"
    settings.set_rotation_mode("off")
    assert Settings(settings.path).poll()["rotation_mode"] == "off"
    settings.set_rotation_mode("all")
    assert Settings(settings.path).poll()["rotation_mode"] == "all"


@pytest.mark.parametrize("value", ("", "active-only", None, 1))
def test_rotation_mode_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="rotation"):
        validate({"rotation_mode": value})


@pytest.mark.parametrize("theme", ("dark", "light", "beach", "pixel", "mechanical", "paper", "glass", "vangogh"))
def test_validate_accepts_all_themes(theme):
    assert validate({"theme": theme})["theme"] == theme


def test_validate_rejects_unknown_theme():
    with pytest.raises(ValueError, match="theme must be"):
        validate({"theme": "unknown"})


@pytest.mark.parametrize("value", ("", "2026-10-22T23:59", "2028-02-29T00:00"))
def test_membership_expiry_round_trip(tmp_path, value):
    settings = Settings(tmp_path / "settings.json")
    settings.set_api_mode("official")
    settings.set_membership_expires_at(value)
    saved = Settings(settings.path).poll()
    assert saved["membership_expires_at"] == value
    assert saved["api_mode"] == "official"
    settings.set_membership_expires_at("")
    assert Settings(settings.path).poll()["membership_expires_at"] == ""


@pytest.mark.parametrize("value", (None, 1, "2026-02-29T12:00", "2026-10-01T24:00",
                                  "2026-1-01T00:00", "2200-01-01T00:00", "2026-10-22"))
def test_membership_expiry_rejects_invalid_dates(value):
    with pytest.raises(ValueError):
        validate_expiry(value)
