using System.Net;
using System.Net.Http.Headers;
using System.Security.Claims;
using System.Text;
using Microsoft.EntityFrameworkCore;
using Yarp.ReverseProxy.Forwarder;

namespace Smacx.Portal.Services;

public sealed class StreamProxyService(
    IHttpForwarder forwarder,
    HttpMessageInvoker streamHttpClient,
    Data.ApplicationDbContext database,
    ControlPlaneClient control,
    StreamPresenceTracker presence,
    ControllerLeaseService controllerLeases,
    MatchAccessService matchAccess,
    AccountConnectionRegistry accountConnections,
    ILogger<StreamProxyService> logger)
{
    private static readonly ForwarderRequestConfig RequestConfig = new()
    {
        ActivityTimeout = TimeSpan.FromMinutes(30),
    };

    public async Task ForwardAsync(HttpContext context)
    {
        var instanceId = context.Request.RouteValues["instanceId"]?.ToString();
        if (string.IsNullOrWhiteSpace(instanceId))
        {
            context.Response.StatusCode = StatusCodes.Status404NotFound;
            return;
        }

        var seat = await database.PortalLobbySeats.AsNoTracking()
            .SingleOrDefaultAsync(item => item.ControlInstanceId == instanceId, context.RequestAborted);
        if (seat is null)
        {
            context.Response.StatusCode = StatusCodes.Status404NotFound;
            return;
        }
        var match = await database.PortalMatches.AsNoTracking()
            .SingleAsync(item => item.MatchId == seat.MatchId, context.RequestAborted);
        var userId = context.User.FindFirstValue(ClaimTypes.NameIdentifier);
        var ownsSeat = userId is not null && seat.UserId == userId;
        var administrator = context.User.IsInRole(Infrastructure.PortalRoles.Administrator);
        var mayView = ownsSeat || await matchAccess.CanSpectateAsync(
            match, userId, administrator, context.RequestAborted);
        if (!mayView)
        {
            context.Response.StatusCode = context.User.Identity?.IsAuthenticated == true
                ? StatusCodes.Status403Forbidden
                : StatusCodes.Status401Unauthorized;
            return;
        }

        // Only the human assigned to this exact browser seat receives input.
        // Non-participating administrators and authenticated spectators always get Selkies' enforced
        // view-only credential, even though administrators may inspect any seat.
        var forceViewOnly = context.Request.Query.TryGetValue("view", out var view)
            && view.Count > 0 && view[0] == "1";
        var leaseId = context.Request.Query.TryGetValue("lease", out var leaseValues)
            ? leaseValues.FirstOrDefault() : null;
        var controllerRevoked = CancellationToken.None;
        var hasControllerLease = userId is not null &&
            controllerLeases.TryGetControllerCancellation(
                instanceId, userId, leaseId, out controllerRevoked);
        var interactive = !forceViewOnly && ownsSeat
            && seat.ControllerKind == "human"
            && seat.JoinMode == "browser"
            && hasControllerLease;
        using var presenceLease = interactive ? presence.Enter(instanceId) : null;
        var accountRevoked = userId is null ? CancellationToken.None : accountConnections.Token(userId);
        using var linkedCancellation = CancellationTokenSource.CreateLinkedTokenSource(
            context.RequestAborted, controllerRevoked, accountRevoked);
        context.RequestAborted = linkedCancellation.Token;
        ControlStreamAccess access;
        try
        {
            access = await control.GetStreamAccessAsync(instanceId, interactive, context.RequestAborted);
        }
        catch (OperationCanceledException) when (accountRevoked.IsCancellationRequested)
        {
            return;
        }
        catch (ControlPlaneException exception)
        {
            logger.LogWarning(exception, "Stream access failed for {InstanceId}", instanceId);
            context.Response.StatusCode = exception.StatusCode ?? StatusCodes.Status502BadGateway;
            await context.Response.WriteAsJsonAsync(new
            {
                ok = false,
                error = new { code = exception.Code, message = exception.Message },
            }, context.RequestAborted);
            return;
        }

        var transformer = new StreamTransformer(access.Password);
        ForwarderError error;
        try
        {
            error = await forwarder.SendAsync(
                context, access.InternalBaseUrl, streamHttpClient, RequestConfig, transformer);
        }
        catch (OperationCanceledException) when (
            interactive && controllerRevoked.IsCancellationRequested)
        {
            logger.LogInformation(
                "Closed superseded controller stream for {InstanceId}", instanceId);
            return;
        }
        catch (OperationCanceledException) when (accountRevoked.IsCancellationRequested)
        {
            logger.LogInformation("Closed stream for revoked account {UserId}", userId);
            return;
        }
        if (interactive && controllerRevoked.IsCancellationRequested)
        {
            logger.LogInformation(
                "Closed superseded controller stream for {InstanceId}", instanceId);
            return;
        }
        if (error != ForwarderError.None)
        {
            var feature = context.GetForwarderErrorFeature();
            logger.LogWarning(feature?.Exception,
                "Stream proxy failed for {InstanceId}: {ForwarderError}", instanceId, error);
        }
    }

    private sealed class StreamTransformer(string password) : HttpTransformer
    {
        private readonly AuthenticationHeaderValue authorization = new(
            "Basic", Convert.ToBase64String(Encoding.UTF8.GetBytes($"smacx:{password}")));

        public override async ValueTask TransformRequestAsync(
            HttpContext httpContext,
            HttpRequestMessage proxyRequest,
            string destinationPrefix,
            CancellationToken cancellationToken)
        {
            await base.TransformRequestAsync(
                httpContext, proxyRequest, destinationPrefix, cancellationToken);
            proxyRequest.Headers.Remove("Cookie");
            proxyRequest.Headers.Authorization = authorization;
            proxyRequest.Headers.Host = null;
            // Selkies rejects cross-origin WebSocket upgrades. The public
            // portal origin is trustworthy, but the worker correctly sees a
            // different internal host after proxying, so normalize Origin to
            // the destination authority for the upstream handshake.
            if (proxyRequest.Headers.Contains("Origin"))
            {
                var destination = new Uri(destinationPrefix, UriKind.Absolute);
                proxyRequest.Headers.Remove("Origin");
                proxyRequest.Headers.TryAddWithoutValidation(
                    "Origin", destination.GetLeftPart(UriPartial.Authority));
            }
        }
    }
}
