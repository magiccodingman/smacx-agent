using System.Diagnostics;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using MudBlazor.Services;
using Smacx.Portal.Client.Pages;
using Smacx.Portal.Client.Services;
using Smacx.Portal.Components;
using Smacx.Portal.Components.Account;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Hubs;
using Smacx.Portal.Infrastructure;
using Smacx.Portal.Services;
using Yarp.ReverseProxy.Forwarder;
using System.Threading.RateLimiting;

var builder = WebApplication.CreateBuilder(args);
var cliMode = args.Length >= 1 && args[0] is "bootstrap-token" or "admin-reset-token";
if (cliMode)
{
    builder.Logging.ClearProviders();
}
const int defaultPasswordMinimumLength = 8;
var passwordMinimumLength = defaultPasswordMinimumLength;
var configuredPasswordMinimumLength = Environment.GetEnvironmentVariable("SMACX_PASSWORD_MIN_LENGTH");
if (!string.IsNullOrWhiteSpace(configuredPasswordMinimumLength) &&
    (!int.TryParse(configuredPasswordMinimumLength, out passwordMinimumLength) ||
     passwordMinimumLength is < 8 or > 128))
{
    throw new InvalidOperationException(
        "SMACX_PASSWORD_MIN_LENGTH must be an integer between 8 and 128.");
}

// Add services to the container.
builder.Services.AddRazorComponents()
    .AddInteractiveServerComponents()
    .AddInteractiveWebAssemblyComponents()
    .AddAuthenticationStateSerialization(options => options.SerializeAllClaims = true);
builder.Services.AddMudServices();
builder.Services.AddMemoryCache();
builder.Services.AddControllersWithViews(options =>
    options.Filters.Add(new AutoValidateAntiforgeryTokenAttribute()));
builder.Services.AddAntiforgery(options =>
{
    options.HeaderName = "X-CSRF-TOKEN";
    options.Cookie.Name = "smacx.portal.csrf";
    options.Cookie.HttpOnly = true;
    options.Cookie.SameSite = SameSiteMode.Strict;
    options.Cookie.SecurePolicy = CookieSecurePolicy.SameAsRequest;
});
builder.Services.AddSignalR();
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    options.OnRejected = async (context, cancellationToken) =>
    {
        await context.HttpContext.Response.WriteAsJsonAsync(
            ApiResponse<bool>.Failure("rate_limited",
                "Too many access attempts were received. Wait a few minutes and try again."),
            cancellationToken);
    };
    options.AddPolicy("authentication", context => RateLimitPartition.GetFixedWindowLimiter(
        context.Connection.RemoteIpAddress?.ToString() ?? "unknown",
        _ => new FixedWindowRateLimiterOptions
        {
            PermitLimit = 20, Window = TimeSpan.FromMinutes(5), QueueLimit = 0,
        }));
    options.AddPolicy("invitation", context => RateLimitPartition.GetFixedWindowLimiter(
        context.Connection.RemoteIpAddress?.ToString() ?? "unknown",
        _ => new FixedWindowRateLimiterOptions
        {
            PermitLimit = 10, Window = TimeSpan.FromMinutes(5), QueueLimit = 0,
        }));
});
builder.Services.AddHttpForwarder();
builder.Services.AddSingleton(new HttpMessageInvoker(new SocketsHttpHandler
{
    UseProxy = false,
    AllowAutoRedirect = false,
    AutomaticDecompression = DecompressionMethods.None,
    UseCookies = false,
    ActivityHeadersPropagator = new ReverseProxyPropagator(DistributedContextPropagator.Current),
    ConnectTimeout = TimeSpan.FromSeconds(10),
}));
builder.Services.AddScoped<StreamProxyService>();
builder.Services.AddSingleton<StreamPresenceTracker>();
builder.Services.AddSingleton<WaitingLobbyPolicy>();
builder.Services.AddSingleton<WaitingLobbyPresenceTracker>();
builder.Services.AddSingleton<RequestNetworkClassifier>();
builder.Services.AddScoped<PortalAccessPolicy>();
builder.Services.AddScoped<MatchAccessService>();
builder.Services.AddSingleton<AccountConnectionRegistry>();
builder.Services.AddSingleton<PortalSecurityTicketService>();
builder.Services.AddSingleton<InstallationFingerprintCatalog>();
builder.Services.AddSingleton<AnalyticsScriptProvider>();
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton<ControllerLeaseService>();
builder.Services.AddScoped<MatchGovernanceService>();
builder.Services.AddSingleton<PersonalityCardLibrary>();
builder.Services.AddSingleton<DatalinksMarkdownRenderer>();
builder.Services.AddHostedService<PortalMatchSupervisor>();
builder.Services.AddHostedService<PortalMaintenanceCoordinator>();
builder.Services.AddHostedService<GraphitiProfileReconciler>();
builder.Services.AddHttpContextAccessor();
builder.Services.AddScoped(serviceProvider =>
{
    var request = serviceProvider.GetRequiredService<IHttpContextAccessor>().HttpContext?.Request;
    var baseAddress = request is null
        ? "http://127.0.0.1:5108/"
        : $"{request.Scheme}://{request.Host}{request.PathBase}/";
    return new HttpClient { BaseAddress = new Uri(baseAddress) };
});
builder.Services.AddScoped<PortalApiClient>();

