const COLOR_PRESETS = [
  { key: "warm", label: "Warm", rgb: "255,188,97" },
  { key: "emerald", label: "Emerald", rgb: "38,205,155" },
  { key: "ocean", label: "Ocean", rgb: "69,134,255" },
  { key: "violet", label: "Violet", rgb: "146,106,255" },
  { key: "rose", label: "Rose", rgb: "255,102,136" },
];

const state = {
  devices: [],
  busyDevices: new Set(),
  lastRefreshLabel: "Never refreshed",
};

const els = {
  deviceGrid: document.querySelector("#device-grid"),
  globalFeedback: document.querySelector("#global-feedback"),
  deviceCount: document.querySelector("#device-count"),
  onlineCount: document.querySelector("#online-count"),
  rgbCount: document.querySelector("#rgb-count"),
  lastRefreshPill: document.querySelector("#last-refresh-pill"),
  oauthPill: document.querySelector("#oauth-pill"),
};

const pretty = (value) => JSON.stringify(value, null, 2);

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const raw = await response.text();
  let data;
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(raw || `Request failed with status ${response.status}`);
  }
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

async function getJson(url) {
  const response = await fetch(url);
  const raw = await response.text();
  let data;
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(raw || `Request failed with status ${response.status}`);
  }
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

function writeOutput(selector, payload) {
  const target = document.querySelector(selector);
  if (!target) return;
  target.textContent = typeof payload === "string" ? payload : pretty(payload);
}

function setGlobalFeedback(message, tone = "default") {
  if (!message) {
    els.globalFeedback.className = "feedback-banner is-hidden";
    els.globalFeedback.textContent = "";
    return;
  }
  els.globalFeedback.className = `feedback-banner${tone === "error" ? " is-error" : tone === "success" ? " is-success" : ""}`;
  els.globalFeedback.textContent = message;
}

function bindAction(selector, handler) {
  const node = document.querySelector(selector);
  if (node) node.addEventListener("click", handler);
}

function bindForm(selector, handler) {
  const node = document.querySelector(selector);
  if (!node) return;
  node.addEventListener("submit", (event) => {
    event.preventDefault();
    handler(new FormData(event.currentTarget));
  });
}

function updateHeroStats() {
  const devices = state.devices;
  const onlineCount = devices.filter((device) => device.online === true).length;
  const rgbCount = devices.filter((device) => device.is_rgb_capable).length;
  els.deviceCount.textContent = String(devices.length);
  els.onlineCount.textContent = String(onlineCount);
  els.rgbCount.textContent = String(rgbCount);
  els.lastRefreshPill.textContent = state.lastRefreshLabel;
}

function powerBadge(powerState) {
  if (powerState === "on") {
    return `<span class="badge badge-on">On</span>`;
  }
  if (powerState === "off") {
    return `<span class="badge badge-off">Off</span>`;
  }
  return `<span class="badge badge-unknown">Unknown</span>`;
}

