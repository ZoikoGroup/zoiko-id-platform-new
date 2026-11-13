using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Zoiko.Id;

/// <summary>An RFC 9457 problem. <see cref="Code"/> is the document's public code (for example <c>unsupported_market</c>, <c>idempotency_conflict</c>).</summary>
public sealed class V2Error : Exception
{
    public string Code { get; }
    public int Status { get; }
    public string Detail { get; }
    public string? TraceId { get; }
    public bool Retryable { get; }

    public V2Error(string code, int status, string detail = "", string? traceId = null, bool retryable = false)
        : base(string.IsNullOrEmpty(detail) ? code : code + ": " + detail)
    {
        Code = code; Status = status; Detail = detail; TraceId = traceId; Retryable = retryable;
    }
}

/// <summary>
/// The /v2 global trust API client (ZID-INT-002, Document 02 v2.0). Same rules as the Python, TypeScript, Go and Java clients: errors are
/// RFC 9457 problems (<see cref="V2Error"/>), every POST that makes something takes a REQUIRED idempotency key (the caller's, never
/// generated here), answers are the plain JSON document, and the SDK never holds a provider secret or contacts a source itself.
/// </summary>
public sealed class V2Client
{
    private readonly string _base;
    private readonly string _token;
    private readonly HttpClient _http;

    /// <param name="http">Pass your own (or a test handler); null uses a client with a 15 second timeout.</param>
    public V2Client(string baseUrl, string accessToken, HttpClient? http = null)
    {
        _base = baseUrl.TrimEnd('/');
        _token = accessToken;
        _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
    }

    // ------------------------------------------------------------------------------------------------------------ plumbing

    private async Task<JsonObject> Call(HttpMethod method, string path, object? body = null, string? key = null, byte[]? raw = null,
        IDictionary<string, string>? extra = null, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(method, path.StartsWith("http") ? path : _base + path);
        req.Headers.Accept.ParseAdd("application/json");
        req.Headers.Accept.ParseAdd("application/problem+json");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _token);
        if (raw is not null)
        {
            req.Content = new ByteArrayContent(raw);
            req.Content.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
        }
        else if (body is not null)
        {
            req.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        }
        if (key is not null) req.Headers.Add("Idempotency-Key", key);
        if (extra is not null) foreach (var (h, v) in extra) req.Headers.Add(h, v);

