# syntax=docker/dockerfile:1.7

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY global.json Directory.Build.props Directory.Packages.props Smacx.Agent.slnx ./
COPY portal/Smacx.Portal.Contracts/ portal/Smacx.Portal.Contracts/
COPY portal/Smacx.Portal.Client/ portal/Smacx.Portal.Client/
COPY portal/Smacx.Portal/ portal/Smacx.Portal/
COPY docs/personality-cards.md docs/personality-cards.md
RUN dotnet restore portal/Smacx.Portal/Smacx.Portal.csproj
RUN dotnet publish portal/Smacx.Portal/Smacx.Portal.csproj -c Release --no-restore -o /out

FROM mcr.microsoft.com/dotnet/aspnet:10.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 smacx \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin smacx \
    && install -d -o smacx -g smacx /var/lib/smacx-portal
WORKDIR /opt/smacx-portal
COPY --from=build /out/ ./
# Imported artwork can retain owner-only modes; published web assets are public.
RUN chmod -R a+rX ./wwwroot
ENV ASPNETCORE_URLS=http://0.0.0.0:8080 \
    ASPNETCORE_FORWARDEDHEADERS_ENABLED=true \
    DOTNET_EnableDiagnostics=0 \
    SMACX_PORTAL_DATA=/var/lib/smacx-portal
VOLUME ["/var/lib/smacx-portal"]
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=4s --start-period=15s --retries=5 \
    CMD ["curl", "--fail", "--silent", "--show-error", "http://127.0.0.1:8080/api/status"]
USER smacx
ENTRYPOINT ["dotnet", "Smacx.Portal.dll"]
