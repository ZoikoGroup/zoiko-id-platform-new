using Xunit;

namespace Zoiko.Id.Tests;

public sealed class ErrorClassesTests
{
    [Fact]
    public void TheShippedTableIsTheSharedTableAndEveryCodeHasAKnownClass()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "contracts", "openapi", "partner", "error-classes.json"))) dir = dir.Parent;
        if (dir is not null)  // absent in a standalone checkout of this SDK
        {
            var shared = File.ReadAllBytes(Path.Combine(dir.FullName, "contracts", "openapi", "partner", "error-classes.json"));
            Assert.Equal(shared, ErrorClasses.RawBytes());
        }
        Assert.NotEmpty(ErrorClasses.Classes);
        Assert.NotEmpty(ErrorClasses.Codes);
        foreach (var code in ErrorClasses.Codes) Assert.Contains(ErrorClasses.Of(code)!.Class, ErrorClasses.Classes);
        var limited = ErrorClasses.Of("RATE_LIMITED");
        Assert.NotNull(limited);
        Assert.True(limited!.Retryable);
        Assert.Equal("RATE_LIMITED", limited.Class);
        Assert.Null(ErrorClasses.Of("NO_SUCH_CODE"));
    }
}
