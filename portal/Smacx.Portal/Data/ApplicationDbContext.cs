using Microsoft.AspNetCore.Identity.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;

namespace Smacx.Portal.Data;

public class ApplicationDbContext(DbContextOptions<ApplicationDbContext> options) : IdentityDbContext<ApplicationUser>(options)
{
    public DbSet<PortalSetting> PortalSettings => Set<PortalSetting>();

    public DbSet<PasswordResetGrant> PasswordResetGrants => Set<PasswordResetGrant>();

    public DbSet<PortalMatchProfile> PortalMatches => Set<PortalMatchProfile>();

    public DbSet<PortalMatchMember> PortalMatchMembers => Set<PortalMatchMember>();

    public DbSet<PortalLobbyMessage> PortalLobbyMessages => Set<PortalLobbyMessage>();

    public DbSet<PortalLobbySeat> PortalLobbySeats => Set<PortalLobbySeat>();

    public DbSet<PortalAiProfileVersion> PortalAiProfileVersions => Set<PortalAiProfileVersion>();

    public DbSet<PortalMatchEvent> PortalMatchEvents => Set<PortalMatchEvent>();

    public DbSet<PortalTurnMetric> PortalTurnMetrics => Set<PortalTurnMetric>();

    public DbSet<PortalChatGroup> PortalChatGroups => Set<PortalChatGroup>();

    public DbSet<PortalChatGroupMember> PortalChatGroupMembers => Set<PortalChatGroupMember>();

    public DbSet<PortalChatDelivery> PortalChatDeliveries => Set<PortalChatDelivery>();

    public DbSet<PortalGovernanceProposal> PortalGovernanceProposals => Set<PortalGovernanceProposal>();

    public DbSet<PortalGovernanceVote> PortalGovernanceVotes => Set<PortalGovernanceVote>();

    public DbSet<PortalMaintenanceOperation> PortalMaintenanceOperations => Set<PortalMaintenanceOperation>();

    public DbSet<PortalStableCheckpoint> PortalStableCheckpoints => Set<PortalStableCheckpoint>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<ApplicationUser>(entity =>
        {
            entity.Property(user => user.DisplayName).HasMaxLength(31);
            entity.Property(user => user.NormalizedDisplayName).HasMaxLength(31);
            entity.Property(user => user.GameHandle).HasMaxLength(31);
            entity.Property(user => user.NormalizedGameHandle).HasMaxLength(31);
            entity.HasIndex(user => user.NormalizedGameHandle).IsUnique();
            entity.HasIndex(user => user.NormalizedDisplayName).IsUnique()
                .HasFilter("NormalizedDisplayName <> ''");
        });

        builder.Entity<PortalSetting>(entity =>
        {
            entity.HasKey(setting => setting.Key);
            entity.Property(setting => setting.Key).HasMaxLength(96);
            entity.Property(setting => setting.Value).HasMaxLength(4096);
            entity.Property(setting => setting.UpdatedAt).HasConversion<long>();
        });

