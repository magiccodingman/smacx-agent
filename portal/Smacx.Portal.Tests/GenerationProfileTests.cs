using System.Text.Json;
using Smacx.Portal.Contracts;
using Smacx.Portal.Controllers;

namespace Smacx.Portal.Tests;

public sealed class GenerationProfileTests
{
    [Fact]
    public void NamedTemplatesPreserveExplicitEditableValues()
    {
        var chatTemplate = JsonSerializer.SerializeToElement(new
        {
            enable_thinking = true,
            preserve_thinking = false,
        });
        var requested = new ModelGenerationSettings(
            "qwen38-low", Temperature: 0.42, TopP: 0.81, TopK: 17, MinP: 0.05,
            PresencePenalty: 0.2, FrequencyPenalty: -0.1, RepetitionPenalty: 1.07,
            MaxOutputTokens: 4096, Seed: 7,
            ExtraParameters: new Dictionary<string, JsonElement>
            {
                ["chat_template_kwargs"] = chatTemplate,
                ["future_provider_option"] = JsonSerializer.SerializeToElement(new { mode = "exact" }),
            });

        var normalized = AdministrationController.NormalizeGeneration(requested);

        Assert.Equal(requested, normalized);
        Assert.False(normalized.ExtraParameters!["chat_template_kwargs"]
            .GetProperty("preserve_thinking").GetBoolean());
    }

    [Fact]
    public void ProviderDefaultMayContainExplicitOverrides()
    {
        var normalized = AdministrationController.NormalizeGeneration(
            new ModelGenerationSettings("provider-default", Temperature: 0.3));

        Assert.Equal("provider-default", normalized.Preset);
        Assert.Equal(0.3, normalized.Temperature);
    }
}
