using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Smacx.Portal.Contracts;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/knowledge")]
[AllowAnonymous]
public sealed class KnowledgeController(ControlPlaneClient control) : ControllerBase
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

    [HttpGet("search")]
    public async Task<ActionResult<ApiResponse<KnowledgeSearchResponse>>> Search(
        [FromQuery] string q, [FromQuery] string? topic = null, [FromQuery] int limit = 12)
    {
        try
        {
            var path = $"api/v1/reference/search?q={Uri.EscapeDataString(q ?? string.Empty)}&limit={Math.Clamp(limit, 1, 30)}" +
                (string.IsNullOrEmpty(topic) ? "" : $"&topic={Uri.EscapeDataString(topic)}");
            using var document = await control.GetRawAsync(path, HttpContext.RequestAborted);
            var results = document.RootElement.GetProperty("results").EnumerateArray()
                .Select(item => Map(item)).ToArray();
            return ApiResponse<KnowledgeSearchResponse>.Success(new(q ?? string.Empty, topic, results));
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
            return ApiResponse<KnowledgeResult>.Success(Map(document.RootElement.GetProperty("document"), true));
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<KnowledgeResult>.Failure(exception.Code, exception.Message));
        }
    }

    internal static KnowledgeTopic MapTopic(System.Text.Json.JsonElement item) => new(
        item.GetProperty("title").GetString()!, item.GetProperty("document_count").GetInt32());

    internal static KnowledgeResult Map(System.Text.Json.JsonElement item, bool includeBody = false) => new(
        item.GetProperty("document_id").GetString()!, item.GetProperty("topic").GetString()!,
        item.GetProperty("title").GetString()!, item.GetProperty("description").GetString() ?? "",
        ReadTags(item),
        item.TryGetProperty("source", out var source) ? source.GetString() ?? "" : "",
        "Built locally from operator-configured sources; not distributed with SMACX Agent.",
        includeBody && item.TryGetProperty("body", out var body) ? body.GetString() : null);

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
}
