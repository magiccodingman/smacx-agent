const $ = (selector) => document.querySelector(selector);
const sections = ["setup", "login", "dashboard"];
let dashboardState = {agents: [], sources: [], runtimes: [], workers: [], providers: [], matches: [], harnessProfiles: []};

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
  const [status, providerResult, agentsResult, sourcesResult, runtimesResult, workersResult, matchesResult, harnessResult] = await Promise.all([
    api("/api/v1/status"), api("/api/v1/providers"), api("/api/v1/agents"),
    api("/api/v1/game-sources"), api("/api/v1/runtimes"), api("/api/v1/workers"),
    api("/api/v1/matches"), api("/api/v1/harness-profiles"),
  ]);
  dashboardState = {
    agents: agentsResult.agents,
    sources: sourcesResult.game_sources,
    runtimes: runtimesResult.runtimes,
    workers: workersResult.workers,
    providers: providerResult.providers,
    matches: matchesResult.matches,
    harnessProfiles: harnessResult.harness_profiles,
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
  populateSelect("#match-agent", dashboardState.agents, "agent_id", "display_name", "Create an agent first");
  populateSelect("#match-source", dashboardState.sources, "game_source_id", "display_name", "Validate game files first");
  populateSelect("#match-runtime", dashboardState.runtimes, "runtime_id", "display_name", "Import Proton first");
  populateSelect("#lan-agents", dashboardState.agents, "agent_id", "display_name", "Create at least two agents first");
  populateSelect("#lan-source", dashboardState.sources, "game_source_id", "display_name", "Validate game files first");
  populateSelect("#lan-runtime", dashboardState.runtimes, "runtime_id", "display_name", "Import Proton first");
  populateSelect("#harness-match", dashboardState.matches, "match_id", "display_name", "Create a match first");
  populateHarnessAgents();
  populateSelect("#harness-provider", dashboardState.providers.filter((item) => item.default_model_id), "provider_id", "display_name", "Select a provider model first");
  show("dashboard");
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
    const externalDetail = external
      ? ` · join ${external.host_address} · ${external.session_name}` : "";
    const item = record(
      match.display_name,
      `${match.match_id} · ${match.ruleset_id}${externalDetail}`,
      match.status,
    );
    item.classList.add("worker");
    const actions = document.createElement("div");
    actions.className = "actions";
    const start = document.createElement("button");
    start.textContent = match.status === "lobby" && external
      ? "Check humans & start"
      : match.status === "parked" ? "Start fresh lobby" : "Start";
    start.disabled = match.status === "running" || match.status === "starting";
    const sessionPayload = {
      session_name: match.metadata?.lan_session_name || "SMACX Managed LAN",
    };
    start.addEventListener("click", () => matchAction(
      match.match_id, "start", start, true, sessionPayload,
    ));
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
  notify(`${action === "start" ? "Starting native LAN" : action === "park" ? "Parking every seat" : "Inspecting LAN seats"}…`);
  try {
    const result = await api(`/api/v1/matches/${matchId}/${action}`, {
      method: "POST", body: JSON.stringify({profile: "small_easy", ...payload}),
    });
    if (result.awaiting_external_humans) {
      const join = result.external_join || {};
      const blockers = (join.blockers || []).map((item) => item.player_name
        ? `${item.player_name}: ${item.reason}` : item.reason).join(", ");
      notify(`Lobby open at ${join.host_address} (${join.session_name}). ${blockers || "Humans should join with their assigned names and mark Ready."}`);
    } else if (action === "status") {
      const live = result.seats.filter((seat) => seat.native?.lifecycle === "game").length;
      notify(`${live} of ${result.seats.length} LAN seats report native gameplay.`);
      button.disabled = false;
    } else {
      notify(action === "start" ? "Native LAN started for every managed seat." : "Every LAN seat parked.");
    }
    if (reload) await loadDashboard();
  } catch (error) {
    button.disabled = false;
    notify(error.message, true);
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
  if (agentIds.length < 1 || agentIds.length + humanNames.length < 2) {
    notify("Choose an agent host and at least one additional agent or human seat.", true);
    return;
  }
  button.disabled = true;
  try {
    await api("/api/v1/matches/lan", {
      method: "POST", body: JSON.stringify({
        display_name: values.display_name, session_name: values.session_name,
        agent_ids: agentIds, game_source_id: values.game_source_id,
        human_player_names: humanNames,
        runtime_id: values.runtime_id, profile: values.profile,
        view_enabled: values.view_enabled === "on",
        start_now: values.start_now === "on",
      }),
    });
    notify(values.start_now === "on" && humanNames.length
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
    const result = await api("/api/v1/harness-profiles/hermes", {
      method: "POST", body: JSON.stringify(values),
    });
    const descriptor = result.descriptor;
    const command = `./scripts/smacx-hermes configure-from-control --control-url ${location.origin} --match-id ${descriptor.match_id} --agent-id ${descriptor.agent_id} --provider-id ${descriptor.provider_id} --reasoning ${descriptor.reasoning_effort} --start`;
    $("#harness-command").textContent = command;
    $("#harness-detail").textContent = `${descriptor.agent_name} · ${descriptor.model_id} · exact worker ${descriptor.instance_id}`;
    $("#harness-result").classList.remove("hidden");
    notify("Hermes binding validated; the host profile command is ready.");
    await loadDashboard();
  } catch (error) {
    notify(error.message, true);
  } finally {
    button.disabled = false;
  }
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
