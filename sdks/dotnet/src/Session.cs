namespace Zoiko.Id;

/// <summary>A stable code: TRANSITION_NOT_ALLOWED or AUTHORITY_REQUIRED.</summary>
public sealed class SessionError : Exception
{
    public string Code { get; }
    public SessionError(string code, string message) : base(message) => Code = code;
}

/// <summary>One allowed move; <see cref="Event"/> is null where the contract names none.</summary>
public sealed record Transition(string From, string To, string? Event);

/// <summary>
/// The verification session state machine (ZID-INT-002 section 05.1): a pure mirror of the TypeScript, Python, Go and Java ones. The server
/// is authoritative; this only stops a client doing or showing what the contract forbids: nothing is captured before consent, a corrupt
/// payload never advances, an issuer outage keeps the session SOURCE_PENDING (there is no FAILED state), a human decision needs a documented
/// authority, and withdrawing consent closes capture.
/// </summary>
public static class Session
{
    public static readonly IReadOnlyList<string> States = new[]
    {
        "NEW", "CONSENT_REQUIRED", "READY", "COLLECTING", "RETRY_REQUIRED", "EVIDENCE_RECEIVED", "SOURCE_PENDING", "REVIEW_REQUIRED",
        "APPEALED", "COMPLETE", "REJECTED", "EXPIRED", "DECLINED", "CANCELED", "UNAVAILABLE",
    };

    private static readonly Dictionary<string, string[]> Next = new()
    {
        ["NEW"] = new[] { "CONSENT_REQUIRED", "UNAVAILABLE" },
        ["CONSENT_REQUIRED"] = new[] { "READY", "DECLINED" },
        ["READY"] = new[] { "COLLECTING", "CANCELED" },
        ["COLLECTING"] = new[] { "EVIDENCE_RECEIVED", "RETRY_REQUIRED", "CANCELED" },
        ["RETRY_REQUIRED"] = new[] { "COLLECTING", "CANCELED" },
        ["EVIDENCE_RECEIVED"] = new[] { "SOURCE_PENDING", "REVIEW_REQUIRED" },
        ["SOURCE_PENDING"] = new[] { "COMPLETE", "REVIEW_REQUIRED", "EXPIRED" },
        ["REVIEW_REQUIRED"] = new[] { "COMPLETE", "APPEALED", "REJECTED" },
        ["APPEALED"] = new[] { "REVIEW_REQUIRED" },
        ["COMPLETE"] = Array.Empty<string>(), ["REJECTED"] = Array.Empty<string>(), ["EXPIRED"] = Array.Empty<string>(),
        ["DECLINED"] = Array.Empty<string>(), ["CANCELED"] = Array.Empty<string>(), ["UNAVAILABLE"] = Array.Empty<string>(),
    };

    private static readonly Dictionary<string, string> EventFor = new()
    {
        ["CONSENT_REQUIRED"] = "session.created", ["READY"] = "consent.recorded", ["COLLECTING"] = "capture.started",
        ["EVIDENCE_RECEIVED"] = "document.submitted", ["SOURCE_PENDING"] = "issuer.pending", ["REVIEW_REQUIRED"] = "review.required",
        ["COMPLETE"] = "session.completed", ["CANCELED"] = "session.canceled",
    };

    public static bool CanMove(string from, string to) => Next.TryGetValue(from, out var n) && n.Contains(to);

    public static bool IsTerminal(string state) => !Next.TryGetValue(state, out var n) || n.Length == 0;

    /// <summary>A human decision at REVIEW_REQUIRED (to COMPLETE or REJECTED) must name its authority.</summary>
    public static Transition Move(string from, string to, string? decidedBy = null)
    {
        if (!CanMove(from, to)) throw new SessionError("TRANSITION_NOT_ALLOWED", $"{from} cannot move to {to}");
        if (from == "REVIEW_REQUIRED" && (to == "COMPLETE" || to == "REJECTED") && string.IsNullOrWhiteSpace(decidedBy))
            throw new SessionError("AUTHORITY_REQUIRED", "a review decision needs a documented authority");
        return new Transition(from, to, EventFor.GetValueOrDefault(to));
    }

    /// <summary>Capture closes where it is open; a finished session is untouched (and the call refused).</summary>
    public static Transition WithdrawConsent(string state) => state switch
    {
        "READY" or "COLLECTING" or "RETRY_REQUIRED" => Move(state, "CANCELED"),
        "CONSENT_REQUIRED" => Move(state, "DECLINED"),
        _ => throw new SessionError("TRANSITION_NOT_ALLOWED", $"consent cannot be withdrawn in {state}"),
    };

    public static string AfterCapture(bool payloadIntact) => payloadIntact ? "EVIDENCE_RECEIVED" : "RETRY_REQUIRED";

    /// <summary>An outage is not a failure: the session waits.</summary>
    public static string AfterSource(string answer) => answer switch
    {
        "CONFIRMED" => "COMPLETE",
        "UNCLEAR" => "REVIEW_REQUIRED",
        "TIMED_OUT" => "EXPIRED",
        _ => "SOURCE_PENDING",
    };
}
