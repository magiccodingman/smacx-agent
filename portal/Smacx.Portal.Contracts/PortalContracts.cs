namespace Smacx.Portal.Contracts;

public sealed record PortalSetupState(
    bool SetupRequired,
    bool RegistrationEnabled,
    string DefaultAdministrator,
    string BootstrapCommand,
    int PasswordMinimumLength);

public sealed record PortalUser(
    string Id,
    string Username,
    string DisplayName,
    string GameHandle,
    IReadOnlyList<string> Roles,
    bool IsAdministrator,
    bool MustResetPassword);

public sealed record PortalSession(bool Authenticated, PortalUser? User);

public sealed record LoginRequest(string Username, string Password, bool RememberMe = false);

public sealed record RegistrationRequest(
    string Username,
    string DisplayName,
    string Password,
    string ConfirmPassword);

public sealed record BootstrapRequest(string Token, string Password, string ConfirmPassword);

public sealed record ChangePasswordRequest(
    string CurrentPassword, string NewPassword, string ConfirmPassword);

public sealed record CsrfTokenResponse(string Token);

public sealed record PortalStatus(
    string Service,
    string Version,
    bool ControlConnected,
    string ControlState,
    int ActiveMatches,
    int OnlinePlayers,
    int RunningAiSeats,
    int RecoverableMatches);

public sealed record PublicLobbySummary(
    string MatchId,
    string DisplayName,
    string Status,
    int CurrentTurn,
    int SeatCount,
    bool AnonymousSpectators,
    DateTimeOffset UpdatedAt);

public sealed record CreateLobbyRequest(
    string DisplayName,
    string GameSourceId,
    string RuntimeId,
    string Profile,
    string Mode,
    string WorldSize,
    string Difficulty,
    bool RandomMap,
    bool DoOrDie,
    bool AllowAnonymousSpectators,
    bool ManagedClientsOnly,
    bool GraphitiEnabled,
    string PersonalityCardId,
    IReadOnlyList<string> AgentIds,
    IReadOnlyList<string> InvitedHumanHandles,
    string HostController = "human",
    bool OwnerPlays = true,
    int NativeBotCount = 0,
    string NativeBotDifficulty = "librarian",
    bool StartNow = false,
    string RankingMode = "unranked",
    string HumanJoinMode = "browser",
    int TimeControl = 2,
    int OceanCoverage = 1,
    int ErosiveForces = 1,
    int NativeLife = 1,
    int CloudCover = 1,
    IReadOnlyDictionary<string, bool>? RuleOptions = null,
    string? ScenarioId = null,
    string? ResumeSlot = null,
    IReadOnlyList<AgentSeatRequest>? AgentSeats = null);

public sealed record JoinLobbyRequest(int SeatIndex, string JoinMode = "browser");

public sealed record UpdateLobbySeatRequest(
    string ControllerKind,
    string? AgentId = null,
    string? PlayerHandle = null,
    string JoinMode = "browser",
    string FactionId = "random",
    string PersonalityId = "standard");

public sealed record MatchLifecycleRequest(string Action, string? Slot = null);

public sealed record ControllerLeaseRequest(string PlayInstanceId);

public sealed record ControllerLeaseActionRequest(string LeaseId);

public sealed record ControllerLeaseState(
    string LeaseId, string Role, DateTimeOffset ExpiresAt, long Generation,
    bool ControllerPresent, int ExpiresInSeconds);

public sealed record LobbySeatSummary(
    int SeatIndex,
    string ControllerKind,
    string? AgentId,
    string? PlayerHandle,
    int? FactionId,
    string? FactionName,
    string Status,
    bool Managed,
    string? InstanceId = null,
    string? JoinMode = null,
    bool CanControl = false,
    bool CanSpectate = false,
    bool CanJoin = false,
    string ConnectionState = "unknown",
    string DelegationStatus = "none",
    string TemporaryControllerKind = "none",
    DateTimeOffset? LastBrowserSeenAt = null,
    bool IsManagedHost = false,
    string RequestedFactionId = "random",
    string? ResolvedFactionKey = null,
    string RequestedPersonalityId = "standard",
    string? PersonalityName = null);

public sealed record FactionPersonalityCatalog(
    IReadOnlyList<FactionCatalogItem> Factions,
    IReadOnlyList<PersonalityCatalogItem> PersonalityModes,
    IReadOnlyList<PersonalityCatalogItem> BuiltInCards);

