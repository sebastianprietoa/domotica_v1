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

function bindAction(selector, handler) {
  document.querySelector(selector).addEventListener("click", handler);
}

function bindForm(selector, handler) {
  document.querySelector(selector).addEventListener("submit", (event) => {
    event.preventDefault();
    handler(new FormData(event.currentTarget));
  });
}

function writeOutput(selector, payload) {
  document.querySelector(selector).textContent = typeof payload === "string" ? payload : pretty(payload);
}

bindAction("[data-action='refresh-status']", async () => {
  try {
    writeOutput("#status-output", await getJson("/api/status"));
  } catch (error) {
    writeOutput("#status-output", error.message);
  }
});

bindAction("[data-action='list-devices']", async () => {
  try {
    writeOutput("#devices-output", await postJson("/api/list-devices"));
  } catch (error) {
    writeOutput("#devices-output", error.message);
  }
});

bindForm("#device-status-form", async (form) => {
  try {
    writeOutput("#device-status-output", await postJson("/api/get-device-status", {
      device_id: form.get("device_id"),
    }));
  } catch (error) {
    writeOutput("#device-status-output", error.message);
  }
});

bindForm("#power-form", async (form) => {
  try {
    writeOutput("#power-output", await postJson("/api/set-power", {
      device_id: form.get("device_id"),
      zone: form.get("zone"),
      state: form.get("state"),
    }));
  } catch (error) {
    writeOutput("#power-output", error.message);
  }
});

bindForm("#fixed-color-form", async (form) => {
  try {
    writeOutput("#fixed-color-output", await postJson("/api/set-fixed-color", {
      device_id: form.get("device_id"),
      zone: form.get("zone"),
      rgb: form.get("rgb"),
    }));
  } catch (error) {
    writeOutput("#fixed-color-output", error.message);
  }
});

bindForm("#sample-form", async (form) => {
  try {
    writeOutput("#sample-output", await postJson("/api/screen-sample", {
      monitor_index: form.get("monitor_index") || null,
    }));
  } catch (error) {
    writeOutput("#sample-output", error.message);
  }
});

bindForm("#sync-form", async (form) => {
  try {
    writeOutput("#sync-output", await postJson("/api/sync/start", {
      duration: form.get("duration") || null,
      monitor_index: form.get("monitor_index") || null,
      dry_run: form.get("dry_run") === "on",
    }));
  } catch (error) {
    writeOutput("#sync-output", error.message);
  }
});

bindAction("[data-action='stop-sync']", async () => {
  try {
    writeOutput("#sync-output", await postJson("/api/sync/stop"));
  } catch (error) {
    writeOutput("#sync-output", error.message);
  }
});
