using System.Net.Http.Json;
using System.Text.Json;
using Smacx.Portal.Contracts;

namespace Smacx.Portal.Client.Services;

public sealed class PortalApiClient(HttpClient http)
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private string? csrfToken;

    public async Task<ApiResponse<T>?> GetAsync<T>(string path, CancellationToken cancellationToken = default) =>
        await http.GetFromJsonAsync<ApiResponse<T>>(path, Json, cancellationToken);

    public async Task<(ApiResponse<T>? Payload, int StatusCode)> PostAsync<T>(
        string path,
        object body,
        CancellationToken cancellationToken = default)
    {
        csrfToken ??= (await GetAsync<CsrfTokenResponse>("api/auth/csrf", cancellationToken))?.Data?.Token;
        using var request = new HttpRequestMessage(HttpMethod.Post, path)
        {
            Content = JsonContent.Create(body, options: Json),
        };
        if (!string.IsNullOrWhiteSpace(csrfToken))
        {
            request.Headers.TryAddWithoutValidation("X-CSRF-TOKEN", csrfToken);
        }
        using var response = await http.SendAsync(request, cancellationToken);
        var payload = await response.Content.ReadFromJsonAsync<ApiResponse<T>>(Json, cancellationToken);
        return (payload, (int)response.StatusCode);
    }
}
