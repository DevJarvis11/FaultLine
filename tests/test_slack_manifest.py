"""The Slack app manifest matches BUILD_SPEC and Slack's manifest reference (keys verified)."""

from pathlib import Path

import yaml

MANIFEST = Path("config/slack_manifest.yaml")


def test_manifest_matches_spec() -> None:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert set(data) == {"display_information", "features", "oauth_config", "settings"}
    assert data["display_information"]["name"] == "Faultline"
    assert data["display_information"]["description"]
    bot = data["features"]["bot_user"]
    assert bot["display_name"] == "Faultline" and bot["always_online"] is True
    assert data["oauth_config"]["scopes"]["bot"] == ["chat:write"]
    settings = data["settings"]
    assert settings["interactivity"]["is_enabled"] is True
    assert settings["socket_mode_enabled"] is True
    assert settings["org_deploy_enabled"] is False
    assert settings["token_rotation_enabled"] is False