public sealed record LobbyDetails(
    string MatchId,
    string DisplayName,
    string Mode,
    string Status,
    string? RulesetId,
    int? CurrentTurn,
    int? CurrentYear,
    bool IsListed,
    bool AllowAnonymousSpectators,
    bool ManagedClientsOnly,
    string RankingMode,
    bool GraphitiEnabled,
    string PersonalityCardId,
    bool CanManage,
    IReadOnlyList<LobbySeatSummary> Seats,
    IReadOnlyDictionary<string, object?> Settings,
    NativeJoinDetails? NativeJoin,
    string? LastError,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt,
    MatchPresenceState? Presence = null);

public sealed record MatchPresenceState(
    string State, string Summary, bool AutomaticParkingEnabled,
    int? SecondsUntilParking = null, DateTimeOffset? ParkingEligibleAt = null);

public sealed record LobbyMessage(
    string Id,
    string MatchId,
    string SenderHandle,
    string Content,
    bool DeliveredToGame,
    int? SenderFactionId,
    int RecipientFactionId,
    DateTimeOffset CreatedAt,
    string Channel = "global",
    string? ConversationId = null,
    string? ConversationName = null,
    string? LogicalMessageId = null,
    IReadOnlyList<ChatDelivery>? Deliveries = null);

public sealed record ChatDelivery(
    int RecipientFactionId, string? RecipientHandle, string Status,
    string? NativeMessageUid = null);

public sealed record SendLobbyMessageRequest(
    string Content, int RecipientFactionId = 0,
    string Channel = "global", string? ConversationId = null);

public sealed record ChatConversation(
    string ConversationId, string MatchId, string Kind, string DisplayName,
    IReadOnlyList<ChatParticipant> Participants, int UnreadCount,
    DateTimeOffset UpdatedAt);

public sealed record ChatParticipant(
    string ActorId, string DisplayName, int? FactionId, string? FactionName,
    string MembershipStatus, bool Local, bool PrivateEligible = false);

public sealed record CreateChatGroupRequest(
    string DisplayName, IReadOnlyList<int> MemberFactionIds);

public sealed record RespondChatGroupRequest(string Response);

public sealed record ResolutionProfile(
    string Id, int Width, int Height, string Label, string DeviceClass,
    bool TouchRecommended, bool Ultrawide = false);

public sealed record HumanUiState(
    string MatchId, string InstanceId, string Surface,
    bool RootMenuVisible, int MenuDepth, bool Modal,
    string? PopupLabel, string? LifecycleIntent, long Revision,
    string ResolutionProfileId = "1280x800", int NativeWidth = 1280,
    int NativeHeight = 800, bool CanRequestNativeChange = true,
    int StreamBitrateKbps = 5000, string StreamEncoder = "h264enc",
    bool NativeQuitIntercepted = false);

public sealed record GovernanceProposal(
    string ProposalId, string MatchId, string Kind, string Status,
    string RequestedByHandle, string Title, string Description,
    string PayloadJson, int EligibleVoters, int YesVotes, int NoVotes,
    bool CurrentUserEligible, string? CurrentUserVote,
    DateTimeOffset CreatedAt, DateTimeOffset ExpiresAt);

public sealed record CreateGovernanceProposalRequest(
    string Kind, string PayloadJson = "{}", int TimeoutSeconds = 120);

public sealed record GovernanceVoteRequest(string Vote);

public sealed record MaintenanceProgress(
    string? OperationId, string MatchId, string Kind, string Status,
    string Phase, string Summary, int CompletedSteps, int TotalSteps,
    int? StableTurn, int? StableYear, DateTimeOffset UpdatedAt,
    bool CanCancel = false);

public static class ResolutionProfiles
{
    public const string Automatic = "auto";
    public const string MobileDefault = "800x600";
    public const string TabletDefault = "1024x768";
    public const string DesktopDefault = "1280x800";

