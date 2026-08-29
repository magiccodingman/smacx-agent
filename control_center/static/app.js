const $ = (selector) => document.querySelector(selector);
const sections = ["setup", "login", "dashboard"];
let dashboardState = {agents: [], sources: [], runtimes: [], workers: [], providers: [], matches: [], harnessProfiles: [], harnessRuns: [], backups: [], schedules: [], graphiti: null};

function show(name) {
  sections.forEach((id) => $(`#${id}`).classList.toggle("hidden", id !== name));
}

function cookie(name) {
  const row = document.cookie.split("; ").find((item) => item.startsWith(`${name}=`));
  return row ? row.slice(name.length + 1) : "";
}

function notify(text, error = false) {
  const node = $("#message");
  node.textContent = text;
  node.classList.remove("hidden", "error");
  if (error) node.classList.add("error");
}

async function api(path, options = {}) {
  const request = {credentials: "same-origin", ...options};
  request.headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (request.method && request.method !== "GET") {
    const csrf = cookie("smacx_csrf");
    if (csrf) request.headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(path, request);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || `HTTP ${response.status}`);
  return body;
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

async function loadDashboard() {
  const [status, providerResult, agentsResult, sourcesResult, runtimesResult, workersResult, matchesResult, harnessResult, harnessRunsResult, operationsResult, backupsResult, schedulesResult, graphitiResult] = await Promise.all([
    api("/api/v1/status"), api("/api/v1/providers"), api("/api/v1/agents"),
    api("/api/v1/game-sources"), api("/api/v1/runtimes"), api("/api/v1/workers"),
    api("/api/v1/matches"), api("/api/v1/harness-profiles"), api("/api/v1/harness-runs"),
    api("/api/v1/operations/status"), api("/api/v1/backups"), api("/api/v1/schedules"),
    api("/api/v1/graphiti"),
  ]);
  dashboardState = {
    agents: agentsResult.agents,
    sources: sourcesResult.game_sources,
    runtimes: runtimesResult.runtimes,
    workers: workersResult.workers,
    providers: providerResult.providers,
    matches: matchesResult.matches,
    harnessProfiles: harnessResult.harness_profiles,
    harnessRuns: harnessRunsResult.harness_runs,
    backups: backupsResult.backups,
    schedules: schedulesResult.schedules,
    graphiti: graphitiResult,
  };
  $("#installation").textContent = status.installation_id;
  $("#provider-count").textContent = status.counts.model_providers;
  $("#match-count").textContent = status.counts.matches;
  $("#worker-count").textContent = status.counts.instances;
  renderProviders(providerResult.providers);
  renderAssets(dashboardState.sources, dashboardState.runtimes, workersResult.docker);
  renderAgents(dashboardState.agents);
  renderWorkers(dashboardState.workers);
  renderMatches(dashboardState.matches);
  renderHarnessProfiles(dashboardState.harnessProfiles);
  renderHarnessRuns(dashboardState.harnessRuns);
  renderOperations(operationsResult, dashboardState.backups, dashboardState.schedules);
  renderGraphiti(graphitiResult);
  populateSelect("#match-agent", dashboardState.agents, "agent_id", "display_name", "Create an agent first");
  populateSelect("#match-source", dashboardState.sources, "game_source_id", "display_name", "Validate game files first");
  populateSelect("#match-runtime", dashboardState.runtimes, "runtime_id", "display_name", "Import Proton first");
  populateSelect("#lan-agents", dashboardState.agents, "agent_id", "display_name", "Create at least two agents first");
  populateSelect("#lan-source", dashboardState.sources, "game_source_id", "display_name", "Validate game files first");
  populateSelect("#lan-runtime", dashboardState.runtimes, "runtime_id", "display_name", "Import Proton first");
  populateSelect("#harness-match", dashboardState.matches, "match_id", "display_name", "Create a match first");
  populateHarnessAgents();
  populateSelect("#harness-provider", dashboardState.providers.filter((item) => item.default_model_id), "provider_id", "display_name", "Select a provider model first");
  populateScheduleMatches();
  show("dashboard");
}

function renderGraphiti(graphiti) {
  const runtime = graphiti.runtime || {};
  const state = $("#graphiti-state");
  state.textContent = `${graphiti.enabled ? "Enabled" : "Disabled"} · ${runtime.status || "stopped"}`;
  state.className = `pill ${["ready", "disabled"].includes(runtime.status) ? "healthy" : runtime.status === "degraded" ? "error" : ""}`;
  $("#graphiti-enabled").checked = graphiti.enabled === true;
  const select = $("#graphiti-scope");
  const previous = select.value;
  select.replaceChildren();
  (graphiti.scopes || []).forEach((scope) => {
    const option = document.createElement("option");
    option.value = JSON.stringify([scope.match_id, scope.agent_id, scope.perspective_id]);
    option.textContent = `${scope.match_name} · ${scope.agent_name}`;
    option.selected = previous === option.value;
    select.append(option);
  });
  if (!select.options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Create a match perspective first";
    select.append(option);
  }
  const detail = $("#graphiti-detail");
  detail.replaceChildren(record(
    "Derived graph health",
    `${runtime.projected_events || 0} projected · ${runtime.failed_events || 0} failed · ${runtime.active_scopes || 0} active scope(s) · ${graphiti.queued_rebuilds || 0} rebuild(s) queued`,
    runtime.status || "stopped",
  ));
}

function populateScheduleMatches() {
  const select = $("#schedule-match");
  const previous = select.value;
  select.replaceChildren();
  const whole = document.createElement("option");
  whole.value = "";
  whole.textContent = "Whole installation";
  select.append(whole);
  dashboardState.matches.forEach((match) => {
    const option = document.createElement("option");
    option.value = match.match_id;
    option.textContent = match.display_name;
    option.selected = previous === option.value;
    select.append(option);
  });
}

function renderOperations(status, backups, schedules) {
  const state = $("#supervisor-state");
  state.textContent = status.running
    ? `Supervisor active · ${status.open_incidents} incident(s)` : "Supervisor stopped";
  state.className = `pill ${status.running && !status.open_incidents ? "healthy" : "error"}`;
  const backupRoot = $("#backups");
  backupRoot.replaceChildren();
  backups.forEach((backup) => backupRoot.append(record(
    backup.backup_id,
    `${backup.worker_count} worker volume(s) · ${backup.size_bytes || 0} bytes · ${backup.includes_secrets ? "secrets included" : "no secrets"}`,
    backup.status,
  )));
  if (!backups.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No recovery set has been created yet.";
    backupRoot.append(empty);
  }
  const scheduleRoot = $("#schedules");
  scheduleRoot.replaceChildren();
  schedules.forEach((schedule) => {
    const item = record(
      schedule.display_name,
      `${schedule.operation_kind} · every ${Math.round(schedule.interval_seconds / 60)} minute(s) · next ${new Date(schedule.next_run_unix * 1000).toLocaleString()}`,
      schedule.status,
    );
    const actions = document.createElement("div");
    actions.className = "actions";
    const action = document.createElement("button");
    action.className = "quiet";
    action.textContent = schedule.status === "active" ? "Pause" : "Activate";
    action.addEventListener("click", async () => {
      action.disabled = true;
      try {
        await api(`/api/v1/schedules/${schedule.schedule_id}/${schedule.status === "active" ? "pause" : "activate"}`, {method: "POST", body: "{}"});
        notify("Schedule updated.");
        await loadDashboard();
      } catch (error) { notify(error.message, true); action.disabled = false; }
    });
    actions.append(action);
    item.append(actions);
    scheduleRoot.append(item);
  });
}

function populateSelect(selector, items, valueKey, labelKey, emptyLabel) {
  const select = $(selector);
  const previous = select.value;
  select.replaceChildren();
  if (!items.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = emptyLabel;
    select.append(option);
    return;
  }
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item[valueKey];
    option.textContent = item[labelKey];
    option.selected = previous === option.value;
    select.append(option);
  });
}

