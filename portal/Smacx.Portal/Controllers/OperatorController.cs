using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Smacx.Portal.Contracts;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

// The operator uses the portal's normal authenticated session and CSRF policy.
// These privileged observations are never added to the sovereign tool surface.
[ApiController]
[Route("api/operator/matches/{matchId}")]
[Authorize(Roles = "Administrator")]
public sealed class OperatorController(ControlPlaneClient control) : ControllerBase
{
    [HttpGet("/api/operator/preflight")]
    public async Task<ActionResult<ApiResponse<JsonElement>>> Preflight()
    {
        try
        {
            using var response = await control.GetRawAsync("api/v1/operator/preflight", HttpContext.RequestAborted);
            return ApiResponse<JsonElement>.Success(response.RootElement.GetProperty("report").Clone());
        }
        catch (ControlPlaneException error)
        {
            return StatusCode(error.StatusCode ?? 502, ApiResponse<JsonElement>.Failure(error.Code, error.Message));
        }
    }

    [HttpGet("{reportKind:regex(^(health|events|inspect)$)}")]
    public async Task<ActionResult<ApiResponse<JsonElement>>> Read(string matchId, string reportKind, [FromQuery] string? cursor = null, [FromQuery] string? objectRef = null)
    {
        try
        {
            using var response = await control.GetRawAsync(
                $"api/v1/matches/{Uri.EscapeDataString(matchId)}/operator/{reportKind}" +
                "?cursor=" + Uri.EscapeDataString(cursor ?? "") + "&object_ref=" + Uri.EscapeDataString(objectRef ?? ""), HttpContext.RequestAborted);
            return ApiResponse<JsonElement>.Success(response.RootElement.GetProperty("report").Clone());
        }
        catch (ControlPlaneException error)
        {
            return StatusCode(error.StatusCode ?? 502, ApiResponse<JsonElement>.Failure(error.Code, error.Message));
        }
    }

    [HttpPost("resume")]
    public async Task<ActionResult<ApiResponse<JsonElement>>> Resume(string matchId, MatchLifecycleRequest request)
    {
        try
        {
            using var response = await control.PostRawAsync(
                $"api/v1/matches/{Uri.EscapeDataString(matchId)}/operator/resume", new { incident_id = request.IncidentId });
            return ApiResponse<JsonElement>.Success(response.RootElement.GetProperty("report").Clone());
        }
        catch (ControlPlaneException error)
        {
            return StatusCode(error.StatusCode ?? 502, ApiResponse<JsonElement>.Failure(error.Code, error.Message));
        }
    }

    [HttpPost("pause")]
    public async Task<ActionResult<ApiResponse<JsonElement>>> Pause(string matchId)
    {
        try
        {
            using var response = await control.PostRawAsync(
                $"api/v1/matches/{Uri.EscapeDataString(matchId)}/operator/pause", new { });
            return ApiResponse<JsonElement>.Success(response.RootElement.GetProperty("report").Clone());
        }
        catch (ControlPlaneException error)
        {
            return StatusCode(error.StatusCode ?? 502, ApiResponse<JsonElement>.Failure(error.Code, error.Message));
        }
    }
}
