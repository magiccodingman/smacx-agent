using System.Security.Claims;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Infrastructure;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Authorize]
[Route("api/lobbies/{matchId}/ai-activity/{seatIndex:int}")]
public sealed class AiActivityController(ApplicationDbContext database,
    MatchAccessService access, ControlPlaneClient control) : ControllerBase
{
    [HttpGet("/api/lobbies/{matchId}/ai-activity/download")]
    public async Task<IActionResult> Download(string matchId)
    {
        var token = HttpContext.RequestAborted;
        var match = await database.PortalMatches.AsNoTracking().SingleOrDefaultAsync(m => m.MatchId == matchId, token);
        if (match is null) return NotFound();
        if (!await access.CanReadAiActivityAsync(match, User.FindFirstValue(ClaimTypes.NameIdentifier),
                User.IsInRole(PortalRoles.Administrator), token)) return Forbid();
        var seats = await database.PortalLobbySeats.AsNoTracking().Where(s => s.MatchId == matchId && s.ControllerKind == "agent" && s.AgentId != null).OrderBy(s => s.SeatIndex).ToArrayAsync(token);
        Response.ContentType = "application/json";
        Response.Headers.CacheControl = "no-store";
        Response.Headers.ContentDisposition = $"attachment; filename=\"ai-activity-{DateTimeOffset.UtcNow:yyyyMMdd-HHmmss}.json\"";
        await using var writer = new Utf8JsonWriter(Response.Body);
        writer.WriteStartObject(); writer.WriteString("schema", "smacx.ai-activity-export.v1");
        writer.WriteString("match_id", matchId); writer.WriteString("exported_at", DateTimeOffset.UtcNow);
        writer.WriteStartArray("seats");
        foreach (var seat in seats)
        {
            writer.WriteStartObject(); writer.WriteNumber("seat_index", seat.SeatIndex);
            writer.WriteStartArray("events");
            var cursor = ""; var gaps = new HashSet<string>();
            try
            {
                while (true)
                {
                    using var data = await control.GetRawAsync(
                        $"api/v1/matches/{Uri.EscapeDataString(matchId)}/activity/{Uri.EscapeDataString(seat.AgentId!)}?cursor={Uri.EscapeDataString(cursor)}", token);
                    var report = data.RootElement.GetProperty("report");
                    foreach (var item in report.GetProperty("events").EnumerateArray()) item.WriteTo(writer);
                    foreach (var gap in report.GetProperty("gaps").EnumerateArray()) gaps.Add(gap.GetString() ?? "capture_gap");
                    await writer.FlushAsync(token);
                    if (!report.GetProperty("has_more").GetBoolean()) break;
                    var next = report.GetProperty("cursor").GetString() ?? "";
                    if (next == cursor) { gaps.Add("export_cursor_stalled"); break; }
                    cursor = next;
                }
            }
            catch (ControlPlaneException) { gaps.Add("activity_read_failed"); }
            writer.WriteEndArray(); writer.WriteStartArray("gaps");
            foreach (var gap in gaps) writer.WriteStringValue(gap);
            writer.WriteEndArray(); writer.WriteEndObject();
            await writer.FlushAsync(token);
        }
        writer.WriteEndArray(); writer.WriteEndObject(); await writer.FlushAsync(token);
        return new EmptyResult();
    }

    [HttpGet]
    public async Task<ActionResult<ApiResponse<JsonElement>>> Read(string matchId, int seatIndex,
        [FromQuery] string? cursor = null)
    {
        Response.Headers.CacheControl = "no-store";
        var match = await database.PortalMatches.AsNoTracking().SingleOrDefaultAsync(
            m => m.MatchId == matchId, HttpContext.RequestAborted);
        if (match is null) return NotFound();
        if (!await access.CanReadAiActivityAsync(match, User.FindFirstValue(ClaimTypes.NameIdentifier),
                User.IsInRole(PortalRoles.Administrator), HttpContext.RequestAborted)) return Forbid();
        var seat = await database.PortalLobbySeats.AsNoTracking().SingleOrDefaultAsync(
            s => s.MatchId == matchId && s.SeatIndex == seatIndex, HttpContext.RequestAborted);
        if (seat is null || seat.ControllerKind != "agent" || seat.AgentId is null) return NotFound();
        if (cursor?.Length > 16000) return BadRequest();
        try
        {
            using var data = await control.GetRawAsync(
                $"api/v1/matches/{Uri.EscapeDataString(matchId)}/activity/{Uri.EscapeDataString(seat.AgentId)}?cursor={Uri.EscapeDataString(cursor ?? "")}",
                HttpContext.RequestAborted);
            return ApiResponse<JsonElement>.Success(data.RootElement.GetProperty("report").Clone());
        }
        catch (ControlPlaneException error)
        {
            return StatusCode(error.StatusCode ?? 502, ApiResponse<JsonElement>.Failure(error.Code, error.Message));
        }
    }
}
