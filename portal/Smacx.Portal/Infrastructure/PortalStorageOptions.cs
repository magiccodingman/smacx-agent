namespace Smacx.Portal.Infrastructure;

public sealed class PortalStorageOptions
{
    public const string SectionName = "PortalStorage";

    public string DataRoot { get; set; } = "Data/runtime";
}
