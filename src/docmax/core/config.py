"""One configuration strategy, with one precedence chain.

Settings arrive from four places, and the whole point of this module is that
they are combined in exactly one order, in exactly one place:

.. code-block:: text

    defaults  →  config file (TOML)  →  environment  →  runtime override

Each layer wins over the ones before it. A ``--engine cloud`` on the command
line beats ``DOCMAX_TOOL_OCR_ENGINE``, which beats ``[tools.ocr] engine`` in the
file, which beats the built-in default.

**Nothing else in DocMax reads the environment.** Scattered ``os.environ`` lookups
are how a setting comes to mean two different things in two code paths, and how
"why is it using the cloud?" becomes unanswerable. There is one reader, here.

``core`` may not import a UI framework, so this module never prompts, never
prints, and never exits. It reads, validates, and raises — the interface layer
decides what a user sees.

Unknown keys are an **error**, not a shrug. v2's settings screen wrote config
keys that nothing ever read and reported "settings saved", which was untrue; a
misspelled ``offlien = true`` doing nothing silently is the same failure. The
cost is that a config written for a newer DocMax fails on an older one, which is
the better direction to fail in: it says so, rather than quietly ignoring the
setting the user cared about.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

import platformdirs

from docmax.core.branding import CONFIG_DIR_NAME, DEFAULT_CLOUD_ENDPOINT, ENV_PREFIX
from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The file a user edits. DocMax reads it and never writes it — see ADR 0008.
CONFIG_FILENAME: Final = "config.toml"

#: The file DocMax maintains. Separate owner, separate format. See ADR 0008.
CONSENT_FILENAME: Final = "consent.json"

#: ``DOCMAX_OFFLINE``, ``DOCMAX_CLOUD_ENDPOINT``, ``DOCMAX_API_KEY``.
ENV_OFFLINE: Final = f"{ENV_PREFIX}OFFLINE"
ENV_ENDPOINT: Final = f"{ENV_PREFIX}CLOUD_ENDPOINT"
ENV_API_KEY: Final = f"{ENV_PREFIX}API_KEY"
#: ``DOCMAX_TOOL_OCR_ENGINE`` — per-tool, for scripts and CI.
ENV_TOOL_PREFIX: Final = f"{ENV_PREFIX}TOOL_"
ENV_TOOL_SUFFIX: Final = "_ENGINE"

#: Accepted spellings of a boolean in the environment. Deliberately closed: an
#: unrecognised value raises rather than being read as false, because
#: ``DOCMAX_OFFLINE=yes`` silently meaning "not offline" would leak documents.
_TRUE: Final = frozenset({"1", "true", "yes", "on"})
_FALSE: Final = frozenset({"0", "false", "no", "off"})

_TOP_LEVEL_KEYS: Final = frozenset({"offline", "cloud", "tools"})
_CLOUD_KEYS: Final = frozenset({"endpoint", "api_key"})
_TOOL_KEYS: Final = frozenset({"engine"})


def config_dir() -> Path:
    """The one configuration directory. Not created here — nothing is."""
    return Path(platformdirs.user_config_dir(CONFIG_DIR_NAME))


def config_file() -> Path:
    return config_dir() / CONFIG_FILENAME


def consent_file() -> Path:
    return config_dir() / CONSENT_FILENAME


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved settings: what every layer below the interfaces reads.

    Frozen, because a setting that changes underneath a running operation is a
    setting whose value cannot be reported afterwards. :meth:`with_overrides`
    returns a new one.
    """

    #: Cloud is unreachable, **regardless of flags** — including an explicit
    #: ``--engine cloud``. This is the switch someone flips because policy says
    #: documents do not leave the building, so an argument must not defeat it.
    offline: bool = False
    cloud_endpoint: str = DEFAULT_CLOUD_ENDPOINT
    api_key: str | None = None
    #: Per-tool engine preference, ``{"ocr": Engine.CLOUD}``. Absent means auto.
    tool_engines: Mapping[str, Engine] = field(default_factory=dict)
    #: Where the file came from, or ``None`` if there was none. Carried so an
    #: error can name the file the user has to edit.
    source: Path | None = None

    def engine_for(self, tool: str) -> Engine | None:
        """The user's preference for ``tool``, or ``None`` if they have none."""
        return self.tool_engines.get(tool)

    def with_overrides(
        self,
        *,
        offline: bool | None = None,
        cloud_endpoint: str | None = None,
        api_key: str | None = None,
    ) -> Config:
        """Apply the highest-precedence layer: explicit runtime arguments.

        ``None`` means "not specified", so a caller passing nothing changes
        nothing. ``offline`` is one-way — see :meth:`Config.offline`; an
        override may turn it *on* but never off, because the flag exists to be
        un-overridable by the command line.
        """
        endpoint = _validated_endpoint(cloud_endpoint) if cloud_endpoint is not None else None
        return replace(
            self,
            offline=self.offline or bool(offline),
            cloud_endpoint=endpoint if endpoint is not None else self.cloud_endpoint,
            api_key=api_key if api_key is not None else self.api_key,
        )