function reachabilityBadge(device) {
  if (device.online === true) {
    return `<span class="badge badge-online">Online</span>`;
  }
  if (device.online === false) {
    return `<span class="badge badge-offline">Offline</span>`;
  }
  return `<span class="badge badge-unknown">Reachability unknown</span>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderDevices() {
  updateHeroStats();

  if (!state.devices.length) {
    els.deviceGrid.innerHTML = `
      <article class="empty-state">
        <div>
          <h3>No devices available</h3>
          <p>Try <strong>Refresh devices</strong>. If discovery is blocked, you can still use the manual controls in the advanced drawer.</p>
        </div>
      </article>
    `;
    return;
  }

  els.deviceGrid.innerHTML = state.devices.map((device) => {
    const isBusy = state.busyDevices.has(device.id);
    const statusText = device.statusMessage || (device.online === false ? "Device appears offline." : "Ready for control.");
    const colorControls = device.is_rgb_capable
      ? `
        <section class="device-color-panel">
          <div class="device-meta-row">
            <strong>Color presets</strong>
            <span class="pill pill-muted">RGB enabled</span>
          </div>
          <div class="color-presets">
            ${COLOR_PRESETS.map((preset) => `
              <button
                type="button"
                class="color-chip"
                data-color="${preset.key}"
                data-device-color="${escapeHtml(device.id)}"
                data-rgb="${preset.rgb}"
                title="${preset.label}"
                ${isBusy ? "disabled" : ""}
              ></button>
            `).join("")}
          </div>
          <div class="color-input-row">
            <input type="text" value="255,80,40" data-custom-color-input="${escapeHtml(device.id)}" aria-label="Custom RGB value for ${escapeHtml(device.name)}">
            <button type="button" data-device-custom-color="${escapeHtml(device.id)}" ${isBusy ? "disabled" : ""}>Apply color</button>
          </div>
        </section>
      `
      : "";

    return `
      <article class="device-card" data-device-card="${escapeHtml(device.id)}">
        <div class="device-top">
          <div class="device-meta-row">
            <span class="pill">${escapeHtml(device.type_label || "Tuya device")}</span>
            ${reachabilityBadge(device)}
          </div>
          <div>
            <h3 class="device-name">${escapeHtml(device.name)}</h3>
            <p class="device-subtitle">${escapeHtml(device.product_name || device.category || "Connected device")}</p>
          </div>
          <div class="device-meta-row">
            <span class="device-id">${escapeHtml(device.short_id || device.id)}</span>
            ${powerBadge(device.power_state)}
          </div>
        </div>

        <div class="device-actions">
          <button type="button" data-device-on="${escapeHtml(device.id)}" ${isBusy ? "disabled" : ""}>On</button>
          <button type="button" class="button-off" data-device-off="${escapeHtml(device.id)}" ${isBusy ? "disabled" : ""}>Off</button>
          <button type="button" class="button-ghost" data-device-refresh="${escapeHtml(device.id)}" ${isBusy ? "disabled" : ""}>Refresh</button>
        </div>

        ${colorControls}

        <div class="device-footer">
          <div class="device-feedback ${device.feedbackTone ? `is-${device.feedbackTone}` : ""}" data-feedback-for="${escapeHtml(device.id)}">${escapeHtml(statusText)}</div>
        </div>
      </article>
    `;
  }).join("");

  bindDeviceCardActions();
}

function setDeviceFeedback(deviceId, message, tone = "default") {
  state.devices = state.devices.map((device) => (
    device.id === deviceId
      ? { ...device, statusMessage: message, feedbackTone: tone }
      : device
  ));
  renderDevices();
}

function mergeDeviceUpdate(deviceId, patch) {
  state.devices = state.devices.map((device) => (
    device.id === deviceId ? { ...device, ...patch } : device
  ));
}

function withDeviceBusy(deviceId, isBusy) {
  if (isBusy) {
    state.busyDevices.add(deviceId);
  } else {
    state.busyDevices.delete(deviceId);
  }
  renderDevices();
}

function formatNowLabel(prefix) {
  return `${prefix} ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function normalizeStatusPayload(payload, previousDevice = {}) {
  return {
    online: payload.online,
    reachability_label: payload.reachability_label,
    power_state: payload.power_state || previousDevice.power_state || "unknown",
    state_label: payload.state_label || previousDevice.state_label || "Unknown",
    is_rgb_capable: payload.is_rgb_capable ?? previousDevice.is_rgb_capable ?? false,
    status_map: payload.status_map || previousDevice.status_map || {},
    statusMessage: payload.online === false ? "Device appears offline." : "Status refreshed.",
    feedbackTone: "success",
  };
}

async function refreshDiagnostics() {
  try {
    writeOutput("#debug-output", await getJson("/api/debug/logs"));
  } catch (error) {
    writeOutput("#debug-output", error.message);
  }
}

async function refreshSystemStatus() {
  try {
    const payload = await getJson("/api/status");
    writeOutput("#status-output", payload);
    els.oauthPill.textContent = payload.oauth?.authorized ? "OAuth active" : "OAuth idle";
  } catch (error) {
    writeOutput("#status-output", error.message);
  } finally {
    await refreshDiagnostics();
  }
}

async function refreshDevices() {
  setGlobalFeedback("Refreshing device catalog...");
  try {
    const payload = await postJson("/api/list-devices");
    state.devices = (payload.devices || []).map((device) => ({
      ...device,
      statusMessage: device.online === false ? "Device appears offline." : "Ready for control.",
      feedbackTone: "default",
    }));
    state.lastRefreshLabel = formatNowLabel("Devices refreshed");
    renderDevices();
    setGlobalFeedback(`Loaded ${state.devices.length} device${state.devices.length === 1 ? "" : "s"}.`, "success");
  } catch (error) {
    state.devices = [];
    renderDevices();
    setGlobalFeedback(error.message, "error");
  } finally {
    await refreshSystemStatus();
  }
}

async function refreshDeviceStatus(deviceId, { quiet = false } = {}) {
  const device = state.devices.find((item) => item.id === deviceId);
  if (!device) return;
  withDeviceBusy(deviceId, true);
  try {
    const payload = await postJson("/api/get-device-status", { device_id: deviceId });
    mergeDeviceUpdate(deviceId, normalizeStatusPayload(payload, device));
    if (!quiet) {
      setDeviceFeedback(deviceId, "Status updated.", "success");
    }
  } catch (error) {
    mergeDeviceUpdate(deviceId, { statusMessage: error.message, feedbackTone: "error" });
    if (!quiet) {
      setGlobalFeedback(`Status refresh failed for ${device.name}.`, "error");
    }
  } finally {
    withDeviceBusy(deviceId, false);
    await refreshDiagnostics();
  }
}

async function refreshAllStatuses() {
  if (!state.devices.length) {
    setGlobalFeedback("Load devices first so the dashboard knows what to refresh.", "error");
    return;
  }
  setGlobalFeedback("Refreshing device status...");
  await Promise.allSettled(state.devices.map((device) => refreshDeviceStatus(device.id, { quiet: true })));
  state.lastRefreshLabel = formatNowLabel("Status refreshed");
  renderDevices();
  setGlobalFeedback("Status refresh finished.", "success");
  await refreshSystemStatus();
}

async function applyPower(deviceId, powerState) {
  const device = state.devices.find((item) => item.id === deviceId);
  if (!device) return;
  withDeviceBusy(deviceId, true);
  try {
    await postJson("/api/set-power", { device_id: deviceId, state: powerState });
    mergeDeviceUpdate(deviceId, {
      power_state: powerState,
      state_label: powerState === "on" ? "On" : "Off",
      statusMessage: `Power ${powerState} command sent.`,
      feedbackTone: "success",
    });
    renderDevices();
    await refreshDeviceStatus(deviceId, { quiet: true });
    setGlobalFeedback(`${device.name}: power ${powerState}.`, "success");
  } catch (error) {
    mergeDeviceUpdate(deviceId, { statusMessage: error.message, feedbackTone: "error" });
    renderDevices();
    setGlobalFeedback(`${device.name}: power command failed.`, "error");
  } finally {
    withDeviceBusy(deviceId, false);
    await refreshSystemStatus();
  }
}

async function applyColor(deviceId, rgbValue) {
  const device = state.devices.find((item) => item.id === deviceId);
  if (!device) return;
  withDeviceBusy(deviceId, true);
  try {
    await postJson("/api/set-fixed-color", { device_id: deviceId, rgb: rgbValue });
    mergeDeviceUpdate(deviceId, {
      power_state: "on",
      state_label: "On",
      statusMessage: `Color ${rgbValue} applied.`,
      feedbackTone: "success",
    });
    renderDevices();
    await refreshDeviceStatus(deviceId, { quiet: true });
    setGlobalFeedback(`${device.name}: color updated.`, "success");
  } catch (error) {
    mergeDeviceUpdate(deviceId, { statusMessage: error.message, feedbackTone: "error" });
    renderDevices();
    setGlobalFeedback(`${device.name}: color command failed.`, "error");
  } finally {
    withDeviceBusy(deviceId, false);
    await refreshSystemStatus();
  }
}

function bindDeviceCardActions() {
  document.querySelectorAll("[data-device-on]").forEach((button) => {
    button.addEventListener("click", () => applyPower(button.dataset.deviceOn, "on"));
  });
  document.querySelectorAll("[data-device-off]").forEach((button) => {
    button.addEventListener("click", () => applyPower(button.dataset.deviceOff, "off"));
  });
  document.querySelectorAll("[data-device-refresh]").forEach((button) => {
    button.addEventListener("click", () => refreshDeviceStatus(button.dataset.deviceRefresh));
  });
  document.querySelectorAll("[data-device-color]").forEach((button) => {
    button.addEventListener("click", () => applyColor(button.dataset.deviceColor, button.dataset.rgb));
  });
  document.querySelectorAll("[data-device-custom-color]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.querySelector(`[data-custom-color-input="${button.dataset.deviceCustomColor}"]`);
      const value = input?.value?.trim() || "";
      applyColor(button.dataset.deviceCustomColor, value);
    });
  });
}

