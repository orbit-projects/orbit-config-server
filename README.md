# Orbit Config Server

`orbit-config-server` defines a provider-neutral contract for complete, versioned remote
configuration snapshots. It is the capability layer between Orbit Core's typed configuration
sections and optional server/provider adapters. It does not include a server, network client,
transport, credential provider, polling loop, or vendor SDK.

## Install

```bash
python -m pip install orbit-core orbit-config-server
```

## Validate a provider snapshot

The application declares the settings model. An adapter fetches a complete JSON-object snapshot;
the package bounds and validates the wire payload, then validates the values against that model
before an application can pass the model to Core's atomic section reload API.

```python
from pydantic import BaseModel, SecretStr

from orbit.config import ApplicationConfig, Config
from orbit_config_server import RemoteConfigSection, RemoteConfigSnapshot


class DatabaseSettings(BaseModel):
    host: str
    password: SecretStr


snapshot = RemoteConfigSnapshot(
    version=12,
    payload=b'{"host":"db.internal","password":"loaded-by-the-adapter"}',
)
# After Core has frozen the section registry:
config = Config(ApplicationConfig(name="orders"))
settings = load_database_settings()  # validated settings; secrets come from a secret manager
config.register("database", settings)
config.freeze()
section = RemoteConfigSection(config, "database", DatabaseSettings)
section.apply(snapshot)
```

Use `SecretStr`/`SecretBytes` for credential fields. The snapshot's repr omits its payload and
validation errors omit submitted values, but the payload is intentionally accessible to the
adapter that must decode it. Never log snapshot payloads, resolved models, transport bodies, or
provider exceptions containing remote input.

## Provider contract

Implement `RemoteConfigurationProvider` in a separately installable adapter. The provider owns
its SDK, endpoint and tenant configuration, authentication and credentials, transport, reconnect
policy, and resource cleanup. `fetch()` returns the current complete snapshot; `watch(after_version=)`
yields complete replacements with strictly increasing positive versions; `aclose()` releases owned
resources. Providers may omit intermediate revisions only because each yielded value is a full
replacement. Credential delivery and rotation must follow the provider's supported secure
mechanism and must not use command-line arguments or logs.

The current contract accepts at most 1 MiB of UTF-8 JSON per snapshot, rejects duplicate object
keys, non-finite numbers, invalid keys, and structures beyond 64 levels or 100,000 values. Core's
registered Pydantic model remains the final application schema and Core's `Config.reload_section()`
provides the local atomic commit and observer veto. `RemoteConfigSection` serializes concurrent
updates and ignores repeated or stale versions; its watermark is process-local unless the adapter
persists and restores it through `last_version`. A rejected Core observer leaves both the section
and watermark unchanged. Adapters must define durable cursor persistence, retry/backoff, access
control, transport timeouts, and lifecycle integration; this package does not silently choose those
policies.

## Scope and status

This package is the Python capability contract for Python Core applications. A provider adapter
may use its vendor's best-supported language when it has a real process boundary and implements
the same versioned behavior; application configuration must select that adapter explicitly.
There is no remote server or provider adapter in this repository, and no live remote configuration
behavior is claimed. The package supports Python 3.11 through 3.14 and is pre-alpha.

## Documentation

The package-specific guides cover [architecture](docs/architecture/overview.md), [operations and security](docs/operations/README.md), and [development](docs/development/README.md), with [security guidance](docs/security/overview.md). The [documentation index](docs/README.md) links to the full package overview and project policies.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
ruff check src tests
mypy
```

Licensed under Apache-2.0.

