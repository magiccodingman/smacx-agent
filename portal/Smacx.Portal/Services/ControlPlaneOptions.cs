namespace Smacx.Portal.Services;

public sealed class ControlPlaneOptions
{
    public const string SectionName = "ControlPlane";
    public string BaseUrl { get; set; } = "http://127.0.0.1:8765/";
    public string ServiceTokenFile { get; set; } = "Data/runtime/secrets/portal-service-token";
}
