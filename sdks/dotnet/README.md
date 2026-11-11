# Zoiko iD .NET SDK

`Zoiko.Id.V2Client` (the `/v2` API, ZID-INT-002) and `Zoiko.Id.Session` (the verification session state machine). net8.0, no dependencies.

Every POST that makes something needs the caller's own idempotency key; errors are RFC 9457 problems (`V2Error`).
Test: `docker run --rm -v "$PWD:/src" -w /src/tests mcr.microsoft.com/dotnet/sdk:8.0 dotnet test`
