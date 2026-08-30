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
            var topics = document.RootElement.GetProperty("topics").EnumerateArray().Select(item => new KnowledgeTopic(
                item.GetProperty("topic").GetString()!, item.GetProperty("document_count").GetInt32())).ToArray();
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

    private static KnowledgeResult Map(System.Text.Json.JsonElement item, bool includeBody = false) => new(
        item.GetProperty("document_id").GetString()!, item.GetProperty("topic").GetString()!,
        item.GetProperty("title").GetString()!, item.GetProperty("summary").GetString()!,
        (item.TryGetProperty("tags", out var tags) ? tags.GetString() ?? "" : "")
            .Split(' ', StringSplitOptions.RemoveEmptyEntries),
        item.TryGetProperty("provenance", out var provenance) ? provenance.GetString() ?? "" : "",
        item.TryGetProperty("source_license", out var license) ? license.GetString() ?? "" : "",
        includeBody && item.TryGetProperty("body", out var body) ? body.GetString() : null);
}