function populateHarnessAgents() {
  const matchId = $("#harness-match").value;
  const agentIds = new Set(
    dashboardState.workers.filter((worker) => worker.match_id === matchId).map((worker) => worker.agent_id),
  );
  populateSelect(
    "#harness-agent", dashboardState.agents.filter((agent) => agentIds.has(agent.agent_id)),
    "agent_id", "display_name", "Select a provisioned match",
  );
}

function record(title, detail, state) {
  const item = document.createElement("article");
  item.className = "record";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const text = document.createElement("p");
  text.textContent = detail;
  const badge = document.createElement("span");
  badge.className = `pill ${state || ""}`;
  badge.textContent = state || "ready";
  item.append(heading, text, badge);
  return item;
}

function renderAssets(sources, runtimes, docker) {
  const dockerState = $("#docker-state");
  dockerState.textContent = docker.ok ? `Docker ${docker.server_version || "ready"}` : docker.error;
  dockerState.className = `pill ${docker.ok ? "healthy" : "unreachable"}`;
  const root = $("#assets");
  root.replaceChildren();
  sources.forEach((source) => root.append(record(
    source.display_name, `Game source · ${source.host_path}`, source.status,
  )));
  runtimes.forEach((runtime) => root.append(record(
    runtime.display_name, `Private ${runtime.runtime_kind} · ${runtime.storage_ref}`, runtime.status,
  )));
  if (!sources.length && !runtimes.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No validated game source or managed runtime yet.";
    root.append(empty);
  }
}

