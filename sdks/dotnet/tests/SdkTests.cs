using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Xunit;

namespace Zoiko.Id.Tests;

public sealed class Fake : HttpMessageHandler
{
    public readonly List<(HttpMethod Method, string Url, HttpRequestMessage Request, string Body)> Calls = new();
    public readonly Queue<(int Status, string Body)> Queue = new();
    private (int Status, string Body) _last = (200, "{}");

    public Fake(int status, string body) => _last = (status, body);

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct);
        Calls.Add((request.Method, request.RequestUri!.AbsoluteUri, request, body));
        if (Queue.Count > 0) _last = Queue.Dequeue();
        return new HttpResponseMessage((HttpStatusCode)_last.Status) { Content = new StringContent(_last.Body, Encoding.UTF8, "application/json") };
    }
}

public class SdkTests
{
    private static (V2Client, Fake) Client(int status = 200, string body = "{}")
    {
        var f = new Fake(status, body);
        return (new V2Client("https://api.example.test/", "tok-9", new HttpClient(f)), f);
    }

    private static readonly Dictionary<string, string> Us = new() { ["country"] = "US" };

    [Fact]
    public async Task APostSendsTheBearerTheKeyAndJsonAndReturnsThePlainDocument()
    {
        var (c, f) = Client(200, "{\"credential_ref\":\"cred_1\"}");
        var o = await c.VerifyCredential("professional_license", new { name = "Board" }, new { license_ref = "L-1" }, Us, "k-0001",
            new Dictionary<string, object?> { ["purpose"] = "professional_onboarding" });
        Assert.Equal("cred_1", (string?)o["credential_ref"]);
        var call = f.Calls[0];
        Assert.Equal("https://api.example.test/v2/credentials/verify", call.Url);
        Assert.Equal("Bearer tok-9", call.Request.Headers.Authorization!.ToString());
        Assert.Equal("k-0001", call.Request.Headers.GetValues("Idempotency-Key").Single());
        Assert.Equal("professional_onboarding", (string?)JsonNode.Parse(call.Body)!["purpose"]);
    }

    [Fact]
    public async Task TheIdempotencyKeyIsRequiredSoARetryIsAlwaysDeliberate()
    {
        var (c, f) = Client();
        await Assert.ThrowsAsync<V2Error>(() => c.OpenCase("p", Us, new Dictionary<string, string> { ["profile_id"] = "x" }, new[] { "identity" }, " "));
        await Assert.ThrowsAsync<V2Error>(() => c.EvaluateEligibility(Us, "p", "s", ""));
        Assert.Empty(f.Calls);
    }

    [Fact]
    public async Task AProblemDocumentBecomesAV2ErrorWithTheDocumentsCode()
    {
        var (c, _) = Client(409, "{\"code\":\"idempotency_conflict\",\"detail\":\"changed body\",\"trace_id\":\"tr-1\",\"retryable\":false}");
        var e = await Assert.ThrowsAsync<V2Error>(() => c.EvaluateEligibility(Us, "p", "s", "k-1"));
        Assert.Equal(("idempotency_conflict", 409, "tr-1"), (e.Code, e.Status, e.TraceId));
        var (c2, _) = Client(502, "not json");
        var e2 = await Assert.ThrowsAsync<V2Error>(() => c2.Capabilities());
        Assert.Equal("http_error", e2.Code);
    }

    [Fact]
    public async Task AnUploadAnnouncesTheDigestThenSendsTheBytesWithTheTokenOnly()
    {
        var (c, f) = Client();
        f.Queue.Enqueue((201, "{\"upload_url\":\"/v2/evidence/uploads/u1/content\",\"upload_token\":\"tk\"}"));
        f.Queue.Enqueue((200, "{\"evidence_ref\":\"ev_1\"}"));
        var content = Encoding.UTF8.GetBytes("hello");
        var o = await c.UploadEvidence(content, "image/png", "k-up");
        Assert.Equal("ev_1", (string?)o["evidence_ref"]);
        var announce = JsonNode.Parse(f.Calls[0].Body)!;
        Assert.Equal(Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant(), (string?)announce["sha256"]);
        Assert.Equal(5, (int?)announce["size_bytes"]);
        var put = f.Calls[1];
        Assert.Equal(HttpMethod.Put, put.Method);
        Assert.Equal("tk", put.Request.Headers.GetValues("X-Upload-Token").Single());
        Assert.False(put.Request.Headers.Contains("Idempotency-Key"));
    }

