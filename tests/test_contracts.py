from __future__ import annotations

import json
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest
from orbit.config import Config, ConfigChange
from orbit.config.models import ApplicationConfig
from pydantic import BaseModel, SecretStr

from orbit_config_server import (
    MAX_REMOTE_CONFIG_BYTES,
    RemoteConfigSection,
    RemoteConfigSnapshot,
    RemoteConfigurationError,
    RemoteConfigurationProvider,
)


class DatabaseSettings(BaseModel):
    host: str
    password: SecretStr


def test_snapshot_validates_a_complete_typed_section_and_redacts_repr() -> None:
    payload = b'{"host":"db.internal","password":"not-for-logs"}'
    snapshot = RemoteConfigSnapshot(version=3, payload=payload)

    parsed = snapshot.validate_as(DatabaseSettings)

    assert snapshot.version == 3
    assert parsed.host == "db.internal"
    assert parsed.password.get_secret_value() == "not-for-logs"
    assert "not-for-logs" not in repr(snapshot)
    assert "db.internal" not in repr(snapshot)


@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1"])
def test_snapshot_rejects_invalid_versions(version: object) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        RemoteConfigSnapshot(version=version, payload=b"{}")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b"null",
        b'{"key":1,"key":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"{",
        b"\xff",
    ],
)
def test_snapshot_rejects_ambiguous_or_invalid_json(payload: bytes) -> None:
    with pytest.raises(RemoteConfigurationError):
        RemoteConfigSnapshot(version=1, payload=payload)


def test_snapshot_bounds_payload_bytes() -> None:
    with pytest.raises(ValueError, match="1 to 1,048,576 bytes"):
        RemoteConfigSnapshot(version=1, payload=b"")
    with pytest.raises(ValueError, match="1 to 1,048,576 bytes"):
        RemoteConfigSnapshot(version=1, payload=b" " * (MAX_REMOTE_CONFIG_BYTES + 1))
    with pytest.raises(TypeError, match="immutable bytes"):
        RemoteConfigSnapshot(version=1, payload=bytearray(b"{}"))  # type: ignore[arg-type]


def test_snapshot_bounds_nested_values_and_keys() -> None:
    deep = b'{"x":' * 65 + b"0" + b"}" * 65
    long_key = (b"a" * 256).join((b'{"', b'":1}'))
    with pytest.raises(RemoteConfigurationError, match="nested too deeply"):
        RemoteConfigSnapshot(version=1, payload=deep)
    with pytest.raises(RemoteConfigurationError, match="invalid key"):
        RemoteConfigSnapshot(version=1, payload=long_key)


def test_snapshot_rejects_too_many_values() -> None:
    payload = b'{"values":[' + b",".join([b"0"] * 100_000) + b"]}"
    with pytest.raises(RemoteConfigurationError, match="too many values"):
        RemoteConfigSnapshot(version=1, payload=payload)


def test_model_validation_error_does_not_disclose_input_values() -> None:
    snapshot = RemoteConfigSnapshot(version=1, payload=b'{"host":"secret-host"}')

    with pytest.raises(RemoteConfigurationError) as error:
        snapshot.validate_as(DatabaseSettings)

    assert "secret-host" not in str(error.value)
    assert "secret-host" not in repr(error.value)


class FakeProvider:
    async def fetch(self) -> RemoteConfigSnapshot:
        return RemoteConfigSnapshot(version=1, payload=b"{}")

    async def _updates(self) -> AsyncIterator[RemoteConfigSnapshot]:
        yield RemoteConfigSnapshot(version=2, payload=b"{}")

    def watch(self, *, after_version: int) -> AsyncIterator[RemoteConfigSnapshot]:
        assert after_version >= 1
        return self._updates()

    async def aclose(self) -> None:
        return None


def test_provider_contract_is_structural() -> None:
    assert isinstance(FakeProvider(), RemoteConfigurationProvider)


def configured_core_section() -> Config:
    config = Config(ApplicationConfig(name="config-bridge-tests"))
    config.register("database", DatabaseSettings(host="local", password=SecretStr("local")))
    config.freeze()
    return config


def database_settings(config: Config) -> DatabaseSettings:
    """Read a typed detached Core section for bridge assertions."""
    return cast(DatabaseSettings, config.get("database"))


def test_remote_section_applies_new_versions_and_ignores_replays() -> None:
    config = configured_core_section()
    section = RemoteConfigSection(config, "database", DatabaseSettings)
    snapshot = RemoteConfigSnapshot(
        version=4,
        payload=b'{"host":"remote.internal","password":"rotated"}',
    )
    before = config.version

    assert section.apply(snapshot) is True
    assert section.last_version == 4
    assert database_settings(config).host == "remote.internal"
    assert config.version == before + 1
    assert section.apply(snapshot) is False
    assert config.version == before + 1


def test_remote_section_keeps_watermark_when_core_observer_rejects() -> None:
    config = configured_core_section()
    section = RemoteConfigSection(config, "database", DatabaseSettings)

    def reject(_change: ConfigChange) -> None:
        raise RuntimeError("operator veto")

    config.subscribe(reject)
    with pytest.raises(RuntimeError, match="operator veto"):
        section.apply(
            RemoteConfigSnapshot(
                version=8,
                payload=b'{"host":"remote.internal","password":"rotated"}',
            )
        )

    assert section.last_version == 0
    assert database_settings(config).host == "local"


def test_remote_section_serializes_concurrent_revisions() -> None:
    config = configured_core_section()
    section = RemoteConfigSection(config, "database", DatabaseSettings)

    def apply_version(version: int) -> bool:
        payload = json.dumps(
            {"host": f"db-{version}.internal", "password": "value"}, separators=(",", ":")
        ).encode()
        return section.apply(RemoteConfigSnapshot(version=version, payload=payload))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(apply_version, range(1, 21)))

    assert section.last_version == 20
    assert database_settings(config).host == "db-20.internal"
