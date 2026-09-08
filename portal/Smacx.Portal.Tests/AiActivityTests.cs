using System.Net;
using System.Security.Claims;
using System.Text.Json;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Smacx.Portal.Controllers;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class AiActivityTests
{
    [Fact]
    public async Task ExportStreamsAllPagesAndParticipantCannotReadOrDownload()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();
        await using var db = new ApplicationDbContext(new DbContextOptionsBuilder<ApplicationDbContext>().UseSqlite(connection).Options);
        await db.Database.EnsureCreatedAsync();
        db.Users.Add(new ApplicationUser { Id="viewer", UserName="viewer", DisplayName="Viewer", NormalizedDisplayName="VIEWER", GameHandle="Viewer", NormalizedGameHandle="VIEWER" });
        db.PortalMatches.Add(new PortalMatchProfile { MatchId="match-export", OwnerUserId="viewer", DisplayName="Export", Status="completed" });
        db.PortalLobbySeats.Add(new PortalLobbySeat { MatchId="match-export", SeatIndex=0, ControllerKind="agent", AgentId="agent-export" });
        await db.SaveChangesAsync();
        var handler = new Pages();
        await using var tokenFile = new FileStream(Path.GetTempFileName(), FileMode.Open,
            FileAccess.ReadWrite, FileShare.ReadWrite, 4096, FileOptions.DeleteOnClose);
        await tokenFile.WriteAsync("test-service-token"u8.ToArray()); await tokenFile.FlushAsync();
        var control = new ControlPlaneClient(new HttpClient(handler) { BaseAddress=new Uri("http://control/") },
            Options.Create(new ControlPlaneOptions { ServiceTokenFile=tokenFile.Name }), NullLogger<ControlPlaneClient>.Instance);
        var context = new DefaultHttpContext { User=new ClaimsPrincipal(new ClaimsIdentity([new Claim(ClaimTypes.NameIdentifier,"viewer")],"test")) };
        context.Response.Body = new MemoryStream();
        var controller = new AiActivityController(db,new MatchAccessService(db),control) { ControllerContext=new ControllerContext { HttpContext=context } };
        Assert.IsType<NotFoundResult>((await controller.Read("match-export",9)).Result);
        Assert.Equal(0,handler.Requests);
        var live = await controller.Read("match-export",0);
        Assert.True(live.Value!.Ok);
        Assert.Equal(1, live.Value.Data.GetProperty("events").GetArrayLength());
        handler.Calls = 0;
        Assert.IsType<EmptyResult>(await controller.Download("match-export"));
        context.Response.Body.Position=0;
        using var exported=await JsonDocument.ParseAsync(context.Response.Body);
        Assert.Equal(2,exported.RootElement.GetProperty("seats")[0].GetProperty("events").GetArrayLength());
        Assert.Equal(2,handler.Calls);
        handler.MissingRuntimeSeat = true;
        var missing = Assert.IsType<ConflictObjectResult>((await controller.Read("match-export",0)).Result);
        Assert.Contains("activity_runtime_agent_unavailable",JsonSerializer.Serialize(missing.Value));
        Assert.Equal(2,handler.Calls); // Never falls back to the profile identity.
        var requests = handler.Requests;
        db.PortalMatchParticipants.Add(new PortalMatchParticipant { MatchId="match-export",UserId="viewer",FirstSeatIndex=1 });
        await db.SaveChangesAsync();
        Assert.IsType<ForbidResult>((await controller.Read("match-export",0)).Result);
        Assert.IsType<ForbidResult>(await controller.Download("match-export"));
        Assert.Equal(2,handler.Calls);
        Assert.Equal(requests,handler.Requests);
    }
    private sealed class Pages : HttpMessageHandler
    {
        public int Calls;
        public int Requests;
        public bool MissingRuntimeSeat;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
        {
            Requests++;
            if (request.RequestUri!.AbsolutePath == "/api/v1/matches/match-export")
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = JsonContent(new {
                    ok=true, match=new { match_id="match-export",display_name="Export",mode="standard",status="completed",created_unix=0,updated_unix=0 },
                    seats=MissingRuntimeSeat ? Array.Empty<object>() : new object[] {
                        new {seat_index=1,controller_kind="agent",agent_id="other-seat-agent",status="assigned"},
                        new {seat_index=0,controller_kind="agent",agent_id="match-sovereign-agent",status="assigned"} }
                }) });
            Calls++;
            Assert.Contains("/activity/match-sovereign-agent",request.RequestUri!.AbsolutePath);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content=new StringContent(JsonSerializer.Serialize(new {
                ok=true, report=new { events=new[]{new{event_id=$"event-{Calls}"}}, cursor="next", has_more=Calls==1, gaps=Array.Empty<string>() }
            })) });
        }
        private static StringContent JsonContent(object value) => new(JsonSerializer.Serialize(value));
    }
}