    public static IReadOnlyList<ResolutionProfile> All { get; } = new[]
    {
        new ResolutionProfile("800x600", 800, 600, "Mobile · 800 × 600", "phone", true),
        new ResolutionProfile("1024x768", 1024, 768, "Tablet · 1024 × 768", "tablet", true),
        new ResolutionProfile("1280x720", 1280, 720, "Compact widescreen · 1280 × 720", "desktop", false),
        new ResolutionProfile("1280x800", 1280, 800, "Balanced desktop · 1280 × 800", "desktop", false),
        new ResolutionProfile("1440x900", 1440, 900, "Large desktop · 1440 × 900", "desktop", false),
        new ResolutionProfile("1600x900", 1600, 900, "HD+ · 1600 × 900", "desktop", false),
        new ResolutionProfile("1600x1200", 1600, 1200, "Classic high resolution · 1600 × 1200", "desktop", false),
        new ResolutionProfile("1920x1080", 1920, 1080, "Full HD · 1920 × 1080", "desktop", false),
        new ResolutionProfile("1920x1200", 1920, 1200, "WUXGA · 1920 × 1200", "desktop", false),
        new ResolutionProfile("2560x1080", 2560, 1080, "Ultrawide · 2560 × 1080", "desktop", false, true),
        new ResolutionProfile("2560x1440", 2560, 1440, "QHD · 2560 × 1440", "desktop", false),
        new ResolutionProfile("2560x1600", 2560, 1600, "QHD+ · 2560 × 1600", "desktop", false),
        new ResolutionProfile("3440x1440", 3440, 1440, "Ultrawide QHD · 3440 × 1440", "desktop", false, true),
        new ResolutionProfile("3840x1600", 3840, 1600, "Ultrawide 4K · 3840 × 1600", "desktop", false, true),
        new ResolutionProfile("3840x2160", 3840, 2160, "4K · 3840 × 2160", "desktop", false),
        new ResolutionProfile("5120x1440", 5120, 1440, "Super ultrawide · 5120 × 1440", "desktop", false, true),
    };

    public static ResolutionProfile? Find(string? id) =>
        All.FirstOrDefault(item => item.Id.Equals(id, StringComparison.OrdinalIgnoreCase));
}

public sealed record NativeJoinPlayer(int SeatIndex, string PlayerName, int? ExpectedFactionId);
public sealed record NativeJoinDetails(
    string HostAddress,
    string SessionName,
    string? NetworkSessionId,
    string Network,
    IReadOnlyList<NativeJoinPlayer> Players,
    string Instructions);

public sealed record PortalActivityItem(
    string MatchId, string MatchName, string Status, string Summary, DateTimeOffset CreatedAt);

public sealed record CatalogItem(string Id, string DisplayName, string Status);

public sealed record ScenarioCatalogItem(string Id, string DisplayName, string RelativePath);

public sealed record LobbyCatalog(
    IReadOnlyList<CatalogItem> GameSources,
    IReadOnlyList<CatalogItem> Runtimes,
    IReadOnlyList<CatalogItem> Agents,
    bool ControlConnected,
    string? ErrorCode = null);

public sealed record MatchHistoryItem(
    string MatchId, string DisplayName, string Status, string Mode,
    int? CurrentTurn, int? CurrentYear, string RankingMode,
    bool CanResume, DateTimeOffset CreatedAt, DateTimeOffset UpdatedAt);
public sealed record MatchHistoryPage(
    IReadOnlyList<MatchHistoryItem> Items, int Total, int FilteredTotal, int Active,
    int Recoverable, int Completed, int Offset, int Limit);

public sealed record AnalyticsSummary(
    int CompletedMatches, int ActiveMatches, int RecoverableMatches,
    int RecordedTurns, double? MedianTurnSeconds, long PromptTokens,
    long CompletionTokens, long CacheReadTokens, long CacheWriteTokens,
    long ReasoningTokens, long ApiCalls, double? RecoveryRate,
    IReadOnlyList<AnalyticsProfileRow> Profiles);

public sealed record AnalyticsProfileRow(
    string ProfileName, string Provider, string Model, string ReasoningEffort,
    string GenerationPreset,
    int Matches, int ClassifiedOutcomes, int Wins, double? WinRate, double? MedianTurnSeconds,
    long PromptTokens, long CompletionTokens, long CacheReadTokens,
    long CacheWriteTokens, long ReasoningTokens, long ApiCalls);
public sealed record AnalyticsQueryRequest(string Sql);
public sealed record AnalyticsQueryResult(
    IReadOnlyList<string> Columns, IReadOnlyList<IReadOnlyList<object?>> Rows,
    bool Truncated);

public sealed record KnowledgeTopic(string Topic, int DocumentCount);
public sealed record KnowledgeCollection(
    string Id, string? ParentId, string Title, string Description,
    IReadOnlyList<string> Tags, IReadOnlyList<string> Path,
    int DirectDocumentCount, int DocumentCount);
public sealed record KnowledgeHeading(int Level, string Text, string Anchor);
public sealed record KnowledgeResult(
    string DocumentId, string Topic, string Title, string Summary,
    IReadOnlyList<string> Tags, string Provenance, string SourceLicense,
    string? Body = null, string? CollectionId = null,
    IReadOnlyList<string>? CollectionPath = null, string? RenderedHtml = null,
    IReadOnlyList<KnowledgeHeading>? Headings = null);