builder.Services.AddCascadingAuthenticationState();
builder.Services.AddScoped<AuthenticationStateProvider, IdentityRevalidatingAuthenticationStateProvider>();

builder.Services.AddAuthentication(options =>
    {
        options.DefaultScheme = IdentityConstants.ApplicationScheme;
        options.DefaultSignInScheme = IdentityConstants.ExternalScheme;
    })
    .AddIdentityCookies(options =>
    {
        options.ApplicationCookie!.Configure(cookie =>
        {
            cookie.LoginPath = "/login";
            cookie.AccessDeniedPath = "/access-denied";
            cookie.Cookie.Name = "smacx.portal.session";
            cookie.Cookie.HttpOnly = true;
            cookie.Cookie.SameSite = SameSiteMode.Strict;
            cookie.Cookie.SecurePolicy = CookieSecurePolicy.SameAsRequest;
            cookie.SlidingExpiration = true;
            cookie.ExpireTimeSpan = TimeSpan.FromHours(12);
            cookie.Events.OnRedirectToLogin = context => ApiStatus(context, StatusCodes.Status401Unauthorized);
            cookie.Events.OnRedirectToAccessDenied = context => ApiStatus(context, StatusCodes.Status403Forbidden);
            cookie.Events.OnValidatePrincipal = async context =>
            {
                var userId = context.Principal?.FindFirst(
                    System.Security.Claims.ClaimTypes.NameIdentifier)?.Value;
                if (userId is null) return;
                var database = context.HttpContext.RequestServices
                    .GetRequiredService<ApplicationDbContext>();
                var user = await database.Users.AsNoTracking().SingleOrDefaultAsync(
                    item => item.Id == userId, context.HttpContext.RequestAborted);
                var access = context.HttpContext.RequestServices
                    .GetRequiredService<PortalAccessPolicy>();
                var remote = access.Zone(context.HttpContext) == PortalRequestZone.Remote;
                if (user is null || !user.IsActive ||
                    remote && user.IsPrimaryAdministrator &&
                        !access.PrimaryAdministratorRemoteLoginAllowed ||
                    remote && !user.IsPrimaryAdministrator &&
                        user.InstallationVerifiedAt is null)
                {
                    context.RejectPrincipal();
                    await context.HttpContext.SignOutAsync(IdentityConstants.ApplicationScheme);
                }
            };
        });
    });

builder.Services.Configure<ForwardedHeadersOptions>(options =>
{
    options.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto;
    options.ForwardLimit = 1;
    options.KnownIPNetworks.Clear();
    options.KnownProxies.Clear();
    var configured = Environment.GetEnvironmentVariable("SMACX_TRUSTED_PROXY_NETWORKS")
        ?? "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7";
    foreach (var value in configured.Split(',',
                 StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries))
    {
        var parts = value.Split('/', 2);
        if (!IPAddress.TryParse(parts[0], out var address) || parts.Length != 2 ||
            !int.TryParse(parts[1], out var prefix))
            throw new InvalidOperationException(
                $"Invalid SMACX_TRUSTED_PROXY_NETWORKS entry '{value}'.");
        options.KnownIPNetworks.Add(new System.Net.IPNetwork(address, prefix));
    }
});

