# Zoiko iD Go SDK

The `/v2` client (ZID-INT-002) and the verification session state machine. Standard library only.

    c := zoiko.NewV2Client("https://api.example", token, nil)
    out, err := c.EvaluateEligibility(ctx, map[string]string{"country": "US"}, "lawyer", "advice", "my-key-1", "")

Every POST that makes something needs the caller's own idempotency key; errors are RFC 9457 problems (`*zoiko.V2Error`). The SDK holds no
provider secret and calls only this platform. Test: `docker run --rm -v "$PWD:/src" -w /src golang:1.23-alpine go test ./...`

## Error classes (shared table)

`error_classes.json` maps every error code the Verify API can return to its documented class (for example `AUTH_SCOPE_DENIED`, `RATE_LIMITED`) and says whether a retry can help. It is generated from the platform's catalog and is byte for byte the same in every Zoiko SDK. `zoiko.ErrorClassOf(code)` returns the class and whether a retry can help; `zoiko.ErrorClasses()` lists the classes. A test in this SDK compares the shipped table with the shared file, and a backend test fails if any copy differs.
