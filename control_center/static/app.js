const $ = (selector) => document.querySelector(selector);
const sections = ["setup", "login", "dashboard"];
let dashboardState = {agents: [], sources: [], runtimes: [], workers: []};

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
  const [status, providerResult, agentsResult, sourcesResult, runtimesResult, workersResult] = await Promise.all([
    api("/api/v1/status"), api("/api/v1/providers"), api("/api/v1/agents"),
    api("/api/v1/game-sources"), api("/api/v1/runtimes"), api("/api/v1/workers"),
  ]);
  dashboardState = {
    agents: agentsResult.agents,
    sources: sourcesResult.game_sources,
    runtimes: runtimesResult.runtimes,
    workers: workersResult.workers,
  };
  $("#installation").textContent = status.installation_id;
  $("#provider-count").textContent = status.counts.model_providers;
  $("#match-count").textContent = status.counts.matches;
  $("#worker-count").textContent = status.counts.instances;
  renderProviders(providerResult.providers);
  renderAssets(dashboardState.sources, dashboardState.runtimes, workersResult.docker);
  renderAgents(dashboardState.agents);
  renderWorkers(dashboardState.workers);
  populateSelect("#match-agent", dashboardState.agents, "agent_id", "display_name", "Create an agent first");
  populateSelect("#match-source", dashboardState.sources, "game_source_id", "display_name", "Validate game files first");
  populateSelect("#match-runtime", dashboardState.runtimes, "runtime_id", "display_name", "Import Proton first");
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
