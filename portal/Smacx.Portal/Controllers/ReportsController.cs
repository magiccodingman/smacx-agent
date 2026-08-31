using System.Security.Claims;
using System.Text;
using System.Text.RegularExpressions;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/reports")]
[Authorize]
public sealed partial class ReportsController(ApplicationDbContext database) : ControllerBase
{
    [HttpGet("history")]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<MatchHistoryItem>>>> History()
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)!;
        var admin = User.IsInRole("Administrator");
        var memberIds = database.PortalMatchMembers.AsNoTracking()
            .Where(item => item.UserId == userId).Select(item => item.MatchId);
        var items = await database.PortalMatches.AsNoTracking()
            .Where(item => admin || memberIds.Contains(item.MatchId))
            .OrderByDescending(item => item.UpdatedAt)
            .Select(item => new MatchHistoryItem(
                item.MatchId, item.DisplayName, item.Status, item.Mode,
                item.CurrentTurn, item.CurrentYear, item.RankingMode,
                item.Status == "parked" || item.Status == "error",
                item.CreatedAt, item.UpdatedAt))
            .ToArrayAsync(HttpContext.RequestAborted);
        return ApiResponse<IReadOnlyList<MatchHistoryItem>>.Success(items);
    }

    [HttpGet("history/page")]
    public async Task<ActionResult<ApiResponse<MatchHistoryPage>>> HistoryPage(
        [FromQuery] int offset = 0, [FromQuery] int limit = 24,
        [FromQuery] string status = "all", [FromQuery] string? query = null)
    {
        offset = Math.Max(0, offset);
        limit = Math.Clamp(limit, 1, 100);
        if (status is not ("all" or "active" or "parked" or "completed"))
            return BadRequest(ApiResponse<MatchHistoryPage>.Failure(
                "invalid_history_filter", "Choose all, active, parked, or completed."));
        query = query?.Trim();
        if (query?.Length > 160)
            return BadRequest(ApiResponse<MatchHistoryPage>.Failure(
                "history_query_too_long", "Search text must be 160 characters or fewer."));

        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)!;
        var admin = User.IsInRole("Administrator");
        var memberIds = database.PortalMatchMembers.AsNoTracking()
            .Where(item => item.UserId == userId).Select(item => item.MatchId);
        var visible = database.PortalMatches.AsNoTracking()
            .Where(item => admin || memberIds.Contains(item.MatchId));
        var total = await visible.CountAsync(HttpContext.RequestAborted);
        var active = await visible.CountAsync(item => item.Status == "waiting" ||
            item.Status == "provisioning" || item.Status == "starting" ||
            item.Status == "lobby" || item.Status == "running" ||
            item.Status == "recovering" || item.Status == "parking",
            HttpContext.RequestAborted);
        var recoverable = await visible.CountAsync(item => item.Status == "parked" ||
            item.Status == "error",
            HttpContext.RequestAborted);
        var completed = await visible.CountAsync(item => item.Status == "completed",
            HttpContext.RequestAborted);
        var filtered = visible;
        if (!string.IsNullOrWhiteSpace(query))
            filtered = filtered.Where(item => item.DisplayName.Contains(query));
        filtered = status switch
        {
            "active" => filtered.Where(item => item.Status == "waiting" ||
                item.Status == "provisioning" || item.Status == "starting" ||
                item.Status == "lobby" || item.Status == "running" ||
                item.Status == "recovering" || item.Status == "parking"),
            "parked" => filtered.Where(item => item.Status == "parked" || item.Status == "error"),
            "completed" => filtered.Where(item => item.Status == "completed"),
            _ => filtered,
        };
        var filteredTotal = await filtered.CountAsync(HttpContext.RequestAborted);
        var items = await filtered.OrderByDescending(item => item.UpdatedAt)
            .Skip(offset).Take(limit)
            .Select(item => new MatchHistoryItem(
                item.MatchId, item.DisplayName, item.Status, item.Mode,
                item.CurrentTurn, item.CurrentYear, item.RankingMode,
                item.Status == "parked" || item.Status == "error",
                item.CreatedAt, item.UpdatedAt))
            .ToArrayAsync(HttpContext.RequestAborted);
        return ApiResponse<MatchHistoryPage>.Success(new(
            items, total, filteredTotal, active, recoverable, completed, offset, limit));
    }

    [HttpGet("analytics")]
    public async Task<ActionResult<ApiResponse<AnalyticsSummary>>> Analytics()
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)!;
        var admin = User.IsInRole("Administrator");
        var memberIds = database.PortalMatchMembers.AsNoTracking()
            .Where(item => item.UserId == userId).Select(item => item.MatchId);
        var matches = await database.PortalMatches.AsNoTracking()
            .Where(item => admin || memberIds.Contains(item.MatchId))
            .ToArrayAsync(HttpContext.RequestAborted);
        var visibleIds = matches.Select(item => item.MatchId).ToHashSet(StringComparer.Ordinal);
        var turns = await database.PortalTurnMetrics.AsNoTracking()
            .Where(item => visibleIds.Contains(item.MatchId))
            .ToArrayAsync(HttpContext.RequestAborted);
        var profiles = await database.PortalAiProfileVersions.AsNoTracking().ToArrayAsync(HttpContext.RequestAborted);
        var profileSeats = await database.PortalLobbySeats.AsNoTracking()
            .Where(item => item.AiProfileVersionId != null && visibleIds.Contains(item.MatchId))
            .ToArrayAsync(HttpContext.RequestAborted);
        var completed = matches.Count(item => item.Status == "completed");
        var recoverable = matches.Count(item => item.Status is "parked" or "error");
        var recovered = await database.PortalMatchEvents.AsNoTracking().CountAsync(
            item => visibleIds.Contains(item.MatchId) && item.EventType == "recover",
            HttpContext.RequestAborted);
        var recoveryAttempts = recovered + await database.PortalMatchEvents.AsNoTracking().CountAsync(
            item => visibleIds.Contains(item.MatchId) && item.EventType == "recovery_failed",
            HttpContext.RequestAborted);
        var rows = profiles.Select(profile =>
        {
            var profileTurns = turns.Where(item => item.ProfileVersionId == profile.ProfileVersionId).ToArray();
            var seats = profileSeats.Where(item => item.AiProfileVersionId == profile.ProfileVersionId).ToArray();
            var profileMatches = seats.Select(item => item.MatchId)
                .Concat(profileTurns.Select(item => item.MatchId)).Distinct().Count();
            var outcomes = seats.Where(item => item.OutcomeFinalized &&
                item.OutcomeResult is "win" or "loss").ToArray();
            var wins = outcomes.Count(item => item.OutcomeResult == "win");
            return new AnalyticsProfileRow(
                $"{profile.DisplayName} v{profile.Version}", profile.ProviderId, profile.ModelId,
                profile.ReasoningEffort, GenerationPreset(profile.GenerationSettingsJson),
                profileMatches, outcomes.Length, wins,
                outcomes.Length == 0 ? null : (double)wins / outcomes.Length,
                Median(profileTurns.Where(item => !item.Errored).Select(item => item.DurationSeconds)),
                profileTurns.Sum(item => item.PromptTokens), profileTurns.Sum(item => item.CompletionTokens),
                profileTurns.Sum(item => item.CacheReadTokens), profileTurns.Sum(item => item.CacheWriteTokens),
                profileTurns.Sum(item => item.ReasoningTokens), profileTurns.Sum(item => item.ApiCalls));
        }).ToArray();
        return ApiResponse<AnalyticsSummary>.Success(new(
            completed, matches.Count(item => item.Status == "running"), recoverable,
            turns.Count(item => !item.Errored),
            Median(turns.Where(item => !item.Errored).Select(item => item.DurationSeconds)),
            turns.Sum(item => item.PromptTokens), turns.Sum(item => item.CompletionTokens),
            turns.Sum(item => item.CacheReadTokens), turns.Sum(item => item.CacheWriteTokens),
            turns.Sum(item => item.ReasoningTokens), turns.Sum(item => item.ApiCalls),
            recoveryAttempts == 0 ? null : (double)recovered / recoveryAttempts, rows));
    }

    [HttpGet("analytics.csv")]
    public async Task<IActionResult> AnalyticsCsv()
    {
        var result = (await Analytics()).Value?.Data;
        var text = new StringBuilder("profile,provider,model,reasoning,generation_preset,matches,classified_outcomes,wins,win_rate,median_turn_seconds,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,reasoning_tokens,api_calls\n");
        foreach (var row in result?.Profiles ?? [])
            text.AppendLine(string.Join(',', new[]
            {
                Csv(row.ProfileName), Csv(row.Provider), Csv(row.Model), Csv(row.ReasoningEffort),
                Csv(row.GenerationPreset),
                row.Matches.ToString(), row.ClassifiedOutcomes.ToString(), row.Wins.ToString(),
                row.WinRate?.ToString("0.####") ?? "",
                row.MedianTurnSeconds?.ToString("0.###") ?? "", row.PromptTokens.ToString(),
                row.CompletionTokens.ToString(), row.CacheReadTokens.ToString(),
                row.CacheWriteTokens.ToString(), row.ReasoningTokens.ToString(),
                row.ApiCalls.ToString(),
            }));
        return File(Encoding.UTF8.GetBytes(text.ToString()), "text/csv", "smacx-analytics.csv");
    }

    [HttpPost("query")]
    [Authorize(Roles = "Administrator")]
    public async Task<ActionResult<ApiResponse<AnalyticsQueryResult>>> Query(AnalyticsQueryRequest request)
    {
        var sql = request.Sql.Trim();
        if (sql.Length is < 1 or > 8000 || !SafeSelect().IsMatch(sql) || sql.Contains(';') ||
            ForbiddenSql().IsMatch(sql))
            return BadRequest(ApiResponse<AnalyticsQueryResult>.Failure(
                "unsafe_report_query", "Use one read-only SELECT against matches, turn_metrics, or ai_profiles."));
        await using var report = new SqliteConnection("Data Source=:memory:");
        await report.OpenAsync(HttpContext.RequestAborted);
        await ExecuteAsync(report, "CREATE TABLE matches(match_id TEXT, display_name TEXT, status TEXT, mode TEXT, current_turn INTEGER, current_year INTEGER, ranking_mode TEXT, created_at TEXT, updated_at TEXT); CREATE TABLE turn_metrics(match_id TEXT, agent_id TEXT, profile_version_id TEXT, turn INTEGER, duration_seconds REAL, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, api_calls INTEGER, errored INTEGER); CREATE TABLE ai_profiles(profile_version_id TEXT, stable_profile_id TEXT, version INTEGER, display_name TEXT, provider_id TEXT, model_id TEXT, reasoning_effort TEXT, generation_preset TEXT, generation_settings_json TEXT, context_length INTEGER, active INTEGER); CREATE TABLE ai_outcomes(match_id TEXT, seat_index INTEGER, profile_version_id TEXT, result TEXT, victory_type TEXT, finalized INTEGER);");
        foreach (var item in await database.PortalMatches.AsNoTracking().ToArrayAsync(HttpContext.RequestAborted))
            await InsertAsync(report, "INSERT INTO matches VALUES($a,$b,$c,$d,$e,$f,$g,$h,$i)", item.MatchId,item.DisplayName,item.Status,item.Mode,item.CurrentTurn,item.CurrentYear,item.RankingMode,item.CreatedAt.ToString("O"),item.UpdatedAt.ToString("O"));
        foreach (var item in await database.PortalTurnMetrics.AsNoTracking().ToArrayAsync(HttpContext.RequestAborted))
            await InsertAsync(report, "INSERT INTO turn_metrics VALUES($a,$b,$c,$d,$e,$f,$g,$h,$i,$j,$k,$l)", item.MatchId,item.AgentId,item.ProfileVersionId,item.Turn,item.DurationSeconds,item.PromptTokens,item.CompletionTokens,item.CacheReadTokens,item.CacheWriteTokens,item.ReasoningTokens,item.ApiCalls,item.Errored?1:0);
        foreach (var item in await database.PortalAiProfileVersions.AsNoTracking().ToArrayAsync(HttpContext.RequestAborted))
            await InsertAsync(report, "INSERT INTO ai_profiles VALUES($a,$b,$c,$d,$e,$f,$g,$h,$i,$j,$k)", item.ProfileVersionId,item.StableProfileId,item.Version,item.DisplayName,item.ProviderId,item.ModelId,item.ReasoningEffort,GenerationPreset(item.GenerationSettingsJson),item.GenerationSettingsJson,item.ContextLength,item.Active?1:0);
        foreach (var item in await database.PortalLobbySeats.AsNoTracking().Where(item => item.AiProfileVersionId != null).ToArrayAsync(HttpContext.RequestAborted))
            await InsertAsync(report, "INSERT INTO ai_outcomes VALUES($a,$b,$c,$d,$e,$f)", item.MatchId,item.SeatIndex,item.AiProfileVersionId,item.OutcomeResult,item.VictoryType,item.OutcomeFinalized?1:0);
        await using var command = report.CreateCommand(); command.CommandText = sql; command.CommandTimeout = 5;
        var rows = new List<IReadOnlyList<object?>>(); var columns = new List<string>();
        await using var reader = await command.ExecuteReaderAsync(HttpContext.RequestAborted);
        for(var i=0;i<reader.FieldCount;i++) columns.Add(reader.GetName(i));
        while(rows.Count<1001 && await reader.ReadAsync(HttpContext.RequestAborted))
            rows.Add(Enumerable.Range(0,reader.FieldCount).Select(i=>reader.IsDBNull(i)?null:reader.GetValue(i)).ToArray());
        var truncated=rows.Count>1000;if(truncated)rows.RemoveAt(1000);
        return ApiResponse<AnalyticsQueryResult>.Success(new(columns,rows,truncated));
    }

    private static double? Median(IEnumerable<double?> values)
    {
        var ordered=values.Where(value=>value.HasValue).Select(value=>value!.Value).Order().ToArray();
        if(ordered.Length==0)return null;var middle=ordered.Length/2;
        return ordered.Length%2==0?(ordered[middle-1]+ordered[middle])/2:ordered[middle];
    }
    private static string Csv(string value)=>$"\"{value.Replace("\"","\"\"")}\"";
    private static string GenerationPreset(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return "provider-default";
        try
        {
            using var document = JsonDocument.Parse(json);
            foreach (var name in new[] { "Preset", "preset" })
                if (document.RootElement.TryGetProperty(name, out var value) &&
                    !string.IsNullOrWhiteSpace(value.GetString()))
                    return value.GetString()!;
        }
        catch (JsonException) { }
        return "provider-default";
    }
    private static async Task ExecuteAsync(SqliteConnection connection,string sql){await using var command=connection.CreateCommand();command.CommandText=sql;await command.ExecuteNonQueryAsync();}
    private static async Task InsertAsync(SqliteConnection connection,string sql,params object?[] values){await using var command=connection.CreateCommand();command.CommandText=sql;for(var i=0;i<values.Length;i++)command.Parameters.AddWithValue($"${(char)('a'+i)}",values[i]??DBNull.Value);await command.ExecuteNonQueryAsync();}
    [GeneratedRegex("^select\\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant)] private static partial Regex SafeSelect();
    [GeneratedRegex("\\b(pragma|attach|detach|insert|update|delete|replace|create|alter|drop|vacuum|reindex|identity|aspnet|portalsettings|password)\\b",RegexOptions.IgnoreCase|RegexOptions.CultureInvariant)] private static partial Regex ForbiddenSql();
}