var storage = builder.Configuration.GetSection(PortalStorageOptions.SectionName)
    .Get<PortalStorageOptions>() ?? new PortalStorageOptions();
var configuredDataRoot = Environment.GetEnvironmentVariable("SMACX_PORTAL_DATA");
if (!string.IsNullOrWhiteSpace(configuredDataRoot))
{
    storage.DataRoot = configuredDataRoot;
}
storage.DataRoot = Path.GetFullPath(storage.DataRoot, builder.Environment.ContentRootPath);
Directory.CreateDirectory(storage.DataRoot);
var dataProtectionRoot = Path.Combine(storage.DataRoot, "data-protection");
Directory.CreateDirectory(dataProtectionRoot);
builder.Services.AddDataProtection()
    .PersistKeysToFileSystem(new DirectoryInfo(dataProtectionRoot))
    .SetApplicationName("Smacx.Portal");
builder.Services.Configure<PortalStorageOptions>(options => options.DataRoot = storage.DataRoot);

var connectionString = new Microsoft.Data.Sqlite.SqliteConnectionStringBuilder
{
    DataSource = Path.Combine(storage.DataRoot, "portal.sqlite3"),
    Cache = Microsoft.Data.Sqlite.SqliteCacheMode.Shared,
    Mode = Microsoft.Data.Sqlite.SqliteOpenMode.ReadWriteCreate,
}.ToString();
builder.Services.AddDbContext<ApplicationDbContext>(options =>
    options.UseSqlite(connectionString));

builder.Services.AddIdentityCore<ApplicationUser>(options =>
    {
        options.SignIn.RequireConfirmedAccount = false;
        options.Password.RequiredLength = passwordMinimumLength;
        options.Password.RequireDigit = true;
        options.Password.RequireLowercase = true;
        options.Password.RequireUppercase = true;
        options.Password.RequireNonAlphanumeric = false;
        options.Lockout.MaxFailedAccessAttempts = 8;
        options.Lockout.DefaultLockoutTimeSpan = TimeSpan.FromMinutes(5);
        options.User.RequireUniqueEmail = false;
    })
    .AddRoles<IdentityRole>()
    .AddEntityFrameworkStores<ApplicationDbContext>()
    .AddSignInManager()
    .AddDefaultTokenProviders();

builder.Services.AddScoped<BootstrapTokenStore>();
builder.Services.AddSingleton<PortalDatabaseInitializer>();

var controlOptions = builder.Configuration.GetSection(ControlPlaneOptions.SectionName)
    .Get<ControlPlaneOptions>() ?? new ControlPlaneOptions();
controlOptions.BaseUrl = Environment.GetEnvironmentVariable("SMACX_CONTROL_URL") ?? controlOptions.BaseUrl;
controlOptions.ServiceTokenFile = Environment.GetEnvironmentVariable("SMACX_PORTAL_SERVICE_TOKEN_FILE")
    ?? controlOptions.ServiceTokenFile;
controlOptions.ServiceTokenFile = Path.GetFullPath(
    controlOptions.ServiceTokenFile, builder.Environment.ContentRootPath);
builder.Services.Configure<ControlPlaneOptions>(options =>
{
    options.BaseUrl = controlOptions.BaseUrl;
    options.ServiceTokenFile = controlOptions.ServiceTokenFile;
});
var controlBaseUrl = controlOptions.BaseUrl;
builder.Services.AddHttpClient<ControlPlaneClient>(client =>
{
    client.BaseAddress = new Uri(controlBaseUrl.EndsWith('/') ? controlBaseUrl : $"{controlBaseUrl}/");
    client.Timeout = TimeSpan.FromMinutes(15);
});
builder.Services.AddHttpClient("plausible-script", client =>
{
    client.Timeout = TimeSpan.FromSeconds(15);
    client.DefaultRequestHeaders.UserAgent.ParseAdd("SMACX-Agent/1.0 analytics-script-proxy");
});

