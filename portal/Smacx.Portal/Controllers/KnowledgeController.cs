using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Smacx.Portal.Contracts;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/knowledge")]
[AllowAnonymous]
public sealed class KnowledgeController(
    ControlPlaneClient control,
    DatalinksMarkdownRenderer markdown) : ControllerBase
{
    [HttpGet("topics")]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<KnowledgeTopic>>>> Topics()
    {
        try
        {
            using var document = await control.GetRawAsync("api/v1/reference/topics", HttpContext.RequestAborted);
            var topics = document.RootElement.GetProperty("topics").EnumerateArray().Select(MapTopic).ToArray();
            return ApiResponse<IReadOnlyList<KnowledgeTopic>>.Success(topics);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<IReadOnlyList<KnowledgeTopic>>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpGet("tree")]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<KnowledgeCollection>>>> Tree()
    {
        try
        {
            using var document = await control.GetRawAsync(
                "api/v1/reference/tree?include_documents=true", HttpContext.RequestAborted);
            var collections = document.RootElement.GetProperty("collections").EnumerateArray()
                .Select(MapCollection).ToArray();
            return ApiResponse<IReadOnlyList<KnowledgeCollection>>.Success(collections);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<IReadOnlyList<KnowledgeCollection>>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpGet("collections/{collectionId}/documents")]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<KnowledgeResult>>>> CollectionDocuments(string collectionId)
    {
        try
        {
            using var document = await control.GetRawAsync(
                $"api/v1/reference/collections/{Uri.EscapeDataString(collectionId)}/documents",
                HttpContext.RequestAborted);
            return ApiResponse<IReadOnlyList<KnowledgeResult>>.Success(
                document.RootElement.GetProperty("documents").EnumerateArray().Select(item => Map(item)).ToArray());
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<IReadOnlyList<KnowledgeResult>>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpGet("search")]
    public async Task<ActionResult<ApiResponse<KnowledgeSearchResponse>>> Search(
        [FromQuery] string q, [FromQuery] string? topic = null, [FromQuery] int limit = 12)
    {
        try
        {
            var path = $"api/v1/reference/search?q={Uri.EscapeDataString(q ?? string.Empty)}&limit={Math.Clamp(limit, 1, 30)}&max_query_tokens=512" +
                (string.IsNullOrEmpty(topic) ? "" : $"&topic={Uri.EscapeDataString(topic)}");
            using var document = await control.GetRawAsync(path, HttpContext.RequestAborted);
            var results = document.RootElement.GetProperty("results").EnumerateArray()
                .Select(item => Map(item)).ToArray();
            var root = document.RootElement;
            return ApiResponse<KnowledgeSearchResponse>.Success(new(
                root.TryGetProperty("query", out var actual) ? actual.GetString() ?? q ?? string.Empty : q ?? string.Empty,
                topic, results,
                root.TryGetProperty("query_truncated", out var truncated) && truncated.GetBoolean(),
                root.TryGetProperty("query_tokens", out var tokens) ? tokens.GetInt32() : null));
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<KnowledgeSearchResponse>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpGet("documents/{documentId}")]
    public async Task<ActionResult<ApiResponse<KnowledgeResult>>> Document(string documentId)
    {
        try
        {
            using var document = await control.GetRawAsync(
                $"api/v1/reference/documents/{Uri.EscapeDataString(documentId)}", HttpContext.RequestAborted);
            var mapped = Map(document.RootElement.GetProperty("document"), true);
            var rendered = markdown.Render(mapped.Body ?? string.Empty);
            return ApiResponse<KnowledgeResult>.Success(mapped with
            {
                RenderedHtml = rendered.Html,
                Headings = rendered.Headings,
            });
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<KnowledgeResult>.Failure(exception.Code, exception.Message));
        }
    }

    internal static KnowledgeTopic MapTopic(System.Text.Json.JsonElement item) => new(
        item.GetProperty("title").GetString()!, item.GetProperty("document_count").GetInt32());

    internal static KnowledgeCollection MapCollection(System.Text.Json.JsonElement item) => new(
        item.GetProperty("id").GetString()!,
        item.TryGetProperty("parent_id", out var parent) && parent.ValueKind != System.Text.Json.JsonValueKind.Null
            ? parent.GetString() : null,
        item.GetProperty("title").GetString()!, item.GetProperty("description").GetString() ?? "",
        ReadTags(item), ReadStrings(item, "path"),
        item.GetProperty("direct_document_count").GetInt32(), item.GetProperty("document_count").GetInt32(),
        item.TryGetProperty("documents", out var documents) && documents.ValueKind == System.Text.Json.JsonValueKind.Array
            ? documents.EnumerateArray().Select(document => new KnowledgeDocumentLink(
                document.GetProperty("document_id").GetString()!,
                document.GetProperty("title").GetString()!,
                document.TryGetProperty("description", out var description) ? description.GetString() ?? "" : "")).ToArray()
            : []);

    internal static KnowledgeResult Map(System.Text.Json.JsonElement item, bool includeBody = false) => new(
        item.GetProperty("document_id").GetString()!, item.GetProperty("topic").GetString()!,
        item.GetProperty("title").GetString()!, item.GetProperty("description").GetString() ?? "",
        ReadTags(item),
        item.TryGetProperty("source", out var source) ? source.GetString() ?? "" : "",
        "Built locally from operator-configured sources; not distributed with SMACX Agent.",
        includeBody && item.TryGetProperty("body", out var body) ? body.GetString() : null,
        item.TryGetProperty("collection_id", out var collection) ? collection.GetString() : null,
        ReadStrings(item, "collection_path"));

    internal static string[] ReadTags(System.Text.Json.JsonElement item)
    {
        if (!item.TryGetProperty("tags", out var tags)) return [];
        return tags.ValueKind switch
        {
            System.Text.Json.JsonValueKind.Array => tags.EnumerateArray()
                .Select(value => value.GetString()).Where(value => !string.IsNullOrWhiteSpace(value))
                .Select(value => value!).ToArray(),
            System.Text.Json.JsonValueKind.String => (tags.GetString() ?? "")
                .Split(' ', StringSplitOptions.RemoveEmptyEntries),
            _ => [],
        };
    }

    internal static string[] ReadStrings(System.Text.Json.JsonElement item, string property) =>
        item.TryGetProperty(property, out var values) && values.ValueKind == System.Text.Json.JsonValueKind.Array
            ? values.EnumerateArray().Select(value => value.GetString())
                .Where(value => !string.IsNullOrWhiteSpace(value)).Select(value => value!).ToArray()
            : [];
}