function renderAgents(agents) {
  const root = $("#agents");
  root.replaceChildren();
  agents.forEach((agent) => root.append(record(
    agent.display_name, agent.personality_ref || agent.agent_id, agent.status,
  )));
  if (!agents.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No durable agent identities yet.";
    root.append(empty);
  }
}

function renderWorkers(workers) {
  const root = $("#workers");
  root.replaceChildren();
  workers.forEach((worker) => {
    const item = record(
      worker.match_id, `${worker.agent_id} · ${worker.instance_id}`,
      worker.observed_status,
    );
    item.classList.add("worker");
    const actions = document.createElement("div");
    actions.className = "actions";
    const start = document.createElement("button");
    start.textContent = worker.observed_status === "parked" ? "Resume" : "Start";
    start.disabled = worker.observed_status === "running";
    start.addEventListener("click", () => workerAction(worker.instance_id, "start", start));
    const park = document.createElement("button");
    park.className = "quiet";
    park.textContent = "Park";
    park.disabled = worker.observed_status !== "running";
    park.addEventListener("click", () => workerAction(worker.instance_id, "park", park));
    const inspect = document.createElement("button");
    inspect.className = "quiet";
    inspect.textContent = "Inspect";
    inspect.addEventListener("click", () => workerAction(worker.instance_id, "status", inspect, false));
    actions.append(start, park, inspect);
    if (match.status === "running" && match.metadata?.host_controller_kind !== "human") {
      const checkpoint = document.createElement("button");
      checkpoint.className = "quiet";
      checkpoint.textContent = "Recovery checkpoint";
      checkpoint.addEventListener("click", () => matchAction(
        match.match_id, "checkpoint", checkpoint, true, {slot: "control_recovery"},
      ));
      actions.append(checkpoint);
    }
    if (match.status === "error" && match.metadata?.recovery_checkpoint?.verified) {
      const recover = document.createElement("button");
      recover.className = "quiet";
      recover.textContent = "Recover verified save";
      recover.addEventListener("click", () => matchAction(match.match_id, "recover", recover));
      actions.append(recover);
    }
    if (worker.network?.view_enabled) {
      const watch = document.createElement("button");
      watch.className = "quiet";
      watch.textContent = "Watch";
      watch.disabled = worker.observed_status !== "running";
      watch.addEventListener("click", () => openSpectator(worker.instance_id, watch));
      actions.append(watch);
    }
    item.append(actions);
    root.append(item);
  });
  if (!workers.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No game worker has been provisioned.";
    root.append(empty);
  }
}

