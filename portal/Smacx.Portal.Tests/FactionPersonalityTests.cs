using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.FileProviders;
using Smacx.Portal.Contracts;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class FactionPersonalityTests
{
    [Fact]
    public void CatalogContainsEveryStockAndCrossfireFactionWithReservedLeaders()
    {
        Assert.Equal(14, FactionCatalog.All.Count);
        Assert.Equal(14, FactionCatalog.All.Select(item => item.NativeChoiceId).Distinct().Count());
        Assert.All(FactionCatalog.All, faction =>
        {
            Assert.True(FactionCatalog.IsReservedLeaderName(faction.LeaderName.ToUpperInvariant()));
            Assert.InRange(faction.LeaderName.Length, 1, 31);
            Assert.All(faction.LeaderName, character => Assert.InRange((int)character, 32, 126));
        });
    }

    [Fact]
    public void AuthoredLibraryContainsFourCardsPerFactionAndLocksRandomResolution()
    {
        var library = new PersonalityCardLibrary(new TestEnvironment());

        Assert.All(FactionCatalog.All, faction =>
            Assert.Equal(4, library.ForFaction(faction.Id).Count));
        var allCards = FactionCatalog.All.SelectMany(faction => library.ForFaction(faction.Id))
            .ToArray();
        Assert.Equal(56, allCards.Length);
        Assert.Equal(56, allCards.Select(card => card.Id).Distinct().Count());
        Assert.All(allCards, card =>
        {
            Assert.False(string.IsNullOrWhiteSpace(card.Description));
            Assert.False(string.IsNullOrWhiteSpace(card.Prompt));
            Assert.Equal(card.Sha256, Convert.ToHexStringLower(
                System.Security.Cryptography.SHA256.HashData(
                    System.Text.Encoding.UTF8.GetBytes(card.Prompt))));
        });
        var first = library.Resolve("gaians", "random", "match-stable", 2);
        var repeated = library.Resolve("gaians", "random", "match-stable", 2);
        Assert.NotNull(first);
        Assert.Equal(first, repeated);
        Assert.Null(library.Resolve("gaians", "none", "match-stable", 2));
        Assert.Contains("Active Personality", first!.Prompt, StringComparison.Ordinal);
    }

    private sealed class TestEnvironment : IWebHostEnvironment
    {
        public string ApplicationName { get; set; } = "Smacx.Portal.Tests";
        public IFileProvider WebRootFileProvider { get; set; } = new NullFileProvider();
        public string WebRootPath { get; set; } = AppContext.BaseDirectory;
        public string EnvironmentName { get; set; } = "Testing";
        public string ContentRootPath { get; set; } = AppContext.BaseDirectory;
        public IFileProvider ContentRootFileProvider { get; set; } = new NullFileProvider();
    }
}
