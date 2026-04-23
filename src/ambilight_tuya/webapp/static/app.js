const COLOR_PRESETS = [
  { key: "warm", label: "Warm", rgb: "255,188,97" },
  { key: "emerald", label: "Emerald", rgb: "38,205,155" },
  { key: "ocean", label: "Ocean", rgb: "69,134,255" },
  { key: "violet", label: "Violet", rgb: "146,106,255" },
  { key: "rose", label: "Rose", rgb: "255,102,136" },
];

const KNOWN_DEVICES_STORAGE_KEY = "ambilight_tuya_known_devices";
const DEVICE_SPACES_STORAGE_KEY = "ambilight_tuya_device_spaces";
const DEFAULT_SPACES = ["Living Room", "Dormitorio", "Cocina", "Oficina", "Sin espacio"];

const state = {
  devices: [],
  busyDevices: new Set(),
  lastRefreshLabel: "Never refreshed",
  selectedDeviceId: "",
  selectedSpace: "all",
  previewTimerId: null,
  previewBusy: false,
  previewTargetFps: 4,
  previewRunning: false,
};

const els = {
  deviceGrid: document.querySelector("#device-grid"),
  globalFeedback: document.querySelector("#global-feedback"),
  deviceCount: document.querySelector("#device-count"),
  onlineCount: document.querySelector("#online-count"),
  rgbCount: document.querySelector("#rgb-count"),
  lastRefreshPill: document.querySelector("#last-refresh-pill"),
  oauthPill: document.querySelector("#oauth-pill"),
  knownDeviceOutput: document.querySelector("#known-device-output"),
  devicePicker: document.querySelector("#device-picker"),
  spaceFilter: document.querySelector("#space-filter"),
  ambilightGrid: document.querySelector("#ambilight-grid"),
  previewStatusPill: document.querySelector("#preview-status-pill"),
  previewRatePill: document.querySelector("#preview-rate-pill"),
  previewSampledPill: document.querySelector("#preview-sampled-pill"),
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function friendlyErrorMessage(message) {
  const text = String(message || "");
  if (text.includes("1106") || text.includes("permission deny")) {
    return "Tuya bloqueo el discovery de este usuario. Puedes seguir usando los dispositivos guardados localmente.";
  }
  if (text.includes("2008") || text.includes("command or value not support")) {
    return "Este dispositivo no acepta ese comando con su perfil actual.";
  }
  if (text.includes("brightness datapoint")) {
    return "Este dispositivo no ofrece control de brillo.";
  }
  if (text.includes("power switch datapoint")) {
    return "Este dispositivo no expone un switch compatible para control remoto.";
  }
  if (text.includes("color datapoints")) {
    return "Este dispositivo no soporta color.";
  }
  return text;
}

function powerBadge(powerState) {
  if (powerState === "on") return `<span class="badge badge-on">On</span>`;
  if (powerState === "off") return `<span class="badge badge-off">Off</span>`;
  return `<span class="badge badge-unknown">Unknown</span>`;
}

function renderPreviewPlaceholder(message) {
  if (!els.ambilightGrid) return;
  els.ambilightGrid.innerHTML = `<div class="ambilight-empty">${escapeHtml(message)}</div>`;
}

function renderAmbilightGrid(payload) {
  if (!els.ambilightGrid) return;
  const cells = Array.isArray(payload?.cells) ? payload.cells : [];
  if (!cells.length) {
    renderPreviewPlaceholder("No preview cells returned yet.");
    return;
  }
  els.ambilightGrid.innerHTML = cells.map((cell) => `
    <article class="ambilight-cell" style="background: linear-gradient(180deg, rgba(255,255,255,0.08), rgba(0,0,0,0.12)), ${escapeHtml(cell.hex)};">
      <div class="ambilight-cell-meta">
        <span>R${cell.row + 1} / C${cell.col + 1}</span>
        <span>#${cell.index + 1}</span>
      </div>
      <div class="ambilight-cell-hex">${escapeHtml(cell.hex)}</div>
    </article>
  `).join("");
}

function reachabilityBadge(device) {
  if (device.online === true) return `<span class="badge badge-online">Online</span>`;
  if (device.online === false) return `<span class="badge badge-offline">Offline</span>`;
  return `<span class="badge badge-unknown">Reachability unknown</span>`;
}

function defaultStatusMessage(device) {
  if (device.power_supported === false) return "This device is visible but does not expose remote power control.";
  if (device.online === false) return "Device appears offline.";
  if (device.source === "manual") return "Saved locally. Refresh status to validate reachability.";
  return "Ready for control.";
}

function loadKnownDevices() {
  try {
    const raw = window.localStorage.getItem(KNOWN_DEVICES_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveKnownDevices(devices) {
  window.localStorage.setItem(KNOWN_DEVICES_STORAGE_KEY, JSON.stringify(devices));
}

function loadDeviceSpaces() {
  try {
    const raw = window.localStorage.getItem(DEVICE_SPACES_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveDeviceSpaces(deviceSpaces) {
  window.localStorage.setItem(DEVICE_SPACES_STORAGE_KEY, JSON.stringify(deviceSpaces));
}

function setDeviceSpace(deviceId, room) {
  const nextSpaces = loadDeviceSpaces();
  if (!room || room === "Sin espacio") delete nextSpaces[deviceId];
  else nextSpaces[deviceId] = room;
  saveDeviceSpaces(nextSpaces);
}

function applyLocalSpaces(devices) {
  const deviceSpaces = loadDeviceSpaces();
  return devices.map((device) => ({
    ...device,
    room: deviceSpaces[device.id] || device.room || "",
  }));
}

function availableSpaces(devices = state.devices) {
  const fromDevices = devices
    .map((device) => device.room || "")
    .filter(Boolean);
  return Array.from(new Set([...DEFAULT_SPACES, ...fromDevices])).filter(Boolean);
}

function normalizeKnownDevice(device) {
  return {
    id: String(device.id || device.device_id || "").trim(),
    short_id: String(device.short_id || "").trim() || null,
    name: String(device.name || "Saved device").trim(),
    category: String(device.category || "manual").trim(),
    type_label: String(device.type_label || "Known device").trim(),
    product_name: String(device.product_name || "").trim(),
    online: device.online ?? null,
    reachability_label: device.reachability_label || "Unknown",
    power_state: device.power_state || "unknown",
    state_label: device.state_label || "Unknown",
    is_rgb_capable: Boolean(device.is_rgb_capable),
    supports_color: Boolean(device.is_rgb_capable),
    power_supported: device.power_supported ?? true,
    brightness_supported: Boolean(device.brightness_supported ?? device.is_rgb_capable),
    current_brightness: Number.isFinite(device.current_brightness) ? Number(device.current_brightness) : null,
    room: String(device.room || "").trim(),
    status_map: device.status_map || {},
    raw: device.raw || {},
    source: "manual",
    statusMessage: device.statusMessage || "Saved locally. Refresh status to validate reachability.",
    feedbackTone: device.feedbackTone || "default",
  };
}

function mergeKnownDevices(cloudDevices, knownDevices) {
  const merged = new Map();
  cloudDevices.forEach((device) => {
    merged.set(device.id, { ...device, source: device.source || "cloud" });
  });
  knownDevices.forEach((knownDevice) => {
    const normalizedKnown = normalizeKnownDevice(knownDevice);
    const existing = merged.get(normalizedKnown.id);
    if (existing) {
      merged.set(normalizedKnown.id, {
        ...normalizedKnown,
        ...existing,
        name: existing.name || normalizedKnown.name,
        type_label: existing.type_label || normalizedKnown.type_label,
        product_name: existing.product_name || normalizedKnown.product_name,
        is_rgb_capable: existing.is_rgb_capable ?? normalizedKnown.is_rgb_capable,
        supports_color: existing.supports_color ?? normalizedKnown.is_rgb_capable,
        room: existing.room || normalizedKnown.room,
        source: "cloud+manual",
      });
    } else {
      merged.set(normalizedKnown.id, normalizedKnown);
    }
  });
  return applyLocalSpaces(Array.from(merged.values())).sort((left, right) => {
    const leftKey = `${left.name}`.toLowerCase();
    const rightKey = `${right.name}`.toLowerCase();
    return leftKey.localeCompare(rightKey);
  });
}

function syncKnownDevicesFromState() {
  const savedDevices = state.devices
    .filter((device) => device.source === "manual" || device.source === "cloud+manual")
    .map((device) => ({
      id: device.id,
      name: device.name,
      type_label: device.type_label,
      category: device.category,
      product_name: device.product_name,
      is_rgb_capable: device.is_rgb_capable,
      brightness_supported: device.brightness_supported,
      room: device.room || "",
    }));
  saveKnownDevices(savedDevices);
  writeOutput("#known-device-output", { devices: savedDevices, count: savedDevices.length });
}

function updateHeroStats() {
  const devices = state.devices;
  els.deviceCount.textContent = String(devices.length);
  els.onlineCount.textContent = String(devices.filter((device) => device.online === true).length);
  els.rgbCount.textContent = String(devices.filter((device) => device.is_rgb_capable).length);
  els.lastRefreshPill.textContent = state.lastRefreshLabel;
}

function updatePreviewStatus({ running = state.previewRunning, sampledAt = null } = {}) {
  if (els.previewStatusPill) {
    els.previewStatusPill.textContent = running ? "Preview running" : "Preview idle";
  }
  if (els.previewRatePill) {
    els.previewRatePill.textContent = `${state.previewTargetFps} fps target`;
  }
  if (els.previewSampledPill) {
    els.previewSampledPill.textContent = sampledAt
      ? `Sampled ${new Date(sampledAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
      : "No sample yet";
  }
}

function setDevices(devices) {
  state.devices = devices.map((device) => ({
    ...device,
    room: device.room || "",
    statusMessage: device.statusMessage || defaultStatusMessage(device),
    feedbackTone: device.feedbackTone || "default",
  }));
  if (!state.devices.some((device) => device.id === state.selectedDeviceId)) {
    state.selectedDeviceId = state.devices[0]?.id || "";
  }
  renderDevices();
}

function updateDeviceSelectors() {
  const options = state.devices.map((device) => `
    <option value="${escapeHtml(device.id)}"${state.selectedDeviceId === device.id ? " selected" : ""}>
      ${escapeHtml(`${device.name} · ${device.short_id || device.id} · ${device.type_label || "Tuya device"} · ${device.reachability_label || "Unknown"}`)}
    </option>
  `).join("");

  if (els.devicePicker) {
    els.devicePicker.innerHTML = `<option value="">Select a device</option>${options}`;
    els.devicePicker.value = state.selectedDeviceId || "";
  }

  document.querySelectorAll("[data-device-select]").forEach((select) => {
    const currentValue = select.value;
    select.innerHTML = `<option value="">Select from dashboard</option>${options}`;
    select.value = state.devices.some((device) => device.id === currentValue) ? currentValue : state.selectedDeviceId || "";
  });

  if (els.spaceFilter) {
    const spaceOptions = availableSpaces().map((space) => {
      const selected = state.selectedSpace === space ? " selected" : "";
      return `<option value="${escapeHtml(space)}"${selected}>${escapeHtml(space)}</option>`;
    }).join("");
    els.spaceFilter.innerHTML = `<option value="all"${state.selectedSpace === "all" ? " selected" : ""}>All spaces</option>${spaceOptions}`;
  }
}

function renderDevices() {
  updateHeroStats();
  updateDeviceSelectors();

  if (!state.devices.length) {
    els.deviceGrid.innerHTML = `
      <article class="empty-state">
        <div>
          <h3>No devices available</h3>
          <p>Try <strong>Refresh devices</strong>, or add known device IDs in the local catalog above.</p>
        </div>
      </article>
    `;
    return;
  }

  const visibleDevices = state.selectedSpace === "all"
    ? state.devices
    : state.devices.filter((device) => (device.room || "Sin espacio") === state.selectedSpace);
  if (!visibleDevices.length) {
    els.deviceGrid.innerHTML = `
      <article class="empty-state">
        <div>
          <h3>No devices in this space</h3>
          <p>Try another filter or assign devices to a different space.</p>
        </div>
      </article>
    `;
    return;
  }

  const groupedDevices = new Map();
  visibleDevices.forEach((device) => {
    const room = device.room || "Sin espacio";
    const bucket = groupedDevices.get(room) || [];
    bucket.push(device);
    groupedDevices.set(room, bucket);
  });

  els.deviceGrid.innerHTML = Array.from(groupedDevices.entries()).map(([room, devices]) => `
    <section class="device-group">
      <div class="device-group-head">
        <div>
          <p class="section-kicker">Space</p>
          <h3>${escapeHtml(room)}</h3>
        </div>
        <span class="pill pill-muted">${devices.length} device${devices.length === 1 ? "" : "s"}</span>
      </div>
      <div class="device-group-grid">
        ${devices.map((device) => {
    const isBusy = state.busyDevices.has(device.id);
    const powerDisabled = isBusy || device.power_supported === false;
    const statusText = device.statusMessage || defaultStatusMessage(device);
    const sourceBadge = device.source === "manual" || device.source === "cloud+manual"
      ? `<span class="pill pill-muted">Saved</span>`
      : "";
    const dimmerControls = (device.brightness_supported || device.is_rgb_capable)
      ? `
        <section class="device-dimmer-panel">
          <div class="device-meta-row">
            <strong>Brightness</strong>
            <span class="pill pill-muted">${device.current_brightness ?? 0}%</span>
          </div>
          <div class="dimmer-row">
            <input
              type="range"
              min="0"
              max="100"
              value="${escapeHtml(device.current_brightness ?? 50)}"
              data-device-brightness-input="${escapeHtml(device.id)}"
              ${isBusy ? "disabled" : ""}
            >
            <button type="button" class="button-ghost" data-device-brightness-apply="${escapeHtml(device.id)}" ${isBusy ? "disabled" : ""}>Apply</button>
          </div>
        </section>
      `
      : "";
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
    const removeButton = (device.source === "manual" || device.source === "cloud+manual")
      ? `<button type="button" class="button-ghost" data-device-remove="${escapeHtml(device.id)}" ${isBusy ? "disabled" : ""}>Forget saved device</button>`
      : "";
    const roomOptions = availableSpaces(state.devices).map((space) => {
      const selected = (device.room || "Sin espacio") === space ? " selected" : "";
      return `<option value="${escapeHtml(space)}"${selected}>${escapeHtml(space)}</option>`;
    }).join("");

    return `
      <article class="device-card" data-device-card="${escapeHtml(device.id)}">
        <div class="device-top">
          <div class="device-meta-row">
            <div class="device-badge-stack">
              <span class="pill">${escapeHtml(device.type_label || "Tuya device")}</span>
              ${sourceBadge}
            </div>
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
          <button type="button" data-device-on="${escapeHtml(device.id)}" ${powerDisabled ? "disabled" : ""}>On</button>
          <button type="button" class="button-off" data-device-off="${escapeHtml(device.id)}" ${powerDisabled ? "disabled" : ""}>Off</button>
          <button type="button" class="button-ghost" data-device-refresh="${escapeHtml(device.id)}" ${isBusy ? "disabled" : ""}>Refresh</button>
          ${removeButton}
        </div>

        <div class="device-inline-selects">
          <label>Space
            <select data-device-room="${escapeHtml(device.id)}" ${isBusy ? "disabled" : ""}>
              ${roomOptions}
            </select>
          </label>
        </div>

        ${dimmerControls}
        ${colorControls}

        <div class="device-footer">
          <div class="device-feedback ${device.feedbackTone ? `is-${device.feedbackTone}` : ""}">${escapeHtml(statusText)}</div>
        </div>
      </article>
    `;
  }).join("")}
      </div>
    </section>
  `).join("");

  bindDeviceCardActions();
}

function mergeDeviceUpdate(deviceId, patch) {
  state.devices = state.devices.map((device) => (
    device.id === deviceId ? { ...device, ...patch } : device
  ));
}

function withDeviceBusy(deviceId, isBusy) {
  if (isBusy) state.busyDevices.add(deviceId);
  else state.busyDevices.delete(deviceId);
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
    supports_color: payload.supports_color ?? previousDevice.supports_color ?? false,
    power_supported: payload.power_supported ?? previousDevice.power_supported ?? true,
    brightness_supported: payload.brightness_supported ?? previousDevice.brightness_supported ?? false,
    current_brightness: payload.current_brightness ?? previousDevice.current_brightness ?? null,
    room: previousDevice.room || payload.room || "",
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

async function refreshAmbilightPreview({ quiet = false } = {}) {
  if (state.previewBusy) return;
  state.previewBusy = true;
  try {
    const payload = await getJson("/api/ambilight-preview");
    renderAmbilightGrid(payload);
    updatePreviewStatus({ running: state.previewRunning, sampledAt: payload.sampled_at });
    if (!quiet) {
      setGlobalFeedback("Ambilight preview updated.", "success");
    }
  } catch (error) {
    if (state.previewRunning && state.previewTimerId) {
      window.clearInterval(state.previewTimerId);
      state.previewTimerId = null;
      state.previewRunning = false;
    }
    renderPreviewPlaceholder(friendlyErrorMessage(error.message));
    updatePreviewStatus({ running: false, sampledAt: null });
    if (!quiet) {
      setGlobalFeedback(friendlyErrorMessage(error.message), "error");
    }
  } finally {
    state.previewBusy = false;
  }
}

async function startAmbilightPreview() {
  if (state.previewRunning) return;
  state.previewRunning = true;
  updatePreviewStatus({ running: true });
  await refreshAmbilightPreview({ quiet: true });
  state.previewTimerId = window.setInterval(() => {
    refreshAmbilightPreview({ quiet: true });
  }, Math.round(1000 / state.previewTargetFps));
  setGlobalFeedback("Ambilight preview started.", "success");
}

function stopAmbilightPreview() {
  if (state.previewTimerId) {
    window.clearInterval(state.previewTimerId);
    state.previewTimerId = null;
  }
  state.previewRunning = false;
  updatePreviewStatus({ running: false });
  setGlobalFeedback("Ambilight preview stopped.", "success");
}

async function refreshSystemStatus() {
  try {
    const payload = await getJson("/api/status");
    writeOutput("#status-output", payload);
    els.oauthPill.textContent = payload.oauth?.authorized ? "OAuth active" : "OAuth idle";
    if (payload.preview?.target_fps) {
      state.previewTargetFps = payload.preview.target_fps;
    }
    updatePreviewStatus({ running: state.previewRunning });
  } catch (error) {
    writeOutput("#status-output", error.message);
  } finally {
    await refreshDiagnostics();
  }
}

function loadSavedDevicesIntoState() {
  const knownDevices = loadKnownDevices().map((device) => ({
    ...normalizeKnownDevice(device),
    statusMessage: "Saved locally. Refresh status to validate reachability.",
  }));
  if (knownDevices.length) {
    setDevices(mergeKnownDevices(state.devices, knownDevices));
  }
  writeOutput("#known-device-output", { devices: loadKnownDevices(), count: loadKnownDevices().length });
  return knownDevices;
}

async function refreshDevices() {
  setGlobalFeedback("Refreshing device catalog...");
  const knownDevices = loadKnownDevices();
  try {
    const payload = await postJson("/api/list-devices");
    setDevices(mergeKnownDevices(payload.devices || [], knownDevices));
    state.lastRefreshLabel = formatNowLabel("Devices refreshed");
    setGlobalFeedback(`Loaded ${state.devices.length} device${state.devices.length === 1 ? "" : "s"}.`, "success");
  } catch (error) {
    const friendlyMessage = friendlyErrorMessage(error.message);
    if (knownDevices.length) {
      setDevices(mergeKnownDevices([], knownDevices));
      state.lastRefreshLabel = formatNowLabel("Saved catalog loaded");
      setGlobalFeedback(`${friendlyMessage} Showing ${knownDevices.length} saved device${knownDevices.length === 1 ? "" : "s"} instead.`, "error");
    } else {
      setDevices([]);
      setGlobalFeedback(`${friendlyMessage} Add known devices manually to see cards.`, "error");
    }
  } finally {
    syncKnownDevicesFromState();
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
    if (!quiet) setGlobalFeedback(`${device.name}: status updated.`, "success");
  } catch (error) {
    mergeDeviceUpdate(deviceId, { statusMessage: friendlyErrorMessage(error.message), feedbackTone: "error" });
    if (!quiet) setGlobalFeedback(`Status refresh failed for ${device.name}.`, "error");
  } finally {
    withDeviceBusy(deviceId, false);
    syncKnownDevicesFromState();
    await refreshDiagnostics();
  }
}

async function refreshAllStatuses() {
  if (!state.devices.length) {
    setGlobalFeedback("Load or save devices first so the dashboard knows what to refresh.", "error");
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
    mergeDeviceUpdate(deviceId, { statusMessage: friendlyErrorMessage(error.message), feedbackTone: "error" });
    renderDevices();
    setGlobalFeedback(`${device.name}: power command failed.`, "error");
  } finally {
    withDeviceBusy(deviceId, false);
    syncKnownDevicesFromState();
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
    mergeDeviceUpdate(deviceId, { statusMessage: friendlyErrorMessage(error.message), feedbackTone: "error" });
    renderDevices();
    setGlobalFeedback(`${device.name}: color command failed.`, "error");
  } finally {
    withDeviceBusy(deviceId, false);
    syncKnownDevicesFromState();
    await refreshSystemStatus();
  }
}

async function applyBrightness(deviceId, level) {
  const device = state.devices.find((item) => item.id === deviceId);
  if (!device) return;
  withDeviceBusy(deviceId, true);
  try {
    await postJson("/api/set-brightness", { device_id: deviceId, level });
    mergeDeviceUpdate(deviceId, {
      current_brightness: Number(level),
      statusMessage: `Brightness set to ${level}%.`,
      feedbackTone: "success",
    });
    renderDevices();
    await refreshDeviceStatus(deviceId, { quiet: true });
    setGlobalFeedback(`${device.name}: brightness updated.`, "success");
  } catch (error) {
    mergeDeviceUpdate(deviceId, { statusMessage: friendlyErrorMessage(error.message), feedbackTone: "error" });
    renderDevices();
    setGlobalFeedback(`${device.name}: brightness command failed.`, "error");
  } finally {
    withDeviceBusy(deviceId, false);
    syncKnownDevicesFromState();
    await refreshSystemStatus();
  }
}

function assignDeviceSpace(deviceId, room) {
  setDeviceSpace(deviceId, room);
  mergeDeviceUpdate(deviceId, { room: room === "Sin espacio" ? "" : room });
  renderDevices();
  syncKnownDevicesFromState();
  setGlobalFeedback("Device space updated.", "success");
}

function removeKnownDevice(deviceId) {
  const knownDevices = loadKnownDevices().filter((device) => device.id !== deviceId);
  saveKnownDevices(knownDevices);
  setDeviceSpace(deviceId, "");
  state.devices = state.devices.filter((device) => device.id !== deviceId);
  renderDevices();
  writeOutput("#known-device-output", { devices: knownDevices, count: knownDevices.length });
  setGlobalFeedback("Saved device removed from local catalog.", "success");
}

function resolveFormDeviceId(form, selectName = "device_select", textName = "device_id") {
  return String(form.get(selectName) || form.get(textName) || "").trim();
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
      applyColor(button.dataset.deviceCustomColor, input?.value?.trim() || "");
    });
  });
  document.querySelectorAll("[data-device-brightness-apply]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.querySelector(`[data-device-brightness-input="${button.dataset.deviceBrightnessApply}"]`);
      applyBrightness(button.dataset.deviceBrightnessApply, Number(input?.value || 0));
    });
  });
  document.querySelectorAll("[data-device-room]").forEach((select) => {
    select.addEventListener("change", () => assignDeviceSpace(select.dataset.deviceRoom, select.value));
  });
  document.querySelectorAll("[data-device-remove]").forEach((button) => {
    button.addEventListener("click", () => removeKnownDevice(button.dataset.deviceRemove));
  });
}

bindAction("[data-action='refresh-devices']", async () => {
  await refreshDevices();
});

bindAction("[data-action='refresh-status']", async () => {
  await refreshAllStatuses();
});

bindAction("[data-action='preview-start']", async () => {
  await startAmbilightPreview();
});

bindAction("[data-action='preview-stop']", () => {
  stopAmbilightPreview();
});

bindAction("[data-action='preview-refresh']", async () => {
  await refreshAmbilightPreview();
});

bindAction("[data-action='picker-on']", async () => {
  if (!state.selectedDeviceId) {
    setGlobalFeedback("Select a device first.", "error");
    return;
  }
  await applyPower(state.selectedDeviceId, "on");
});

bindAction("[data-action='picker-off']", async () => {
  if (!state.selectedDeviceId) {
    setGlobalFeedback("Select a device first.", "error");
    return;
  }
  await applyPower(state.selectedDeviceId, "off");
});

bindAction("[data-action='picker-refresh']", async () => {
  if (!state.selectedDeviceId) {
    setGlobalFeedback("Select a device first.", "error");
    return;
  }
  await refreshDeviceStatus(state.selectedDeviceId);
});

bindAction("[data-action='load-known-devices']", async () => {
  const knownDevices = loadSavedDevicesIntoState();
  setGlobalFeedback(
    knownDevices.length
      ? `Loaded ${knownDevices.length} saved device${knownDevices.length === 1 ? "" : "s"} from local catalog.`
      : "No saved devices found in this browser.",
    knownDevices.length ? "success" : "error",
  );
  await refreshSystemStatus();
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

bindForm("#known-device-form", async (form) => {
  const entry = normalizeKnownDevice({
    id: form.get("device_id"),
    name: form.get("name"),
    type_label: form.get("type_label") || "Known device",
    room: form.get("room") || "",
    is_rgb_capable: form.get("is_rgb_capable") === "on",
  });
  if (!entry.id) {
    setGlobalFeedback("Device ID is required to save a known device.", "error");
    return;
  }
  const knownDevices = loadKnownDevices().filter((device) => device.id !== entry.id);
  knownDevices.push({
    id: entry.id,
    name: entry.name,
    type_label: entry.type_label,
    category: entry.category,
    product_name: entry.product_name,
    is_rgb_capable: entry.is_rgb_capable,
    room: entry.room,
  });
  if (entry.room) setDeviceSpace(entry.id, entry.room);
  saveKnownDevices(knownDevices);
  setDevices(mergeKnownDevices(state.devices, knownDevices));
  syncKnownDevicesFromState();
  setGlobalFeedback(`${entry.name} saved to the local catalog.`, "success");
  const formNode = document.querySelector("#known-device-form");
  if (formNode) formNode.reset();
  await refreshSystemStatus();
});

bindForm("#device-status-form", async (form) => {
  try {
    const payload = await postJson("/api/get-device-status", {
      device_id: resolveFormDeviceId(form),
    });
    writeOutput("#device-status-output", payload);
    setGlobalFeedback("Manual status lookup completed.", "success");
  } catch (error) {
    writeOutput("#device-status-output", friendlyErrorMessage(error.message));
    setGlobalFeedback(friendlyErrorMessage(error.message), "error");
  } finally {
    await refreshDiagnostics();
  }
});

bindForm("#power-form", async (form) => {
  try {
    const payload = await postJson("/api/set-power", {
      device_id: resolveFormDeviceId(form),
      zone: form.get("zone"),
      state: form.get("state"),
    });
    writeOutput("#power-output", payload);
    setGlobalFeedback("Manual power command sent.", "success");
  } catch (error) {
    writeOutput("#power-output", friendlyErrorMessage(error.message));
    setGlobalFeedback(friendlyErrorMessage(error.message), "error");
  } finally {
    await refreshDiagnostics();
  }
});

bindForm("#fixed-color-form", async (form) => {
  try {
    const payload = await postJson("/api/set-fixed-color", {
      device_id: resolveFormDeviceId(form),
      zone: form.get("zone"),
      rgb: form.get("rgb"),
    });
    writeOutput("#fixed-color-output", payload);
    setGlobalFeedback("Manual color command sent.", "success");
  } catch (error) {
    writeOutput("#fixed-color-output", friendlyErrorMessage(error.message));
    setGlobalFeedback(friendlyErrorMessage(error.message), "error");
  } finally {
    await refreshDiagnostics();
  }
});

bindForm("#brightness-form", async (form) => {
  try {
    const payload = await postJson("/api/set-brightness", {
      device_id: resolveFormDeviceId(form),
      level: Number(form.get("level")),
    });
    writeOutput("#brightness-output", payload);
    setGlobalFeedback("Manual brightness command sent.", "success");
  } catch (error) {
    writeOutput("#brightness-output", friendlyErrorMessage(error.message));
    setGlobalFeedback(friendlyErrorMessage(error.message), "error");
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

if (els.devicePicker) {
  els.devicePicker.addEventListener("change", (event) => {
    state.selectedDeviceId = event.target.value;
  });
}

if (els.spaceFilter) {
  els.spaceFilter.addEventListener("change", (event) => {
    state.selectedSpace = event.target.value;
    renderDevices();
  });
}

window.addEventListener("beforeunload", () => {
  stopAmbilightPreview();
});

renderPreviewPlaceholder("Start preview to sample the main monitor.");
updatePreviewStatus({ running: false, sampledAt: null });
loadSavedDevicesIntoState();
refreshDiagnostics();
refreshSystemStatus();
refreshDevices();
