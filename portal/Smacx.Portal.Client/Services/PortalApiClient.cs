using System.Net.Http.Json;
using System.Text.Json;
using Smacx.Portal.Contracts;

namespace Smacx.Portal.Client.Services;

public sealed class PortalApiClient(HttpClient http)
{
    public Uri BaseAddress => http.BaseAddress ?? new Uri("/", UriKind.Relative);
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private string? csrfToken;

    public async Task<ApiResponse<T>?> GetAsync<T>(string path, CancellationToken cancellationToken = default)
    {
        using var response = await http.GetAsync(path, cancellationToken);
        return await ReadPayloadAsync<T>(response, cancellationToken);
    }

    public async Task<(ApiResponse<T>? Payload, int StatusCode)> PostAsync<T>(
        string path,
        object body,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(csrfToken))
        {
            var csrf = await GetAsync<CsrfTokenResponse>("api/auth/csrf", cancellationToken);
            csrfToken = csrf?.Data?.Token;
            if (csrf?.Ok != true || string.IsNullOrWhiteSpace(csrfToken))
            {
                return (ApiResponse<T>.Failure(
                    csrf?.Error?.Code ?? "csrf_unavailable",
                    csrf?.Error?.Message ?? "The portal could not prepare a secure request. Refresh and try again."), 0);
            }
        }
        return await SendAsync<T>(HttpMethod.Post, path, body, cancellationToken);
    }

    public Task<(ApiResponse<T>? Payload, int StatusCode)> PutAsync<T>(
        string path, object body, CancellationToken cancellationToken = default) =>
        SendAsync<T>(HttpMethod.Put, path, body, cancellationToken);

    private async Task<(ApiResponse<T>? Payload, int StatusCode)> SendAsync<T>(
        HttpMethod method, string path, object body, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(csrfToken))
        {
            var csrf = await GetAsync<CsrfTokenResponse>("api/auth/csrf", cancellationToken);
            csrfToken = csrf?.Data?.Token;
            if (csrf?.Ok != true || string.IsNullOrWhiteSpace(csrfToken))
                return (ApiResponse<T>.Failure(csrf?.Error?.Code ?? "csrf_unavailable",
                    csrf?.Error?.Message ?? "The portal could not prepare a secure request. Refresh and try again."), 0);
        }
        using var request = new HttpRequestMessage(method, path)
        {
            Content = JsonContent.Create(body, options: Json),
        };
        if (!string.IsNullOrWhiteSpace(csrfToken))
        {
            request.Headers.TryAddWithoutValidation("X-CSRF-TOKEN", csrfToken);
        }
        using var response = await http.SendAsync(request, cancellationToken);
        var payload = await ReadPayloadAsync<T>(response, cancellationToken);
        return (payload, (int)response.StatusCode);
    }

    private static async Task<ApiResponse<T>> ReadPayloadAsync<T>(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        try
        {
            var payload = await response.Content.ReadFromJsonAsync<ApiResponse<T>>(Json, cancellationToken);
            return payload ?? ApiResponse<T>.Failure(
                "empty_portal_response",
                $"The portal returned an empty response (HTTP {(int)response.StatusCode}).");
        }
        catch (JsonException)
        {
            return ApiResponse<T>.Failure(
                "invalid_portal_response",
                $"The portal returned an invalid response (HTTP {(int)response.StatusCode}). Refresh and try again.");
        }
        catch (NotSupportedException)
        {
            return ApiResponse<T>.Failure(
                "invalid_portal_response",
                $"The portal returned an unsupported response (HTTP {(int)response.StatusCode}). Refresh and try again.");
        }
    }
}
