using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Authorize]
[Route("api/lobbies/{matchId}/controller/{seatIndex:int}")]
public sealed class ControllerLeasesController(
    ApplicationDbContext database,
    ControllerLeaseService leases) : ControllerBase
{
    [HttpPost("acquire")]
    public async Task<ActionResult<ApiResponse<ControllerLeaseState>>> Acquire(
        string matchId, int seatIndex, ControllerLeaseRequest request)
    {
        var resolved = await ResolveSeatAsync(matchId, seatIndex);
        if (resolved.Result is not null) return resolved.Result;
        if (!ValidPlayInstance(request.PlayInstanceId))
            return BadRequest(ApiResponse<ControllerLeaseState>.Failure(
                "invalid_play_instance", "Open a fresh game view and try again."));
        var (seat, userId) = resolved.Value;
        var snapshot = leases.Acquire(
            matchId, seatIndex, seat.ControlInstanceId!, userId, request.PlayInstanceId);
        return ApiResponse<ControllerLeaseState>.Success(ToContract(snapshot));
    }

    [HttpPost("heartbeat")]
    public async Task<ActionResult<ApiResponse<ControllerLeaseState>>> Heartbeat(
        string matchId, int seatIndex, ControllerLeaseActionRequest request)
    {
        var resolved = await ResolveSeatAsync(matchId, seatIndex);
        if (resolved.Result is not null) return resolved.Result;
        var (seat, userId) = resolved.Value;
        try
        {
            var snapshot = leases.Heartbeat(matchId, seatIndex, userId, request.LeaseId);
            return ApiResponse<ControllerLeaseState>.Success(ToContract(snapshot));
        }
        catch (ControllerLeaseException exception)
        {
            return Conflict(ApiResponse<ControllerLeaseState>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpPost("take-control")]
    public async Task<ActionResult<ApiResponse<ControllerLeaseState>>> TakeControl(
        string matchId, int seatIndex, ControllerLeaseActionRequest request)
    {
        var resolved = await ResolveSeatAsync(matchId, seatIndex);
        if (resolved.Result is not null) return resolved.Result;
        var (seat, userId) = resolved.Value;
        try
        {
            var snapshot = leases.TakeControl(matchId, seatIndex, userId, request.LeaseId);
            return ApiResponse<ControllerLeaseState>.Success(ToContract(snapshot));
        }
        catch (ControllerLeaseException exception)
        {
            return Conflict(ApiResponse<ControllerLeaseState>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpPost("release")]
    public async Task<ActionResult<ApiResponse<bool>>> Release(
        string matchId, int seatIndex, ControllerLeaseActionRequest request)
    {
        var resolved = await ResolveSeatAsync(matchId, seatIndex);
        if (resolved.Result is not null) return resolved.Result;
        var (seat, userId) = resolved.Value;
        leases.Release(matchId, seatIndex, userId, request.LeaseId);
        return ApiResponse<bool>.Success(true);
    }

    private async Task<SeatResolution> ResolveSeatAsync(string matchId, int seatIndex)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (userId is null)
            return new SeatResolution(Unauthorized(ApiResponse<ControllerLeaseState>.Failure(
                "authentication_required", "Sign in to control this seat.")));
        var seat = await database.PortalLobbySeats.AsNoTracking().SingleOrDefaultAsync(item =>
            item.MatchId == matchId && item.SeatIndex == seatIndex, HttpContext.RequestAborted);
        if (seat is null) return new SeatResolution(NotFound(ApiResponse<ControllerLeaseState>.Failure(
            "seat_not_found", "The player seat was not found.")));
        if (seat.UserId != userId || seat.ControllerKind != "human" ||
            seat.JoinMode != "browser" || seat.ControlInstanceId is null)
            return new SeatResolution(StatusCode(StatusCodes.Status403Forbidden,
                ApiResponse<ControllerLeaseState>.Failure(
                    "seat_control_forbidden", "This account does not own the browser-managed seat.")));
        return new SeatResolution(seat, userId);
    }

    private static ControllerLeaseState ToContract(ControllerLeaseSnapshot value) => new(
        value.LeaseId, value.Role, value.ExpiresAt, value.Generation,
        value.ControllerPresent, value.ExpiresInSeconds);

    private static bool ValidPlayInstance(string value) =>
        !string.IsNullOrWhiteSpace(value) && value.Length <= 96 &&
        value.All(character => char.IsLetterOrDigit(character) || character is '-' or '_');

    private sealed record SeatResolution
    {
        public SeatResolution(PortalLobbySeat seat, string userId)
        {
            Seat = seat;
            UserId = userId;
        }

        public SeatResolution(ActionResult result) => Result = result;

        public PortalLobbySeat? Seat { get; }
        public string? UserId { get; }
        public ActionResult? Result { get; }
        public (PortalLobbySeat Seat, string UserId) Value => (Seat!, UserId!);
    }
}
