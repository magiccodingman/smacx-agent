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
        Assert.Equal(0,handler.Calls);
        Assert.IsType<EmptyResult>(await controller.Download("match-export"));
        context.Response.Body.Position=0;
        using var exported=await JsonDocument.ParseAsync(context.Response.Body);
        Assert.Equal(2,exported.RootElement.GetProperty("seats")[0].GetProperty("events").GetArrayLength());
        Assert.Equal(2,handler.Calls);
        db.PortalMatchParticipants.Add(new PortalMatchParticipant { MatchId="match-export",UserId="viewer",FirstSeatIndex=1 });
        await db.SaveChangesAsync();
        Assert.IsType<ForbidResult>((await controller.Read("match-export",0)).Result);
        Assert.IsType<ForbidResult>(await controller.Download("match-export"));
        Assert.Equal(2,handler.Calls);
    }
    private sealed class Pages : HttpMessageHandler
    {
        public int Calls;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
        {
            Calls++;
            Assert.Contains("/activity/agent-export",request.RequestUri!.AbsolutePath);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content=new StringContent(JsonSerializer.Serialize(new {
                ok=true, report=new { events=new[]{new{event_id=$"event-{Calls}"}}, cursor="next", has_more=Calls==1, gaps=Array.Empty<string>() }
            })) });
        }
    }
}
