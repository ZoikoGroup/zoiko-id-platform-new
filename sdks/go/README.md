# Zoiko iD Go SDK

The `/v2` client (ZID-INT-002) and the verification session state machine. Standard library only.

    c := zoiko.NewV2Client("https://api.example", token, nil)
    out, err := c.EvaluateEligibility(ctx, map[string]string{"country": "US"}, "lawyer", "advice", "my-key-1", "")

Every POST that makes something needs the caller's own idempotency key; errors are RFC 9457 problems (`*zoiko.V2Error`). The SDK holds no
provider secret and calls only this platform. Test: `docker run --rm -v "$PWD:/src" -w /src golang:1.23-alpine go test ./...`
