# Zoiko iD .NET SDK

`Zoiko.Id.V2Client` (the `/v2` API, ZID-INT-002) and `Zoiko.Id.Session` (the verification session state machine). net8.0, no dependencies.

Every POST that makes something needs the caller's own idempotency key; errors are RFC 9457 problems (`V2Error`).
Test: `docker run --rm -v "$PWD:/src" -w /src/tests mcr.microsoft.com/dotnet/sdk:8.0 dotnet test`

## Error classes (shared table)

`error_classes.json` maps every error code the Verify API can return to its documented class (for example `AUTH_SCOPE_DENIED`, `RATE_LIMITED`) and says whether a retry can help. It is generated from the platform's catalog and is byte for byte the same in every Zoiko SDK. `ErrorClasses.Of(code)` returns the class and whether a retry can help; `ErrorClasses.Classes` and `ErrorClasses.Codes` list them. A test in this SDK compares the shipped table with the shared file, and a backend test fails if any copy differs.
