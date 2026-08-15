"""Configuration: one precedence chain, and a refusal to fail silently.

The tests that matter here are the ones asserting a setting *lands in the right
order* and that a mistake is *reported*. v2's settings screen wrote keys nothing
read and said "settings saved"; a config that accepts `offlien = true` and does
nothing is the same failure wearing different clothes.

Every test injects both the file path and the environment, so none of them can
read the developer's real config or be perturbed by a stray `DOCMAX_*` variable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docmax.core.config import (
    ENV_API_KEY,
    ENV_ENDPOINT,
    ENV_OFFLINE,
    Config,
    config_dir,
    config_file,
    consent_file,
    load,
)
from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine


def write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Defaults and locations
# ---------------------------------------------------------------------------


def test_defaults_apply_with_no_file_and_no_environment(tmp_path: Path) -> None:
    config = load(path=tmp_path / "absent.toml", environ={})

    assert config.offline is False
    assert config.api_key is None
    assert config.tool_engines == {}
    assert config.source is None, "no file was read, so none is named"


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """First run has no config. That is normal, not a failure."""
    assert load(path=tmp_path / "nope.toml", environ={}).offline is False


def test_the_two_files_share_one_directory() -> None:
    """v2 shipped two config directories, one of which was never read."""
    assert config_file().parent == config_dir()
    assert consent_file().parent == config_dir()
    assert config_file() != consent_file()


def test_locating_the_config_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking where config lives must not have side effects on disk.

    v2 created a config directory as an *import* side effect, and then never
    read from it. Patching `platformdirs` rather than `XDG_CONFIG_HOME` keeps
    this meaningful on Windows and macOS, where that variable is ignored.
    """
    target = tmp_path / "nowhere"
    monkeypatch.setattr(
        "docmax.core.config.platformdirs.user_config_dir", lambda *a, **k: str(target)
    )

    assert config_dir() == target
    assert config_file() == target / "config.toml"
    assert consent_file() == target / "consent.json"
    assert not target.exists(), "locating a path must not create it"


# ---------------------------------------------------------------------------
# The file layer
# ---------------------------------------------------------------------------


def test_settings_are_read_from_the_file(tmp_path: Path) -> None:
    path = write(
        tmp_path / "config.toml",
        """
        offline = true

        [cloud]
        endpoint = "https://api.example.com"
        api_key = "dmx_live_abc"

        [tools.ocr]
        engine = "cloud"
        """,
    )

    config = load(path=path, environ={})

    assert config.offline is True
    assert config.cloud_endpoint == "https://api.example.com"
    assert config.api_key == "dmx_live_abc"
    assert config.engine_for("ocr") is Engine.CLOUD
    assert config.source == path


def test_a_trailing_slash_is_stripped_from_the_endpoint(tmp_path: Path) -> None:
    """So that joining a path cannot produce a double slash."""
    path = write(tmp_path / "c.toml", '[cloud]\nendpoint = "https://api.example.com/"\n')

    assert load(path=path, environ={}).cloud_endpoint == "https://api.example.com"


def test_engine_for_an_unconfigured_tool_is_none(tmp_path: Path) -> None:
    """Absent means auto — it does not mean local."""
    assert load(path=tmp_path / "x.toml", environ={}).engine_for("merge") is None


# ---------------------------------------------------------------------------
# Refusing to fail silently
# ---------------------------------------------------------------------------


def test_an_unknown_top_level_key_is_reported(tmp_path: Path) -> None:
    """`offlien = true` must not be accepted and quietly ignored."""
    path = write(tmp_path / "c.toml", "offlien = true\n")

    with pytest.raises(InvalidParameterError) as caught:
        load(path=path, environ={})

    assert "offlien" in str(caught.value)
    assert caught.value.remedy


def test_an_unknown_key_inside_cloud_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[cloud]\nendpont = "https://x.example"\n')

    with pytest.raises(InvalidParameterError) as caught:
        load(path=path, environ={})

    assert "endpont" in str(caught.value)


def test_an_unknown_key_inside_a_tool_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[tools.ocr]\nengien = "cloud"\n')

    with pytest.raises(InvalidParameterError):
        load(path=path, environ={})


