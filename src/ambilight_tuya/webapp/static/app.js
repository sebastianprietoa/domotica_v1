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
const BRIGHTNESS_DEBOUNCE_MS = 180;

const state = {
  devices: [],
  busyDevices: new Set(),
  brightnessTimers: new Map(),
  lastRefreshLabel: "Never refreshed",
  selectedDeviceKey: "",
  selectedSpace: "all",
  previewTimerId: null,
  previewBusy: false,
  previewTargetFps: 8,
  previewRunning: false,
  previewMonitorIndex: 1,
  previewMonitors: [],
  previewMapping: {},
  lastPreviewPayload: null,
  liveApplyRunning: false,
  liveApplyBusy: false,
  liveApplyMinIntervalMs: 250,
  lastLiveApplyAt: 0,
};

const els = {
  deviceGrid: document.querySelector("#device-grid"),
  globalFeedback: document.querySelector("#global-feedback"),
  deviceCount: document.querySelector("#device-count"),
  onlineCount: document.querySelector("#online-count"),
  rgbCount: document.querySelector("#rgb-count"),
  lastRefreshPill: document.querySelector("#last-refresh-pill"),
  oauthPill: document.querySelector("#oauth-pill"),
  huePill: document.querySelector("#hue-pill"),
  knownDeviceOutput: document.querySelector("#known-device-output"),
  devicePicker: document.querySelector("#device-picker"),
  spaceFilter: document.querySelector("#space-filter"),
  ambilightGrid: document.querySelector("#ambilight-grid"),
  previewStatusPill: document.querySelector("#preview-status-pill"),
  previewRatePill: document.querySelector("#preview-rate-pill"),
  previewSampledPill: document.querySelector("#preview-sampled-pill"),
  previewMonitorSelect: document.querySelector("#preview-monitor-select"),
  previewMonitorPill: document.querySelector("#preview-monitor-pill"),
  previewApplyPill: document.querySelector("#preview-apply-pill"),
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

function setGlobalFeedback(message, tone = "default") {
  if (!message) {
    els.globalFeedback.className = "feedback-banner is-hidden";
    els.globalFeedback.textContent = "";
    return;
  }
  els.globalFeedback.className = `feedback-banner${tone === "error" ? " is-error" : tone === "success" ? " is-success" : ""}`;
  els.globalFeedback.textContent = message;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function deviceKey(provider, deviceId) {
  return `${provider}:${deviceId}`;
}

function splitDeviceKey(rawValue) {
  const text = String(rawValue || "").trim();
  if (!text.includes(":")) {
    return { provider: "tuya", deviceId: text, deviceKey: deviceKey("tuya", text) };
  }
  const [provider, ...rest] = text.split(":");
  const deviceId = rest.join(":");
  return {
    provider: provider || "tuya",
    deviceId,
    deviceKey: deviceKey(provider || "tuya", deviceId),
  };
}

function getDeviceByKey(rawDeviceKey) {
  return state.devices.find((device) => device.device_key === rawDeviceKey);
}

function formatNowLabel(prefix) {
  return `${prefix} ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function friendlyErrorMessage(message) {
  const text = String(message || "");
  if (text.includes("1106") || text.includes("permission deny")) {
    return "Tuya bloqueo el discovery de este usuario. Puedes seguir usando dispositivos guardados o proveedores alternos.";
  }
  if (text.includes("2008") || text.includes("command or value not support")) {
    return "Este dispositivo no acepta ese comando con su perfil actual.";
  }
  if (text.includes("brightness datapoint")) {
    return "Este dispositivo no ofrece control de brillo.";
  }
  if (text.includes("Hue Bridge is not configured")) {
    return "Hue Bridge no esta configurado todavia en el backend.";
  }
  if (text.includes("Hue Bridge request failed")) {
    return "Hue Bridge rechazo el comando o no respondio como se esperaba.";
  }
  return text;
}

function powerBadge(powerState) {
  if (powerState === "on") return `<span class="badge badge-on">On</span>`;
  if (powerState === "off") return `<span class="badge badge-off">Off</span>`;
  return `<span class="badge badge-unknown">Unknown</span>`;
}

function reachabilityBadge(device) {
  if (device.online === true) return `<span class="badge badge-online">Online</span>`;
  if (device.online === false) return `<span class="badge badge-offline">Offline</span>`;
  return `<span class="badge badge-unknown">Reachability unknown</span>`;
}

function providerBadge(device) {
  return `<span class="pill provider-badge provider-${escapeHtml(device.provider || "tuya")}">${escapeHtml(device.provider_label || device.provider || "Provider")}</span>`;
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

function setDeviceSpace(deviceKeyValue, room) {
  const nextSpaces = loadDeviceSpaces();
  if (!room || room === "Sin espacio") delete nextSpaces[deviceKeyValue];
  else nextSpaces[deviceKeyValue] = room;
  saveDeviceSpaces(nextSpaces);
}

function applyLocalSpaces(devices) {
  const deviceSpaces = loadDeviceSpaces();
  return devices.map((device) => ({
    ...device,
    room: deviceSpaces[device.device_key] || device.room || "",
  }));
}

function availableSpaces(devices = state.devices) {
  const fromDevices = devices
    .map((device) => device.room || "")
    .filter(Boolean);
  return Array.from(new Set([...DEFAULT_SPACES, ...fromDevices])).filter(Boolean);
}

function normalizeKnownDevice(device) {
  const provider = String(device.provider || "tuya").trim().toLowerCase() || "tuya";
  const id = String(device.id || device.device_id || "").trim();
  return {
    id,
    provider,
    provider_label: provider === "hue" ? "Hue" : "Tuya",
    device_key: device.device_key || deviceKey(provider, id),
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
    supports_color: Boolean(device.supports_color ?? device.is_rgb_capable),
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
    merged.set(device.device_key, { ...device, source: device.source || "cloud" });
  });
  knownDevices.forEach((knownDevice) => {
    const normalizedKnown = normalizeKnownDevice(knownDevice);
    const existing = merged.get(normalizedKnown.device_key);
    if (existing) {
      merged.set(normalizedKnown.device_key, {
        ...normalizedKnown,
        ...existing,
        room: existing.room || normalizedKnown.room,
        source: "cloud+manual",
      });
    } else {
      merged.set(normalizedKnown.device_key, normalizedKnown);
    }
  });
  return applyLocalSpaces(Array.from(merged.values())).sort((left, right) => {
    const leftKey = `${left.room || ""} ${left.name}`.toLowerCase();
    const rightKey = `${right.room || ""} ${right.name}`.toLowerCase();
    return leftKey.localeCompare(rightKey);
  });
}

function syncKnownDevicesFromState() {
  const savedDevices = state.devices
    .filter((device) => device.source === "manual" || device.source === "cloud+manual")
    .map((device) => ({
      id: device.id,
      provider: device.provider,
      device_key: device.device_key,
      name: device.name,
      type_label: device.type_label,
      category: device.category,
      product_name: device.product_name,
      is_rgb_capable: device.is_rgb_capable,
      supports_color: device.supports_color,
      brightness_supported: device.brightness_supported,
      room: device.room || "",
    }));
  saveKnownDevices(savedDevices);
  writeOutput("#known-device-output", { devices: savedDevices, count: savedDevices.length });
}

function colorCapableDevices() {
  return state.devices.filter((device) => device.supports_color || device.is_rgb_capable);
}

function updateHeroStats() {
  els.deviceCount.textContent = String(state.devices.length);
  els.onlineCount.textContent = String(state.devices.filter((device) => device.online === true).length);
  els.rgbCount.textContent = String(colorCapableDevices().length);
  els.lastRefreshPill.textContent = state.lastRefreshLabel;
}

function updatePreviewMonitorOptions(monitors = state.previewMonitors) {
  if (!els.previewMonitorSelect) return;
  const options = monitors.map((monitor) => {
    const label = `Monitor ${monitor.index}${monitor.is_primary ? " (Primary)" : ""} - ${monitor.width}x${monitor.height}`;
    const selected = Number(monitor.index) === Number(state.previewMonitorIndex) ? " selected" : "";
    return `<option value="${escapeHtml(monitor.index)}"${selected}>${escapeHtml(label)}</option>`;
  }).join("");
  els.previewMonitorSelect.innerHTML = options || '<option value="1">Monitor 1</option>';
}

function updatePreviewStatus({ running = state.previewRunning, sampledAt = null } = {}) {
  if (els.previewStatusPill) {
    els.previewStatusPill.textContent = running ? "Preview running" : "Preview idle";
  }
  if (els.previewRatePill) {
    const applyFps = Math.round(1000 / state.liveApplyMinIntervalMs);
    els.previewRatePill.textContent = `${state.previewTargetFps} fps preview · ${applyFps} fps apply`;
  }
  if (els.previewSampledPill) {
    els.previewSampledPill.textContent = sampledAt
      ? `Sampled ${new Date(sampledAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
      : "No sample yet";
  }
  if (els.previewMonitorPill) {
    const currentMonitor = state.previewMonitors.find((monitor) => Number(monitor.index) === Number(state.previewMonitorIndex));
    els.previewMonitorPill.textContent = currentMonitor
      ? `Source M${currentMonitor.index}${currentMonitor.is_primary ? " primary" : ""}`
      : `Source M${state.previewMonitorIndex}`;
  }
  if (els.previewApplyPill) {
    els.previewApplyPill.textContent = state.liveApplyRunning ? "Live apply active" : "Live apply idle";
  }
}

function setDevices(devices) {
  state.devices = devices.map((device) => ({
    ...device,
    device_key: device.device_key || deviceKey(device.provider || "tuya", device.id),
    provider: device.provider || "tuya",
    provider_label: device.provider_label || (device.provider === "hue" ? "Hue" : "Tuya"),
    room: device.room || "",
    statusMessage: device.statusMessage || defaultStatusMessage(device),
    feedbackTone: device.feedbackTone || "default",
  }));
  if (!state.devices.some((device) => device.device_key === state.selectedDeviceKey)) {
    state.selectedDeviceKey = state.devices[0]?.device_key || "";
  }
  renderDevices();
}

function updateDeviceSelectors() {
  const options = state.devices.map((device) => `
    <option value="${escapeHtml(device.device_key)}"${state.selectedDeviceKey === device.device_key ? " selected" : ""}>
      ${escapeHtml(`${device.provider_label} · ${device.name} · ${device.short_id || device.id} · ${device.type_label || "Device"} · ${device.reachability_label || "Unknown"}`)}
    </option>
  `).join("");

  if (els.devicePicker) {
    els.devicePicker.innerHTML = `<option value="">Select a device</option>${options}`;
    els.devicePicker.value = state.selectedDeviceKey || "";
  }

  document.querySelectorAll("[data-device-select]").forEach((select) => {
    const currentValue = select.value;
    select.innerHTML = `<option value="">Select from dashboard</option>${options}`;
    select.value = state.devices.some((device) => device.device_key === currentValue) ? currentValue : state.selectedDeviceKey || "";
  });

  if (els.spaceFilter) {
    const spaceOptions = availableSpaces().map((space) => {
      const selected = state.selectedSpace === space ? " selected" : "";
      return `<option value="${escapeHtml(space)}"${selected}>${escapeHtml(space)}</option>`;
    }).join("");
    els.spaceFilter.innerHTML = `<option value="all"${state.selectedSpace === "all" ? " selected" : ""}>All spaces</option>${spaceOptions}`;
  }
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

  const mappingOptions = colorCapableDevices().map((device) => `
    <option value="${escapeHtml(device.device_key)}">${escapeHtml(`${device.provider_label} · ${device.name}`)}</option>
  `).join("");

  els.ambilightGrid.innerHTML = cells.map((cell) => {
    const cellKey = `r${cell.row}c${cell.col}`;
    const mapping = state.previewMapping[cellKey] || null;
    const mappedKey = mapping ? deviceKey(mapping.provider, mapping.device_id) : "";
    const mappedDevice = mappedKey ? getDeviceByKey(mappedKey) : null;
    return `
      <article class="ambilight-cell" style="background: linear-gradient(180deg, rgba(255,255,255,0.08), rgba(0,0,0,0.12)), ${escapeHtml(cell.hex)};">
        <div class="ambilight-cell-meta">
          <span>R${cell.row + 1} / C${cell.col + 1}</span>
          <span>#${cell.index + 1}</span>
        </div>
        <div class="ambilight-cell-hex">${escapeHtml(cell.hex)}</div>
        <div class="ambilight-cell-assignment">
          <label class="ambilight-cell-label">Target device</label>
          <select data-cell-mapping="${escapeHtml(cellKey)}">
            <option value="">Unmapped</option>
            ${mappingOptions}
          </select>
          <div class="ambilight-cell-assigned">${escapeHtml(mappedDevice ? `${mappedDevice.provider_label} · ${mappedDevice.name}` : "No device mapped")}</div>
        </div>
      </article>
    `;
  }).join("");

  document.querySelectorAll("[data-cell-mapping]").forEach((select) => {
    const cellKey = select.dataset.cellMapping;
    const mapping = state.previewMapping[cellKey];
    select.value = mapping ? deviceKey(mapping.provider, mapping.device_id) : "";
    select.addEventListener("change", async () => {
      await updateCellMapping(cellKey, select.value);
    });
  });
}

function renderDevices() {
  updateHeroStats();
  updateDeviceSelectors();

  if (!state.devices.length) {
    els.deviceGrid.innerHTML = `
      <article class="empty-state">
        <div>
          <h3>No devices available</h3>
          <p>Try <strong>Refresh devices</strong>. If discovery is blocked, you can still use saved devices or Hue devices.</p>
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
          const isBusy = state.busyDevices.has(device.device_key);
          const powerDisabled = isBusy || device.power_supported === false;
          const statusText = device.statusMessage || defaultStatusMessage(device);
          const sourceBadge = device.source === "manual" || device.source === "cloud+manual"
            ? `<span class="pill pill-muted">Saved</span>`
            : "";
          const roomOptions = availableSpaces(state.devices).map((space) => {
            const selected = (device.room || "Sin espacio") === space ? " selected" : "";
            return `<option value="${escapeHtml(space)}"${selected}>${escapeHtml(space)}</option>`;
          }).join("");
          const dimmerControls = (device.brightness_supported || device.is_rgb_capable)
            ? `
              <section class="device-dimmer-panel">
                <div class="device-meta-row">
                  <strong>Brightness</strong>
                  <span class="pill pill-muted" data-device-brightness-label="${escapeHtml(device.device_key)}">${device.current_brightness ?? 0}%</span>
                </div>
                <div class="dimmer-row">
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value="${escapeHtml(device.current_brightness ?? 50)}"
                    data-device-brightness-input="${escapeHtml(device.device_key)}"
                    ${isBusy ? "disabled" : ""}
                  >
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
                      data-device-color="${escapeHtml(device.device_key)}"
                      data-rgb="${preset.rgb}"
                      title="${preset.label}"
                      ${isBusy ? "disabled" : ""}
                    ></button>
                  `).join("")}
                </div>
                <div class="color-input-row">
                  <input type="text" value="255,80,40" data-custom-color-input="${escapeHtml(device.device_key)}" aria-label="Custom RGB value for ${escapeHtml(device.name)}">
                  <button type="button" data-device-custom-color="${escapeHtml(device.device_key)}" ${isBusy ? "disabled" : ""}>Apply color</button>
                </div>
              </section>
            `
            : "";
          const removeButton = (device.source === "manual" || device.source === "cloud+manual")
            ? `<button type="button" class="button-ghost" data-device-remove="${escapeHtml(device.device_key)}" ${isBusy ? "disabled" : ""}>Forget saved device</button>`
            : "";

          return `
            <article class="device-card" data-device-card="${escapeHtml(device.device_key)}">
              <div class="device-top">
                <div class="device-meta-row">
                  <div class="device-badge-stack">
                    ${providerBadge(device)}
                    <span class="pill">${escapeHtml(device.type_label || "Device")}</span>
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
                <button type="button" data-device-on="${escapeHtml(device.device_key)}" ${powerDisabled ? "disabled" : ""}>On</button>
                <button type="button" class="button-off" data-device-off="${escapeHtml(device.device_key)}" ${powerDisabled ? "disabled" : ""}>Off</button>
                <button type="button" class="button-ghost" data-device-refresh="${escapeHtml(device.device_key)}" ${isBusy ? "disabled" : ""}>Refresh</button>
                ${removeButton}
              </div>

              <div class="device-inline-selects">
                <label>Space
                  <select data-device-room="${escapeHtml(device.device_key)}" ${isBusy ? "disabled" : ""}>
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

function mergeDeviceUpdate(deviceKeyValue, patch) {
  state.devices = state.devices.map((device) => (
    device.device_key === deviceKeyValue ? { ...device, ...patch } : device
  ));
}

function withDeviceBusy(deviceKeyValue, isBusy) {
  if (isBusy) state.busyDevices.add(deviceKeyValue);
  else state.busyDevices.delete(deviceKeyValue);
  renderDevices();
}

function normalizeStatusPayload(payload, previousDevice = {}) {
  return {
    provider: payload.provider || previousDevice.provider || "tuya",
    provider_label: payload.provider_label || previousDevice.provider_label || "Tuya",
    device_key: payload.device_key || previousDevice.device_key,
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

async function refreshAmbilightMapping({ quiet = true } = {}) {
  try {
    const payload = await getJson("/api/ambilight-mapping");
    state.previewMapping = payload.mapping || {};
    if (!quiet && payload.warnings?.length) {
      setGlobalFeedback(payload.warnings[0].message, "error");
    }
    if (state.lastPreviewPayload) {
      renderAmbilightGrid(state.lastPreviewPayload);
    }
  } catch (error) {
    if (!quiet) setGlobalFeedback(friendlyErrorMessage(error.message), "error");
  }
}

async function updateCellMapping(cellKey, value) {
  const nextMapping = { ...state.previewMapping };
  if (!value) {
    delete nextMapping[cellKey];
  } else {
    const target = splitDeviceKey(value);
    nextMapping[cellKey] = { provider: target.provider, device_id: target.deviceId };
  }
  try {
    const payload = await postJson("/api/ambilight-mapping", { mapping: nextMapping });
    state.previewMapping = payload.mapping || {};
    if (state.lastPreviewPayload) {
      renderAmbilightGrid(state.lastPreviewPayload);
    }
    setGlobalFeedback("Ambilight mapping saved.", "success");
  } catch (error) {
    setGlobalFeedback(friendlyErrorMessage(error.message), "error");
  } finally {
    await refreshDiagnostics();
    await refreshSystemStatus();
  }
}

async function applyCurrentPreviewFrame({ quiet = false } = {}) {
  if (state.liveApplyBusy) return;
  const now = Date.now();
  if (quiet && now - state.lastLiveApplyAt < state.liveApplyMinIntervalMs) {
    return;
  }
  if (!state.lastPreviewPayload?.cells?.length) {
    if (!quiet) setGlobalFeedback("Capture a preview frame first.", "error");
    return;
  }
  state.liveApplyBusy = true;
  try {
    const payload = await postJson("/api/ambilight/apply-preview-frame", {
      monitor_index: state.previewMonitorIndex,
      cells: state.lastPreviewPayload.cells,
    });
    state.lastLiveApplyAt = Date.now();
    if (!quiet) {
      if (payload.applied?.length) setGlobalFeedback(`Applied ${payload.applied.length} mapped cell${payload.applied.length === 1 ? "" : "s"}.`, "success");
      else setGlobalFeedback("No mapped cells were applied.", "error");
    }
  } catch (error) {
    if (!quiet) setGlobalFeedback(friendlyErrorMessage(error.message), "error");
  } finally {
    state.liveApplyBusy = false;
    await refreshDiagnostics();
  }
}

async function refreshAmbilightPreview({ quiet = false } = {}) {
  if (state.previewBusy) return;
  state.previewBusy = true;
  try {
    const payload = await getJson(`/api/ambilight-preview?monitor_index=${encodeURIComponent(state.previewMonitorIndex)}`);
    state.lastPreviewPayload = payload;
    state.previewMapping = payload.mapping || state.previewMapping;
    if (Array.isArray(payload.monitors)) {
      state.previewMonitors = payload.monitors;
      updatePreviewMonitorOptions(payload.monitors);
    }
    if (payload.source_monitor?.index) {
      state.previewMonitorIndex = Number(payload.source_monitor.index);
    }
    renderAmbilightGrid(payload);
    updatePreviewStatus({ running: state.previewRunning, sampledAt: payload.sampled_at });
    if (state.liveApplyRunning) {
      await applyCurrentPreviewFrame({ quiet: true });
    } else if (!quiet) {
      setGlobalFeedback("Ambilight preview updated.", "success");
    }
  } catch (error) {
    if (state.previewRunning && state.previewTimerId) {
      window.clearInterval(state.previewTimerId);
      state.previewTimerId = null;
      state.previewRunning = false;
    }
    state.liveApplyRunning = false;
    renderPreviewPlaceholder(friendlyErrorMessage(error.message));
    updatePreviewStatus({ running: false, sampledAt: null });
    if (!quiet) setGlobalFeedback(friendlyErrorMessage(error.message), "error");
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

async function startLiveApply() {
  state.liveApplyRunning = true;
  updatePreviewStatus({ running: state.previewRunning });
  if (!state.previewRunning) {
    await startAmbilightPreview();
  } else {
    await refreshAmbilightPreview({ quiet: true });
  }
  setGlobalFeedback("Live apply started.", "success");
}

function stopLiveApply() {
  state.liveApplyRunning = false;
  updatePreviewStatus({ running: state.previewRunning });
  setGlobalFeedback("Live apply stopped.", "success");
}

async function refreshSystemStatus() {
  try {
    const payload = await getJson("/api/status");
    writeOutput("#status-output", payload);
    els.oauthPill.textContent = payload.oauth?.authorized ? "OAuth active" : "OAuth idle";
    if (els.huePill) {
      els.huePill.textContent = payload.hue?.configured ? `Hue ${payload.hue.bridge_ip}` : "Hue idle";
    }
    if (payload.preview?.target_fps) {
      state.previewTargetFps = payload.preview.target_fps;
    }
    if (payload.preview?.apply_target_fps) {
      state.liveApplyMinIntervalMs = Math.max(50, Math.round(1000 / payload.preview.apply_target_fps));
    }
    if (Array.isArray(payload.monitors)) {
      state.previewMonitors = payload.monitors;
      if (!state.previewMonitorIndex) {
        state.previewMonitorIndex = Number(payload.preview?.default_monitor_index || payload.monitors[0]?.index || 1);
      }
      updatePreviewMonitorOptions(payload.monitors);
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
    state.previewMapping = payload.mapping || state.previewMapping;
    setDevices(mergeKnownDevices(payload.devices || [], knownDevices));
    state.lastRefreshLabel = formatNowLabel("Devices refreshed");
    const warning = payload.warnings?.[0];
    if (warning) {
      setGlobalFeedback(`${warning.message} Loaded ${state.devices.length} visible device${state.devices.length === 1 ? "" : "s"}.`, "error");
    } else {
      setGlobalFeedback(`Loaded ${state.devices.length} device${state.devices.length === 1 ? "" : "s"}.`, "success");
    }
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
    await refreshAmbilightMapping({ quiet: true });
    await refreshSystemStatus();
  }
}

async function refreshDeviceStatus(deviceKeyValue, { quiet = false } = {}) {
  const device = getDeviceByKey(deviceKeyValue);
  if (!device) return;
  withDeviceBusy(deviceKeyValue, true);
  try {
    const payload = await postJson("/api/get-device-status", {
      provider: device.provider,
      device_id: device.id,
    });
    mergeDeviceUpdate(deviceKeyValue, normalizeStatusPayload(payload, device));
    if (!quiet) setGlobalFeedback(`${device.name}: status updated.`, "success");
  } catch (error) {
    mergeDeviceUpdate(deviceKeyValue, { statusMessage: friendlyErrorMessage(error.message), feedbackTone: "error" });
    if (!quiet) setGlobalFeedback(`Status refresh failed for ${device.name}.`, "error");
  } finally {
    withDeviceBusy(deviceKeyValue, false);
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
  await Promise.allSettled(state.devices.map((device) => refreshDeviceStatus(device.device_key, { quiet: true })));
  state.lastRefreshLabel = formatNowLabel("Status refreshed");
  renderDevices();
  setGlobalFeedback("Status refresh finished.", "success");
  await refreshSystemStatus();
}

async function applyPower(deviceKeyValue, powerState) {
  const device = getDeviceByKey(deviceKeyValue);
  if (!device) return;
  withDeviceBusy(deviceKeyValue, true);
  try {
    await postJson("/api/set-power", { provider: device.provider, device_id: device.id, state: powerState });
    mergeDeviceUpdate(deviceKeyValue, {
      power_state: powerState,
      state_label: powerState === "on" ? "On" : "Off",
      statusMessage: `Power ${powerState} command sent.`,
      feedbackTone: "success",
    });
    renderDevices();
    await refreshDeviceStatus(deviceKeyValue, { quiet: true });
    setGlobalFeedback(`${device.name}: power ${powerState}.`, "success");
  } catch (error) {
    mergeDeviceUpdate(deviceKeyValue, { statusMessage: friendlyErrorMessage(error.message), feedbackTone: "error" });
    renderDevices();
    setGlobalFeedback(`${device.name}: power command failed.`, "error");
  } finally {
    withDeviceBusy(deviceKeyValue, false);
    syncKnownDevicesFromState();
    await refreshSystemStatus();
  }
}

async function applyColor(deviceKeyValue, rgbValue) {
  const device = getDeviceByKey(deviceKeyValue);
  if (!device) return;
  withDeviceBusy(deviceKeyValue, true);
  try {
    await postJson("/api/set-fixed-color", { provider: device.provider, device_id: device.id, rgb: rgbValue });
    mergeDeviceUpdate(deviceKeyValue, {
      power_state: "on",
      state_label: "On",
      statusMessage: `Color ${rgbValue} applied.`,
      feedbackTone: "success",
    });
    renderDevices();
    await refreshDeviceStatus(deviceKeyValue, { quiet: true });
    setGlobalFeedback(`${device.name}: color updated.`, "success");
  } catch (error) {
    mergeDeviceUpdate(deviceKeyValue, { statusMessage: friendlyErrorMessage(error.message), feedbackTone: "error" });
    renderDevices();
    setGlobalFeedback(`${device.name}: color command failed.`, "error");
  } finally {
    withDeviceBusy(deviceKeyValue, false);
    syncKnownDevicesFromState();
    await refreshSystemStatus();
  }
}

async function applyBrightness(deviceKeyValue, level, { quiet = false } = {}) {
  const device = getDeviceByKey(deviceKeyValue);
  if (!device) return;
  withDeviceBusy(deviceKeyValue, true);
  try {
    await postJson("/api/set-brightness", { provider: device.provider, device_id: device.id, level });
    mergeDeviceUpdate(deviceKeyValue, {
      current_brightness: Number(level),
      statusMessage: `Brightness set to ${level}%.`,
      feedbackTone: "success",
    });
    renderDevices();
    if (!quiet) setGlobalFeedback(`${device.name}: brightness updated.`, "success");
  } catch (error) {
    mergeDeviceUpdate(deviceKeyValue, { statusMessage: friendlyErrorMessage(error.message), feedbackTone: "error" });
    renderDevices();
    if (!quiet) setGlobalFeedback(`${device.name}: brightness command failed.`, "error");
  } finally {
    withDeviceBusy(deviceKeyValue, false);
    syncKnownDevicesFromState();
    await refreshDiagnostics();
  }
}

function scheduleBrightnessApply(deviceKeyValue, level) {
  const previousTimer = state.brightnessTimers.get(deviceKeyValue);
  if (previousTimer) {
    window.clearTimeout(previousTimer);
  }
  const timerId = window.setTimeout(async () => {
    state.brightnessTimers.delete(deviceKeyValue);
    await applyBrightness(deviceKeyValue, level, { quiet: true });
    await refreshDeviceStatus(deviceKeyValue, { quiet: true });
    await refreshSystemStatus();
  }, BRIGHTNESS_DEBOUNCE_MS);
  state.brightnessTimers.set(deviceKeyValue, timerId);
}

function assignDeviceSpace(deviceKeyValue, room) {
  setDeviceSpace(deviceKeyValue, room);
  mergeDeviceUpdate(deviceKeyValue, { room: room === "Sin espacio" ? "" : room });
  renderDevices();
  syncKnownDevicesFromState();
  setGlobalFeedback("Device space updated.", "success");
}

function removeKnownDevice(deviceKeyValue) {
  const knownDevices = loadKnownDevices().filter((device) => {
    const normalized = normalizeKnownDevice(device);
    return normalized.device_key !== deviceKeyValue;
  });
  saveKnownDevices(knownDevices);
  setDeviceSpace(deviceKeyValue, "");
  state.devices = state.devices.filter((device) => device.device_key !== deviceKeyValue);
  renderDevices();
  writeOutput("#known-device-output", { devices: knownDevices, count: knownDevices.length });
  setGlobalFeedback("Saved device removed from local catalog.", "success");
}

function resolveFormTarget(form, selectName = "device_select", textName = "device_id") {
  const selected = String(form.get(selectName) || "").trim();
  if (selected) {
    return splitDeviceKey(selected);
  }
  const manualDeviceId = String(form.get(textName) || "").trim();
  return {
    provider: "tuya",
    deviceId: manualDeviceId,
    deviceKey: manualDeviceId ? deviceKey("tuya", manualDeviceId) : "",
  };
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
  document.querySelectorAll("[data-device-brightness-input]").forEach((input) => {
    input.addEventListener("input", () => {
      const deviceKeyValue = input.dataset.deviceBrightnessInput;
      const level = Number(input.value || 0);
      const label = document.querySelector(`[data-device-brightness-label="${deviceKeyValue}"]`);
      if (label) label.textContent = `${level}%`;
      scheduleBrightnessApply(deviceKeyValue, level);
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

bindAction("[data-action='preview-apply-once']", async () => {
  await applyCurrentPreviewFrame();
});

bindAction("[data-action='preview-live-start']", async () => {
  await startLiveApply();
});

bindAction("[data-action='preview-live-stop']", () => {
  stopLiveApply();
});

bindAction("[data-action='picker-on']", async () => {
  if (!state.selectedDeviceKey) {
    setGlobalFeedback("Select a device first.", "error");
    return;
  }
  await applyPower(state.selectedDeviceKey, "on");
});

bindAction("[data-action='picker-off']", async () => {
  if (!state.selectedDeviceKey) {
    setGlobalFeedback("Select a device first.", "error");
    return;
  }
  await applyPower(state.selectedDeviceKey, "off");
});

bindAction("[data-action='picker-refresh']", async () => {
  if (!state.selectedDeviceKey) {
    setGlobalFeedback("Select a device first.", "error");
    return;
  }
  await refreshDeviceStatus(state.selectedDeviceKey);
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
    provider: form.get("provider"),
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
  const knownDevices = loadKnownDevices().filter((device) => normalizeKnownDevice(device).device_key !== entry.device_key);
  knownDevices.push({
    id: entry.id,
    provider: entry.provider,
    device_key: entry.device_key,
    name: entry.name,
    type_label: entry.type_label,
    category: entry.category,
    product_name: entry.product_name,
    is_rgb_capable: entry.is_rgb_capable,
    supports_color: entry.supports_color,
    brightness_supported: entry.brightness_supported,
    room: entry.room,
  });
  if (entry.room) setDeviceSpace(entry.device_key, entry.room);
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
    const target = resolveFormTarget(form);
    const payload = await postJson("/api/get-device-status", {
      provider: target.provider,
      device_id: target.deviceId,
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
    const target = resolveFormTarget(form);
    const payload = await postJson("/api/set-power", {
      provider: target.provider,
      device_id: target.deviceId,
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
    const target = resolveFormTarget(form);
    const payload = await postJson("/api/set-fixed-color", {
      provider: target.provider,
      device_id: target.deviceId,
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
    const target = resolveFormTarget(form);
    const payload = await postJson("/api/set-brightness", {
      provider: target.provider,
      device_id: target.deviceId,
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
    state.selectedDeviceKey = event.target.value;
  });
}

if (els.spaceFilter) {
  els.spaceFilter.addEventListener("change", (event) => {
    state.selectedSpace = event.target.value;
    renderDevices();
  });
}

if (els.previewMonitorSelect) {
  els.previewMonitorSelect.addEventListener("change", (event) => {
    state.previewMonitorIndex = Number(event.target.value || 1);
    updatePreviewStatus({ running: state.previewRunning });
    if (state.previewRunning) {
      refreshAmbilightPreview({ quiet: true });
    }
  });
}

window.addEventListener("beforeunload", () => {
  stopLiveApply();
  stopAmbilightPreview();
});

renderPreviewPlaceholder("Start preview to sample the selected monitor.");
updatePreviewStatus({ running: false, sampledAt: null });
loadSavedDevicesIntoState();
refreshDiagnostics();
refreshSystemStatus();
refreshAmbilightMapping({ quiet: true });
refreshDevices();