        builder.Entity<PasswordResetGrant>(entity =>
        {
            entity.HasKey(grant => grant.Id);
            entity.Property(grant => grant.TokenHash).HasMaxLength(64);
            entity.Property(grant => grant.CreatedAt).HasConversion<long>();
            entity.Property(grant => grant.ExpiresAt).HasConversion<long>();
            entity.Property(grant => grant.UsedAt).HasConversion<long?>();
            entity.HasIndex(grant => grant.TokenHash).IsUnique();
            entity.HasIndex(grant => new { grant.UserId, grant.UsedAt, grant.ExpiresAt });
            entity.HasOne<ApplicationUser>()
                .WithMany()
                .HasForeignKey(grant => grant.UserId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalMatchProfile>(entity =>
        {
            entity.HasKey(match => match.MatchId);
            entity.Property(match => match.MatchId).HasMaxLength(96);
            entity.Property(match => match.OwnerUserId).HasMaxLength(450);
            entity.Property(match => match.DisplayName).HasMaxLength(160);
            entity.Property(match => match.Status).HasMaxLength(24);
            entity.Property(match => match.Mode).HasMaxLength(24);
            entity.Property(match => match.GameSourceId).HasMaxLength(96);
            entity.Property(match => match.RuntimeId).HasMaxLength(96);
            entity.Property(match => match.LanProfile).HasMaxLength(40);
            entity.Property(match => match.SettingsJson).HasMaxLength(16384);
            entity.Property(match => match.NativeSettingsJson).HasMaxLength(16384);
            entity.Property(match => match.LastError).HasMaxLength(4000);
            entity.Property(match => match.ScenarioId).HasMaxLength(512);
            entity.Property(match => match.ResumeSlot).HasMaxLength(32);
            entity.Property(match => match.CreatedAt).HasConversion<long>();
            entity.Property(match => match.UpdatedAt).HasConversion<long>();
            entity.Property(match => match.RankingMode).HasMaxLength(16);
            entity.Property(match => match.PersonalityCardId).HasMaxLength(96);
            entity.HasIndex(match => new { match.IsListed, match.UpdatedAt });
            entity.HasOne<ApplicationUser>()
                .WithMany()
                .HasForeignKey(match => match.OwnerUserId)
                .OnDelete(DeleteBehavior.Restrict);
        });

        builder.Entity<PortalMatchMember>(entity =>
        {
            entity.HasKey(member => new { member.MatchId, member.UserId });
            entity.Property(member => member.MatchId).HasMaxLength(96);
            entity.Property(member => member.UserId).HasMaxLength(450);
            entity.Property(member => member.Role).HasMaxLength(24);
            entity.Property(member => member.JoinMode).HasMaxLength(24);
            entity.Property(member => member.JoinedAt).HasConversion<long>();
            entity.Property(member => member.LeftAt).HasConversion<long?>();
            entity.HasIndex(member => new { member.MatchId, member.SeatIndex }).IsUnique();
            entity.HasOne<PortalMatchProfile>()
                .WithMany()
                .HasForeignKey(member => member.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
            entity.HasOne<ApplicationUser>()
                .WithMany()
                .HasForeignKey(member => member.UserId)
                .OnDelete(DeleteBehavior.Restrict);
        });

        builder.Entity<PortalLobbyMessage>(entity =>
        {
            entity.HasKey(message => message.Id);
            entity.Property(message => message.MatchId).HasMaxLength(96);
            entity.Property(message => message.UserId).HasMaxLength(450);
            entity.Property(message => message.SenderHandle).HasMaxLength(31);
            entity.Property(message => message.Content).HasMaxLength(1000);
            entity.Property(message => message.NativeMessageUid).HasMaxLength(192);
            entity.Property(message => message.Channel).HasMaxLength(24);
            entity.Property(message => message.ConversationId).HasMaxLength(96);
            entity.Property(message => message.ConversationName).HasMaxLength(80);
            entity.Property(message => message.LogicalMessageId).HasMaxLength(96);
            entity.Property(message => message.CreatedAt).HasConversion<long>();
            entity.HasIndex(message => new { message.MatchId, message.CreatedAt });
            entity.HasIndex(message => new { message.MatchId, message.NativeMessageUid }).IsUnique();
            entity.HasOne<PortalMatchProfile>()
                .WithMany()
                .HasForeignKey(message => message.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalLobbySeat>(entity =>
        {
            entity.HasKey(seat => new { seat.MatchId, seat.SeatIndex });
            entity.Property(seat => seat.MatchId).HasMaxLength(96);
            entity.Property(seat => seat.ControllerKind).HasMaxLength(24);
            entity.Property(seat => seat.UserId).HasMaxLength(450);
            entity.Property(seat => seat.AgentId).HasMaxLength(96);
            entity.Property(seat => seat.PlayerHandle).HasMaxLength(31);
            entity.Property(seat => seat.FactionName).HasMaxLength(80);
            entity.Property(seat => seat.PersonalityCardId).HasMaxLength(96);
            entity.Property(seat => seat.RequestedFactionId).HasMaxLength(40);
            entity.Property(seat => seat.ResolvedFactionKey).HasMaxLength(40);
            entity.Property(seat => seat.LeaderName).HasMaxLength(80);
            entity.Property(seat => seat.RequestedPersonalityId).HasMaxLength(96);
            entity.Property(seat => seat.PersonalityName).HasMaxLength(160);
            entity.Property(seat => seat.PersonalityPrompt).HasMaxLength(32768);
            entity.Property(seat => seat.PersonalityPromptSha256).HasMaxLength(64);
            entity.Property(seat => seat.Status).HasMaxLength(24);
            entity.Property(seat => seat.JoinMode).HasMaxLength(24);
            entity.Property(seat => seat.ControlInstanceId).HasMaxLength(96);
            entity.Property(seat => seat.AiProfileVersionId).HasMaxLength(96);
            entity.Property(seat => seat.OutcomeResult).HasMaxLength(16);
            entity.Property(seat => seat.VictoryType).HasMaxLength(64);
            entity.Property(seat => seat.TemporaryControllerKind).HasMaxLength(24);
            entity.Property(seat => seat.DelegationStatus).HasMaxLength(24);
            entity.Property(seat => seat.ConnectionState).HasMaxLength(24);
            entity.Property(seat => seat.LastExitKind).HasMaxLength(32);
            entity.Property(seat => seat.DelegatedAt).HasConversion<long?>();
            entity.Property(seat => seat.LastBrowserSeenAt).HasConversion<long?>();
            entity.Property(seat => seat.LastWorkerSeenAt).HasConversion<long?>();
            entity.Property(seat => seat.UpdatedAt).HasConversion<long>();
            entity.HasOne<PortalMatchProfile>()
                .WithMany()
                .HasForeignKey(seat => seat.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalAiProfileVersion>(entity =>
        {
            entity.HasKey(item => item.ProfileVersionId);
            entity.Property(item => item.ProfileVersionId).HasMaxLength(96);
            entity.Property(item => item.StableProfileId).HasMaxLength(96);
            entity.Property(item => item.DisplayName).HasMaxLength(160);
            entity.Property(item => item.AgentId).HasMaxLength(96);
            entity.Property(item => item.ProviderId).HasMaxLength(96);
            entity.Property(item => item.ModelId).HasMaxLength(512);
            entity.Property(item => item.ReasoningEffort).HasMaxLength(16);
            entity.Property(item => item.GenerationSettingsJson).HasMaxLength(4096);
            entity.Property(item => item.Notes).HasMaxLength(2000);
            entity.Property(item => item.PersonalityCardId).HasMaxLength(96);
            entity.Property(item => item.CreatedAt).HasConversion<long>();
            entity.HasIndex(item => new { item.StableProfileId, item.Version }).IsUnique();
            entity.HasIndex(item => new { item.Active, item.DisplayName });
        });

        builder.Entity<PortalMatchEvent>(entity =>
        {
            entity.HasKey(item => item.Id);
            entity.Property(item => item.MatchId).HasMaxLength(96);
            entity.Property(item => item.EventType).HasMaxLength(64);
            entity.Property(item => item.Summary).HasMaxLength(2000);
            entity.Property(item => item.DetailsJson).HasMaxLength(16384);
            entity.Property(item => item.CreatedAt).HasConversion<long>();
            entity.HasIndex(item => new { item.MatchId, item.CreatedAt });
            entity.HasOne<PortalMatchProfile>().WithMany().HasForeignKey(item => item.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalTurnMetric>(entity =>
        {
            entity.HasKey(item => item.Id);
            entity.Property(item => item.MatchId).HasMaxLength(96);
            entity.Property(item => item.AgentId).HasMaxLength(96);
            entity.Property(item => item.ProfileVersionId).HasMaxLength(96);
            entity.Property(item => item.StartedAt).HasConversion<long>();
            entity.Property(item => item.CompletedAt).HasConversion<long?>();
            entity.HasIndex(item => new { item.MatchId, item.AgentId, item.Turn }).IsUnique();
            entity.HasOne<PortalMatchProfile>().WithMany().HasForeignKey(item => item.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalChatGroup>(entity =>
        {
            entity.HasKey(item => item.GroupId);
            entity.Property(item => item.GroupId).HasMaxLength(96);
            entity.Property(item => item.MatchId).HasMaxLength(96);
            entity.Property(item => item.DisplayName).HasMaxLength(80);
            entity.Property(item => item.CreatedByUserId).HasMaxLength(450);
            entity.Property(item => item.Status).HasMaxLength(24);
            entity.Property(item => item.CreatedAt).HasConversion<long>();
            entity.Property(item => item.UpdatedAt).HasConversion<long>();
            entity.HasIndex(item => new { item.MatchId, item.Status, item.UpdatedAt });
            entity.HasOne<PortalMatchProfile>().WithMany().HasForeignKey(item => item.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalChatGroupMember>(entity =>
        {
            entity.HasKey(item => new { item.GroupId, item.ActorKey });
            entity.Property(item => item.GroupId).HasMaxLength(96);
            entity.Property(item => item.ActorKey).HasMaxLength(160);
            entity.Property(item => item.UserId).HasMaxLength(450);
            entity.Property(item => item.DisplayName).HasMaxLength(80);
            entity.Property(item => item.FactionName).HasMaxLength(80);
            entity.Property(item => item.Status).HasMaxLength(24);
            entity.Property(item => item.RespondedAt).HasConversion<long?>();
            entity.HasOne<PortalChatGroup>().WithMany().HasForeignKey(item => item.GroupId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalChatDelivery>(entity =>
        {
            entity.HasKey(item => item.DeliveryId);
            entity.Property(item => item.DeliveryId).HasMaxLength(96);
            entity.Property(item => item.MessageId).HasMaxLength(96);
            entity.Property(item => item.RecipientHandle).HasMaxLength(31);
            entity.Property(item => item.Status).HasMaxLength(24);
            entity.Property(item => item.NativeMessageUid).HasMaxLength(192);
            entity.Property(item => item.DeliveredAt).HasConversion<long?>();
            entity.HasIndex(item => new { item.MessageId, item.RecipientFactionId }).IsUnique();
            entity.HasOne<PortalLobbyMessage>().WithMany().HasForeignKey(item => item.MessageId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalGovernanceProposal>(entity =>
        {
            entity.HasKey(item => item.ProposalId);
            entity.Property(item => item.ProposalId).HasMaxLength(96);
            entity.Property(item => item.MatchId).HasMaxLength(96);
            entity.Property(item => item.Kind).HasMaxLength(48);
            entity.Property(item => item.Status).HasMaxLength(24);
            entity.Property(item => item.RequestedByUserId).HasMaxLength(450);
            entity.Property(item => item.RequestedByHandle).HasMaxLength(31);
            entity.Property(item => item.Title).HasMaxLength(160);
            entity.Property(item => item.Description).HasMaxLength(2000);
            entity.Property(item => item.PayloadJson).HasMaxLength(16384);
            entity.Property(item => item.EligibleVotersJson).HasMaxLength(16384);
            entity.Property(item => item.CreatedAt).HasConversion<long>();
            entity.Property(item => item.ExpiresAt).HasConversion<long>();
            entity.Property(item => item.ResolvedAt).HasConversion<long?>();
            entity.HasIndex(item => new { item.MatchId, item.Status, item.ExpiresAt });
            entity.HasOne<PortalMatchProfile>().WithMany().HasForeignKey(item => item.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalGovernanceVote>(entity =>
        {
            entity.HasKey(item => new { item.ProposalId, item.UserId });
            entity.Property(item => item.ProposalId).HasMaxLength(96);
            entity.Property(item => item.UserId).HasMaxLength(450);
            entity.Property(item => item.Vote).HasMaxLength(8);
            entity.Property(item => item.UpdatedAt).HasConversion<long>();
            entity.HasOne<PortalGovernanceProposal>().WithMany().HasForeignKey(item => item.ProposalId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalMaintenanceOperation>(entity =>
        {
            entity.HasKey(item => item.OperationId);
            entity.Property(item => item.OperationId).HasMaxLength(96);
            entity.Property(item => item.MatchId).HasMaxLength(96);
            entity.Property(item => item.ProposalId).HasMaxLength(96);
            entity.Property(item => item.Kind).HasMaxLength(48);
            entity.Property(item => item.Status).HasMaxLength(24);
            entity.Property(item => item.Phase).HasMaxLength(64);
            entity.Property(item => item.Summary).HasMaxLength(2000);
            entity.Property(item => item.PayloadJson).HasMaxLength(16384);
            entity.Property(item => item.CreatedAt).HasConversion<long>();
            entity.Property(item => item.UpdatedAt).HasConversion<long>();
            entity.Property(item => item.CompletedAt).HasConversion<long?>();
            entity.HasIndex(item => new { item.MatchId, item.Status, item.UpdatedAt });
            entity.HasOne<PortalMatchProfile>().WithMany().HasForeignKey(item => item.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<PortalStableCheckpoint>(entity =>
        {
            entity.HasKey(item => item.CheckpointId);
            entity.Property(item => item.CheckpointId).HasMaxLength(96);
            entity.Property(item => item.MatchId).HasMaxLength(96);
            entity.Property(item => item.OperationId).HasMaxLength(96);
            entity.Property(item => item.Slot).HasMaxLength(32);
            entity.Property(item => item.Sha256).HasMaxLength(64);
            entity.Property(item => item.SessionId).HasMaxLength(96);
            entity.Property(item => item.SeatMapJson).HasMaxLength(16384);
            entity.Property(item => item.Stability).HasMaxLength(24);
            entity.Property(item => item.CreatedAt).HasConversion<long>();
            entity.HasIndex(item => new { item.MatchId, item.CreatedAt });
            entity.HasOne<PortalMatchProfile>().WithMany().HasForeignKey(item => item.MatchId)
                .OnDelete(DeleteBehavior.Cascade);
        });
    }
}

public sealed class PortalSetting
{
    public string Key { get; set; } = string.Empty;

    public string Value { get; set; } = string.Empty;

    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PasswordResetGrant
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");

    public string UserId { get; set; } = string.Empty;

    public string TokenHash { get; set; } = string.Empty;

    public string IssuedByUserId { get; set; } = string.Empty;

    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;

    public DateTimeOffset ExpiresAt { get; set; }

    public DateTimeOffset? UsedAt { get; set; }
}

public sealed class PortalMatchProfile
{
    public string MatchId { get; set; } = string.Empty;
    public string OwnerUserId { get; set; } = string.Empty;
    public string DisplayName { get; set; } = string.Empty;
    public string Status { get; set; } = "waiting";
    public string Mode { get; set; } = "standard";
    public string GameSourceId { get; set; } = string.Empty;
    public string RuntimeId { get; set; } = string.Empty;
    public string LanProfile { get; set; } = "small_easy";
    public string SettingsJson { get; set; } = "{}";
    public string NativeSettingsJson { get; set; } = "{}";
    public string? LastError { get; set; }
    public int? CurrentTurn { get; set; }
    public int? CurrentYear { get; set; }
    public string? ScenarioId { get; set; }
    public string? ResumeSlot { get; set; }
    public bool IsListed { get; set; } = true;
    public bool AllowAnonymousSpectators { get; set; }
    public bool ManagedClientsOnly { get; set; }
    public string RankingMode { get; set; } = "unranked";
    public bool GraphitiEnabled { get; set; } = true;
    public string PersonalityCardId { get; set; } = "none";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PortalMatchMember
{
    public string MatchId { get; set; } = string.Empty;
    public string UserId { get; set; } = string.Empty;
    public int? SeatIndex { get; set; }
    public string Role { get; set; } = "player";
    public string JoinMode { get; set; } = "browser";
    public DateTimeOffset JoinedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? LeftAt { get; set; }
}

public sealed class PortalLobbyMessage
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string MatchId { get; set; } = string.Empty;
    public string? UserId { get; set; }
    public string SenderHandle { get; set; } = string.Empty;
    public string Content { get; set; } = string.Empty;
    public bool DeliveredToGame { get; set; }
    public string? NativeMessageUid { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public string Channel { get; set; } = "global";
    public string? ConversationId { get; set; }
    public string? ConversationName { get; set; }
    public string? LogicalMessageId { get; set; }
    public int? SenderFactionId { get; set; }
    public int RecipientFactionId { get; set; }
}

public sealed class PortalLobbySeat
{
    public string MatchId { get; set; } = string.Empty;
    public int SeatIndex { get; set; }
    public string ControllerKind { get; set; } = "open";
    public string? UserId { get; set; }
    public string? AgentId { get; set; }
    public string? PlayerHandle { get; set; }
    public int? FactionId { get; set; }
    public string? FactionName { get; set; }
    public string PersonalityCardId { get; set; } = "none";
    public string RequestedFactionId { get; set; } = "random";
    public string? ResolvedFactionKey { get; set; }
    public string? LeaderName { get; set; }
    public string RequestedPersonalityId { get; set; } = "standard";
    public string? PersonalityName { get; set; }
    public string? PersonalityPrompt { get; set; }
    public string? PersonalityPromptSha256 { get; set; }
    public string Status { get; set; } = "open";
    public string JoinMode { get; set; } = "browser";
    public string? ControlInstanceId { get; set; }
    public string? AiProfileVersionId { get; set; }
    public string? OutcomeResult { get; set; }
    public string? VictoryType { get; set; }
    public bool OutcomeFinalized { get; set; }
    public long LastChatSequence { get; set; }
    public string TemporaryControllerKind { get; set; } = "none";
    public string DelegationStatus { get; set; } = "none";
    public string ConnectionState { get; set; } = "unknown";
    public string? LastExitKind { get; set; }
    public DateTimeOffset? DelegatedAt { get; set; }
    public DateTimeOffset? LastBrowserSeenAt { get; set; }
    public DateTimeOffset? LastWorkerSeenAt { get; set; }
    public bool IsManagedHost { get; set; }
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PortalAiProfileVersion
{
    public string ProfileVersionId { get; set; } = $"profile-version-{Guid.NewGuid():N}";
    public string StableProfileId { get; set; } = $"profile-{Guid.NewGuid():N}";
    public int Version { get; set; } = 1;
    public string DisplayName { get; set; } = string.Empty;
    public string AgentId { get; set; } = string.Empty;
    public string ProviderId { get; set; } = string.Empty;
    public string ModelId { get; set; } = string.Empty;
    public string ReasoningEffort { get; set; } = "low";
    public int? ContextLength { get; set; }
    public string GenerationSettingsJson { get; set; } = "{}";
    public string? Notes { get; set; }
    public bool Active { get; set; } = true;
    public string PersonalityCardId { get; set; } = "none";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PortalMatchEvent
{
    public string Id { get; set; } = $"portal-event-{Guid.NewGuid():N}";
    public string MatchId { get; set; } = string.Empty;
    public string EventType { get; set; } = string.Empty;
    public string Summary { get; set; } = string.Empty;
    public string DetailsJson { get; set; } = "{}";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PortalTurnMetric
{
    public string Id { get; set; } = $"turn-metric-{Guid.NewGuid():N}";
    public string MatchId { get; set; } = string.Empty;
    public string AgentId { get; set; } = string.Empty;
    public string? ProfileVersionId { get; set; }
    public int Turn { get; set; }
    public double? DurationSeconds { get; set; }
    public long PromptTokens { get; set; }
    public long CompletionTokens { get; set; }
    public long CacheReadTokens { get; set; }
    public long CacheWriteTokens { get; set; }
    public long ReasoningTokens { get; set; }
    public long ApiCalls { get; set; }
    public bool Errored { get; set; }
    public DateTimeOffset StartedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? CompletedAt { get; set; }
}

public sealed class PortalChatGroup
{
    public string GroupId { get; set; } = $"chat-group-{Guid.NewGuid():N}";
    public string MatchId { get; set; } = string.Empty;
    public string DisplayName { get; set; } = string.Empty;
    public string CreatedByUserId { get; set; } = string.Empty;
    public int Version { get; set; } = 1;
    public string Status { get; set; } = "inviting";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PortalChatGroupMember
{
    public string GroupId { get; set; } = string.Empty;
    public string ActorKey { get; set; } = string.Empty;
    public string? UserId { get; set; }
    public int? FactionId { get; set; }
    public string DisplayName { get; set; } = string.Empty;
    public string? FactionName { get; set; }
    public string Status { get; set; } = "invited";
    public bool LocalCreator { get; set; }
    public DateTimeOffset? RespondedAt { get; set; }
}

public sealed class PortalChatDelivery
{
    public string DeliveryId { get; set; } = $"chat-delivery-{Guid.NewGuid():N}";
    public string MessageId { get; set; } = string.Empty;
    public int RecipientFactionId { get; set; }
    public string? RecipientHandle { get; set; }
    public string Status { get; set; } = "pending";
    public string? NativeMessageUid { get; set; }
    public DateTimeOffset? DeliveredAt { get; set; }
}

public sealed class PortalGovernanceProposal
{
    public string ProposalId { get; set; } = $"proposal-{Guid.NewGuid():N}";
    public string MatchId { get; set; } = string.Empty;
    public string Kind { get; set; } = string.Empty;
    public string Status { get; set; } = "open";
    public string RequestedByUserId { get; set; } = string.Empty;
    public string RequestedByHandle { get; set; } = string.Empty;
    public string Title { get; set; } = string.Empty;
    public string Description { get; set; } = string.Empty;
    public string PayloadJson { get; set; } = "{}";
    public string EligibleVotersJson { get; set; } = "[]";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset ExpiresAt { get; set; }
    public DateTimeOffset? ResolvedAt { get; set; }
}

public sealed class PortalGovernanceVote
{
    public string ProposalId { get; set; } = string.Empty;
    public string UserId { get; set; } = string.Empty;
    public string Vote { get; set; } = "abstain";
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PortalMaintenanceOperation
{
    public string OperationId { get; set; } = $"maintenance-{Guid.NewGuid():N}";
    public string MatchId { get; set; } = string.Empty;
    public string? ProposalId { get; set; }
    public string Kind { get; set; } = string.Empty;
    public string Status { get; set; } = "queued";
    public string Phase { get; set; } = "waiting_for_safe_boundary";
    public string Summary { get; set; } = string.Empty;
    public string PayloadJson { get; set; } = "{}";
    public int CompletedSteps { get; set; }
    public int TotalSteps { get; set; } = 8;
    public int? StableTurn { get; set; }
    public int? StableYear { get; set; }
    public bool CanCancel { get; set; } = true;
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? CompletedAt { get; set; }
}

public sealed class PortalStableCheckpoint
{
    public string CheckpointId { get; set; } = $"checkpoint-{Guid.NewGuid():N}";
    public string MatchId { get; set; } = string.Empty;
    public string? OperationId { get; set; }
    public string Slot { get; set; } = string.Empty;
    public string? Sha256 { get; set; }
    public long? Bytes { get; set; }
    public int? Turn { get; set; }
    public int? Year { get; set; }
    public string? SessionId { get; set; }
    public string SeatMapJson { get; set; } = "[]";
    public string Stability { get; set; } = "verified";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
}
