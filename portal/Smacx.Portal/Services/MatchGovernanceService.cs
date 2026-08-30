using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;

namespace Smacx.Portal.Services;

/// <summary>
/// Durable, match-local governance for operations which may interrupt native
/// multiplayer. A passed proposal authorizes an operation; it never bypasses
/// the native checkpoint and quiescence gates.
/// </summary>
public sealed class MatchGovernanceService(
    ApplicationDbContext database,
    StreamPresenceTracker presence)
{
    private static readonly HashSet<string> SupportedKinds = new(StringComparer.Ordinal)
    {
        "native_resolution_change",
        "waive_resolution_cooldown",
        "continue_without_player",
        "reclaim_player_seat",
        "transfer_host",
        "park_match",
        "end_match",
    };

    public async Task<IReadOnlyList<GovernanceProposal>> ListAsync(
        string matchId, string? userId, CancellationToken cancellationToken)
    {
        await ExpireAsync(matchId, cancellationToken);
        var rows = await database.PortalGovernanceProposals.AsNoTracking()
            .Where(item => item.MatchId == matchId)
            .OrderByDescending(item => item.CreatedAt)
            .Take(50)
            .ToArrayAsync(cancellationToken);
        var ids = rows.Select(item => item.ProposalId).ToArray();
        var votes = await database.PortalGovernanceVotes.AsNoTracking()
            .Where(item => ids.Contains(item.ProposalId))
            .ToArrayAsync(cancellationToken);
        return rows.Select(item => Map(item, votes, userId)).ToArray();
    }

    public async Task<GovernanceProposal> CreateAsync(
        PortalMatchProfile match, ApplicationUser requester,
        CreateGovernanceProposalRequest request,
        CancellationToken cancellationToken)
    {
        if (!SupportedKinds.Contains(request.Kind))
            throw new GovernanceException("unsupported_proposal_kind",
                "That match proposal is not supported.");
        if (request.TimeoutSeconds is < 30 or > 900)
            throw new GovernanceException("invalid_proposal_timeout",
                "Proposal timeouts must be between 30 seconds and 15 minutes.");
        if (request.PayloadJson.Length > 16384)
            throw new GovernanceException("proposal_payload_too_large",
                "The proposal payload is too large.");
        JsonDocument payload;
        try { payload = JsonDocument.Parse(request.PayloadJson); }
        catch (JsonException)
        {
            throw new GovernanceException("invalid_proposal_payload",
                "The proposal payload must be a JSON object.");
        }
        using (payload)
        {
            if (payload.RootElement.ValueKind != JsonValueKind.Object)
                throw new GovernanceException("invalid_proposal_payload",
                    "The proposal payload must be a JSON object.");
            ValidatePayload(request.Kind, payload.RootElement);
            await ValidateCurrentStateAsync(
                match, request.Kind, payload.RootElement, cancellationToken);
        }

        var duplicate = await database.PortalGovernanceProposals.AnyAsync(item =>
            item.MatchId == match.MatchId && item.Kind == request.Kind && item.Status == "open",
            cancellationToken);
        if (duplicate)
            throw new GovernanceException("proposal_already_open",
                "A proposal of this type is already open for the match.");

        var eligible = await EligibleVotersAsync(
            match.MatchId, requester.Id, cancellationToken);
        var copy = Describe(request.Kind, request.PayloadJson);
        var now = DateTimeOffset.UtcNow;
        var proposal = new PortalGovernanceProposal
        {
            MatchId = match.MatchId,
            Kind = request.Kind,
            RequestedByUserId = requester.Id,
            RequestedByHandle = requester.GameHandle,
            Title = copy.Title,
            Description = copy.Description,
            PayloadJson = request.PayloadJson,
            EligibleVotersJson = JsonSerializer.Serialize(eligible),
            CreatedAt = now,
            ExpiresAt = now.AddSeconds(request.TimeoutSeconds),
            Status = eligible.Count == 0 ? "approved" : "open",
            ResolvedAt = eligible.Count == 0 ? now : null,
        };
        database.PortalGovernanceProposals.Add(proposal);
        database.PortalMatchEvents.Add(new PortalMatchEvent
        {
            MatchId = match.MatchId,
            EventType = "governance_proposal",
            Summary = eligible.Count == 0
                ? $"{requester.GameHandle}'s {copy.Title} request requires no peer vote."
                : $"{requester.GameHandle} opened a vote: {copy.Title}.",
            DetailsJson = JsonSerializer.Serialize(new
            {
                proposal.ProposalId, proposal.Kind, EligibleVoters = eligible.Count,
            }),
        });
        await database.SaveChangesAsync(cancellationToken);
        return Map(proposal, [], requester.Id);
    }

    public async Task<GovernanceProposal> VoteAsync(
        string matchId, string proposalId, string userId, string vote,
        CancellationToken cancellationToken)
    {
        if (vote is not ("yes" or "no" or "abstain"))
            throw new GovernanceException("invalid_vote", "Choose yes, no, or abstain.");
        var proposal = await database.PortalGovernanceProposals.SingleOrDefaultAsync(
            item => item.MatchId == matchId && item.ProposalId == proposalId,
            cancellationToken) ?? throw new GovernanceException(
                "proposal_not_found", "The proposal was not found.");
        if (proposal.Status != "open" || proposal.ExpiresAt <= DateTimeOffset.UtcNow)
            throw new GovernanceException("proposal_closed", "That vote is already closed.");
        var eligible = JsonSerializer.Deserialize<string[]>(proposal.EligibleVotersJson) ?? [];
        if (!eligible.Contains(userId, StringComparer.Ordinal))
            throw new GovernanceException("vote_not_eligible",
                "This account is not an eligible voter for that proposal.");
        var row = await database.PortalGovernanceVotes.SingleOrDefaultAsync(
            item => item.ProposalId == proposalId && item.UserId == userId,
            cancellationToken);
        if (row is null)
        {
            row = new PortalGovernanceVote { ProposalId = proposalId, UserId = userId };
            database.PortalGovernanceVotes.Add(row);
        }
        row.Vote = vote;
        row.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(cancellationToken);
        await ResolveAsync(proposal, eligible.Length, cancellationToken);
        var votes = await database.PortalGovernanceVotes.AsNoTracking()
            .Where(item => item.ProposalId == proposalId).ToArrayAsync(cancellationToken);
        return Map(proposal, votes, userId);
    }

    private async Task<List<string>> EligibleVotersAsync(
        string matchId, string requesterId, CancellationToken cancellationToken)
    {
        var seats = await database.PortalLobbySeats.AsNoTracking()
            .Where(item => item.MatchId == matchId && item.ControllerKind == "human" &&
                item.UserId != null && item.UserId != requesterId &&
                item.DelegationStatus != "active")
            .ToArrayAsync(cancellationToken);
        return seats.Where(item => item.JoinMode == "browser"
                ? item.ControlInstanceId is not null &&
                    presence.Get(item.ControlInstanceId).ActiveConnections > 0
                : item.Status is "ready" or "connected" or "running")
            .Select(item => item.UserId!).Distinct(StringComparer.Ordinal).ToList();
    }

    private async Task ValidateCurrentStateAsync(
        PortalMatchProfile match, string kind, JsonElement payload,
        CancellationToken cancellationToken)
    {
        if (match.Status != "running")
            throw new GovernanceException("match_not_running",
                "This operation can only be proposed while the match is running.");
        if (kind is "native_resolution_change" or "waive_resolution_cooldown")
        {
            var cutoff = DateTimeOffset.UtcNow - TimeSpan.FromMinutes(5);
            var recentChange = await database.PortalMaintenanceOperations.AsNoTracking()
                .Where(item => item.MatchId == match.MatchId &&
                    item.Kind == "native_resolution_change" && item.Status == "completed" &&
                    item.CompletedAt >= cutoff)
                .OrderByDescending(item => item.CompletedAt)
                .FirstOrDefaultAsync(cancellationToken);
            var waived = recentChange is not null &&
                await database.PortalGovernanceProposals.AsNoTracking().AnyAsync(
                    item => item.MatchId == match.MatchId &&
                        item.Kind == "waive_resolution_cooldown" && item.Status == "executed" &&
                        item.ResolvedAt >= recentChange.CompletedAt,
                    cancellationToken);
            if (kind == "native_resolution_change" && recentChange is not null && !waived)
            {
                throw new GovernanceException("resolution_cooldown_active",
                    "Native resolution changes are limited to one every five minutes in multiplayer. Players can vote to waive this cooldown; CSS fitting remains instant.");
            }
            if (kind == "waive_resolution_cooldown" && (recentChange is null || waived))
                throw new GovernanceException("resolution_cooldown_not_active",
                    "There is no active native-resolution cooldown to waive.");
        }
        if (kind is not ("continue_without_player" or "reclaim_player_seat" or "transfer_host"))
            return;
        var seatIndex = payload.GetProperty("seatIndex").GetInt32();
        var seat = await database.PortalLobbySeats.AsNoTracking().SingleOrDefaultAsync(
            item => item.MatchId == match.MatchId && item.SeatIndex == seatIndex,
            cancellationToken) ?? throw new GovernanceException(
                "target_seat_not_found", "That match seat no longer exists.");
        if (kind == "continue_without_player")
        {
            if (seat.ControllerKind != "human" || seat.JoinMode != "browser" ||
                seat.ControlInstanceId is null)
                throw new GovernanceException("seat_not_delegatable",
                    "Only a browser-managed human seat can use temporary computer control.");
            if (seat.DelegationStatus == "active")
                throw new GovernanceException("seat_already_delegated",
                    "That faction is already temporarily computer-controlled.");
            if (seat.IsManagedHost)
                throw new GovernanceException("transfer_host_before_delegation",
                    "Transfer the managed host to another active seat before delegating the current host.");
            if (seat.ConnectionState != "disconnected" || seat.LastBrowserSeenAt is null ||
                seat.LastBrowserSeenAt > DateTimeOffset.UtcNow - TimeSpan.FromSeconds(30))
                throw new GovernanceException("player_disconnect_grace_active",
                    "Wait 30 seconds after the player's browser disconnects before proposing temporary control.");
        }
        else if (kind == "reclaim_player_seat" && seat.DelegationStatus != "active")
            throw new GovernanceException("seat_not_delegated",
                "That faction is not currently delegated.");
        else if (kind == "transfer_host" &&
            (seat.ControlInstanceId is null || seat.DelegationStatus == "active"))
            throw new GovernanceException("host_target_unavailable",
                "Choose an active managed seat for the new host.");
    }

    private async Task ResolveAsync(
        PortalGovernanceProposal proposal, int eligibleCount,
        CancellationToken cancellationToken)
    {
        var votes = await database.PortalGovernanceVotes
            .Where(item => item.ProposalId == proposal.ProposalId)
            .ToArrayAsync(cancellationToken);
        var yes = votes.Count(item => item.Vote == "yes");
        var no = votes.Count(item => item.Vote == "no");
        var majority = eligibleCount / 2 + 1;
        if (yes >= majority)
        {
            proposal.Status = "approved";
            proposal.ResolvedAt = DateTimeOffset.UtcNow;
        }
        else if (no >= majority || votes.Count(item => item.Vote is "yes" or "no") == eligibleCount)
        {
            proposal.Status = "declined";
            proposal.ResolvedAt = DateTimeOffset.UtcNow;
        }
        await database.SaveChangesAsync(cancellationToken);
    }

    private async Task ExpireAsync(string matchId, CancellationToken cancellationToken)
    {
        var now = DateTimeOffset.UtcNow;
        var expired = await database.PortalGovernanceProposals
            .Where(item => item.MatchId == matchId && item.Status == "open" &&
                item.ExpiresAt <= now).ToArrayAsync(cancellationToken);
        foreach (var item in expired)
        {
            item.Status = "expired";
            item.ResolvedAt = now;
        }
        if (expired.Length > 0) await database.SaveChangesAsync(cancellationToken);
    }

    private static GovernanceProposal Map(
        PortalGovernanceProposal item, IEnumerable<PortalGovernanceVote> votes,
        string? userId)
    {
        var eligible = JsonSerializer.Deserialize<string[]>(item.EligibleVotersJson) ?? [];
        var rows = votes.Where(vote => vote.ProposalId == item.ProposalId).ToArray();
        return new GovernanceProposal(
            item.ProposalId, item.MatchId, item.Kind, item.Status,
            item.RequestedByHandle, item.Title, item.Description, item.PayloadJson,
            eligible.Length, rows.Count(vote => vote.Vote == "yes"),
            rows.Count(vote => vote.Vote == "no"),
            userId is not null && eligible.Contains(userId, StringComparer.Ordinal),
            rows.FirstOrDefault(vote => vote.UserId == userId)?.Vote,
            item.CreatedAt, item.ExpiresAt);
    }

    private static void ValidatePayload(string kind, JsonElement payload)
    {
        if (kind == "native_resolution_change")
        {
            if (!payload.TryGetProperty("profileId", out var value) ||
                ResolutionProfiles.Find(value.GetString()) is null)
                throw new GovernanceException("invalid_resolution_profile",
                    "Choose a validated native resolution profile.");
        }
        if (kind is "continue_without_player" or "reclaim_player_seat" or "transfer_host")
        {
            if (!payload.TryGetProperty("seatIndex", out var seat) ||
                !seat.TryGetInt32(out var seatIndex) || seatIndex is < 0 or > 6)
                throw new GovernanceException("invalid_target_seat",
                    "Choose a valid match seat.");
        }
    }

    private static (string Title, string Description) Describe(string kind, string payloadJson)
    {
        using var payload = JsonDocument.Parse(payloadJson);
        var root = payload.RootElement;
        return kind switch
        {
            "native_resolution_change" => (
                "Change native game resolution",
                $"Rehost at {ResolutionProfiles.Find(root.GetProperty("profileId").GetString())?.Label}. Browser scaling remains available while this waits for a stable checkpoint."),
            "waive_resolution_cooldown" => (
                "Waive the resolution cooldown",
                "Allow another native resolution request before the multiplayer cooldown expires."),
            "continue_without_player" => (
                "Continue with a temporary computer player",
                "Restore the latest stable checkpoint and temporarily delegate the absent player's faction to the game AI."),
            "reclaim_player_seat" => (
                "Return a delegated seat to its player",
                "At the next stable checkpoint, rehost and return the faction to its original human player."),
            "transfer_host" => (
                "Transfer the managed host",
                "Move native host authority at the next stable checkpoint."),
            "park_match" => ("Park the match", "Save a stable checkpoint and stop every managed seat."),
            "end_match" => ("End the match", "End this managed match for every participant."),
            _ => ("Match proposal", "A player requested a managed match operation."),
        };
    }
}

public sealed class GovernanceException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
