using Smacx.Portal.Infrastructure;

namespace Smacx.Portal.Tests;

public sealed class NativePlayerIdentityTests
{
    [Fact]
    public void NativeAliasIsBoundedStableAndDoesNotReplaceCanonicalHandle()
    {
        var occupied = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "Alice",
            "Semantic Host",
        };

        var alias = NativePlayerIdentity.AllocateAlias(
            "match-one", 2, "Alice", occupied);
        var repeated = NativePlayerIdentity.AllocateAlias(
            "match-one", 2, "Alice", new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            {
                "Alice",
                "Semantic Host",
            });

        Assert.Equal(repeated, alias);
        Assert.False(string.Equals("Alice", alias, StringComparison.OrdinalIgnoreCase));
        Assert.True(alias.Length <= 31);
        Assert.StartsWith("Alice~3", alias, StringComparison.Ordinal);
    }

    [Fact]
    public void OccupiedCandidateAdvancesWithoutCaseSensitiveCollision()
    {
        var first = NativePlayerIdentity.AllocateAlias(
            "match-one", 1, "A very long native player handle", new HashSet<string>(
                StringComparer.OrdinalIgnoreCase));
        var occupied = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            first.ToLowerInvariant(),
        };

        var second = NativePlayerIdentity.AllocateAlias(
            "match-one", 1, "A very long native player handle", occupied);

        Assert.NotEqual(first, second);
        Assert.True(second.Length <= 31);
    }
}