def _fail(message: str, *, remedy: str, key: str | None = None) -> InvalidParameterError:
    return InvalidParameterError(message, remedy=remedy, context={"key": key} if key else None)


def _validated_endpoint(value: str) -> str:
    """Reject an endpoint that would send documents in the clear.

    ``cloud-api.md`` requires TLS and exempts ``localhost`` so that self-hosted
    development works. The check lives here rather than in the client because a
    bad endpoint should fail when it is *configured*, not on the first upload.
    """
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        raise _fail(
            f"Cloud endpoint must be an http(s) URL: {value!r}",
            remedy="Use a URL such as https://api.example.com.",
            key="cloud.endpoint",
        )
    if not parts.netloc:
        raise _fail(
            f"Cloud endpoint has no host: {value!r}",
            remedy="Use a full URL, including the scheme and host.",
            key="cloud.endpoint",
        )
    host = parts.hostname or ""
    if parts.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise _fail(
            f"Refusing a plaintext endpoint: {value!r}",
            remedy="Use https://. Plaintext is allowed only for localhost.",
            key="cloud.endpoint",
        )
    return value.rstrip("/")


def _validated_engine(value: object, *, key: str) -> Engine:
    if not isinstance(value, str):
        raise _fail(
            f"{key} must be a string, not {type(value).__name__}.",
            remedy=f"Use one of: {', '.join(e.value for e in Engine)}.",
            key=key,
        )
    try:
        return Engine(value.lower())
    except ValueError:
        raise _fail(
            f"Unknown engine {value!r} for {key}.",
            remedy=f"Use one of: {', '.join(e.value for e in Engine)}.",
            key=key,
        ) from None