bindAction("[data-action='refresh-devices']", async () => {
  await refreshDevices();
});

bindAction("[data-action='refresh-status']", async () => {
  await refreshAllStatuses();
});

bindAction("[data-action='stop-sync']", async () => {
  try {
    writeOutput("#sync-output", await postJson("/api/sync/stop"));
    setGlobalFeedback("Sync engine stopped.", "success");
  } catch (error) {
    writeOutput("#sync-output", error.message);
    setGlobalFeedback(error.message, "error");
  } finally {
    await refreshDiagnostics();
    await refreshSystemStatus();
  }
});

bindAction("[data-action='refresh-debug']", async () => {
  await refreshDiagnostics();
});

bindAction("[data-action='clear-debug']", async () => {
  try {
    writeOutput("#debug-output", await postJson("/api/debug/clear"));
    setGlobalFeedback("Diagnostics cleared.", "success");
  } catch (error) {
    writeOutput("#debug-output", error.message);
    setGlobalFeedback(error.message, "error");
  } finally {
    await refreshDiagnostics();
    await refreshSystemStatus();
  }
});

bindForm("#device-status-form", async (form) => {
  try {
    const payload = await postJson("/api/get-device-status", {
      device_id: form.get("device_id"),
    });
    writeOutput("#device-status-output", payload);
    setGlobalFeedback("Manual status lookup completed.", "success");
  } catch (error) {
    writeOutput("#device-status-output", error.message);
    setGlobalFeedback(error.message, "error");
  } finally {
    await refreshDiagnostics();
  }
});

