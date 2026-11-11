# Zoiko iD Java SDK

`com.zoiko.id.V2Client` (the `/v2` API, ZID-INT-002) and `com.zoiko.id.Session` (the verification session state machine). Java 17+, Jackson for JSON.

Every POST that makes something needs the caller's own idempotency key; errors are RFC 9457 problems (`V2Error`).
Test: `docker run --rm -v "$PWD:/src" -v zoiko-m2:/root/.m2 -w /src maven:3.9-eclipse-temurin-21 mvn -B test`