        HttpResponseMessage resp;
        try { resp = await _http.SendAsync(req, ct).ConfigureAwait(false); }
        catch (HttpRequestException e) { throw new V2Error("transport_error", 0, e.Message, null, true); }
        using (resp)
        {
            var payload = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            JsonObject doc = new();
            if (payload.Length > 0)
            {
                try { doc = JsonNode.Parse(payload) as JsonObject ?? new JsonObject(); }
                catch (JsonException) { doc = new JsonObject(); }
            }
            var status = (int)resp.StatusCode;
            if (status >= 400)
            {
                throw new V2Error(
                    (string?)doc["code"] ?? "http_error", status, (string?)doc["detail"] ?? "", (string?)doc["trace_id"],
                    (bool?)doc["retryable"] ?? false);
            }
            return doc;
        }
    }

    private static string Need(string? key) =>
        string.IsNullOrWhiteSpace(key)
            ? throw new V2Error("invalid_request", 0, "an idempotency key is required: a retry must reuse the same key")
            : key;

    private Task<JsonObject> Post(string path, Dictionary<string, object?> body, string? key, CancellationToken ct) =>
        Call(HttpMethod.Post, path, body, Need(key), ct: ct);

    private Task<JsonObject> Get(string path, CancellationToken ct) => Call(HttpMethod.Get, path, ct: ct);

    private static Dictionary<string, object?> With(Dictionary<string, object?> b, IDictionary<string, object?>? extra)
    {
        if (extra is not null) foreach (var (k, v) in extra) b[k] = v;
        return b;
    }

    // ------------------------------------------------------------------------------------------------------------ discovery

    public Task<JsonObject> Capabilities(CancellationToken ct = default) => Get("/v2/capabilities", ct);

    /// <summary>The other ways to complete a check instead of a selfie or a photographed document. No reason is needed; choosing one is not a failed attempt.</summary>
    public Task<JsonObject> CaptureAlternatives(string country, string? reason = null, CancellationToken ct = default) =>
        Get("/v2/capture-alternatives?country=" + Uri.EscapeDataString(country) + (reason is null ? "" : "&reason=" + Uri.EscapeDataString(reason)), ct);

    public Task<JsonObject> Coverage(string country, string? profession = null, CancellationToken ct = default) =>
        Get("/v2/coverage?country=" + Uri.EscapeDataString(country) + (profession is null ? "" : "&profession=" + Uri.EscapeDataString(profession)), ct);

    // ------------------------------------------------------------------------------------------------------------ credentials

    /// <summary>One credential check; <paramref name="extra"/> may carry subject_ref, evidence_refs, purpose, consent_ref and consumer.</summary>
    public Task<JsonObject> VerifyCredential(string credentialType, object issuerClaim, object credentialClaim,
        IDictionary<string, string> jurisdiction, string idempotencyKey, IDictionary<string, object?>? extra = null, CancellationToken ct = default) =>
        Post("/v2/credentials/verify", With(new()
        {
            ["credential_type"] = credentialType, ["issuer_claim"] = issuerClaim, ["credential_claim"] = credentialClaim,
            ["jurisdiction"] = jurisdiction,
        }, extra), idempotencyKey, ct);

    public Task<JsonObject> Credential(string reference, CancellationToken ct = default) => Get("/v2/credentials/" + reference, ct);

    // ------------------------------------------------------------------------------------------------------------ eligibility

    public Task<JsonObject> EvaluateEligibility(IDictionary<string, string> jurisdiction, string professionCode, string serviceCode,
        string idempotencyKey, string? subjectRef = null, CancellationToken ct = default)
    {
        Dictionary<string, object?> b = new() { ["jurisdiction"] = jurisdiction, ["profession_code"] = professionCode, ["service_code"] = serviceCode };
        if (subjectRef is not null) b["subject_ref"] = subjectRef;
        return Post("/v2/eligibility/evaluate", b, idempotencyKey, ct);
    }

    public Task<JsonObject> Eligibility(string reference, CancellationToken ct = default) => Get("/v2/eligibility/" + reference, ct);

    /// <summary>Zoikorum engagement eligibility per service, signed for Zoikorum.</summary>
    public Task<JsonObject> EngagementCheck(string professionalSubjectRef, string engagementRef, string professionCode,
        IEnumerable<string> requestedServices, IDictionary<string, string> jurisdiction, string idempotencyKey,
        IDictionary<string, object?>? extra = null, CancellationToken ct = default) =>
        Post("/v2/zoikorum/engagement-checks", With(new()
        {
            ["professional_subject_ref"] = professionalSubjectRef, ["engagement_ref"] = engagementRef, ["profession_code"] = professionCode,
            ["requested_services"] = requestedServices, ["jurisdiction"] = jurisdiction,
        }, extra), idempotencyKey, ct);

    // ------------------------------------------------------------------------------------------------------------ cases

    public Task<JsonObject> OpenCase(string purpose, IDictionary<string, string> jurisdiction, IDictionary<string, string> workflow,
        IEnumerable<string> requestedChecks, string idempotencyKey, IDictionary<string, object?>? extra = null, CancellationToken ct = default) =>
        Post("/v2/verifications", With(new()
        {
            ["purpose"] = purpose, ["jurisdiction"] = jurisdiction, ["workflow"] = workflow, ["requested_checks"] = requestedChecks,
        }, extra), idempotencyKey, ct);

    public Task<JsonObject> Case(string id, CancellationToken ct = default) => Get("/v2/verifications/" + id, ct);

    public Task<JsonObject> SubmitTask(string caseId, string check, IDictionary<string, object?> body, string idempotencyKey, CancellationToken ct = default) =>
        Post($"/v2/verifications/{caseId}/tasks/{check}", new Dictionary<string, object?>(body), idempotencyKey, ct);

    public Task<JsonObject> CancelCase(string id, CancellationToken ct = default) => Call(HttpMethod.Post, $"/v2/verifications/{id}/cancel", ct: ct);

    // ------------------------------------------------------------------------------------------------------------ evidence

    /// <summary>Announce, then send the bytes with the short-lived token. A rejected upload throws <see cref="V2Error"/> (for example evidence_rejected_infected).</summary>
    public async Task<JsonObject> UploadEvidence(byte[] content, string declaredType, string idempotencyKey, string? subjectRef = null, CancellationToken ct = default)
    {
        Dictionary<string, object?> announce = new()
        {
            ["declared_type"] = declaredType, ["size_bytes"] = content.Length,
            ["sha256"] = Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant(),
        };
        if (subjectRef is not null) announce["subject_ref"] = subjectRef;
        var made = await Post("/v2/evidence/uploads", announce, idempotencyKey, ct).ConfigureAwait(false);
        var url = (string?)made["upload_url"];
        var tok = (string?)made["upload_token"];
        if (url is null || tok is null) throw new V2Error("invalid_response", 0, "the announcement carried no upload_url or upload_token");
        return await Call(HttpMethod.Put, url, raw: content, extra: new Dictionary<string, string> { ["X-Upload-Token"] = tok }, ct: ct).ConfigureAwait(false);
    }

    public Task<JsonObject> EvidenceManifest(string reference, CancellationToken ct = default) => Get($"/v2/evidence/{reference}/manifest", ct);

    public Task<JsonObject> DeleteEvidence(string reference, CancellationToken ct = default) => Call(HttpMethod.Delete, "/v2/evidence/" + reference, ct: ct);

    /// <summary>Exactly one of <paramref name="evidenceRef"/> (a stored upload) and <paramref name="content"/> (checked in memory and discarded).</summary>
    public Task<JsonObject> ValidateDocument(string declaredType, IEnumerable<string> checks, string? evidenceRef = null, byte[]? content = null,
        IDictionary<string, object?>? extra = null, CancellationToken ct = default)
    {
        if ((evidenceRef is null) == (content is null)) throw new V2Error("invalid_request", 0, "send exactly one of evidenceRef and content");
        var b = With(new() { ["declared_type"] = declaredType, ["checks"] = checks }, extra);
        if (evidenceRef is not null) b["evidence_ref"] = evidenceRef;
        else b["content_base64"] = Convert.ToBase64String(content!);
        return Call(HttpMethod.Post, "/v2/documents/validate", b, ct: ct);
    }

    public Task<JsonObject> DocumentResult(string reference, CancellationToken ct = default) => Get("/v2/documents/" + reference, ct);

    // ------------------------------------------------------------------------------------------------------------ organizations

    /// <summary>A company lookup in the official sources the country's route names; one outcome, no person named.</summary>
    public Task<JsonObject> VerifyOrganization(string country, string registrationNumber, string legalName, IDictionary<string, object?>? extra = null,
        CancellationToken ct = default) =>
        Call(HttpMethod.Post, "/v2/organizations/verify",
            With(new() { ["country"] = country, ["registration_number"] = registrationNumber, ["legal_name"] = legalName }, extra), ct: ct);

    // ------------------------------------------------------------------------------------------------------------ issuers, presentations, events

    /// <summary>The returned nonce is shown ONCE: a replay of this call will not return it again.</summary>
    public Task<JsonObject> OpenIssuerCheck(string issuerRef, string credentialType, string idempotencyKey, int? days = null, CancellationToken ct = default)
    {
        Dictionary<string, object?> b = new() { ["issuer_ref"] = issuerRef, ["credential_type"] = credentialType };
        if (days is not null) b["days"] = days;
        return Post("/v2/issuer-checks", b, idempotencyKey, ct);
    }

    public Task<JsonObject> IssuerCheck(string reference, CancellationToken ct = default) => Get("/v2/issuer-checks/" + reference, ct);

    /// <summary><paramref name="expiresInSeconds"/> can only SHORTEN the request's life (at most 600).</summary>
    public Task<JsonObject> RequestPresentation(string configurationId, string recipientRef, string purpose, IEnumerable<string> requestedClaims,
        IDictionary<string, string> jurisdiction, string idempotencyKey, int? expiresInSeconds = null, IDictionary<string, object?>? extra = null,
        CancellationToken ct = default)
    {
        var b = With(new()
        {
            ["configuration_id"] = configurationId, ["recipient_ref"] = recipientRef, ["purpose"] = purpose,
            ["requested_claims"] = requestedClaims, ["jurisdiction"] = jurisdiction,
        }, extra);
        if (expiresInSeconds is not null) b["expires_in_seconds"] = expiresInSeconds;
        return Post("/v2/presentations/requests", b, idempotencyKey, ct);
    }

    public Task<JsonObject> Presentation(string reference, CancellationToken ct = default) => Get("/v2/presentations/" + reference, ct);

    public Task<JsonObject> RegisterWebhook(string url, IEnumerable<string> eventTypes, string idempotencyKey, int? maxInflight = null, CancellationToken ct = default)
    {
        Dictionary<string, object?> b = new() { ["url"] = url, ["event_types"] = eventTypes };
        if (maxInflight is not null) b["max_inflight"] = maxInflight;
        return Post("/v2/webhook-endpoints", b, idempotencyKey, ct);
    }

    public Task<JsonObject> WebhookEndpoints(CancellationToken ct = default) => Get("/v2/webhook-endpoints", ct);

    public Task<JsonObject> Event(string id, CancellationToken ct = default) => Get("/v2/events/" + id, ct);

    public Task<JsonObject> ReplayEvent(string id, CancellationToken ct = default) => Call(HttpMethod.Post, $"/v2/events/{id}/replay", ct: ct);

    /// <summary>Every route the client calls, for the parity check against the other clients and the server's published contract.</summary>
    public static readonly IReadOnlyList<string> Paths = new[]
    {
        "GET /v2/capabilities", "GET /v2/coverage", "GET /v2/capture-alternatives", "POST /v2/credentials/verify", "GET /v2/credentials/{ref}",
        "POST /v2/eligibility/evaluate", "GET /v2/eligibility/{ref}", "POST /v2/zoikorum/engagement-checks",
        "POST /v2/verifications", "GET /v2/verifications/{id}", "POST /v2/verifications/{id}/tasks/{check}",
        "POST /v2/verifications/{id}/cancel", "POST /v2/evidence/uploads", "PUT /v2/evidence/uploads/{id}/content",
        "GET /v2/evidence/{ref}/manifest", "DELETE /v2/evidence/{ref}", "POST /v2/documents/validate", "GET /v2/documents/{ref}",
        "POST /v2/organizations/verify", "POST /v2/issuer-checks", "GET /v2/issuer-checks/{ref}",
        "POST /v2/presentations/requests", "GET /v2/presentations/{ref}", "POST /v2/webhook-endpoints",
        "GET /v2/webhook-endpoints", "GET /v2/events/{id}", "POST /v2/events/{id}/replay",
    };
}