bindForm("#power-form", async (form) => {
  try {
    const payload = await postJson("/api/set-power", {
      device_id: form.get("device_id"),
      zone: form.get("zone"),
      state: form.get("state"),
    });
    writeOutput("#power-output", payload);
    setGlobalFeedback("Manual power command sent.", "success");
  } catch (error) {
    writeOutput("#power-output", error.message);
    setGlobalFeedback(error.message, "error");
  } finally {
    await refreshDiagnostics();
  }
});

bindForm("#fixed-color-form", async (form) => {
  try {
    const payload = await postJson("/api/set-fixed-color", {
      device_id: form.get("device_id"),
      zone: form.get("zone"),
      rgb: form.get("rgb"),
    });
    writeOutput("#fixed-color-output", payload);
    setGlobalFeedback("Manual color command sent.", "success");
  } catch (error) {
    writeOutput("#fixed-color-output", error.message);
    setGlobalFeedback(error.message, "error");
  } finally {
    await refreshDiagnostics();
  }
});

bindForm("#sample-form", async (form) => {
  try {
    const payload = await postJson("/api/screen-sample", {
      monitor_index: form.get("monitor_index") || null,
    });
    writeOutput("#sample-output", payload);
    setGlobalFeedback("Screen sample captured.", "success");
  } catch (error) {
    writeOutput("#sample-output", error.message);
    setGlobalFeedback(error.message, "error");
  } finally {
    await refreshDiagnostics();
  }
});

bindForm("#sync-form", async (form) => {
  try {
    const payload = await postJson("/api/sync/start", {
      duration: form.get("duration") || null,
      monitor_index: form.get("monitor_index") || null,
      dry_run: form.get("dry_run") === "on",
    });
    writeOutput("#sync-output", payload);
    setGlobalFeedback("Sync engine started.", "success");
  } catch (error) {
    writeOutput("#sync-output", error.message);
    setGlobalFeedback(error.message, "error");
  } finally {
    await refreshDiagnostics();
    await refreshSystemStatus();
  }
});

refreshDiagnostics();
refreshSystemStatus();
refreshDevices();
