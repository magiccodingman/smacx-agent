using System.Text;
using Ganss.Xss;
using Markdig;
using Markdig.Renderers.Html;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;
using Smacx.Portal.Contracts;

namespace Smacx.Portal.Services;

public sealed class DatalinksMarkdownRenderer
{
    private readonly MarkdownPipeline pipeline = new MarkdownPipelineBuilder()
        .UseAdvancedExtensions()
        .DisableHtml()
        .Build();
    private readonly HtmlSanitizer sanitizer = CreateSanitizer();

    public RenderedDatalinksDocument Render(string markdown)
    {
        var document = Markdown.Parse(markdown ?? string.Empty, pipeline);
        var headings = document.Descendants<HeadingBlock>()
            .Select(heading => new KnowledgeHeading(
                heading.Level, HeadingText(heading), heading.GetAttributes().Id ?? string.Empty))
            .Where(heading => heading.Text.Length > 0 && heading.Anchor.Length > 0)
            .ToArray();
        return new(sanitizer.Sanitize(Markdown.ToHtml(document, pipeline)), headings);
    }

    private static HtmlSanitizer CreateSanitizer()
    {
        var value = new HtmlSanitizer();
        value.AllowedAttributes.Add("id");
        return value;
    }

    private static string HeadingText(HeadingBlock heading)
    {
        var result = new StringBuilder();
        if (heading.Inline is not null) AppendInline(heading.Inline, result);
        return string.Join(' ', result.ToString().Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
    }

    private static void AppendInline(ContainerInline container, StringBuilder output)
    {
        for (var item = container.FirstChild; item is not null; item = item.NextSibling)
        {
            switch (item)
            {
                case LiteralInline literal: output.Append(literal.Content); break;
                case CodeInline code: output.Append(code.Content); break;
                case HtmlEntityInline entity: output.Append(entity.Transcoded); break;
                case AutolinkInline link: output.Append(link.Url); break;
                case LineBreakInline: output.Append(' '); break;
                case ContainerInline nested: AppendInline(nested, output); break;
            }
        }
    }
}

public sealed record RenderedDatalinksDocument(
    string Html, IReadOnlyList<KnowledgeHeading> Headings);
