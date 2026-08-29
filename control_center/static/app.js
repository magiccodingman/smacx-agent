const $ = (selector) => document.querySelector(selector);
const sections = ["setup", "login", "dashboard"];

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
  const [status, providerResult] = await Promise.all([
    api("/api/v1/status"), api("/api/v1/providers"),
  ]);
  $("#installation").textContent = status.installation_id;
  $("#provider-count").textContent = status.counts.model_providers;
  $("#match-count").textContent = status.counts.matches;
  $("#worker-count").textContent = status.counts.instances;
  renderProviders(providerResult.providers);
  show("dashboard");
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