    [Fact]
    public async Task ValidateDocumentNeedsExactlyOneSourceAndBase64sTheContent()
    {
        var (c, f) = Client(200, "{\"document_results\":[]}");
        await Assert.ThrowsAsync<V2Error>(() => c.ValidateDocument("application/pdf", new[] { "integrity" }));
        await Assert.ThrowsAsync<V2Error>(() => c.ValidateDocument("application/pdf", new[] { "integrity" }, "ev_1", new byte[] { 1 }));
        Assert.Empty(f.Calls);
        await c.ValidateDocument("application/pdf", new[] { "integrity" }, content: Encoding.UTF8.GetBytes("hi"));
        Assert.Equal("aGk=", (string?)JsonNode.Parse(f.Calls[0].Body)!["content_base64"]);
    }

    [Fact]
    public async Task CoverageEscapesItsInputsAndTheOrganizationLookupIsStateless()
    {
        var (c, f) = Client(200, "{\"outcome\":\"VERIFIED\"}");
        await c.Coverage("G B", "a&b");
        Assert.EndsWith("?country=G%20B&profession=a%26b", f.Calls[0].Url);
        await c.VerifyOrganization("GB", "00123456", "Acme");
        Assert.Equal("https://api.example.test/v2/organizations/verify", f.Calls[1].Url);
        Assert.False(f.Calls[1].Request.Headers.Contains("Idempotency-Key"));
        Assert.Equal(27, V2Client.Paths.Count);
    }

    [Fact]
    public async Task CaptureAlternativesIsAPlainGetWithAnOptionalReason()
    {
        var (c, f) = Client(200, "{\"counts_as_failed_attempt\":false}");
        await c.CaptureAlternatives("GB");
        await c.CaptureAlternatives("GB", "no camera");
        Assert.EndsWith("/v2/capture-alternatives?country=GB", f.Calls[0].Url);
        Assert.EndsWith("/v2/capture-alternatives?country=GB&reason=no%20camera", f.Calls[1].Url);
    }

    [Fact]
    public void NothingIsCapturedBeforeConsentAndAReviewDecisionNeedsAnAuthority()
    {
        Assert.False(Session.CanMove("NEW", "COLLECTING"));
        Assert.False(Session.CanMove("CONSENT_REQUIRED", "COLLECTING"));
        Assert.Equal("AUTHORITY_REQUIRED", Assert.Throws<SessionError>(() => Session.Move("REVIEW_REQUIRED", "COMPLETE", " ")).Code);
        Assert.Equal("session.completed", Session.Move("REVIEW_REQUIRED", "COMPLETE", "reviewer-1").Event);
    }

    [Fact]
    public void AnOutageKeepsTheSessionWaitingAndACorruptPayloadNeverAdvances()
    {
        Assert.Equal("SOURCE_PENDING", Session.AfterSource("UNAVAILABLE"));
        Assert.Equal("COMPLETE", Session.AfterSource("CONFIRMED"));
        Assert.Equal("REVIEW_REQUIRED", Session.AfterSource("UNCLEAR"));
        Assert.Equal("EXPIRED", Session.AfterSource("TIMED_OUT"));
        Assert.Equal("RETRY_REQUIRED", Session.AfterCapture(false));
        Assert.DoesNotContain("FAILED", Session.States);
    }

    [Fact]
    public void WithdrawingConsentClosesCaptureAndTerminalStatesStayTerminal()
    {
        Assert.Equal("CANCELED", Session.WithdrawConsent("COLLECTING").To);
        Assert.Equal("DECLINED", Session.WithdrawConsent("CONSENT_REQUIRED").To);
        Assert.Throws<SessionError>(() => Session.WithdrawConsent("COMPLETE"));
        foreach (var s in new[] { "COMPLETE", "REJECTED", "EXPIRED", "DECLINED", "CANCELED", "UNAVAILABLE" }) Assert.True(Session.IsTerminal(s));
    }
}
