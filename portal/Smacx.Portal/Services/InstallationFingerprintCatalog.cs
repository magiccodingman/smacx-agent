using System.Text.Json;
using Smacx.Portal.Contracts;

namespace Smacx.Portal.Services;

public sealed class InstallationFingerprintCatalog
{
    private readonly Catalog catalog;

    public InstallationFingerprintCatalog(IWebHostEnvironment environment)
    {
        var path = Path.Combine(environment.ContentRootPath, "installation-fingerprints.json");
        using var stream = File.OpenRead(path);
        catalog = JsonSerializer.Deserialize<Catalog>(stream, new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true,
        }) ?? throw new InvalidOperationException("The installation fingerprint catalog is empty.");
        if (catalog.Files.Count == 0 || catalog.Fingerprints.Count == 0 ||
            catalog.RequiredRecognizedAnchors < 1)
            throw new InvalidOperationException("The installation fingerprint catalog is invalid.");
    }

    public string ManifestId => catalog.ManifestId;
    public string Edition => catalog.Edition;
    public int RequiredRecognizedAnchors => catalog.RequiredRecognizedAnchors;
    public IReadOnlyList<InstallationFingerprintFile> Files => catalog.Files;

    public (bool Verified, string? FingerprintId, int RecognizedAnchors) Verify(
        IReadOnlyList<InstallationFingerprintEvidence> evidence)
    {
        var submitted = evidence
            .Where(item => item.Size >= 0 && item.Sha256.Length == 64)
            .GroupBy(item => item.Id, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.Ordinal);
        var bestId = default(string);
        var bestCount = 0;
        foreach (var fingerprint in catalog.Fingerprints)
        {
            var count = fingerprint.Anchors.Count(anchor =>
                submitted.TryGetValue(anchor.Key, out var item) &&
                anchor.Value.Contains(item.Sha256, StringComparer.OrdinalIgnoreCase));
            if (count <= bestCount) continue;
            bestCount = count;
            bestId = fingerprint.Id;
        }
        return (bestCount >= catalog.RequiredRecognizedAnchors, bestId, bestCount);
    }

    private sealed class Catalog
    {
        public string ManifestId { get; set; } = string.Empty;
        public string Edition { get; set; } = string.Empty;
        public int RequiredRecognizedAnchors { get; set; }
        public List<InstallationFingerprintFile> Files { get; set; } = [];
        public List<Fingerprint> Fingerprints { get; set; } = [];
    }

    private sealed class Fingerprint
    {
        public string Id { get; set; } = string.Empty;
        public Dictionary<string, List<string>> Anchors { get; set; } = [];
    }
}