var app = builder.Build();

app.UseForwardedHeaders();

// Configure the HTTP request pipeline.
if (app.Environment.IsDevelopment())
{
    app.UseWebAssemblyDebugging();
}
else
{
    app.UseExceptionHandler("/Error", createScopeForErrors: true);
    // The default HSTS value is 30 days. You may want to change this for production scenarios, see https://aka.ms/aspnetcore-hsts.
    app.UseHsts();
}
app.UseStatusCodePagesWithReExecute("/not-found", createScopeForStatusCodePages: true);
app.UseAuthentication();
app.UseAuthorization();
app.UseRateLimiter();
app.UseAntiforgery();
app.UseWebSockets();

app.MapStaticAssets();
app.MapGet("/healthz", async (ControlPlaneClient control, CancellationToken cancellationToken) =>
{
    var health = await control.HealthAsync(cancellationToken);
    return health.Connected
        ? Results.Ok(new { ok = true, service = "smacx-control-center", state = health.State })
        : Results.Json(new { ok = false, service = "smacx-control-center", state = health.State },
            statusCode: StatusCodes.Status503ServiceUnavailable);
}).AllowAnonymous();
app.MapControllers();
app.MapHub<LobbyHub>("/hubs/lobby");
app.Map("/stream/{instanceId}/{**catchall}", async context =>
    await context.RequestServices.GetRequiredService<StreamProxyService>().ForwardAsync(context));
app.MapRazorComponents<App>()
    .AddInteractiveServerRenderMode()
    .AddInteractiveWebAssemblyRenderMode()
    .AddAdditionalAssemblies(typeof(Smacx.Portal.Client._Imports).Assembly);

await app.Services.GetRequiredService<PortalDatabaseInitializer>().InitializeAsync();
await using (var startupScope = app.Services.CreateAsyncScope())
{
    var bootstrapTokens = startupScope.ServiceProvider.GetRequiredService<BootstrapTokenStore>();
    await bootstrapTokens.EnsureAsync();
    if (args.Length == 1 && args[0].Equals("bootstrap-token", StringComparison.OrdinalIgnoreCase))
    {
        Console.WriteLine(bootstrapTokens.RevealForCli());
        return;
    }
    if (args.Length is 1 or 2 &&
        args[0].Equals("admin-reset-token", StringComparison.OrdinalIgnoreCase))
    {
        var username = args.Length == 2 ? args[1] : "admin";
        var users = startupScope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>();
        var database = startupScope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        var administrator = await users.FindByNameAsync(username);
        if (administrator is null || !await users.IsInRoleAsync(administrator, PortalRoles.Administrator))
            throw new InvalidOperationException("The requested administrator account does not exist.");
        var token = Convert.ToBase64String(RandomNumberGenerator.GetBytes(24))
            .Replace('+', '-').Replace('/', '_').TrimEnd('=');
        database.PasswordResetGrants.Add(new PasswordResetGrant
        {
            UserId = administrator.Id,
            IssuedByUserId = administrator.Id,
            TokenHash = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(token))),
            ExpiresAt = DateTimeOffset.UtcNow.AddMinutes(30),
        });
        administrator.MustResetPassword = true;
        administrator.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync();
        Console.WriteLine($"{administrator.UserName}\t{token}");
        return;
    }
}

app.Run();

static Task ApiStatus(RedirectContext<CookieAuthenticationOptions> context, int statusCode)
{
    if (context.Request.Path.StartsWithSegments("/api"))
    {
        context.Response.StatusCode = statusCode;
        return context.Response.WriteAsJsonAsync(ApiResponse<bool>.Failure(
            statusCode == StatusCodes.Status401Unauthorized ? "authentication_required" : "access_denied",
            statusCode == StatusCodes.Status401Unauthorized
                ? "Sign in to continue."
                : "This account is not allowed to perform that action."));
    }
    context.Response.Redirect(context.RedirectUri);
    return Task.CompletedTask;
}

public partial class Program;
