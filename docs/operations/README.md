# Operations

`orbit-config-server` is a contract and validation library. It does not contact a remote service,
run a polling task, persist revisions, or manage adapter resources. The application wires those
responsibilities to an installed provider and owns its startup and shutdown.

## Recommended startup sequence

1. Load a known-good local configuration and register every typed Core section.
2. Freeze the Core section registry.
3. Construct the provider with explicit endpoint, identity, timeout, TLS, and retry settings.
4. Fetch a complete snapshot and apply it through `RemoteConfigSection` before serving traffic if
   startup requires remote configuration. Otherwise, explicitly define the local fallback behavior.
5. Start the provider watch task under the application's lifecycle manager and apply each complete
   snapshot. Treat provider disconnects and rejected revisions as observable operational failures.
6. During shutdown, stop and await the watch task, then call `provider.aclose()` exactly once.

The provider and its SDK own connection pooling, reconnect behavior, authentication, transport
limits, and cleanup. The application owns task supervision, readiness policy, timeout budgets, and
the decision to continue serving with the last accepted configuration. Do not block shutdown
indefinitely while waiting for an unresponsive provider.

## Revision and update semantics

A snapshot is a full JSON object for one registered section, not a patch. Its positive integer
revision must increase for every newer state. A provider may skip a revision only when its next
snapshot completely replaces the same section. `RemoteConfigSection` serializes calls to `apply`,
ignores a revision less than or equal to its in-memory watermark, validates against the exact
registered Pydantic model class, then asks Core to perform its atomic section reload. The watermark
advances only after that reload and its observers succeed.

The watermark is not durable or shared between workers. Supply `last_version` only when the
adapter has a trustworthy persisted cursor for the same section and provider identity. If multiple
application processes consume the provider independently, each has its own Core state and
watermark; this library does not coordinate them. Define startup, replay, missed-update recovery,
and multi-process rollout behavior in the adapter/application design.

## Limits and failures

The wire payload is at most 1 MiB, strict UTF-8 JSON, and a top-level object. Duplicate keys,
`NaN`/infinities, control characters in keys, keys over 255 characters, nesting deeper than 64
levels, and trees over 100,000 values are rejected. Typed Pydantic validation failures are
converted to a generic error so submitted values are not copied into the exception message.

An adapter should distinguish transport failures, provider authorization failures, invalid wire
data, schema mismatch, stale revision, and Core observer rejection in its own structured metrics
and logs. Log stable error categories and provider request identifiers where safe; never log raw
payloads, resolved settings, credentials, or unsanitized provider exception text. Define bounded
retry with jitter for transient failures and avoid retrying permanent schema or access errors.

## Readiness and rollout

Choose and document whether remote configuration is required for readiness. A successful initial
fetch does not prove later provider availability. Roll out schema changes compatibly: deploy code
that accepts both forms before publishing a snapshot that requires the new form, and remove old
fields only after all consumers have moved. Validate rollback behavior because an older application
version may reject a newer snapshot. This package does not provide distributed transactions,
rollback storage, encryption, or cross-process synchronization.

## Production validation checklist

Before using a provider in production, validate its supported Python/runtime and SDK versions,
least-privilege access, TLS verification, request and connect timeouts, retry/backoff limits,
secret rotation, cancellation, shutdown, provider outages, malformed and oversized responses,
revision replay, schema rollout, observer veto, process restart, and multi-worker behavior. Run
these checks against a dedicated non-production service; this repository contains no provider or
live-service integration tests.