public sealed record KnowledgeSearchResponse(
    string Query, string? Topic, IReadOnlyList<KnowledgeResult> Results,
    bool QueryTruncated = false, int? QueryTokens = null);

public sealed record ProviderConfigurationRequest(
    string DisplayName, string BaseUrl, string? ApiKey = null,
    string? ProviderId = null, string? DefaultModelId = null,
    int? ContextLengthOverride = null);
public sealed record ProviderModelSelectionRequest(string ModelId, int? ContextLengthOverride = null);

public sealed record AiProfileRequest(
    string DisplayName, string ProviderId, string ModelId,
    string ReasoningEffort = "low", int? ContextLength = null,
    string? Notes = null, string? ProfileId = null,
    ModelGenerationSettings? Generation = null);

public sealed record ModelGenerationSettings(
    string Preset = "provider-default",
    double? Temperature = null,
    double? TopP = null,
    int? TopK = null,
    double? MinP = null,
    double? PresencePenalty = null,
    double? FrequencyPenalty = null,
    double? RepetitionPenalty = null,
    int? MaxOutputTokens = null,
    int? Seed = null,
    bool? EnableThinking = null,
    bool? PreserveThinking = null,
    IReadOnlyDictionary<string, System.Text.Json.JsonElement>? ExtraParameters = null);

public sealed record AiProfile(
    string ProfileId,
    string DisplayName, string AgentId, string ProviderId, string ModelId,
    string ReasoningEffort, int? ContextLength, string? Notes,
    bool Active, string PersonalityCardId, DateTimeOffset CreatedAt, DateTimeOffset UpdatedAt,
    ModelGenerationSettings Generation,
    GenerationAcceptanceStatus? Acceptance = null);

public sealed record GenerationAcceptanceStatus(
    string State, bool? Accepted, bool SemanticEffectVerified,
    string Message, DateTimeOffset? TestedAt = null,
    IReadOnlyList<string>? SentFields = null, int? HttpStatus = null);

public sealed record GameSourceRequest(string DisplayName, string HostPath);
public sealed record RuntimeImportRequest(string DisplayName, string SourceHostPath);
public sealed record OperationActionRequest(string Action, string? TargetId = null);
public sealed record ScheduleRequest(
    string DisplayName, string OperationKind, string TargetKind,
    string? TargetId, int IntervalSeconds);
public sealed record BackupRequest(bool IncludeSecrets = true, bool IncludeWorkers = true);
public sealed record StoragePolicyRequest(
    int RecentCheckpoints = 10,
    int MilestoneInterval = 25,
    bool RetainFullTurnHistory = false);
public sealed record GraphitiConfigurationRequest(bool Enabled, string? ProfileId = null);
public sealed record EmbeddingConfigurationRequest(
    string Mode, string? ProviderId = null, string? ModelId = null,
    int? Dimensions = null, string? SpaceId = null);

public sealed record AdminSnapshot(
    System.Text.Json.JsonElement Providers,
    System.Text.Json.JsonElement Agents,
    System.Text.Json.JsonElement HarnessProfiles,
    System.Text.Json.JsonElement HarnessRuns,
    System.Text.Json.JsonElement Graphiti,
    System.Text.Json.JsonElement Knowledge,
    System.Text.Json.JsonElement Workers,
    System.Text.Json.JsonElement Operations,
    System.Text.Json.JsonElement Storage,
    System.Text.Json.JsonElement Schedules,
    System.Text.Json.JsonElement Backups);

public sealed record AdminRuntimeSnapshot(
    System.Text.Json.JsonElement GameSources,
    System.Text.Json.JsonElement Runtimes,
    System.Text.Json.JsonElement Workers);

public sealed record AdminUserSummary(
    string Id,
    string Username,
    string DisplayName,
    string GameHandle,
    bool IsAdministrator,
    bool IsProvisional,
    bool MustResetPassword,
    DateTimeOffset CreatedAt);

public sealed record SetAdministratorRequest(bool IsAdministrator);
public sealed record PasswordResetTicket(string Username, string Token, DateTimeOffset ExpiresAt);
public sealed record CompletePasswordResetRequest(
    string Username, string Token, string Password, string ConfirmPassword);

public sealed record ApiError(string Code, string Message);

public sealed record ApiResponse<T>(bool Ok, T? Data = default, ApiError? Error = null)
{
    public static ApiResponse<T> Success(T data) => new(true, data);

    public static ApiResponse<T> Failure(string code, string message) =>
        new(false, default, new ApiError(code, message));
}
