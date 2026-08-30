namespace Smacx.Portal.Contracts;

public sealed record PortalSetupState(
    bool SetupRequired,
    bool RegistrationEnabled,
    string DefaultAdministrator,
    string BootstrapCommand);

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
    string? ResumeSlot = null);

public sealed record JoinLobbyRequest(int SeatIndex, string JoinMode = "browser");

public sealed record MatchLifecycleRequest(string Action, string? Slot = null);

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
    bool CanJoin = false);

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
    DateTimeOffset UpdatedAt);

public sealed record LobbyMessage(
    string Id,
    string MatchId,
    string SenderHandle,
    string Content,
    bool DeliveredToGame,
    int? SenderFactionId,
    int RecipientFactionId,
    DateTimeOffset CreatedAt);

public sealed record SendLobbyMessageRequest(string Content, int RecipientFactionId = 0);

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

public sealed record AnalyticsSummary(
    int CompletedMatches, int ActiveMatches, int RecoverableMatches,
    int RecordedTurns, double? MedianTurnSeconds, long PromptTokens,
    long CompletionTokens, long CacheReadTokens, long CacheWriteTokens,
    long ReasoningTokens, long ApiCalls, double? RecoveryRate,
    IReadOnlyList<AnalyticsProfileRow> Profiles);

public sealed record AnalyticsProfileRow(
    string ProfileName, string Provider, string Model, string ReasoningEffort,
    int Matches, int ClassifiedOutcomes, int Wins, double? WinRate, double? MedianTurnSeconds,
    long PromptTokens, long CompletionTokens, long CacheReadTokens,
    long CacheWriteTokens, long ReasoningTokens, long ApiCalls);
public sealed record AnalyticsQueryRequest(string Sql);
public sealed record AnalyticsQueryResult(
    IReadOnlyList<string> Columns, IReadOnlyList<IReadOnlyList<object?>> Rows,
    bool Truncated);

public sealed record KnowledgeTopic(string Topic, int DocumentCount);
public sealed record KnowledgeResult(
    string DocumentId, string Topic, string Title, string Summary,
    IReadOnlyList<string> Tags, string Provenance, string SourceLicense,
    string? Body = null);
public sealed record KnowledgeSearchResponse(
    string Query, string? Topic, IReadOnlyList<KnowledgeResult> Results);

public sealed record ProviderConfigurationRequest(
    string DisplayName, string BaseUrl, string? ApiKey = null,
    string? ProviderId = null, string? DefaultModelId = null,
    int? ContextLengthOverride = null);
public sealed record ProviderModelSelectionRequest(string ModelId, int? ContextLengthOverride = null);

public sealed record AiProfileVersionRequest(
    string DisplayName, string ProviderId, string ModelId,
    string ReasoningEffort = "low", int? ContextLength = null,
    string? Notes = null, string? StableProfileId = null);
public sealed record AiProfileVersion(
    string ProfileVersionId, string StableProfileId, int Version,
    string DisplayName, string AgentId, string ProviderId, string ModelId,
    string ReasoningEffort, int? ContextLength, string? Notes,
    bool Active, string PersonalityCardId, DateTimeOffset CreatedAt);

public sealed record GameSourceRequest(string DisplayName, string HostPath);
public sealed record RuntimeImportRequest(string DisplayName, string SourceHostPath);
public sealed record OperationActionRequest(string Action, string? TargetId = null);
public sealed record ScheduleRequest(
    string DisplayName, string OperationKind, string TargetKind,
    string? TargetId, int IntervalSeconds);
public sealed record BackupRequest(bool IncludeSecrets = true, bool IncludeWorkers = true);

public sealed record AdminSnapshot(
    System.Text.Json.JsonElement Providers,
    System.Text.Json.JsonElement Agents,
    System.Text.Json.JsonElement HarnessProfiles,
    System.Text.Json.JsonElement HarnessRuns,
    System.Text.Json.JsonElement Graphiti,
    System.Text.Json.JsonElement Workers,
    System.Text.Json.JsonElement Operations,
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
