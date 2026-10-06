# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Provider-neutral remote configuration contracts and bounded wire snapshots."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from threading import RLock
from typing import Protocol, TypeVar, runtime_checkable

from orbit.config import Config
from pydantic import BaseModel

MAX_REMOTE_CONFIG_BYTES = 1_048_576
_MAX_DEPTH = 64
_MAX_VALUES = 100_000
_MAX_KEY_LENGTH = 255

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class RemoteConfigurationError(ValueError):
    """Sanitized error raised when a remote configuration payload is invalid."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous JSON objects rather than silently selecting one value."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RemoteConfigurationError("Remote configuration contains duplicate keys.")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    """Reject non-standard JSON numeric constants such as NaN and Infinity."""
    del value
    raise RemoteConfigurationError("Remote configuration contains an invalid number.")


def _validate_tree(value: object, *, depth: int = 0, count: list[int] | None = None) -> None:
    """Bound parsed JSON structure before passing values to an application model."""
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > _MAX_VALUES:
        raise RemoteConfigurationError("Remote configuration contains too many values.")
    if isinstance(value, dict):
        if depth >= _MAX_DEPTH:
            raise RemoteConfigurationError("Remote configuration is nested too deeply.")
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not 1 <= len(key) <= _MAX_KEY_LENGTH
                or any(ord(character) < 32 or ord(character) == 127 for character in key)
            ):
                raise RemoteConfigurationError("Remote configuration contains an invalid key.")
            _validate_tree(item, depth=depth + 1, count=count)
    elif isinstance(value, list):
        if depth >= _MAX_DEPTH:
            raise RemoteConfigurationError("Remote configuration is nested too deeply.")
        for item in value:
            _validate_tree(item, depth=depth + 1, count=count)
    elif (
        value is None
        or isinstance(value, (bool, str, int))
        or (isinstance(value, float) and math.isfinite(value))
    ):
        return
    else:
        raise RemoteConfigurationError("Remote configuration contains an invalid value.")


@dataclass(frozen=True, repr=False, slots=True)
class RemoteConfigSnapshot:
    """One complete, immutable JSON section at a provider-defined monotonic revision.

    Payload bytes are omitted from repr so configuration values, including credentials, cannot
    accidentally leak through ordinary diagnostics. Providers should send complete replacement
    snapshots rather than patches; Core's typed model and atomic ``Config.reload_section`` remain
    the authority for accepting a new local section.
    """

    version: int
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        """Validate revision and bounded strict JSON at the provider boundary."""
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Remote configuration versions must be positive integers.")
        if not isinstance(self.payload, bytes):
            raise TypeError("Remote configuration payloads must be immutable bytes.")
        if not 1 <= len(self.payload) <= MAX_REMOTE_CONFIG_BYTES:
            raise ValueError(
                f"Remote configuration payloads must be 1 to {MAX_REMOTE_CONFIG_BYTES:,} bytes."
            )
        try:
            decoded = json.loads(
                self.payload,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except RemoteConfigurationError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            raise RemoteConfigurationError("Remote configuration is not valid JSON.") from None
        if not isinstance(decoded, dict):
            raise RemoteConfigurationError("Remote configuration must be a JSON object.")
        _validate_tree(decoded)

    def validate_as(self, model_type: type[_ModelT]) -> _ModelT:
        """Validate this complete snapshot against the application's declared section model.

        Pydantic's detailed validation exception is intentionally not exposed because it can
        include submitted values. Adapters may record the generic failure class, never payloads.
        """
        if not isinstance(model_type, type) or not issubclass(model_type, BaseModel):
            raise TypeError("Remote configuration requires a Pydantic model type.")
        try:
            values = json.loads(
                self.payload,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
            return model_type.model_validate(values)
        except Exception:
            raise RemoteConfigurationError(
                "Remote configuration does not match the registered section model."
            ) from None


@runtime_checkable
class RemoteConfigurationProvider(Protocol):
    """Capability contract implemented by separately installed configuration adapters.

    A provider owns its SDK, transport, credentials, server-specific settings and cleanup. It
    returns complete snapshots with strictly increasing positive versions. ``watch`` may omit
    intermediate versions only because every yielded item is a complete replacement snapshot.
    """

    async def fetch(self) -> RemoteConfigSnapshot:
        """Fetch the current complete section snapshot."""

    def watch(self, *, after_version: int) -> AsyncIterator[RemoteConfigSnapshot]:
        """Yield newer complete snapshots until the consumer closes the iterator."""

    async def aclose(self) -> None:
        """Release provider-owned transport and SDK resources."""


class RemoteConfigSection:
    """Apply newer remote snapshots to one registered Core configuration section.

    Revisions are process-local unless an adapter supplies ``last_version`` from durable cursor
    storage. The binding serializes concurrent updates, ignores stale/repeated revisions, validates
    the complete snapshot against the section's exact registered model type, then delegates the
    local atomic commit and observer veto to Core.
    """

    def __init__(
        self,
        config: Config,
        section_name: str,
        model_type: type[_ModelT],
        *,
        last_version: int = 0,
    ) -> None:
        """Bind one remote stream to an existing Core section without owning provider resources."""
        if not isinstance(config, Config):
            raise TypeError("Remote configuration requires a Core Config owner.")
        if not isinstance(section_name, str) or not section_name:
            raise TypeError("A nonempty Core configuration section name is required.")
        if not isinstance(model_type, type) or not issubclass(model_type, BaseModel):
            raise TypeError("Remote configuration requires a Pydantic section model type.")
        if isinstance(last_version, bool) or not isinstance(last_version, int) or last_version < 0:
            raise ValueError("last_version must be a nonnegative integer.")
        registered = config.get(section_name)
        if type(registered) is not model_type:
            raise TypeError("The remote configuration model must match the registered section.")
        self._config = config
        self._section_name = section_name
        self._model_type = model_type
        self._last_version = last_version
        self._lock = RLock()

    @property
    def last_version(self) -> int:
        """Return the greatest provider revision successfully committed by this binding."""
        with self._lock:
            return self._last_version

    def apply(self, snapshot: RemoteConfigSnapshot) -> bool:
        """Validate and atomically apply a newer snapshot; return false for stale revisions.

        If typed validation or a Core observer rejects the replacement, the current Core section
        and the revision watermark remain unchanged, allowing an adapter to report or retry the
        failure according to its own policy.
        """
        if not isinstance(snapshot, RemoteConfigSnapshot):
            raise TypeError("Remote configuration updates require a RemoteConfigSnapshot.")
        with self._lock:
            if snapshot.version <= self._last_version:
                return False
            settings = snapshot.validate_as(self._model_type)
            self._config.reload_section(self._section_name, settings)
            self._last_version = snapshot.version
            return True


__all__ = [
    "MAX_REMOTE_CONFIG_BYTES",
    "RemoteConfigSnapshot",
    "RemoteConfigurationError",
    "RemoteConfigurationProvider",
    "RemoteConfigSection",
]
