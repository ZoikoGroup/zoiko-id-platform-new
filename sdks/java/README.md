# Zoiko iD Java SDK

`com.zoiko.id.V2Client` (the `/v2` API, ZID-INT-002) and `com.zoiko.id.Session` (the verification session state machine). Java 17+, Jackson for JSON.

Every POST that makes something needs the caller's own idempotency key; errors are RFC 9457 problems (`V2Error`).
Test: `docker run --rm -v "$PWD:/src" -v zoiko-m2:/root/.m2 -w /src maven:3.9-eclipse-temurin-21 mvn -B test`

## Error classes (shared table)

`error_classes.json` maps every error code the Verify API can return to its documented class (for example `AUTH_SCOPE_DENIED`, `RATE_LIMITED`) and says whether a retry can help. It is generated from the platform's catalog and is byte for byte the same in every Zoiko SDK. `ErrorClasses.of(code)` returns the class and whether a retry can help; `ErrorClasses.classes()` and `ErrorClasses.codes()` list them. A test in this SDK compares the shipped table with the shared file, and a backend test fails if any copy differs.