async function openSpectator(instanceId, button) {
  button.disabled = true;
  try {
    const access = await api(`/api/v1/workers/${instanceId}/spectator`, {
      method: "POST", body: "{}",
    });
    const target = new URL(access.path, `${location.protocol}//${location.hostname}:${access.host_port}`);
    let copied = false;
    try {
      await navigator.clipboard.writeText(access.password);
      copied = true;
    } catch (_) {}
    window.open(target.toString(), "_blank", "noopener,noreferrer");
    if (copied) notify("View-only spectator opened; its password was copied to the clipboard.");
    else window.prompt("View-only spectator password", access.password);
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderMatches(matches) {
  const root = $("#matches");
  root.replaceChildren();
  matches.filter((match) => match.mode === "lan").forEach((match) => {
    const external = match.metadata?.external_lan;
    const humanHosted = external?.mode === "human_hosted";
    const externalDetail = external
      ? humanHosted
        ? ` · human host ${external.host_player_name || "pending"} · ${external.phase}`
        : ` · join ${external.host_address} · ${external.session_name}`
      : "";
    const item = record(
      match.display_name,
      `${match.match_id} · ${match.ruleset_id}${externalDetail}`,
      match.status,
    );
    item.classList.add("worker");
    const actions = document.createElement("div");
    actions.className = "actions";
    const start = document.createElement("button");
    start.textContent = match.status === "lobby" && humanHosted
      ? external.phase === "awaiting_human_start" ? "Check human Start" : "Find human lobby"
      : match.status === "lobby" && external
      ? "Check humans & start"
      : match.status === "parked" ? "Start fresh lobby" : "Start";
    start.disabled = match.status === "running" || match.status === "starting";
    const sessionPayload = {
      session_name: match.metadata?.lan_session_name || "SMACX Managed LAN",
      profile: match.metadata?.lan_profile || "small_easy",
    };
    start.addEventListener("click", () => {
      if (match.status === "lobby" && humanHosted
          && external.phase !== "awaiting_human_start") {
        connectHumanHostedLobby(match.match_id, start);
      } else if (match.status === "lobby" && humanHosted) {
        matchAction(match.match_id, "finalize-external-host", start, true);
      } else {
        matchAction(match.match_id, "start", start, true, sessionPayload);
      }
    });
    const park = document.createElement("button");
    park.className = "quiet";
    park.textContent = "Park all seats";
    park.disabled = !["running", "lobby", "error"].includes(match.status);
    park.addEventListener("click", () => matchAction(match.match_id, "park", park));
    const inspect = document.createElement("button");
    inspect.className = "quiet";
    inspect.textContent = "Inspect seats";
    inspect.addEventListener("click", () => matchAction(match.match_id, "status", inspect, false));
    actions.append(start, park, inspect);
    if (match.status === "parked") {
      const resume = document.createElement("button");
      resume.className = "quiet";
      resume.textContent = "Resume checkpoint…";
      resume.addEventListener("click", () => {
        const slot = window.prompt("Exact semantic save slot to resume");
        if (!slot) return;
        if (!/^[A-Za-z0-9_-]{1,32}$/.test(slot)) {
          notify("Save slots use 1–32 letters, digits, underscores, or hyphens.", true);
          return;
        }
        matchAction(match.match_id, "start", resume, true, {
          ...sessionPayload, resume_slot: slot,
        });
      });
      actions.append(resume);
    }
    item.append(actions);
    root.append(item);
  });
  if (!root.children.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No managed LAN match has been created.";
    root.append(empty);
  }
}

async function matchAction(matchId, action, button, reload = true, payload = {}) {
  button.disabled = true;
  const labels = {
    start: "Starting native LAN", park: "Parking every seat", status: "Inspecting LAN seats",
    checkpoint: "Creating a bridge-verified checkpoint", recover: "Recovering the verified checkpoint",
  };
  notify(`${labels[action] || "Running managed operation"}…`);
  try {
    const result = await api(`/api/v1/matches/${matchId}/${action}`, {
      method: "POST", body: JSON.stringify(payload),
    });
    if (result.awaiting_external_host) {
      notify(`Managed agents are ready. Create the native lobby as ${result.external_host?.player_name}, then choose “Find human lobby”.`);
    } else if (result.awaiting_human_start) {
      const blockers = (result.blockers || []).map((item) => item.player_name
        ? `${item.player_name}: ${item.reason}` : item.reason).join(", ");
      notify(blockers || result.external_host?.instructions
        || "Managed agents are Ready; press Start in the human-owned native lobby.");
    } else if (result.awaiting_external_humans) {
      const join = result.external_join || {};
      const blockers = (join.blockers || []).map((item) => item.player_name
        ? `${item.player_name}: ${item.reason}` : item.reason).join(", ");
      notify(`Lobby open at ${join.host_address} (${join.session_name}). ${blockers || "Humans should join with their assigned names and mark Ready."}`);
    } else if (action === "status") {
      const live = result.seats.filter((seat) => seat.native?.lifecycle === "game").length;
      notify(`${live} of ${result.seats.length} LAN seats report native gameplay.`);
      button.disabled = false;
    } else {
      const outcomes = {
        start: "Native LAN started for every managed seat.",
        "finalize-external-host": "Native LAN started for every managed seat.",
        park: "Every LAN seat parked.",
        checkpoint: "Recovery checkpoint created and verified by the native bridge.",
        recover: "The match resumed from its verified recovery checkpoint.",
      };
      notify(outcomes[action] || "Managed operation completed.");
    }
    if (reload) await loadDashboard();
  } catch (error) {
    button.disabled = false;
    notify(error.message, true);
  }
}

async function connectHumanHostedLobby(matchId, button) {
  const hostAddress = window.prompt("Reachable IPv4 address of the human-hosted game");
  if (!hostAddress) return;
  button.disabled = true;
  try {
    notify("Discovering native sessions at the human host…");
    const discovered = await api(`/api/v1/matches/${matchId}/discover-external-host`, {
      method: "POST", body: JSON.stringify({host_address: hostAddress}),
    });
    if (!discovered.sessions.length) {
      notify("No joinable native session was discovered at that address.", true);
      return;
    }
    let selected = discovered.sessions[0];
    if (discovered.sessions.length > 1) {
      const menu = discovered.sessions.map((session) =>
        `${session.network_session_id} — ${session.session_name || "unnamed"}`).join("\n");
      const chosenId = window.prompt(`Choose the exact session ID:\n${menu}`);
      selected = discovered.sessions.find((session) =>
        session.network_session_id === chosenId);
      if (!selected) {
        notify("The selected session ID was not in the fresh discovery result.", true);
        return;
      }
    }
    const joined = await api(`/api/v1/matches/${matchId}/join-external-host`, {
      method: "POST", body: JSON.stringify({
        host_address: hostAddress,
        network_session_id: selected.network_session_id,
      }),
    });
    notify(joined.external_host?.instructions
      || "Managed agents joined and readied. The human host may press Start.");
    await loadDashboard();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderHarnessProfiles(profiles) {
  const root = $("#harness-profiles");
  root.replaceChildren();
  profiles.forEach((profile) => root.append(record(
    profile.display_name,
    `${profile.external_profile_id} · ${profile.model_id} · ${profile.reasoning_effort} reasoning`,
    profile.status,
  )));
  if (!profiles.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No Hermes profile binding has been prepared yet.";
    root.append(empty);
  }
}

function renderHarnessRuns(runs) {
  const root = $("#harness-runs");
  root.replaceChildren();
  runs.forEach((run) => {
    const item = record(
      `Run ${run.run_id}`,
      `${run.match_id} · restart ${run.restart_count}/${run.restart_policy.restart_limit} · ${run.last_heartbeat_unix ? `heartbeat ${new Date(run.last_heartbeat_unix * 1000).toLocaleTimeString()}` : "awaiting heartbeat"}`,
      run.status,
    );
    const actions = document.createElement("div");
    actions.className = "actions";
    const action = document.createElement("button");
    action.className = "quiet";
    const live = ["queued", "starting", "running", "restarting"].includes(run.status);
    action.textContent = live ? "Stop player" : "Resume player";
    action.addEventListener("click", async () => {
      action.disabled = true;
      try {
        await api(`/api/v1/harness-runs/${run.run_id}/${live ? "stop" : "start"}`, {
          method: "POST", body: "{}",
        });
        notify(live ? "Managed player stopped; its conversation remains durable." : "Managed player resumed.");
        await loadDashboard();
      } catch (error) { notify(error.message, true); action.disabled = false; }
    });
    actions.append(action);
    item.append(actions);
    root.append(item);
  });
  if (!runs.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No managed harness run has been started.";
    root.append(empty);
  }
}

async function workerAction(instanceId, action, button, reload = true) {
  button.disabled = true;
  notify(`${action === "start" ? "Starting" : action === "park" ? "Parking" : "Inspecting"} worker…`);
  try {
    const result = await api(`/api/v1/workers/${instanceId}/${action}`, {method: "POST", body: "{}"});
    if (action === "status") {
      notify(`Worker: ${result.health || result.observed_status || "not running"}.`);
      button.disabled = false;
    } else {
      notify(action === "start" ? "Game worker is healthy." : "Worker parked; durable data retained.");
    }
    if (reload) await loadDashboard();
  } catch (error) {
    button.disabled = false;
    notify(error.message, true);
  }
}

function renderProviders(providers) {
  const root = $("#providers");
  root.replaceChildren();
  if (!providers.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No model endpoint configured yet.";
    root.append(empty);
    return;
  }
  providers.forEach((provider) => {
    const item = document.createElement("article");
    item.className = "provider";
    const title = document.createElement("h3");
    title.textContent = provider.display_name;
    const endpoint = document.createElement("code");
    endpoint.textContent = provider.base_url;
    const state = document.createElement("span");
    state.className = `pill ${provider.status}`;
    state.textContent = provider.status;
    const detail = document.createElement("p");
    detail.textContent = provider.models.length
      ? `${provider.models.length} model(s) · selected: ${provider.default_model_id || "choose one"}`
      : "Not discovered yet";
    item.append(title, endpoint, state, detail);
    if (provider.models.length > 1) {
      const select = document.createElement("select");
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Choose model…";
      select.append(placeholder);
      provider.models.forEach((model) => {
        const option = document.createElement("option");
        option.value = model.model_id;
        option.textContent = `${model.model_id}${model.context_length ? ` · ${model.context_length} ctx` : ""}`;
        option.selected = provider.default_model_id === model.model_id;
        select.append(option);
      });
      select.addEventListener("change", async () => {
        if (!select.value) return;
        try {
          await api(`/api/v1/providers/${provider.provider_id}/select`, {
            method: "POST", body: JSON.stringify({model_id: select.value}),
          });
          notify(`Selected ${select.value}.`);
          await loadDashboard();
        } catch (error) { notify(error.message, true); }
      });
      item.append(select);
    }
    root.append(item);
  });
}

$("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/v1/setup/bootstrap", {
      method: "POST", body: JSON.stringify(formObject(event.currentTarget)),
    });
    notify("Administrator created. The one-time bootstrap token has been revoked.");
    await loadDashboard();
  } catch (error) { notify(error.message, true); }
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/v1/auth/login", {
      method: "POST", body: JSON.stringify(formObject(event.currentTarget)),
    });
    notify("Signed in.");
    await loadDashboard();
  } catch (error) { notify(error.message, true); }
});

