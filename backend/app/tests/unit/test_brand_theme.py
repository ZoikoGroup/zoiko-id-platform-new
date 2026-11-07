"""The fuller brand theme: a colour that is readable, plain welcome text, and a logo that is a real inline raster image."""

from __future__ import annotations

import base64

import pytest
from zid_platform.modules.configuration.domain.settings import (
    SettingsError,
    contrast_with_white,
    validate,
)

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode()


def brand(**over: str) -> dict[str, str]:
    return validate(
        "BRANDING",
        {"display_name": "Acme", **over},
        mandatory_kinds=frozenset(),
        known_kinds=frozenset(),
    )  # type: ignore[return-value]


def test_a_dark_enough_colour_is_kept_lower_cased_and_a_pale_one_refused() -> None:
    assert brand(accent_hex="#1A73E8")["accent_hex"] == "#1a73e8"
    assert contrast_with_white("#000000") > 20 and contrast_with_white("#ffffff") == pytest.approx(
        1.0, abs=0.01
    )
    for bad in ("#ffff00", "#cccccc", "blue", "#12345", "#1a73e8; x"):
        with pytest.raises(SettingsError):
            brand(accent_hex=bad)


def test_welcome_text_is_plain_short_and_cannot_pose_as_the_platform() -> None:
    assert brand(welcome_text="Welcome back")["welcome_text"] == "Welcome back"
    for bad in ("<b>hi</b>", "x" * 161, "line\x00break"):
        with pytest.raises(SettingsError) as e:
            brand(welcome_text=bad)
        assert e.value.code == "VALIDATION_FAILED"
    with pytest.raises(SettingsError) as e:
        brand(welcome_text="Official support portal of Zoiko")
    assert e.value.code == "BRANDING_SPOOFING"


def test_a_logo_must_be_a_real_small_inline_raster_image() -> None:
    assert brand(logo=PNG)["logo"] == PNG
    assert brand()["logo"] == ""
    wrong_magic = "data:image/png;base64," + base64.b64encode(b"<svg onload=x>").decode()
    too_big = (
        "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 50_000).decode()
    )
    for bad in (
        "https://evil.example/logo.png",
        "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode(),
        wrong_magic,
        too_big,
        "data:image/png;base64,@@@",
    ):
        with pytest.raises(SettingsError):
            brand(logo=bad)
