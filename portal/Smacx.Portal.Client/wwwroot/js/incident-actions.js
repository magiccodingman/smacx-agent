export async function copyText(value) {
  await navigator.clipboard.writeText(value);
}

const dismissedIncidentKey = "smacx.dismissed-capability-incidents";

function readDismissedIncidents() {
  try {
    const value = JSON.parse(localStorage.getItem(dismissedIncidentKey) || "[]");
    return Array.isArray(value) ? value.filter(item => typeof item === "string") : [];
  } catch {
    return [];
  }
}

export function isIncidentDismissed(incidentId) {
  return readDismissedIncidents().includes(incidentId);
}

export function dismissIncident(incidentId) {
  const incidents = readDismissedIncidents().filter(item => item !== incidentId);
  incidents.push(incidentId);
  try {
    localStorage.setItem(dismissedIncidentKey, JSON.stringify(incidents.slice(-50)));
  } catch {
    // Dismissal still applies to the live component when storage is unavailable.
  }
}

export function restoreIncident(incidentId) {
  const incidents = readDismissedIncidents().filter(item => item !== incidentId);
  try {
    localStorage.setItem(dismissedIncidentKey, JSON.stringify(incidents));
  } catch {
    // The live component can still reopen when storage is unavailable.
  }
}

export async function downloadFile(url, suggestedName) {
  const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Download failed (${response.status})`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = suggestedName || "smacx-capability-gap.zip";
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }
}