$("#provider-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = formObject(event.currentTarget);
  try {
    const result = await api("/api/v1/providers", {
      method: "POST", body: JSON.stringify(values),
    });
    await api(`/api/v1/providers/${result.provider.provider_id}/discover`, {
      method: "POST", body: "{}",
    });
    event.currentTarget.reset();
    notify("Provider saved and models discovered.");
    await loadDashboard();
  } catch (error) { notify(error.message, true); }
});

async function submitBusy(event, path, success, transform = (value) => value) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api(path, {method: "POST", body: JSON.stringify(transform(formObject(form)))});
    notify(success);
    await loadDashboard();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
}

$("#source-form").addEventListener("submit", (event) => submitBusy(
  event, "/api/v1/game-sources/validate", "Game files validated without modifying the source.",
));

$("#runtime-form").addEventListener("submit", (event) => submitBusy(
  event, "/api/v1/runtimes/import-proton", "Private Proton runtime imported.",
));

$("#agent-form").addEventListener("submit", (event) => submitBusy(
  event, "/api/v1/agents", "Durable agent identity created.",
));

$("#match-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const values = formObject(form);
  button.disabled = true;
  try {
    const created = await api("/api/v1/matches/solo", {
      method: "POST",
      body: JSON.stringify({
        display_name: values.display_name,
        agent_id: values.agent_id,
        game_source_id: values.game_source_id,
        runtime_id: values.runtime_id,
        faction_id: Number(values.faction_id),
        autostart: {
          enabled: true,
          faction_id: Number(values.faction_id),
          difficulty: Number(values.difficulty),
          world_size: Number(values.world_size),
          blind_research: true,
          narrative_ui: false,
          tutorial_ui: false,
        },
        view_enabled: values.view_enabled === "on",
      }),
    });
    notify("Match and isolated worker provisioned.");
    if (values.start_now === "on") {
      notify("Starting the game worker; first launch may take a few minutes…");
      await api(`/api/v1/workers/${created.worker.instance_id}/start`, {method: "POST", body: "{}"});
      notify("Game worker is healthy and ready for an agent runtime.");
    }
    await loadDashboard();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#lan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const values = formObject(form);
  const agentIds = [...$("#lan-agents").selectedOptions].map((option) => option.value);
  const humanNames = String(values.human_player_names || "")
    .split(/[\n,]+/).map((value) => value.trim()).filter(Boolean);
  const hostKind = values.host_controller_kind || "agent";
  const humanHostName = String(values.human_host_name || "").trim();
  const totalSeats = agentIds.length + humanNames.length + (hostKind === "human" ? 1 : 0);
  if (agentIds.length < 1 || totalSeats < 2) {
    notify("Choose at least one agent and configure two or more total seats.", true);
    return;
  }
  if (hostKind === "human" && !humanHostName) {
    notify("Enter the exact in-game player name for the human host.", true);
    return;
  }
  button.disabled = true;
  try {
    const created = await api("/api/v1/matches/lan", {
      method: "POST", body: JSON.stringify({
        display_name: values.display_name, session_name: values.session_name,
        agent_ids: agentIds, game_source_id: values.game_source_id,
        host_controller_kind: hostKind,
        human_host_name: hostKind === "human" ? humanHostName : null,
        human_player_names: humanNames,
        runtime_id: values.runtime_id, profile: values.profile,
        view_enabled: values.view_enabled === "on",
        start_now: values.start_now === "on",
      }),
    });
    notify(created.started?.awaiting_external_host
      ? "Managed clients are ready. Create the human lobby, then use Find human lobby."
      : values.start_now === "on" && humanNames.length
      ? "AI-hosted lobby is open. Human players can now join and mark Ready."
      : values.start_now === "on" ? "Managed native LAN is running." : "Managed LAN seats provisioned.");
    await loadDashboard();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#harness-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const values = formObject(form);
  button.disabled = true;
  try {
    const result = await api("/api/v1/harness-runs", {
      method: "POST", body: JSON.stringify(values),
    });
    $("#harness-command").textContent = result.run.run_id;
    $("#harness-detail").textContent = `Match ${result.run.match_id} · exact agent ${result.run.agent_id}`;
    $("#harness-result").classList.remove("hidden");
    notify("Managed Hermes player started with durable session continuation.");
    await loadDashboard();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#graphiti-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api("/api/v1/graphiti", {method: "POST", body: JSON.stringify({
      enabled: $("#graphiti-enabled").checked,
    })});
    notify("Graphiti projection policy saved. SQLite remains authoritative.");
    await loadDashboard();
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
});

