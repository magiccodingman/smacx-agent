using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Hubs;
using Smacx.Portal.Infrastructure;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/lobbies")]
public sealed class LobbiesController(
    ApplicationDbContext database,
    UserManager<ApplicationUser> userManager,
    ControlPlaneClient control,
    IHubContext<LobbyHub> lobbyHub,
    StreamPresenceTracker presence,
    PersonalityCardLibrary personalityCards) : ControllerBase
{
    [HttpGet]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<PublicLobbySummary>>>> List()
    {
        var profiles = await database.PortalMatches.AsNoTracking()
            .Where(match => match.IsListed && match.Status != "deleted")
            .OrderByDescending(match => match.UpdatedAt)
            .ToArrayAsync(HttpContext.RequestAborted);
        var counts = await database.PortalLobbySeats.AsNoTracking()
            .Where(seat => seat.ControllerKind != "open")
            .GroupBy(seat => seat.MatchId)
            .Select(group => new { MatchId = group.Key, Count = group.Count() })
            .ToDictionaryAsync(item => item.MatchId, item => item.Count, HttpContext.RequestAborted);
        var results = profiles.Select(match => new PublicLobbySummary(
            match.MatchId, match.DisplayName, match.Status, match.CurrentTurn ?? 0,
            counts.GetValueOrDefault(match.MatchId), match.AllowAnonymousSpectators,
            match.UpdatedAt)).ToArray();
        return ApiResponse<IReadOnlyList<PublicLobbySummary>>.Success(results);
    }

    [HttpGet("{matchId}")]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<LobbyDetails>>> Get(string matchId)
    {
        var profile = await database.PortalMatches.AsNoTracking()
            .SingleOrDefaultAsync(match => match.MatchId == matchId, HttpContext.RequestAborted);
        if (profile is null || !profile.IsListed && !CanManage(profile) &&
            !await IsMemberAsync(profile.MatchId))
        {
            return NotFound(ApiResponse<LobbyDetails>.Failure("lobby_not_found", "The lobby was not found."));
        }
        return ApiResponse<LobbyDetails>.Success(await MapDetailsAsync(profile));
    }

    [HttpPost]
    [Authorize]
    public async Task<ActionResult<ApiResponse<LobbyDetails>>> Create(CreateLobbyRequest request)
    {
        var user = await userManager.GetUserAsync(User);
        if (user is null)
        {
            return Unauthorized(ApiResponse<LobbyDetails>.Failure("authentication_required", "Sign in to create a lobby."));
        }
        var validation = Validate(request);
        if (validation is not null)
        {
            return BadRequest(ApiResponse<LobbyDetails>.Failure(validation.Value.Code, validation.Value.Message));
        }
        if (FactionCatalog.IsReservedLeaderName(user.DisplayName))
            return Conflict(ApiResponse<LobbyDetails>.Failure(
                "reserved_faction_leader_name",
                "Change your public display name before joining; faction-leader names are reserved for AI players."));
        if ((request.HostController == "human" || request.OwnerPlays) &&
            request.InvitedHumanHandles.Any(handle =>
                handle.Trim().Equals(user.DisplayName, StringComparison.OrdinalIgnoreCase)))
        {
            return BadRequest(ApiResponse<LobbyDetails>.Failure(
                "duplicate_player_handle",
                "Your account is already assigned to this lobby; do not invite the same public display name again."));
        }

        var matchId = $"match-{Guid.NewGuid():N}";
        var now = DateTimeOffset.UtcNow;
        var requestedAgents = EffectiveAgentSeats(request);
        var profile = new PortalMatchProfile
        {
            MatchId = matchId,
            OwnerUserId = user.Id,
            DisplayName = request.DisplayName.Trim(),
            Status = "waiting",
            Mode = request.Mode,
            GameSourceId = request.GameSourceId,
            RuntimeId = request.RuntimeId,
            LanProfile = ResolveProfile(request.WorldSize),
            SettingsJson = JsonSerializer.Serialize(new
            {
                request.WorldSize,
                request.Difficulty,
                request.RandomMap,
                request.DoOrDie,
                request.NativeBotCount,
                request.NativeBotDifficulty,
                request.HostController,
                request.OwnerPlays,
                request.TimeControl,
                request.OceanCoverage,
                request.ErosiveForces,
                request.NativeLife,
                request.CloudCover,
                request.RuleOptions,
                request.ScenarioId,
                request.ResumeSlot,
            }),
            NativeSettingsJson = JsonSerializer.Serialize(new
            {
                difficulty = DifficultyId(request.Difficulty),
                time_control = request.TimeControl,
                world_size = WorldSizeId(request.WorldSize),
                ocean_coverage = request.OceanCoverage,
                erosive_forces = request.ErosiveForces,
                native_life = request.NativeLife,
                cloud_cover = request.CloudCover,
                victory_transcendence = Rule(request, "victory_transcendence", true),
                victory_conquest = Rule(request, "victory_conquest", true),
                victory_diplomatic = Rule(request, "victory_diplomatic", true),
                victory_economic = Rule(request, "victory_economic", true),
                victory_cooperative = Rule(request, "victory_cooperative", !request.DoOrDie),
                do_or_die = request.DoOrDie,
                look_first = Rule(request, "look_first", false),
                tech_stagnation = Rule(request, "tech_stagnation", false),
                spoils_of_war = Rule(request, "spoils_of_war", true),
                blind_research = Rule(request, "blind_research", true),
                intense_rivalry = Rule(request, "intense_rivalry", false),
                unity_survey = Rule(request, "unity_survey", false),
                unity_scattering = Rule(request, "unity_scattering", false),
                random_events = Rule(request, "random_events", true),
                time_warp = Rule(request, "time_warp", false),
                ironman = Rule(request, "ironman", false),
            }),
            AllowAnonymousSpectators = request.AllowAnonymousSpectators,
            ManagedClientsOnly = request.ManagedClientsOnly,
            RankingMode = "unranked",
            GraphitiEnabled = request.GraphitiEnabled,
            PersonalityCardId = "none",
            ScenarioId = string.IsNullOrWhiteSpace(request.ScenarioId) ? null : request.ScenarioId,
            ResumeSlot = string.IsNullOrWhiteSpace(request.ResumeSlot) ? null : request.ResumeSlot,
            CreatedAt = now,
            UpdatedAt = now,
        };
        database.PortalMatches.Add(profile);
        database.PortalMatchMembers.Add(new PortalMatchMember
        {
            MatchId = matchId,
            UserId = user.Id,
            Role = "owner",
            JoinMode = "browser",
            JoinedAt = now,
        });

        var seatIndex = 0;
        if (request.HostController == "agent")
        {
            AddAgentSeats(requestedAgents, ref seatIndex, matchId, now);
            if (request.OwnerPlays)
            {
                AddHumanSeat(user.Id, user.DisplayName, request.HumanJoinMode, ref seatIndex, matchId, now);
            }
        }
        else
        {
            AddHumanSeat(user.Id, user.DisplayName, request.HumanJoinMode, ref seatIndex, matchId, now);
            AddAgentSeats(requestedAgents, ref seatIndex, matchId, now);
        }
        foreach (var handle in request.InvitedHumanHandles)
        {
            var invited = await EnsureLobbyUserAsync(handle.Trim());
            AddHumanSeat(invited.Id, invited.DisplayName, request.HumanJoinMode,
                ref seatIndex, matchId, now);
        }
        for (var index = 0; index < request.NativeBotCount; index++)
        {
            database.PortalLobbySeats.Add(new PortalLobbySeat
            {
                MatchId = matchId,
                SeatIndex = seatIndex++,
                ControllerKind = "native",
                PlayerHandle = $"Native bot {index + 1}",
                Status = "assigned",
                UpdatedAt = now,
            });
        }
        while (seatIndex < 7)
        {
            database.PortalLobbySeats.Add(new PortalLobbySeat
            {
                MatchId = matchId,
                SeatIndex = seatIndex++,
                ControllerKind = "open",
                Status = "open",
                UpdatedAt = now,
            });
        }
        await database.SaveChangesAsync(HttpContext.RequestAborted);

        if (request.StartNow)
        {
            var started = await MaterializeAsync(profile, HttpContext.RequestAborted);
            if (started is not null)
            {
                return StatusCode(started.Value.Status, ApiResponse<LobbyDetails>.Failure(
                    started.Value.Code, started.Value.Message));
            }
        }
        return CreatedAtAction(nameof(Get), new { matchId },
            ApiResponse<LobbyDetails>.Success(await MapDetailsAsync(profile)));
    }

    [HttpPost("{matchId}/join")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<LobbyDetails>>> Join(
        string matchId, JoinLobbyRequest request)
    {
        var user = await userManager.GetUserAsync(User);
        if (user is null) return Unauthorized();
        if (FactionCatalog.IsReservedLeaderName(user.DisplayName))
            return Conflict(ApiResponse<LobbyDetails>.Failure(
                "reserved_faction_leader_name",
                "Change your public display name before joining; faction-leader names are reserved for AI players."));
        if (request.JoinMode is not ("browser" or "native"))
            return BadRequest(ApiResponse<LobbyDetails>.Failure(
                "invalid_join_mode", "Choose browser-managed or direct native play."));
        var profile = await database.PortalMatches.SingleOrDefaultAsync(
            item => item.MatchId == matchId, HttpContext.RequestAborted);
        if (profile is null || profile.Status != "waiting")
            return Conflict(ApiResponse<LobbyDetails>.Failure(
                "lobby_not_joinable", "This lobby is not accepting seat changes."));
        if (profile.ManagedClientsOnly && request.JoinMode != "browser")
            return BadRequest(ApiResponse<LobbyDetails>.Failure(
                "managed_clients_required", "This lobby requires browser-managed seats."));
        if (await database.PortalLobbySeats.AsNoTracking().AnyAsync(item =>
                item.MatchId == matchId && item.UserId != user.Id && item.PlayerHandle != null &&
                item.PlayerHandle.ToUpper() == user.DisplayName.ToUpper(), HttpContext.RequestAborted))
            return Conflict(ApiResponse<LobbyDetails>.Failure(
                "display_name_in_use", "That public display name is already present in this lobby."));
        var seat = await database.PortalLobbySeats.SingleOrDefaultAsync(
            item => item.MatchId == matchId && item.SeatIndex == request.SeatIndex,
            HttpContext.RequestAborted);
        if (seat is null || seat.ControllerKind is not ("open" or "human") ||
            seat.UserId is not null && seat.UserId != user.Id ||
            seat.ControllerKind == "human" && seat.PlayerHandle is not null &&
            !seat.PlayerHandle.Equals(user.DisplayName, StringComparison.OrdinalIgnoreCase))
            return Conflict(ApiResponse<LobbyDetails>.Failure(
                "seat_unavailable", "That seat is no longer available to this player."));
        var old = await database.PortalLobbySeats.SingleOrDefaultAsync(
            item => item.MatchId == matchId && item.UserId == user.Id && item.SeatIndex != request.SeatIndex,
            HttpContext.RequestAborted);
        if (old is not null)
        {
            old.ControllerKind = "open"; old.UserId = null; old.PlayerHandle = null;
            old.Status = "open"; old.UpdatedAt = DateTimeOffset.UtcNow;
        }
        seat.ControllerKind = "human"; seat.UserId = user.Id; seat.PlayerHandle = user.DisplayName;
        seat.JoinMode = request.JoinMode; seat.Status = "ready"; seat.UpdatedAt = DateTimeOffset.UtcNow;
        var member = await database.PortalMatchMembers.SingleOrDefaultAsync(
            item => item.MatchId == matchId && item.UserId == user.Id, HttpContext.RequestAborted);
        if (member is null)
            database.PortalMatchMembers.Add(new PortalMatchMember
            {
                MatchId = matchId, UserId = user.Id, SeatIndex = request.SeatIndex,
                Role = "player", JoinMode = request.JoinMode,
            });
        else
        {
            member.SeatIndex = request.SeatIndex; member.JoinMode = request.JoinMode;
            member.LeftAt = null;
        }
        profile.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        await lobbyHub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
            "LobbyChanged", matchId, HttpContext.RequestAborted);
        return ApiResponse<LobbyDetails>.Success(await MapDetailsAsync(profile));
    }

    [HttpPut("{matchId}/seats/{seatIndex:int}")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<LobbyDetails>>> UpdateSeat(
        string matchId, int seatIndex, UpdateLobbySeatRequest request)
    {
        var profile = await database.PortalMatches.SingleOrDefaultAsync(
            item => item.MatchId == matchId, HttpContext.RequestAborted);
        if (profile is null) return NotFound(ApiResponse<LobbyDetails>.Failure(
            "lobby_not_found", "The lobby was not found."));
        if (!CanManage(profile)) return Forbid();
        if (profile.Status != "waiting") return Conflict(ApiResponse<LobbyDetails>.Failure(
            "seat_edit_requires_waiting_lobby", "Seats can be changed only before the match starts."));
        if (seatIndex is < 0 or > 6) return BadRequest(ApiResponse<LobbyDetails>.Failure(
            "invalid_seat_index", "Choose one of the seven faction seats."));
        if (request.ControllerKind is not ("open" or "agent" or "human" or "native"))
            return BadRequest(ApiResponse<LobbyDetails>.Failure(
                "invalid_seat_controller", "Choose Open, Human, AI, or Stock computer."));

        var seat = await database.PortalLobbySeats.SingleOrDefaultAsync(
            item => item.MatchId == matchId && item.SeatIndex == seatIndex,
            HttpContext.RequestAborted);
        if (seat is null) return NotFound(ApiResponse<LobbyDetails>.Failure(
            "seat_not_found", "That faction seat was not found."));
        var oldUserId = seat.UserId;
        seat.ControllerKind = request.ControllerKind;
        seat.AgentId = null; seat.AiProfileId = null; seat.UserId = null;
        seat.PlayerHandle = null; seat.JoinMode = "browser"; seat.RequestedFactionId = FactionCatalog.Random;
        seat.ResolvedFactionKey = null; seat.LeaderName = null;
        seat.RequestedPersonalityId = "standard"; seat.PersonalityCardId = "none";
        seat.PersonalityName = null; seat.PersonalityPrompt = null; seat.PersonalityPromptSha256 = null;
        seat.FactionId = null; seat.FactionName = null; seat.ControlInstanceId = null;
        seat.UpdatedAt = DateTimeOffset.UtcNow;

        if (request.ControllerKind == "agent")
        {
            if (string.IsNullOrWhiteSpace(request.AgentId)) return BadRequest(
                ApiResponse<LobbyDetails>.Failure("ai_profile_required", "Choose an AI profile for this seat."));
            var aiProfile = await database.PortalAiProfiles.AsNoTracking()
                .Where(item => item.AgentId == request.AgentId && item.Active)
                .FirstOrDefaultAsync(HttpContext.RequestAborted);
            if (aiProfile is null) return BadRequest(ApiResponse<LobbyDetails>.Failure(
                "unknown_ai_profile", "That AI profile is not active."));
            if (request.FactionId != FactionCatalog.Random && FactionCatalog.Find(request.FactionId) is null)
                return BadRequest(ApiResponse<LobbyDetails>.Failure(
                    "invalid_agent_faction", "Choose Random or one official faction."));
            if (BuiltInPersonalityCatalog.FindMode(request.PersonalityId) is null ||
                request.FactionId == FactionCatalog.Random && request.PersonalityId is not ("none" or "standard" or "random"))
                return BadRequest(ApiResponse<LobbyDetails>.Failure(
                    "invalid_agent_personality", "Choose a personality compatible with this faction selection."));
            if (request.FactionId != FactionCatalog.Random && await database.PortalLobbySeats.AsNoTracking().AnyAsync(
                    item => item.MatchId == matchId && item.SeatIndex != seatIndex &&
                        item.ControllerKind == "agent" && item.RequestedFactionId == request.FactionId,
                    HttpContext.RequestAborted))
                return Conflict(ApiResponse<LobbyDetails>.Failure(
                    "duplicate_agent_faction", "Another AI seat already reserves that faction."));
            var faction = FactionCatalog.Find(request.FactionId);
            seat.AgentId = aiProfile.AgentId; seat.AiProfileId = aiProfile.ProfileId;
            seat.RequestedFactionId = request.FactionId;
            seat.RequestedPersonalityId = request.PersonalityId;
            seat.PlayerHandle = faction?.LeaderName ?? "Random faction AI";
            seat.LeaderName = faction?.LeaderName; seat.Status = "assigned";
        }
        else if (request.ControllerKind == "human")
        {
            if (request.JoinMode is not ("browser" or "native")) return BadRequest(
                ApiResponse<LobbyDetails>.Failure("invalid_join_mode", "Choose browser or separate native play."));
            var current = await userManager.GetUserAsync(User);
            var handle = string.IsNullOrWhiteSpace(request.PlayerHandle)
                ? current?.DisplayName ?? "" : request.PlayerHandle.Trim();
            if (handle.Length is < 1 or > 31 || handle.Any(character => character < 32 || character > 126))
                return BadRequest(ApiResponse<LobbyDetails>.Failure(
                    "invalid_player_handle", "Public display names must contain 1–31 printable characters."));
            if (FactionCatalog.IsReservedLeaderName(handle)) return Conflict(
                ApiResponse<LobbyDetails>.Failure("reserved_faction_leader_name",
                    "Faction-leader names are reserved for AI players."));
            if (await database.PortalLobbySeats.AsNoTracking().AnyAsync(item =>
                    item.MatchId == matchId && item.SeatIndex != seatIndex && item.PlayerHandle != null &&
                    item.PlayerHandle.ToUpper() == handle.ToUpper(), HttpContext.RequestAborted))
                return Conflict(ApiResponse<LobbyDetails>.Failure(
                    "display_name_in_use", "That public display name already occupies another seat."));
            var player = current is not null && current.DisplayName.Equals(handle, StringComparison.OrdinalIgnoreCase)
                ? current : await EnsureLobbyUserAsync(handle);
            seat.UserId = player.Id; seat.PlayerHandle = player.DisplayName;
            seat.JoinMode = request.JoinMode; seat.Status = player.IsProvisional ? "invited" : "ready";
            var member = await database.PortalMatchMembers.SingleOrDefaultAsync(
                item => item.MatchId == matchId && item.UserId == player.Id, HttpContext.RequestAborted);
            if (member is null) database.PortalMatchMembers.Add(new PortalMatchMember {
                MatchId = matchId, UserId = player.Id, SeatIndex = seatIndex, Role = "player",
                JoinMode = request.JoinMode, JoinedAt = DateTimeOffset.UtcNow,
            });
            else { member.SeatIndex = seatIndex; member.JoinMode = request.JoinMode; member.LeftAt = null; }
        }
        else if (request.ControllerKind == "native")
        {
            seat.PlayerHandle = $"Stock computer {seatIndex + 1}"; seat.Status = "assigned";
        }
        else seat.Status = "open";

        if (oldUserId is not null && oldUserId != seat.UserId)
        {
            var oldMember = await database.PortalMatchMembers.SingleOrDefaultAsync(
                item => item.MatchId == matchId && item.UserId == oldUserId,
                HttpContext.RequestAborted);
            if (oldMember is not null && oldMember.Role != "owner")
            { oldMember.SeatIndex = null; oldMember.LeftAt = DateTimeOffset.UtcNow; }
        }
        profile.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        await lobbyHub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
            "LobbyChanged", matchId, HttpContext.RequestAborted);
        return ApiResponse<LobbyDetails>.Success(await MapDetailsAsync(profile));
    }

    [HttpPost("{matchId}/lifecycle")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<LobbyDetails>>> Lifecycle(
        string matchId, MatchLifecycleRequest request)
    {
        var profile = await database.PortalMatches.SingleOrDefaultAsync(
            item => item.MatchId == matchId, HttpContext.RequestAborted);
        if (profile is null) return NotFound();
        if (!CanManage(profile)) return Forbid();
        try
        {
            switch (request.Action)
            {
                case "park":
                    // Claim the lifecycle before any slow operation so the
                    // supervisor cannot replace a stopped Hermes run while a
                    // native checkpoint is being taken.
                    profile.Status = "parking";
                    profile.LastError = null;
                    profile.UpdatedAt = DateTimeOffset.UtcNow;
                    await database.SaveChangesAsync(HttpContext.RequestAborted);
                    await lobbyHub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
                        "LobbyChanged", matchId, HttpContext.RequestAborted);
                    await StopAgentRunsAsync(matchId, HttpContext.RequestAborted);
                    await control.PostRawAsync($"api/v1/matches/{matchId}/checkpoint",
                        new { slot = request.Slot ?? "control_recovery" });
                    await control.PostRawAsync($"api/v1/matches/{matchId}/park", new { });
                    break;
                case "checkpoint": await control.PostRawAsync($"api/v1/matches/{matchId}/checkpoint", new { slot = request.Slot ?? "control_recovery" }); break;
                case "recover": await control.PostRawAsync($"api/v1/matches/{matchId}/recover", new { }); break;
                case "end":
                    if (profile.Status != "parked")
                        return Conflict(ApiResponse<LobbyDetails>.Failure(
                            "end_requires_parked_match",
                            "Park the campaign at a verified checkpoint before ending it permanently."));
                    await control.PostRawAsync($"api/v1/matches/{matchId}/complete", new { });
                    break;
                default: return BadRequest(ApiResponse<LobbyDetails>.Failure(
                    "invalid_lifecycle_action", "Choose park, checkpoint, recover, or end."));
            }
            profile.Status = request.Action switch
            {
                "park" => "parked",
                "end" => "completed",
                _ => "running",
            };
            if (request.Action is "park" or "end")
            {
                var managedSeats = await database.PortalLobbySeats
                    .Where(item => item.MatchId == matchId && item.ControlInstanceId != null)
                    .ToArrayAsync(HttpContext.RequestAborted);
                foreach (var seat in managedSeats)
                {
                    seat.ConnectionState = request.Action == "end" ? "retired" : "worker_stopped";
                    seat.UpdatedAt = DateTimeOffset.UtcNow;
                }
            }
            if (request.Action == "end") profile.IsListed = false;
            profile.LastError = null; profile.UpdatedAt = DateTimeOffset.UtcNow;
            database.PortalMatchEvents.Add(new PortalMatchEvent
            {
                MatchId = matchId, EventType = request.Action,
                Summary = $"Match {request.Action} completed.",
            });
            await database.SaveChangesAsync(HttpContext.RequestAborted);
            return ApiResponse<LobbyDetails>.Success(await MapDetailsAsync(profile));
        }
        catch (ControlPlaneException exception)
        {
            if (request.Action == "park")
            {
                profile.Status = "running";
                profile.LastError = exception.Message[..Math.Min(exception.Message.Length, 4000)];
                profile.UpdatedAt = DateTimeOffset.UtcNow;
                database.PortalMatchEvents.Add(new PortalMatchEvent
                {
                    MatchId = matchId, EventType = "park_failed",
                    Summary = $"Parking failed safely: {exception.Message}",
                });
                await database.SaveChangesAsync(CancellationToken.None);
                await lobbyHub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
                    "LobbyChanged", matchId, CancellationToken.None);
            }
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<LobbyDetails>.Failure(exception.Code, exception.Message));
        }
    }

    private async Task StopAgentRunsAsync(string matchId, CancellationToken cancellationToken)
    {
        using var document = await control.GetRawAsync("api/v1/harness-runs", cancellationToken);
        var active = document.RootElement.GetProperty("harness_runs").EnumerateArray()
            .Where(run => run.GetProperty("match_id").GetString() == matchId &&
                run.GetProperty("status").GetString() is "queued" or "starting" or
                    "running" or "restarting")
            .Select(run => run.GetProperty("run_id").GetString())
            .Where(runId => !string.IsNullOrWhiteSpace(runId))
            .ToArray();
        foreach (var runId in active)
        {
            using (await control.PostRawAsync(
                $"api/v1/harness-runs/{Uri.EscapeDataString(runId!)}/stop", new { },
                cancellationToken)) { }
        }
    }

    [HttpPost("{matchId}/start")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<LobbyDetails>>> Start(string matchId)
    {
        var profile = await database.PortalMatches.SingleOrDefaultAsync(
            match => match.MatchId == matchId, HttpContext.RequestAborted);
        if (profile is null)
        {
            return NotFound(ApiResponse<LobbyDetails>.Failure("lobby_not_found", "The lobby was not found."));
        }
        if (!CanManage(profile))
        {
            return Forbid();
        }
        if (profile.Status != "waiting")
        {
            return Conflict(ApiResponse<LobbyDetails>.Failure(
                "lobby_not_waiting", "Only a waiting lobby can be started."));
        }
        var failure = await MaterializeAsync(profile, HttpContext.RequestAborted);
        if (failure is not null)
        {
            return StatusCode(failure.Value.Status,
                ApiResponse<LobbyDetails>.Failure(failure.Value.Code, failure.Value.Message));
        }
        await lobbyHub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
            "LobbyChanged", matchId, HttpContext.RequestAborted);
        return ApiResponse<LobbyDetails>.Success(await MapDetailsAsync(profile));
    }

    [HttpGet("{matchId}/messages")]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<LobbyMessage>>>> Messages(string matchId)
    {
        var profile = await database.PortalMatches.AsNoTracking().SingleOrDefaultAsync(
            match => match.MatchId == matchId, HttpContext.RequestAborted);
        if (profile is null)
        {
            return NotFound(ApiResponse<IReadOnlyList<LobbyMessage>>.Failure(
                "lobby_not_found", "The lobby was not found."));
        }
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        var member = userId is not null && await database.PortalMatchMembers.AsNoTracking()
            .AnyAsync(item => item.MatchId == matchId && item.UserId == userId &&
                item.LeftAt == null, HttpContext.RequestAborted);
        if (!member && !User.IsInRole("Administrator") &&
            !(profile.IsListed && profile.AllowAnonymousSpectators)) return Forbid();
        var localFaction = member ? await database.PortalLobbySeats.AsNoTracking()
            .Where(item => item.MatchId == matchId && item.UserId == userId)
            .Select(item => item.FactionId).SingleOrDefaultAsync(HttpContext.RequestAborted) : null;
        var groupIds = localFaction is not null
            ? await database.PortalChatGroupMembers.AsNoTracking()
                .Where(item => item.FactionId == localFaction && item.Status == "accepted")
                .Select(item => item.GroupId).ToArrayAsync(HttpContext.RequestAborted)
            : [];
        var messages = await database.PortalLobbyMessages.AsNoTracking()
            .Where(message => message.MatchId == matchId)
            .OrderByDescending(message => message.CreatedAt)
            .Take(200)
            .OrderBy(message => message.CreatedAt)
            .ToArrayAsync(HttpContext.RequestAborted);
        var visible = messages.Where(message => message.Channel == "global" || member && (
            message.UserId == userId ||
            message.Channel == "private" && message.RecipientFactionId == localFaction ||
            message.Channel == "group" && message.ConversationId is not null &&
                groupIds.Contains(message.ConversationId))).ToArray();
        var ids = visible.Select(item => item.Id).ToArray();
        var deliveryRows = await database.PortalChatDeliveries.AsNoTracking()
            .Where(item => ids.Contains(item.MessageId)).ToArrayAsync(HttpContext.RequestAborted);
        return ApiResponse<IReadOnlyList<LobbyMessage>>.Success(visible.Select(message =>
            ToMessage(message, deliveryRows.Where(item => item.MessageId == message.Id))).ToArray());
    }

    [HttpPost("{matchId}/messages")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<LobbyMessage>>> SendMessage(
        string matchId, SendLobbyMessageRequest request)
    {
        var user = await userManager.GetUserAsync(User);
        if (user is null)
        {
            return Unauthorized(ApiResponse<LobbyMessage>.Failure("authentication_required", "Sign in to chat."));
        }
        var content = request.Content.Trim();
        if (content.Length is < 1 or > 240 || content.Any(character => character < 0x20 || character > 0x7e))
        {
            return BadRequest(ApiResponse<LobbyMessage>.Failure(
                "invalid_message", "Messages must contain 1–240 printable ASCII characters."));
        }
        var profile = await database.PortalMatches.SingleOrDefaultAsync(
            match => match.MatchId == matchId, HttpContext.RequestAborted);
        if (profile is null)
        {
            return NotFound(ApiResponse<LobbyMessage>.Failure("lobby_not_found", "The lobby was not found."));
        }
        if (profile.Status == "completed")
            return Conflict(ApiResponse<LobbyMessage>.Failure(
                "campaign_chat_archived", "Completed campaign chat is read-only."));
        if (!await database.PortalMatchMembers.AsNoTracking().AnyAsync(item =>
                item.MatchId == matchId && item.UserId == user.Id && item.LeftAt == null,
                HttpContext.RequestAborted)) return Forbid();
        if (request.Channel is not ("global" or "private" or "group"))
            return BadRequest(ApiResponse<LobbyMessage>.Failure(
                "invalid_chat_channel", "Choose global, private, or group chat."));
        if (request.RecipientFactionId is < 0 or > 7)
        {
            return BadRequest(ApiResponse<LobbyMessage>.Failure(
                "invalid_chat_recipient", "Choose everyone or a known faction."));
        }
        if (request.Channel == "private" && request.RecipientFactionId == 0)
            return BadRequest(ApiResponse<LobbyMessage>.Failure(
                "private_recipient_required", "Choose one contacted faction."));
        var entity = new PortalLobbyMessage
        {
            MatchId = matchId,
            UserId = user.Id,
            SenderHandle = user.DisplayName,
            Content = content,
            DeliveredToGame = false,
            NativeMessageUid = $"portal:{Guid.NewGuid():N}:to:{request.RecipientFactionId}",
            Channel = request.Channel,
            ConversationId = request.ConversationId,
            RecipientFactionId = request.RecipientFactionId,
        };
        database.PortalLobbyMessages.Add(entity);
        var managedSeat = await database.PortalLobbySeats.AsNoTracking().SingleOrDefaultAsync(
            seat => seat.MatchId == matchId && seat.UserId == user.Id &&
                seat.ControllerKind == "human" && seat.JoinMode == "browser" &&
                seat.ControlInstanceId != null, HttpContext.RequestAborted);
        entity.SenderFactionId = managedSeat?.FactionId;
        if (request.Channel == "group")
        {
            if (profile.Status != "running" || managedSeat?.ControlInstanceId is null ||
                string.IsNullOrWhiteSpace(request.ConversationId))
                return Conflict(ApiResponse<LobbyMessage>.Failure(
                    "managed_group_chat_unavailable",
                    "Group chat requires this player's running browser-managed seat."));
            try
            {
                using var sent = await control.PostRawAsync(
                    $"api/v1/workers/{managedSeat.ControlInstanceId}/group-chat", new
                    {
                        action = "send", group_id = request.ConversationId, text = content,
                    }, HttpContext.RequestAborted);
                entity.LogicalMessageId = sent.RootElement.GetProperty(
                    "logical_message_id").GetString();
                entity.ConversationName = await database.PortalChatGroups.AsNoTracking()
                    .Where(item => item.GroupId == request.ConversationId)
                    .Select(item => item.DisplayName).SingleOrDefaultAsync(HttpContext.RequestAborted);
                var canonicalHandles = await CanonicalFactionHandlesAsync(matchId);
                foreach (var delivery in sent.RootElement.GetProperty("deliveries").EnumerateArray())
                {
                    var recipientFactionId = delivery.GetProperty("recipient_faction_id").GetInt32();
                    database.PortalChatDeliveries.Add(new PortalChatDelivery
                    {
                        MessageId = entity.Id,
                        RecipientFactionId = recipientFactionId,
                        RecipientHandle = canonicalHandles.GetValueOrDefault(recipientFactionId),
                        Status = delivery.GetProperty("status").GetString() ?? "failed",
                        DeliveredAt = delivery.GetProperty("status").GetString() == "delivered"
                            ? DateTimeOffset.UtcNow : null,
                    });
                }
                entity.DeliveredToGame = sent.RootElement.GetProperty("ok").GetBoolean();
            }
            catch (ControlPlaneException exception)
            {
                return Conflict(ApiResponse<LobbyMessage>.Failure(exception.Code, exception.Message));
            }
        }
        else if (profile.Status == "running" && managedSeat?.ControlInstanceId is not null)
        {
            try
            {
                using var delivered = await control.PostRawAsync(
                    $"api/v1/workers/{managedSeat.ControlInstanceId}/chat", new
                    {
                        action = "send", text = content,
                        recipient_faction_id = request.Channel == "global"
                            ? 0 : request.RecipientFactionId,
                        client_message_id = entity.Id,
                    }, HttpContext.RequestAborted);
                entity.DeliveredToGame = delivered.RootElement.TryGetProperty("sent", out var sent)
                    && sent.GetBoolean();
            }
            catch (ControlPlaneException)
            {
                // Preserve the portal message with an explicit undelivered flag.
                // The user can see that native transport was unavailable.
            }
        }
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        var storedDeliveries = await database.PortalChatDeliveries.AsNoTracking()
            .Where(item => item.MessageId == entity.Id).ToArrayAsync(HttpContext.RequestAborted);
        var message = ToMessage(entity, storedDeliveries);
        // Never push a private/group body through the broad lobby SignalR
        // group. Each client refetches through the authorization-filtered API.
        await lobbyHub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
            "LobbyChanged", matchId, HttpContext.RequestAborted);
        return ApiResponse<LobbyMessage>.Success(message);
    }

    [HttpGet("{matchId}/chat-groups")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<ChatConversation>>>> ChatGroups(
        string matchId)
    {
        var user = await userManager.GetUserAsync(User);
        if (user is null) return Unauthorized();
        var seat = await ManagedHumanSeatAsync(matchId, user.Id);
        if (seat?.ControlInstanceId is null)
            return Conflict(ApiResponse<IReadOnlyList<ChatConversation>>.Failure(
                "managed_group_chat_unavailable",
                "Group chat requires a running browser-managed player seat."));
        try
        {
            using var document = await control.PostRawAsync(
                $"api/v1/workers/{seat.ControlInstanceId}/group-chat",
                new { action = "list" }, HttpContext.RequestAborted);
            var canonicalHandles = await CanonicalFactionHandlesAsync(matchId);
            var groups = new List<ChatConversation>();
            foreach (var group in document.RootElement.GetProperty("groups").EnumerateArray())
            {
                await MirrorGroupAsync(matchId, user.Id, group);
                groups.Add(MapConversation(matchId, group, seat.FactionId, canonicalHandles));
            }
            await database.SaveChangesAsync(HttpContext.RequestAborted);
            return ApiResponse<IReadOnlyList<ChatConversation>>.Success(groups);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<IReadOnlyList<ChatConversation>>.Failure(
                    exception.Code, exception.Message));
        }
    }

    [HttpGet("{matchId}/chat-participants")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<ChatParticipant>>>> ChatParticipants(
        string matchId)
    {
        var user = await userManager.GetUserAsync(User);
        if (user is null) return Unauthorized();
        var seat = await ManagedHumanSeatAsync(matchId, user.Id);
        if (seat?.ControlInstanceId is null)
            return Conflict(ApiResponse<IReadOnlyList<ChatParticipant>>.Failure(
                "managed_chat_unavailable",
                "Native chat requires a running browser-managed player seat."));
        try
        {
            using var document = await control.PostRawAsync(
                $"api/v1/workers/{seat.ControlInstanceId}/group-chat",
                new { action = "list" }, HttpContext.RequestAborted);
            var canonicalHandles = await CanonicalFactionHandlesAsync(matchId);
            var participants = document.RootElement.GetProperty("participants")
                .EnumerateArray().Select(item =>
                {
                    var factionId = item.GetProperty("faction_id").GetInt32();
                    var playerName = item.TryGetProperty("player_name", out var player) &&
                        player.ValueKind == JsonValueKind.String ? player.GetString() : null;
                    var factionName = item.TryGetProperty("faction_name", out var faction) &&
                        faction.ValueKind == JsonValueKind.String ? faction.GetString() : null;
                    return new ChatParticipant(
                        $"faction:{factionId}", canonicalHandles.GetValueOrDefault(factionId)
                            ?? playerName ?? factionName ?? $"Faction {factionId}",
                        factionId, factionName, "available",
                        item.TryGetProperty("local", out var local) && local.ValueKind == JsonValueKind.True,
                        item.TryGetProperty("private_eligible", out var eligible) &&
                            eligible.ValueKind == JsonValueKind.True);
                }).ToArray();
            return ApiResponse<IReadOnlyList<ChatParticipant>>.Success(participants);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<IReadOnlyList<ChatParticipant>>.Failure(
                    exception.Code, exception.Message));
        }
    }

    [HttpPost("{matchId}/chat-groups")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<ChatConversation>>> CreateChatGroup(
        string matchId, CreateChatGroupRequest request)
    {
        var user = await userManager.GetUserAsync(User);
        if (user is null) return Unauthorized();
        var seat = await ManagedHumanSeatAsync(matchId, user.Id);
        if (seat?.ControlInstanceId is null)
            return Conflict(ApiResponse<ChatConversation>.Failure(
                "managed_group_chat_unavailable",
                "Group chat requires a running browser-managed player seat."));
        try
        {
            using var document = await control.PostRawAsync(
                $"api/v1/workers/{seat.ControlInstanceId}/group-chat", new
                {
                    action = "create", display_name = request.DisplayName,
                    member_faction_ids = request.MemberFactionIds,
                }, HttpContext.RequestAborted);
            var group = document.RootElement.GetProperty("group");
            await MirrorGroupAsync(matchId, user.Id, group);
            await database.SaveChangesAsync(HttpContext.RequestAborted);
            var conversation = MapConversation(matchId, group, seat.FactionId,
                await CanonicalFactionHandlesAsync(matchId));
            await lobbyHub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
                "ChatGroupsChanged", matchId, HttpContext.RequestAborted);
            return ApiResponse<ChatConversation>.Success(conversation);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<ChatConversation>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpPost("{matchId}/chat-groups/{groupId}/respond")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<ChatConversation>>> RespondChatGroup(
        string matchId, string groupId, RespondChatGroupRequest request)
    {
        if (request.Response is not ("accepted" or "rejected" or "left"))
            return BadRequest(ApiResponse<ChatConversation>.Failure(
                "invalid_group_response", "Choose accepted, rejected, or left."));
        var user = await userManager.GetUserAsync(User);
        if (user is null) return Unauthorized();
        var seat = await ManagedHumanSeatAsync(matchId, user.Id);
        if (seat?.ControlInstanceId is null)
            return Conflict(ApiResponse<ChatConversation>.Failure(
                "managed_group_chat_unavailable",
                "Group chat requires a running browser-managed player seat."));
        try
        {
            using var document = await control.PostRawAsync(
                $"api/v1/workers/{seat.ControlInstanceId}/group-chat", new
                {
                    action = request.Response == "left" ? "leave" : "respond",
                    group_id = groupId, response = request.Response,
                }, HttpContext.RequestAborted);
            var group = document.RootElement.GetProperty("group");
            await MirrorGroupAsync(matchId, user.Id, group);
            await database.SaveChangesAsync(HttpContext.RequestAborted);
            var conversation = MapConversation(matchId, group, seat.FactionId,
                await CanonicalFactionHandlesAsync(matchId));
            await lobbyHub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
                "ChatGroupsChanged", matchId, HttpContext.RequestAborted);
            return ApiResponse<ChatConversation>.Success(conversation);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<ChatConversation>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpGet("{matchId}/human-ui/{seatIndex:int}")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<HumanUiState>>> HumanUi(
        string matchId, int seatIndex)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (userId is null) return Unauthorized();
        var seat = await database.PortalLobbySeats.AsNoTracking().SingleOrDefaultAsync(
            item => item.MatchId == matchId && item.SeatIndex == seatIndex,
            HttpContext.RequestAborted);
        if (seat is null) return NotFound();
        // Administrative observation remains read-only. The native MENU rail
        // is available only to the human who actually owns this seat.
        if (seat.UserId != userId || seat.ControllerKind != "human" ||
            seat.JoinMode != "browser" || seat.ControlInstanceId is null) return Forbid();
        try
        {
            using var document = await control.PostRawAsync(
                $"api/v1/workers/{seat.ControlInstanceId}/human-ui", new { },
                HttpContext.RequestAborted);
            var root = document.RootElement;
            var rootMenu = root.GetProperty("root_menu_open").GetBoolean();
            var modal = root.GetProperty("modal_open").GetBoolean();
            var menuDepth = root.GetProperty("visible_submenu_count").GetInt32();
            var profileId = root.GetProperty("resolution_profile_id").GetString()
                ?? ResolutionProfiles.DesktopDefault;
            var result = new HumanUiState(
                matchId, seat.ControlInstanceId,
                rootMenu ? "root_menu" : root.GetProperty("lifecycle").GetString() ?? "unknown",
                rootMenu, menuDepth, modal, null, null,
                HashCode.Combine(rootMenu, modal, menuDepth,
                    root.GetProperty("selected_hitbox_tag").GetInt32()),
                profileId, root.GetProperty("native_width").GetInt32(),
                root.GetProperty("native_height").GetInt32(), true,
                root.TryGetProperty("stream_bitrate_kbps", out var bitrate) &&
                    bitrate.TryGetInt32(out var bitrateValue) ? bitrateValue : 3500,
                root.TryGetProperty("stream_encoder", out var encoder) &&
                    encoder.ValueKind == JsonValueKind.String
                        ? encoder.GetString() ?? "h264enc" : "h264enc",
                root.TryGetProperty("native_quit_intercepted", out var intercepted) &&
                    intercepted.ValueKind == JsonValueKind.True);
            return ApiResponse<HumanUiState>.Success(result);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<HumanUiState>.Failure(exception.Code, exception.Message));
        }
    }

    private async Task<(int Status, string Code, string Message)?> MaterializeAsync(
        PortalMatchProfile profile,
        CancellationToken cancellationToken)
    {
        var seats = await database.PortalLobbySeats.Where(seat => seat.MatchId == profile.MatchId)
            .OrderBy(seat => seat.SeatIndex).ToArrayAsync(cancellationToken);
        ResolveAgentSeats(profile.MatchId, seats);
        await database.SaveChangesAsync(cancellationToken);
        var assigned = seats.Where(seat => seat.ControllerKind != "open").ToArray();
        if (assigned.Length is < 1 or > 7)
        {
            return (400, "invalid_participant_count", "Assign between one and seven players before starting.");
        }
        var agents = assigned.Where(seat => seat.ControllerKind == "agent")
            .ToArray();
        var humans = assigned.Where(seat => seat.ControllerKind == "human").ToArray();
        var managedHumans = humans.Where(seat => seat.JoinMode == "browser").ToArray();
        var externalHumans = humans.Where(seat => seat.JoinMode == "native").ToArray();
        var occupiedNativeNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var index in Enumerable.Range(0, 7))
        {
            occupiedNativeNames.Add(index == 0 ? "Semantic Host" : $"Semantic Agent {index + 1}");
            occupiedNativeNames.Add($"Native bot {index + 1}");
        }
        var humanAliases = humans.ToDictionary(
            seat => seat.SeatIndex,
            seat => NativePlayerIdentity.AllocateAlias(
                profile.MatchId, seat.SeatIndex, seat.PlayerHandle!, occupiedNativeNames));
        var networkSeatCount = agents.Length + humans.Length;
        foreach (var candidate in seats) candidate.IsManagedHost = false;
        // The control plane gives network seats a compact native order: host,
        // remaining agents, managed humans, then native clients. Portal seat
        // numbers are presentation slots and can be rearranged while staging,
        // so never infer native ownership from the first visible slot.
        var hostSeat = agents.FirstOrDefault()
            ?? managedHumans.FirstOrDefault()
            ?? externalHumans.FirstOrDefault();
        if (hostSeat is not null) hostSeat.IsManagedHost = true;
        if (networkSeatCount == 1)
        {
            var only = assigned.Single(item => item.ControllerKind is "agent" or "human");
            if (only.ControllerKind == "human" && only.JoinMode != "browser")
                return (400, "solo_requires_managed_client",
                    "A portal-managed solo game requires a browser human or AI seat.");
            try
            {
                var settings = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(profile.SettingsJson)!;
                using var created = await control.PostRawAsync("api/v1/matches/solo", new
                {
                    match_id = profile.MatchId,
                    display_name = profile.DisplayName,
                    controller_kind = only.ControllerKind,
                    agent_id = only.AgentId,
                    human_player_name = only.PlayerHandle,
                    faction_id = 1,
                    game_source_id = profile.GameSourceId,
                    runtime_id = profile.RuntimeId,
                    view_enabled = true,
                    graphiti_enabled = profile.GraphitiEnabled,
                    autostart = new
                    {
                        enabled = true,
                        difficulty = settings["Difficulty"].GetString() is { } difficulty
                            ? DifficultyId(difficulty) : 2,
                        world_size = settings["WorldSize"].GetString() is { } world
                            ? WorldSizeId(world) : 1,
                        faction_id = 1,
                        blind_research = true,
                        initial_research_priority = 0,
                        narrative_ui = false,
                        tutorial_ui = false,
                        scenario_id = profile.ScenarioId,
                        startup_save = profile.ResumeSlot,
                        game_settings = StandaloneSettings(profile.NativeSettingsJson),
                    },
                }, cancellationToken);
                only.ControlInstanceId = created.RootElement.GetProperty("worker")
                    .GetProperty("instance_id").GetString();
                only.Status = "provisioned";
                foreach (var openSeat in seats.Where(seat => seat.ControllerKind == "open"))
                {
                    openSeat.ControllerKind = "native";
                    openSeat.PlayerHandle = $"Native bot {openSeat.SeatIndex + 1}";
                    openSeat.Status = "assigned";
                }
                profile.Mode = "singleplayer";
                profile.Status = "provisioning";
                profile.UpdatedAt = DateTimeOffset.UtcNow;
                await database.SaveChangesAsync(cancellationToken);
                return null;
            }
            catch (ControlPlaneException exception)
            {
                return (exception.StatusCode ?? 502, exception.Code, exception.Message);
            }
        }
        if (networkSeatCount < 2)
        {
            return (400, "lan_requires_two_network_players",
                "A LAN match needs at least two human or AI seats; unused factions are controlled by the game.");
        }
        foreach (var openSeat in seats.Where(seat => seat.ControllerKind == "open"))
        {
            openSeat.ControllerKind = "native";
            openSeat.PlayerHandle = $"Native bot {openSeat.SeatIndex + 1}";
            openSeat.Status = "assigned";
            openSeat.UpdatedAt = DateTimeOffset.UtcNow;
        }
        var hostKind = agents.Length > 0 ? "agent" : "human";
        var humanHost = hostKind == "human" ? hostSeat : null;
        var orderedControlSeats = hostKind == "agent"
            ? agents.Cast<PortalLobbySeat>().Concat(managedHumans).Concat(externalHumans).ToArray()
            : new[] { humanHost! }
                .Concat(managedHumans.Where(seat => seat != humanHost))
                .Concat(externalHumans.Where(seat => seat != humanHost))
                .ToArray();
        try
        {
            // A portal AI profile is reusable configuration, not a player
            // identity. Materialize a distinct control-plane agent for every
            // seat so repeated uses get isolated perspectives, MCP scopes,
            // Hermes workspaces, telemetry, and memory.
            var runtimeAgentIds = new Dictionary<PortalLobbySeat, string>();
            foreach (var seat in agents)
            {
                var runtimeAgentId = MatchSeatAgentId(profile.MatchId, seat.SeatIndex);
                using var ignored = await control.PostRawAsync("api/v1/agents", new
                {
                    agent_id = runtimeAgentId,
                    display_name = seat.PlayerHandle ?? $"AI seat {seat.SeatIndex + 1}",
                }, cancellationToken);
                runtimeAgentIds[seat] = runtimeAgentId;
            }
            var created = await control.CreateLanMatchAsync(new
            {
                match_id = profile.MatchId,
                display_name = profile.DisplayName,
                agent_ids = agents.Select(seat => runtimeAgentIds[seat]).ToArray(),
                agent_seats = agents
                    .Select(seat => new
                    {
                        agent_id = runtimeAgentIds[seat],
                        player_name = seat.PlayerHandle,
                        faction_key = seat.ResolvedFactionKey,
                        faction_choice_id = FactionCatalog.Find(seat.ResolvedFactionKey)?.NativeChoiceId,
                        faction_name = FactionCatalog.Find(seat.ResolvedFactionKey)?.FactionName,
                        leader_name = seat.LeaderName,
                        personality_id = seat.PersonalityCardId,
                        personality_name = seat.PersonalityName,
                        personality_prompt = seat.PersonalityPrompt,
                        personality_prompt_sha256 = seat.PersonalityPromptSha256,
                    }).ToArray(),
                human_player_names = externalHumans
                    .Where(seat => seat != humanHost)
                    .Select(seat => humanAliases[seat.SeatIndex]).ToArray(),
                managed_human_player_names = managedHumans
                    .Where(seat => seat != humanHost)
                    .Select(seat => humanAliases[seat.SeatIndex]).ToArray(),
                host_controller_kind = hostKind,
                human_host_name = hostKind == "human"
                    ? humanAliases[humanHost!.SeatIndex] : null,
                human_host_managed = hostKind == "human" && humanHost!.JoinMode == "browser",
                game_source_id = profile.GameSourceId,
                runtime_id = profile.RuntimeId,
                profile = profile.LanProfile,
                session_name = profile.DisplayName[..Math.Min(profile.DisplayName.Length, 31)],
                view_enabled = true,
                graphiti_enabled = profile.GraphitiEnabled,
                start_now = false,
            }, cancellationToken);
            foreach (var controlSeat in created.Seats)
            {
                var portalSeat = controlSeat.SeatIndex >= 0 &&
                    controlSeat.SeatIndex < orderedControlSeats.Length
                        ? orderedControlSeats[controlSeat.SeatIndex]
                        : null;
                if (portalSeat is not null)
                {
                    portalSeat.ControlInstanceId = controlSeat.InstanceId;
                    portalSeat.Status = controlSeat.Managed ? "provisioned" : portalSeat.Status;
                    portalSeat.UpdatedAt = DateTimeOffset.UtcNow;
                }
            }
            profile.Status = "provisioning";
            profile.UpdatedAt = DateTimeOffset.UtcNow;
            await database.SaveChangesAsync(cancellationToken);
            return null;
        }
        catch (ControlPlaneException exception)
        {
            return (exception.StatusCode is >= 400 and < 600 ? exception.StatusCode.Value : 502,
                exception.Code, exception.Message);
        }
    }

    private static string MatchSeatAgentId(string matchId, int seatIndex)
    {
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes($"{matchId}:{seatIndex}"));
        return $"agent-seat-{Convert.ToHexString(digest)[..24].ToLowerInvariant()}";
    }

    private async Task<LobbyDetails> MapDetailsAsync(PortalMatchProfile profile)
    {
        var seatEntities = await database.PortalLobbySeats.AsNoTracking()
            .Where(seat => seat.MatchId == profile.MatchId)
            .OrderBy(seat => seat.SeatIndex)
            .ToArrayAsync(HttpContext.RequestAborted);
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        var administrator = User.IsInRole("Administrator");
        var seats = seatEntities.Select(seat =>
        {
            var live = profile.Status == "running";
            var canControl = profile.Status is not ("parked" or "completed") &&
                userId is not null && seat.UserId == userId
                && seat.ControllerKind == "human" && seat.JoinMode == "browser";
            var canSpectate = live && seat.ControlInstanceId is not null
                && (canControl || administrator || profile.AllowAnonymousSpectators);
            var canJoin = profile.Status == "waiting" && userId is not null &&
                (seat.ControllerKind == "open" || seat.ControllerKind == "human" &&
                    seat.UserId == userId);
            return new LobbySeatSummary(
                seat.SeatIndex, seat.ControllerKind, seat.AgentId, seat.PlayerHandle,
                seat.FactionId, seat.FactionName, seat.Status,
                seat.ControlInstanceId is not null, seat.ControlInstanceId, seat.JoinMode,
                canControl, canSpectate, canJoin, seat.ConnectionState,
                seat.DelegationStatus, seat.TemporaryControllerKind,
                seat.LastBrowserSeenAt, seat.IsManagedHost,
                seat.RequestedFactionId, seat.ResolvedFactionKey,
                seat.RequestedPersonalityId, seat.PersonalityName);
        }).ToArray();
        var nativeJoin = await ReadNativeJoinAsync(profile.MatchId);
        return new LobbyDetails(
            profile.MatchId, profile.DisplayName, profile.Mode, profile.Status,
            profile.LanProfile, profile.CurrentTurn, profile.CurrentYear, profile.IsListed,
            profile.AllowAnonymousSpectators, profile.ManagedClientsOnly,
            profile.RankingMode, profile.GraphitiEnabled, profile.PersonalityCardId,
            CanManage(profile), seats, ReadSettings(profile.SettingsJson), nativeJoin, profile.LastError,
            profile.CreatedAt, profile.UpdatedAt,
            Presence(profile, seatEntities));
    }

    private void ResolveAgentSeats(string matchId, IReadOnlyList<PortalLobbySeat> seats)
    {
        var agents = seats.Where(item => item.ControllerKind == "agent")
            .OrderBy(item => item.SeatIndex).ToArray();
        var used = new HashSet<string>(
            agents.Where(item => item.RequestedFactionId != FactionCatalog.Random)
                .Select(item => item.RequestedFactionId), StringComparer.OrdinalIgnoreCase);
        foreach (var seat in agents)
        {
            var faction = FactionCatalog.Find(seat.ResolvedFactionKey);
            if (faction is null)
            {
                if (seat.RequestedFactionId == FactionCatalog.Random)
                {
                    var available = FactionCatalog.All.Where(item => !used.Contains(item.Id)).ToArray();
                    if (available.Length == 0) throw new InvalidOperationException("No unique faction remains for this AI seat.");
                    var digest = System.Security.Cryptography.SHA256.HashData(
                        System.Text.Encoding.UTF8.GetBytes($"{matchId}:{seat.SeatIndex}:faction-v1"));
                    faction = available[digest[0] % available.Length];
                }
                else
                {
                    faction = FactionCatalog.Find(seat.RequestedFactionId)
                        ?? throw new InvalidOperationException("The requested AI faction no longer exists.");
                }
                seat.ResolvedFactionKey = faction.Id;
            }
            used.Add(faction.Id);
            seat.LeaderName = faction.LeaderName;
            seat.PlayerHandle = faction.LeaderName;
            var card = personalityCards.Resolve(
                faction.Id, seat.RequestedPersonalityId, matchId, seat.SeatIndex);
            seat.PersonalityCardId = card?.Id ?? "none";
            seat.PersonalityName = card?.DisplayName;
            seat.PersonalityPrompt = card?.Prompt;
            seat.PersonalityPromptSha256 = card?.Sha256;
            seat.UpdatedAt = DateTimeOffset.UtcNow;
        }
        var leaderNames = agents.Select(item => item.PlayerHandle!)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var collision = seats.FirstOrDefault(item => item.ControllerKind == "human" &&
            item.PlayerHandle is not null && leaderNames.Contains(item.PlayerHandle));
        if (collision is not null)
            throw new InvalidOperationException(
                $"Human player '{collision.PlayerHandle}' conflicts with a resolved faction leader identity.");
    }

    private MatchPresenceState Presence(
        PortalMatchProfile profile, IReadOnlyList<PortalLobbySeat> seats)
    {
        if (profile.Status == "completed")
            return new("completed", "The campaign is complete; its history and analytics remain available.", false);
        if (profile.Status == "parking")
            return new("parking", "The campaign is taking a verified checkpoint before its workers stop.", true);
        if (profile.Status == "parked")
            return new("parked", "The campaign is safely parked and ready to resume.", true);
        var humans = seats.Where(item => item.ControllerKind == "human").ToArray();
        if (humans.Length == 0)
            return new("unattended_simulation",
                "No human seat is assigned; AI-only simulation continues unattended.", false);
        if (humans.Any(item => item.JoinMode != "browser" || item.ControlInstanceId is null))
            return new("native_presence_unverified",
                "A direct native player is assigned, so the portal will not infer that every human left.", false);
        var snapshots = humans.Select(item => presence.Get(item.ControlInstanceId!)).ToArray();
        if (snapshots.Any(item => item.ActiveConnections > 0))
            return new("connected", "At least one browser player is connected.", true);
        var last = snapshots.Where(item => item.LastSeen is not null)
            .Select(item => item.LastSeen!.Value).DefaultIfEmpty(profile.CreatedAt).Max();
        var eligibleAt = last + TimeSpan.FromMinutes(10);
        var seconds = Math.Max(0, (int)Math.Ceiling((eligibleAt - DateTimeOffset.UtcNow).TotalSeconds));
        if (seconds == 0)
            return new("checkpoint_pending",
                "Every browser player is away; safe parking will complete at the next verified checkpoint.",
                true, 0, eligibleAt);
        var neverConnected = snapshots.All(item => !item.EverConnected);
        return new(neverConnected ? "awaiting_first_connection" : "idle_grace_period",
            neverConnected
                ? "No browser player has connected yet; the abandoned-lobby timer is running."
                : "Every browser player is away; reconnect before the countdown ends to keep the match live.",
            true, seconds, eligibleAt);
    }

    private async Task<NativeJoinDetails?> ReadNativeJoinAsync(string matchId)
    {
        var value = await database.PortalSettings.AsNoTracking()
            .Where(item => item.Key == $"native-join:{matchId}")
            .Select(item => item.Value)
            .SingleOrDefaultAsync(HttpContext.RequestAborted);
        if (string.IsNullOrWhiteSpace(value)) return null;
        try { return JsonSerializer.Deserialize<NativeJoinDetails>(value); }
        catch (JsonException) { return null; }
    }

    private async Task<PortalLobbySeat?> ManagedHumanSeatAsync(string matchId, string userId) =>
        await database.PortalLobbySeats.AsNoTracking().SingleOrDefaultAsync(item =>
            item.MatchId == matchId && item.UserId == userId &&
            item.ControllerKind == "human" && item.JoinMode == "browser" &&
            item.ControlInstanceId != null, HttpContext.RequestAborted);

    private async Task MirrorGroupAsync(string matchId, string userId, JsonElement group)
    {
        var groupId = group.GetProperty("group_id").GetString()!;
        var row = await database.PortalChatGroups.SingleOrDefaultAsync(
            item => item.GroupId == groupId, HttpContext.RequestAborted);
        if (row is null)
        {
            row = new PortalChatGroup
            {
                GroupId = groupId, MatchId = matchId, CreatedByUserId = userId,
                CreatedAt = DateTimeOffset.UtcNow,
            };
            database.PortalChatGroups.Add(row);
        }
        row.DisplayName = group.GetProperty("display_name").GetString() ?? "Group";
        row.Status = group.GetProperty("status").GetString() ?? "inviting";
        row.Version = group.TryGetProperty("version", out var version) ? version.GetInt32() : 1;
        row.UpdatedAt = DateTimeOffset.UtcNow;
        foreach (var member in group.GetProperty("members").EnumerateArray())
        {
            var factionId = member.GetProperty("faction_id").GetInt32();
            var actorKey = $"faction:{factionId}";
            var existing = await database.PortalChatGroupMembers.SingleOrDefaultAsync(
                item => item.GroupId == groupId && item.ActorKey == actorKey,
                HttpContext.RequestAborted);
            if (existing is null)
            {
                existing = new PortalChatGroupMember
                {
                    GroupId = groupId, ActorKey = actorKey, FactionId = factionId,
                };
                database.PortalChatGroupMembers.Add(existing);
            }
            existing.DisplayName = member.GetProperty("display_name").GetString()
                ?? $"Faction {factionId}";
            existing.FactionName = member.TryGetProperty("faction_name", out var faction) &&
                faction.ValueKind == JsonValueKind.String ? faction.GetString() : null;
            existing.Status = member.GetProperty("status").GetString() ?? "invited";
            var portalSeat = await database.PortalLobbySeats.AsNoTracking().SingleOrDefaultAsync(
                item => item.MatchId == matchId && item.FactionId == factionId,
                HttpContext.RequestAborted);
            if (!string.IsNullOrWhiteSpace(portalSeat?.PlayerHandle))
                existing.DisplayName = portalSeat.PlayerHandle;
            existing.UserId = portalSeat?.UserId;
        }
    }

    private static ChatConversation MapConversation(
        string matchId, JsonElement group, int? localFactionId,
        IReadOnlyDictionary<int, string> canonicalHandles)
    {
        var members = group.GetProperty("members").EnumerateArray().Select(member =>
        {
            var factionId = member.GetProperty("faction_id").GetInt32();
            return new ChatParticipant(
                $"faction:{factionId}",
                canonicalHandles.GetValueOrDefault(factionId)
                    ?? member.GetProperty("display_name").GetString() ?? $"Faction {factionId}",
                factionId,
                member.TryGetProperty("faction_name", out var faction) &&
                    faction.ValueKind == JsonValueKind.String ? faction.GetString() : null,
                member.GetProperty("status").GetString() ?? "invited",
                localFactionId == factionId, true);
        }).ToArray();
        return new ChatConversation(
            group.GetProperty("group_id").GetString()!, matchId, "group",
            group.GetProperty("display_name").GetString() ?? "Group", members, 0,
            DateTimeOffset.FromUnixTimeMilliseconds((long)(
                group.GetProperty("updated_unix").GetDouble() * 1000)));
    }

    private async Task<IReadOnlyDictionary<int, string>> CanonicalFactionHandlesAsync(
        string matchId)
    {
        var seats = await database.PortalLobbySeats.AsNoTracking()
            .Where(item => item.MatchId == matchId && item.FactionId != null &&
                item.PlayerHandle != null)
            .Select(item => new { FactionId = item.FactionId!.Value, item.PlayerHandle })
            .ToArrayAsync(HttpContext.RequestAborted);
        return seats.GroupBy(item => item.FactionId).ToDictionary(
            group => group.Key, group => group.First().PlayerHandle!);
    }

    private static LobbyMessage ToMessage(
        PortalLobbyMessage message,
        IEnumerable<PortalChatDelivery>? deliveries = null)
    {
        var marker = message.NativeMessageUid ?? string.Empty;
        var sender = message.SenderFactionId ?? MarkerFaction(marker, ":from:");
        var recipient = message.RecipientFactionId != 0
            ? message.RecipientFactionId : MarkerFaction(marker, ":to:") ?? 0;
        return new LobbyMessage(
            message.Id, message.MatchId, message.SenderHandle, message.Content,
            message.DeliveredToGame, sender, recipient, message.CreatedAt,
            message.Channel, message.ConversationId, message.ConversationName,
            message.LogicalMessageId ?? message.Id,
            deliveries?.Select(item => new ChatDelivery(
                item.RecipientFactionId, item.RecipientHandle, item.Status,
                item.NativeMessageUid)).ToArray());
    }

    private static int? MarkerFaction(string value, string marker)
    {
        var index = value.LastIndexOf(marker, StringComparison.Ordinal);
        return index >= 0 && int.TryParse(value[(index + marker.Length)..], out var faction)
            ? faction : null;
    }

    private bool CanManage(PortalMatchProfile profile) =>
        User.IsInRole("Administrator") || User.FindFirstValue(ClaimTypes.NameIdentifier) == profile.OwnerUserId;

    private async Task<bool> IsMemberAsync(string matchId)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        return userId is not null && await database.PortalMatchMembers.AsNoTracking().AnyAsync(
            item => item.MatchId == matchId && item.UserId == userId,
            HttpContext.RequestAborted);
    }

    private void AddAgentSeats(
        IReadOnlyList<AgentSeatRequest> agents, ref int seatIndex, string matchId, DateTimeOffset now)
    {
        foreach (var agentSeat in agents)
        {
            var agent = agentSeat.AgentId;
            var requestedFaction = FactionCatalog.Find(agentSeat.FactionId);
            var profile = database.PortalAiProfiles.Local.FirstOrDefault(item => item.AgentId == agent)
                ?? database.PortalAiProfiles.AsNoTracking().FirstOrDefault(item => item.AgentId == agent);
            database.PortalLobbySeats.Add(new PortalLobbySeat
            {
                MatchId = matchId, SeatIndex = seatIndex++, ControllerKind = "agent",
                AgentId = agent,
                AiProfileId = profile?.ProfileId,
                PlayerHandle = requestedFaction?.LeaderName ?? "Random faction AI",
                RequestedFactionId = agentSeat.FactionId,
                LeaderName = requestedFaction?.LeaderName,
                RequestedPersonalityId = agentSeat.PersonalityId,
                Status = "assigned", UpdatedAt = now,
            });
        }
    }

    private void AddHumanSeat(
        string? userId, string handle, string joinMode,
        ref int seatIndex, string matchId, DateTimeOffset now)
    {
        database.PortalLobbySeats.Add(new PortalLobbySeat
        {
            MatchId = matchId, SeatIndex = seatIndex++, ControllerKind = "human",
            UserId = userId, PlayerHandle = handle, Status = userId is null ? "invited" : "ready",
            JoinMode = joinMode,
            UpdatedAt = now,
        });
        if (userId is not null)
        {
            var member = database.PortalMatchMembers.Local.FirstOrDefault(item =>
                item.MatchId == matchId && item.UserId == userId);
            if (member is null)
            {
                member = new PortalMatchMember
                {
                    MatchId = matchId, UserId = userId, Role = "player",
                    JoinMode = joinMode, JoinedAt = now,
                };
                database.PortalMatchMembers.Add(member);
            }
            member.SeatIndex = seatIndex - 1;
        }
    }

    private async Task<ApplicationUser> EnsureLobbyUserAsync(string handle)
    {
        var normalizedDisplay = handle.Trim().ToUpperInvariant();
        var existing = await database.Users.SingleOrDefaultAsync(item =>
            item.NormalizedDisplayName == normalizedDisplay,
            HttpContext.RequestAborted);
        if (existing is not null) return existing;
        var provisionalUsername = $"invite-{Guid.NewGuid():N}"[..31];
        var provisional = new ApplicationUser
        {
            UserName = provisionalUsername,
            DisplayName = handle,
            NormalizedDisplayName = normalizedDisplay,
            GameHandle = handle,
            NormalizedGameHandle = userManager.NormalizeName(handle)!,
            EmailConfirmed = true,
            IsProvisional = true,
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        };
        var created = await userManager.CreateAsync(provisional);
        if (!created.Succeeded)
            throw new InvalidOperationException(
                "Could not reserve the invited LAN public display name: " +
                string.Join(" ", created.Errors.Select(item => item.Description)));
        var role = await userManager.AddToRoleAsync(provisional, PortalRoles.Member);
        if (!role.Succeeded)
            throw new InvalidOperationException(
                "Could not assign the invited LAN account role: " +
                string.Join(" ", role.Errors.Select(item => item.Description)));
        return provisional;
    }

    private static (string Code, string Message)? Validate(CreateLobbyRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.DisplayName) || request.DisplayName.Trim().Length > 160)
            return ("invalid_lobby_name", "Lobby names must contain 1–160 characters.");
        if (request.RankingMode != "unranked")
            return ("ranked_not_available", "All matches are currently unranked.");
        if (request.Mode is not ("standard" or "scenario"))
            return ("invalid_start_mode", "Choose a standard game or an installed scenario.");
        if (request.WorldSize is not ("tiny" or "small" or "standard" or "large" or "huge"))
            return ("invalid_world_size", "Choose a supported native planet size.");
        if (request.Difficulty is not ("citizen" or "specialist" or "talent" or
                "librarian" or "thinker" or "transcend") ||
            request.NativeBotDifficulty is not ("citizen" or "specialist" or "talent" or
                "librarian" or "thinker" or "transcend"))
            return ("invalid_difficulty", "Choose a supported native difficulty.");
        if (request.HostController is not ("human" or "agent"))
            return ("invalid_host_controller", "Choose a human or agent host.");
        if (request.HumanJoinMode is not ("browser" or "native"))
            return ("invalid_human_join_mode", "Choose browser-managed or native human clients.");
        if (request.ManagedClientsOnly && request.HumanJoinMode != "browser")
            return ("managed_clients_required", "Managed-only lobbies require browser human seats.");
        var agents = EffectiveAgentSeats(request);
        if (request.HostController == "agent" && agents.Count == 0)
            return ("agent_host_required",
                "Choose at least one AI player profile, or create one in Administration → Models & AI profiles.");
        if (request.NativeBotCount is < 0 or > 6)
            return ("invalid_native_bot_count", "Choose between zero and six native bots.");
        if (agents.Any(item => item.FactionId != FactionCatalog.Random && FactionCatalog.Find(item.FactionId) is null))
            return ("invalid_agent_faction", "Choose Random or one official Alpha Centauri faction for every AI seat.");
        if (agents.Any(item => BuiltInPersonalityCatalog.FindMode(item.PersonalityId) is null))
            return ("invalid_agent_personality", "Choose None, Standard, Random, Friendly, Aggressive, or Extreme for every AI seat.");
        if (agents.Any(item => item.FactionId == FactionCatalog.Random && item.PersonalityId is not ("none" or "standard" or "random")))
            return ("personality_requires_faction", "Friendly, Aggressive, and Extreme require a specific faction. Random factions support None, Standard, or Random.");
        var fixedFactions = agents.Where(item => item.FactionId != FactionCatalog.Random)
            .Select(item => item.FactionId).ToArray();
        if (fixedFactions.Distinct(StringComparer.OrdinalIgnoreCase).Count() != fixedFactions.Length)
            return ("duplicate_agent_faction", "Each specific faction can be assigned to only one AI seat.");
        if (request.InvitedHumanHandles.Any(handle =>
                string.IsNullOrWhiteSpace(handle) || handle.Trim().Length > 31 ||
                handle.Any(character => character < 32 || character > 126)))
            return ("invalid_player_handle", "Public display names must contain 1–31 printable characters.");
        var normalizedInvites = request.InvitedHumanHandles
            .Select(handle => handle.Trim()).ToArray();
        if (normalizedInvites.Distinct(StringComparer.OrdinalIgnoreCase).Count() !=
            normalizedInvites.Length)
            return ("duplicate_player_handle",
                "Each invited public display name can occupy only one seat, regardless of letter case.");
        if (normalizedInvites.Any(FactionCatalog.IsReservedLeaderName))
            return ("reserved_faction_leader_name",
                "Faction-leader names are reserved for AI players and cannot be assigned to humans.");
        var ownerSeats = request.HostController == "human" || request.OwnerPlays ? 1 : 0;
        if (ownerSeats + agents.Count + request.InvitedHumanHandles.Count + request.NativeBotCount > 7)
            return ("too_many_seats", "A lobby supports at most seven players.");
        if (request.TimeControl is < 0 or > 4 || request.OceanCoverage is < 0 or > 2 ||
            request.ErosiveForces is < 0 or > 2 || request.NativeLife is < 0 or > 2 ||
            request.CloudCover is < 0 or > 2)
            return ("invalid_native_setting", "One or more native setup choices are outside the game range.");
        var knownRules = new HashSet<string>(StringComparer.Ordinal)
        {
            "victory_transcendence", "victory_conquest", "victory_diplomatic",
            "victory_economic", "victory_cooperative", "look_first", "tech_stagnation",
            "spoils_of_war", "blind_research", "intense_rivalry", "unity_survey",
            "unity_scattering", "random_events", "time_warp", "ironman",
        };
        if (request.RuleOptions?.Keys.Any(key => !knownRules.Contains(key)) == true)
            return ("unknown_native_rule", "The request contains an unsupported game rule.");
        if (request.ScenarioId is not null && request.ResumeSlot is not null)
            return ("conflicting_startup_mode", "Choose a scenario or a saved game, not both.");
        if (request.Mode == "scenario" && string.IsNullOrWhiteSpace(request.ScenarioId))
            return ("scenario_required", "Choose an installed scenario before launching scenario mode.");
        if (request.ResumeSlot is not null && !System.Text.RegularExpressions.Regex.IsMatch(
                request.ResumeSlot, "^[A-Za-z0-9_-]{1,32}$", System.Text.RegularExpressions.RegexOptions.CultureInvariant))
            return ("invalid_resume_slot", "Save slots use 1–32 letters, numbers, underscores, or hyphens.");
        return null;
    }

    private static IReadOnlyList<AgentSeatRequest> EffectiveAgentSeats(CreateLobbyRequest request) =>
        request.AgentSeats is { Count: > 0 }
            ? request.AgentSeats
            : request.AgentIds.Select(item => new AgentSeatRequest(item)).ToArray();

    private static bool Rule(CreateLobbyRequest request, string name, bool fallback) =>
        request.RuleOptions?.TryGetValue(name, out var enabled) == true ? enabled : fallback;

    private static IReadOnlyDictionary<string, object?> ReadSettings(string json)
    {
        using var document = JsonDocument.Parse(json);
        return document.RootElement.EnumerateObject().ToDictionary(
            property => property.Name,
            property => property.Value.ValueKind switch
            {
                JsonValueKind.String => (object?)property.Value.GetString(),
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.Number when property.Value.TryGetInt64(out var number) => number,
                _ => property.Value.GetRawText(),
            });
    }

    private static IReadOnlyDictionary<string, object?> StandaloneSettings(string nativeJson)
    {
        using var document = JsonDocument.Parse(nativeJson);
        var root = document.RootElement;
        var result = new Dictionary<string, object?>
        {
            ["map_generation"] = "random",
            ["world_size"] = root.GetProperty("world_size").GetInt32(),
        };
        foreach (var property in root.EnumerateObject())
        {
            if (property.Name is "difficulty" or "time_control" or "ocean_coverage" or
                "erosive_forces" or "native_life" or "cloud_cover") continue;
            if (property.Value.ValueKind is JsonValueKind.True or JsonValueKind.False)
                result[property.Name] = property.Value.GetBoolean();
        }
        return result;
    }

    private static string ResolveProfile(string worldSize) => worldSize switch
    {
        "tiny" => "tiny_citizen",
        "small" => "small_easy",
        "standard" => "standard_librarian",
        "large" => "large_thinker",
        "huge" => "huge_transcend",
        _ => "small_easy",
    };

    private static int WorldSizeId(string worldSize) => worldSize switch
    {
        "tiny" => 0, "small" => 1, "standard" => 2, "large" => 3, "huge" => 4, _ => 1,
    };

    private static int DifficultyId(string difficulty) => difficulty switch
    {
        "citizen" => 0, "specialist" => 1, "talent" => 2,
        "librarian" => 3, "thinker" => 4, "transcend" => 5, _ => 2,
    };
}
