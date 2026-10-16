# Security

## Data handled by this package

Remote configuration may contain credentials, private endpoints, feature flags, and other
sensitive operational data. `RemoteConfigSnapshot` hides payload bytes from its normal `repr`, and
`validate_as()` replaces Pydantic's value-bearing validation detail with a generic error. These
measures reduce accidental disclosure; they do not encrypt memory, protect a compromised process,
or prevent an adapter from reading the payload.

Use `SecretStr` or `SecretBytes` for secret fields in application models. Keep provider credentials
in a supported workload identity or secret manager, grant only read access to the required
configuration, and never put secrets in command-line arguments, source control, exception text, or
logs. Avoid serializing resolved settings for diagnostics. Restrict access to crash dumps and
process inspection according to the deployment's data classification.

## Provider and transport responsibilities

This package has no network client and makes no TLS, authentication, authorization, or encryption
claim. The provider adapter must verify server certificates, set connection and response timeouts,
validate endpoint configuration, authenticate using the provider's supported secure mechanism,
and close resources. Do not disable certificate checks to work around deployment errors.

Treat provider payloads as untrusted input even when fetched over an authenticated channel. The
snapshot constructor enforces a 1 MiB byte limit, strict UTF-8 JSON, unique object keys, finite
numbers, bounded nesting and value count, and safe key shape before model validation. Pydantic
models should also enforce domain-specific constraints such as allowed hosts, numeric ranges, and
secret formats. The contract cannot decide whether a setting value is safe to apply.

## Logging and failure handling

Do not log snapshot payloads, settings objects, request/response bodies, tokens, opaque cursors, or
raw provider errors that may contain remote input. Emit bounded error categories and sanitized
provider request IDs instead. Keep metric labels low-cardinality and free of secret or user supplied
values. An observer rejection leaves the Core setting and this binding's version unchanged; alert
and follow an explicit retry or rollback policy rather than silently advancing the cursor.

## Disclosure

Report suspected vulnerabilities privately using the contact method in the repository's
[security policy](../../SECURITY.md). Do not publish exploit details before maintainers can assess
the issue.