$("#graphiti-rebuild-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  if (!$("#graphiti-scope").value) return;
  button.disabled = true;
  try {
    const [match_id, agent_id, perspective_id] = JSON.parse($("#graphiti-scope").value);
    await api("/api/v1/graphiti/rebuild", {method: "POST", body: JSON.stringify({
      match_id, agent_id, perspective_id,
    })});
    notify("Exact-perspective Graphiti rebuild queued.");
    await loadDashboard();
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
});

$("#backup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const values = formObject(form);
  button.disabled = true;
  try {
    notify("Creating a consistent recovery set; active workers are frozen only while their volumes are copied…");
    const created = await api("/api/v1/backups", {
      method: "POST", body: JSON.stringify({
        include_secrets: values.include_secrets === "on",
        include_workers: values.include_workers === "on",
      }),
    });
    await api(`/api/v1/backups/${created.backup.backup_id}/verify`, {method: "POST", body: "{}"});
    notify(`Backup ${created.backup.backup_id} created and verified.`);
    await loadDashboard();
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
});

$("#schedule-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const values = formObject(form);
  const matchOperation = values.operation_kind !== "backup";
  if (matchOperation && !values.target_id) {
    notify("Choose a match for checkpoint scheduling.", true);
    return;
  }
  button.disabled = true;
  try {
    await api("/api/v1/schedules", {method: "POST", body: JSON.stringify({
      display_name: values.display_name,
      operation_kind: values.operation_kind,
      target_kind: matchOperation ? "match" : "installation",
      target_id: matchOperation ? values.target_id : null,
      interval_seconds: Number(values.interval_minutes) * 60,
      payload: values.operation_kind === "checkpoint"
        ? {slot: "control_recovery"} : {include_secrets: true, include_workers: true},
    })});
    notify("Recurring operation scheduled.");
    await loadDashboard();
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
});

$("#harness-match").addEventListener("change", populateHarnessAgents);

$("#refresh").addEventListener("click", async () => {
  try { await loadDashboard(); notify("Control Center refreshed."); }
  catch (error) { notify(error.message, true); }
});

$("#logout").addEventListener("click", async () => {
  try { await api("/api/v1/auth/logout", {method: "POST", body: "{}"}); } catch (_) {}
  notify("Signed out.");
  show("login");
});

(async () => {
  try {
    await api("/api/v1/auth/session");
    await loadDashboard();
  } catch (_) {
    const setup = await api("/api/v1/setup");
    show(setup.setup_required ? "setup" : "login");
  }
})();
