using System.Text;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
public sealed class AnalyticsScriptController(AnalyticsScriptProvider scripts) : ControllerBase
{
    [HttpGet("/js/potato.js")]
    [AllowAnonymous]
    [ResponseCache(Duration = 86_400, Location = ResponseCacheLocation.Any)]
    public async Task<IActionResult> Get(CancellationToken cancellationToken)
    {
        var script = await scripts.GetAsync(cancellationToken);
        if (script is null)
            return StatusCode(StatusCodes.Status503ServiceUnavailable);

        Response.Headers.XContentTypeOptions = "nosniff";
        return Content(script, "text/javascript", Encoding.UTF8);
    }
}
