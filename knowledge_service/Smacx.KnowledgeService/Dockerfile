# syntax=docker/dockerfile:1.7
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY global.json Directory.Build.props Directory.Packages.props Smacx.Agent.slnx ./
COPY knowledge/ knowledge/
COPY knowledge_service/Smacx.KnowledgeService/ knowledge_service/Smacx.KnowledgeService/
RUN dotnet restore knowledge_service/Smacx.KnowledgeService/Smacx.KnowledgeService.csproj
RUN dotnet publish knowledge_service/Smacx.KnowledgeService/Smacx.KnowledgeService.csproj -c Release --no-restore -o /out

FROM mcr.microsoft.com/dotnet/aspnet:10.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 smacx \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin smacx \
    && install -d -o smacx -g smacx /var/lib/smacx-knowledge
WORKDIR /opt/smacx-knowledge
COPY --from=build /out/ ./
ENV ASPNETCORE_URLS=http://0.0.0.0:8090 \
    DOTNET_EnableDiagnostics=0 \
    SMACX_KNOWLEDGE_DATA=/var/lib/smacx-knowledge
VOLUME ["/var/lib/smacx-knowledge"]
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
    CMD ["curl", "--fail", "--silent", "http://127.0.0.1:8090/healthz"]
USER smacx
ENTRYPOINT ["dotnet", "Smacx.KnowledgeService.dll"]
