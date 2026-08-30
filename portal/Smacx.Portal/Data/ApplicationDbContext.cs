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

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);

        builder.Entity<ApplicationUser>(entity =>
        {
            entity.Property(user => user.DisplayName).HasMaxLength(80);
            entity.Property(user => user.GameHandle).HasMaxLength(31);
            entity.Property(user => user.NormalizedGameHandle).HasMaxLength(31);
            entity.HasIndex(user => user.NormalizedGameHandle).IsUnique();
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
            entity.Property(seat => seat.Status).HasMaxLength(24);
            entity.Property(seat => seat.JoinMode).HasMaxLength(24);
            entity.Property(seat => seat.ControlInstanceId).HasMaxLength(96);
            entity.Property(seat => seat.AiProfileVersionId).HasMaxLength(96);
            entity.Property(seat => seat.OutcomeResult).HasMaxLength(16);
            entity.Property(seat => seat.VictoryType).HasMaxLength(64);
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
    public string Status { get; set; } = "open";
    public string JoinMode { get; set; } = "browser";
    public string? ControlInstanceId { get; set; }
    public string? AiProfileVersionId { get; set; }
    public string? OutcomeResult { get; set; }
    public string? VictoryType { get; set; }
    public bool OutcomeFinalized { get; set; }
    public long LastChatSequence { get; set; }
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