def _validated_bool(value: object, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise _fail(
        f"{key} must be true or false, not {value!r}.",
        remedy="Write it without quotes: offline = true",
        key=key,
    )


def _reject_unknown(found: Mapping[str, Any], allowed: frozenset[str], *, where: str) -> None:
    unknown = sorted(set(found) - allowed)
    if unknown:
        raise _fail(
            f"Unknown setting{'s' if len(unknown) > 1 else ''} in {where}: {', '.join(unknown)}.",
            remedy=f"Known here: {', '.join(sorted(allowed))}.",
            key=f"{where}.{unknown[0]}" if where != "the config file" else unknown[0],
        )


def _from_file(path: Path) -> dict[str, Any]:
    """Parse and validate the TOML file. A missing file is not an error."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise _fail(
            f"Could not read the config file at {path}: {exc.strerror or exc}",
            remedy="Check the file's permissions, or delete it to start fresh.",
        ) from exc

    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _fail(
            f"The config file at {path} is not valid TOML: {exc}",
            remedy="Fix the syntax, or delete the file to fall back to defaults.",
        ) from exc

    _reject_unknown(document, _TOP_LEVEL_KEYS, where="the config file")

    settings: dict[str, Any] = {}
    if "offline" in document:
        settings["offline"] = _validated_bool(document["offline"], key="offline")

    cloud = document.get("cloud", {})
    if not isinstance(cloud, dict):
        raise _fail("[cloud] must be a table.", remedy="Write it as [cloud].", key="cloud")
    _reject_unknown(cloud, _CLOUD_KEYS, where="[cloud]")
    if "endpoint" in cloud:
        settings["cloud_endpoint"] = _validated_endpoint(str(cloud["endpoint"]))
    if "api_key" in cloud:
        settings["api_key"] = str(cloud["api_key"])

    tools = document.get("tools", {})
    if not isinstance(tools, dict):
        raise _fail("[tools] must be a table.", remedy="Write it as [tools.ocr].", key="tools")
    engines: dict[str, Engine] = {}
    for name, table in tools.items():
        if not isinstance(table, dict):
            raise _fail(
                f"[tools.{name}] must be a table.",
                remedy=f'Write it as [tools.{name}] with engine = "local".',
                key=f"tools.{name}",
            )
        _reject_unknown(table, _TOOL_KEYS, where=f"[tools.{name}]")
        if "engine" in table:
            engines[name] = _validated_engine(table["engine"], key=f"tools.{name}.engine")
    if engines:
        settings["tool_engines"] = engines

    return settings


def _env_bool(name: str, environ: Mapping[str, str]) -> bool | None:
    raw = environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise _fail(
        f"{name} must be one of {'/'.join(sorted(_TRUE | _FALSE))}, not {raw!r}.",
        remedy=f"Set {name}=true or unset it.",
        key=name,
    )


def _from_env(environ: Mapping[str, str]) -> dict[str, Any]:
    settings: dict[str, Any] = {}

    offline = _env_bool(ENV_OFFLINE, environ)
    if offline is not None:
        settings["offline"] = offline

    endpoint = environ.get(ENV_ENDPOINT)
    if endpoint:
        settings["cloud_endpoint"] = _validated_endpoint(endpoint)

    api_key = environ.get(ENV_API_KEY)
    if api_key:
        settings["api_key"] = api_key

    engines: dict[str, Engine] = {}
    for name, value in environ.items():
        if not (name.startswith(ENV_TOOL_PREFIX) and name.endswith(ENV_TOOL_SUFFIX)):
            continue
        tool = name[len(ENV_TOOL_PREFIX) : -len(ENV_TOOL_SUFFIX)].lower().replace("_", "-")
        if tool:
            engines[tool] = _validated_engine(value, key=name)
    if engines:
        settings["tool_engines"] = engines

    return settings


def load(
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Resolve the configuration, applying every layer in precedence order.

    ``path`` and ``environ`` are injectable so that tests never touch a real
    home directory or the real environment — and so a caller can load a config
    that is not the user's, which the server needs.

    Runtime overrides are the fourth layer and are applied by the caller through
    :meth:`Config.with_overrides`, because only the interface knows what the
    user typed.
    """
    source = config_file() if path is None else path
    environ = os.environ if environ is None else environ

    settings: dict[str, Any] = {}
    settings.update(_from_file(source))
    file_engines: Mapping[str, Engine] = settings.get("tool_engines", {})

    env_settings = _from_env(environ)
    env_engines: Mapping[str, Engine] = env_settings.pop("tool_engines", {})
    settings.update(env_settings)

    # Per-tool preferences merge rather than replace: setting one tool's engine
    # in the environment must not silently drop the others set in the file.
    if file_engines or env_engines:
        settings["tool_engines"] = {**file_engines, **env_engines}

    return Config(**settings, source=source if source.is_file() else None)


__all__ = [
    "CONFIG_FILENAME",
    "CONSENT_FILENAME",
    "Config",
    "config_dir",
    "config_file",
    "consent_file",
    "load",
]