def test_malformed_toml_names_the_file(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", "offline = = true\n")

    with pytest.raises(InvalidParameterError) as caught:
        load(path=path, environ={})

    assert str(path) in str(caught.value)


def test_a_non_boolean_offline_is_reported(tmp_path: Path) -> None:
    """`offline = "true"` is a string, and would otherwise be truthy forever."""
    path = write(tmp_path / "c.toml", 'offline = "true"\n')

    with pytest.raises(InvalidParameterError):
        load(path=path, environ={})


def test_an_unknown_engine_lists_the_valid_ones(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[tools.ocr]\nengine = "magic"\n')

    with pytest.raises(InvalidParameterError) as caught:
        load(path=path, environ={})

    assert "local" in (caught.value.remedy or "")


# ---------------------------------------------------------------------------
# Endpoint validation — documents must not travel in the clear
# ---------------------------------------------------------------------------


def test_a_plaintext_endpoint_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[cloud]\nendpoint = "http://api.example.com"\n')

    with pytest.raises(InvalidParameterError) as caught:
        load(path=path, environ={})

    assert "https" in (caught.value.remedy or "")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_plaintext_localhost_is_allowed(tmp_path: Path, host: str) -> None:
    """Self-hosted development has to work without a certificate."""
    path = write(tmp_path / "c.toml", f'[cloud]\nendpoint = "http://{host}:8080"\n')

    assert load(path=path, environ={}).cloud_endpoint == f"http://{host}:8080"


@pytest.mark.parametrize("value", ["ftp://x.example", "not-a-url", "https://"])
def test_a_malformed_endpoint_is_refused(tmp_path: Path, value: str) -> None:
    path = write(tmp_path / "c.toml", f'[cloud]\nendpoint = "{value}"\n')

    with pytest.raises(InvalidParameterError):
        load(path=path, environ={})


# ---------------------------------------------------------------------------
# The environment layer
# ---------------------------------------------------------------------------


def test_the_environment_beats_the_file(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[cloud]\nendpoint = "https://from-file.example"\n')

    config = load(path=path, environ={ENV_ENDPOINT: "https://from-env.example"})

    assert config.cloud_endpoint == "https://from-env.example"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_offline_is_settable_from_the_environment(tmp_path: Path, value: str) -> None:
    assert load(path=tmp_path / "x.toml", environ={ENV_OFFLINE: value}).offline is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_offline_is_unsettable_from_the_environment(tmp_path: Path, value: str) -> None:
    path = write(tmp_path / "c.toml", "offline = true\n")

    assert load(path=path, environ={ENV_OFFLINE: value}).offline is False


def test_an_unrecognised_boolean_is_refused_not_read_as_false(tmp_path: Path) -> None:
    """`DOCMAX_OFFLINE=maybe` silently meaning "go online" would leak documents."""
    with pytest.raises(InvalidParameterError) as caught:
        load(path=tmp_path / "x.toml", environ={ENV_OFFLINE: "maybe"})

    assert ENV_OFFLINE in str(caught.value)


def test_the_api_key_comes_from_the_environment(tmp_path: Path) -> None:
    config = load(path=tmp_path / "x.toml", environ={ENV_API_KEY: "dmx_live_env"})

    assert config.api_key == "dmx_live_env"


def test_a_per_tool_engine_comes_from_the_environment(tmp_path: Path) -> None:
    config = load(path=tmp_path / "x.toml", environ={"DOCMAX_TOOL_OCR_ENGINE": "cloud"})

    assert config.engine_for("ocr") is Engine.CLOUD


def test_underscores_in_an_env_tool_name_become_hyphens(tmp_path: Path) -> None:
    """Tools are named `remove-bg`; environment variables cannot contain a hyphen."""
    config = load(path=tmp_path / "x.toml", environ={"DOCMAX_TOOL_REMOVE_BG_ENGINE": "cloud"})

    assert config.engine_for("remove-bg") is Engine.CLOUD


def test_per_tool_preferences_merge_rather_than_replace(tmp_path: Path) -> None:
    """Setting one tool in the environment must not drop the others from the file."""
    path = write(
        tmp_path / "c.toml",
        '[tools.ocr]\nengine = "cloud"\n\n[tools.compress]\nengine = "local"\n',
    )

    config = load(path=path, environ={"DOCMAX_TOOL_COMPRESS_ENGINE": "cloud"})

    assert config.engine_for("ocr") is Engine.CLOUD, "the file's other tool survived"
    assert config.engine_for("compress") is Engine.CLOUD, "the environment won for this one"


def test_an_unrelated_environment_variable_is_ignored(tmp_path: Path) -> None:
    config = load(path=tmp_path / "x.toml", environ={"DOCMAX_SOMETHING_ELSE": "x", "PATH": "/"})

    assert config.tool_engines == {}


# ---------------------------------------------------------------------------
# The runtime-override layer, and the one-way offline flag
# ---------------------------------------------------------------------------


def test_a_runtime_override_beats_the_environment(tmp_path: Path) -> None:
    config = load(path=tmp_path / "x.toml", environ={ENV_ENDPOINT: "https://env.example"})

    overridden = config.with_overrides(cloud_endpoint="https://argv.example")

    assert overridden.cloud_endpoint == "https://argv.example"


def test_overriding_with_nothing_changes_nothing(tmp_path: Path) -> None:
    config = load(path=tmp_path / "x.toml", environ={ENV_API_KEY: "k"})

    assert config.with_overrides() == config


def test_a_runtime_override_may_turn_offline_on() -> None:
    assert Config().with_overrides(offline=True).offline is True


def test_a_runtime_override_may_not_turn_offline_off() -> None:
    """`offline` exists to be un-overridable — including by `--engine cloud`.

    Someone sets this because policy says documents do not leave the building.
    An argument that defeats it makes it decoration.
    """
    config = Config(offline=True)

    assert config.with_overrides(offline=False).offline is True


def test_an_overridden_endpoint_is_validated(tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError):
        Config().with_overrides(cloud_endpoint="http://not-localhost.example")


def test_config_is_frozen() -> None:
    """A setting that changes mid-operation cannot be reported afterwards."""
    from dataclasses import FrozenInstanceError

    config = Config()
    with pytest.raises(FrozenInstanceError):
        config.offline = True  # type: ignore[misc]
