using System.Reflection;
using System.Text.Json.Nodes;

namespace Zoiko.Id;

/// <summary>The documented class of an error code and whether a retry can help.</summary>
public sealed record ErrorClassEntry(string Class, bool Retryable);

/// <summary>The class of every Verify API error code. The table is the same, byte for byte, in every Zoiko SDK.</summary>
public static class ErrorClasses
{
    private static readonly JsonNode Table = Load();

    private static JsonNode Load()
    {
        using var stream = typeof(ErrorClasses).Assembly.GetManifestResourceStream("Zoiko.Id.error_classes.json")
            ?? throw new InvalidOperationException("error_classes.json is missing from the SDK");
        return JsonNode.Parse(stream)!;
    }

    /// <summary>The class for a code, or null for a code the table does not know.</summary>
    public static ErrorClassEntry? Of(string code)
    {
        var n = Table["codes"]?[code];
        return n is null ? null : new ErrorClassEntry(n["class"]!.GetValue<string>(), n["retryable"]!.GetValue<bool>());
    }

    /// <summary>Every class a code can belong to.</summary>
    public static IReadOnlyList<string> Classes => Table["classes"]!.AsArray().Select(c => c!.GetValue<string>()).ToList();

    /// <summary>Every code in the table.</summary>
    public static IReadOnlyList<string> Codes => Table["codes"]!.AsObject().Select(kv => kv.Key).ToList();

    /// <summary>The embedded table's exact bytes, for comparing with the shared file.</summary>
    public static byte[] RawBytes()
    {
        using var stream = typeof(ErrorClasses).Assembly.GetManifestResourceStream("Zoiko.Id.error_classes.json")!;
        using var ms = new MemoryStream();
        stream.CopyTo(ms);
        return ms.ToArray();
    }
}
