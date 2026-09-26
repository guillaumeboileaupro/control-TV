import { invoke } from "@tauri-apps/api/core";

import {
  canDiscover,
  canRefresh,
  describeFailure,
  describeStatus,
  failDiscovery,
  finishDiscovery,
  finishStatusRead,
  initialState,
  refreshStatus,
  selectDevice,
  startDiscovery,
  toBridgeFailure,
  type AppState,
  type Device,
  type DeviceStatus,
  type StatusOutcome,
  type StatusRequest,
} from "./model.ts";

// Application shell. Every `invoke` below maps onto one Tauri command declared in
// src-tauri/src/lib.rs, which forwards to src/control_tv/bridge.py - the single place
// that actually talks to Chromecast. This file performs the I/O and draws the DOM; the
// rules (selection by stable id, stale answers, wording) live in model.ts. It must never
// grow its own device/control logic, and nothing here sends a command to a device: after
// discovery the UI only reads status.

interface PingResult {
  status: string;
  controlTvVersion: string;
}

interface DiscoverDevicesResult {
  devices: Device[];
}

interface GetStatusResult {
  status: DeviceStatus;
}

interface Elements {
  backendStatus: HTMLElement;
  discoverButton: HTMLButtonElement;
  devicesMessage: HTMLElement;
  devicesList: HTMLElement;
  refreshButton: HTMLButtonElement;
  selectedSummary: HTMLElement;
  statusBody: HTMLElement;
}

function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (element === null) {
    throw new Error(`missing #${id} element`);
  }
  return element as T;
}

function paragraph(className: string, text: string): HTMLParagraphElement {
  const element = document.createElement("p");
  element.className = className;
  element.textContent = text;
  return element;
}

async function checkBackendStatus(statusEl: HTMLElement): Promise<void> {
  try {
    const result = await invoke<PingResult>("bridge_ping");
    statusEl.textContent = `Control backend ready (control-tv ${result.controlTvVersion}).`;
    statusEl.classList.remove("status-error");
  } catch (error) {
    const failure = describeFailure(toBridgeFailure(error));
    statusEl.textContent = `${failure.title}: ${failure.detail}`;
    statusEl.classList.add("status-error");
  }
}

function renderDevices(elements: Elements, state: AppState, onSelect: (id: string) => void): void {
  const { devicesList, devicesMessage } = elements;
  devicesList.replaceChildren();

  if (state.discovery.kind === "running") {
    devicesMessage.textContent = "Discovering…";
    return;
  }
  if (state.discovery.kind === "failed") {
    const failure = describeFailure(state.discovery.failure, "discover devices");
    devicesMessage.textContent = `Discovery failed - ${failure.title}: ${failure.detail}`;
    return;
  }
  if (state.discovery.kind === "idle") {
    devicesMessage.textContent = "Press “Discover devices” to search the network.";
    return;
  }
  if (state.devices.length === 0) {
    devicesMessage.textContent = "No devices found on the network.";
    return;
  }
  devicesMessage.textContent = "Select a device to read its status.";

  for (const device of state.devices) {
    const isSelected = state.selected?.id === device.id;
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "device";
    button.dataset["deviceId"] = device.id;
    if (isSelected) {
      button.setAttribute("aria-current", "true");
    }
    const label = document.createElement("span");
    label.textContent = `${device.friendlyName} — ${device.host}:${device.port} (${device.kind})`;
    button.appendChild(label);
    if (isSelected) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "Selected";
      button.appendChild(badge);
    }
    button.addEventListener("click", () => onSelect(device.id));
    item.appendChild(button);
    devicesList.appendChild(item);
  }
}

function renderSelected(elements: Elements, state: AppState): void {
  const { selectedSummary, statusBody } = elements;
  statusBody.replaceChildren();

  const selected = state.selected;
  if (selected === null) {
    selectedSummary.textContent =
      state.notice ?? "No device selected. Select a device above to read its status.";
    return;
  }
  selectedSummary.textContent = `${selected.friendlyName} — ${selected.host}:${selected.port}`;
  statusBody.appendChild(paragraph("device-id", `Device id: ${selected.id}`));

  switch (state.status.kind) {
    case "none":
      break;
    case "loading":
      statusBody.appendChild(paragraph("message", "Reading status…"));
      break;
    case "failed": {
      const failure = describeFailure(state.status.failure);
      statusBody.appendChild(paragraph("failure-title", failure.title));
      statusBody.appendChild(paragraph("failure-detail", failure.detail));
      break;
    }
    case "ready": {
      const description = describeStatus(state.status.status);
      const list = document.createElement("dl");
      list.className = "status-list";
      for (const row of description.rows) {
        const term = document.createElement("dt");
        term.textContent = row.label;
        const value = document.createElement("dd");
        value.textContent = row.value;
        list.append(term, value);
      }
      statusBody.appendChild(list);
      for (const note of description.notes) {
        statusBody.appendChild(paragraph(description.incomplete ? "note note-warn" : "note", note));
      }
      break;
    }
  }
}

function start(elements: Elements): void {
  let state = initialState();

  function render(): void {
    renderDevices(elements, state, (id) => {
      const next = selectDevice(state, id);
      update(next.state);
      if (next.request !== null) {
        void readStatus(next.request);
      }
    });
    renderSelected(elements, state);
    elements.discoverButton.disabled = !canDiscover(state);
    elements.refreshButton.disabled = !canRefresh(state);
  }

  function update(next: AppState): void {
    state = next;
    render();
  }

  async function readStatus(request: StatusRequest): Promise<void> {
    let outcome: StatusOutcome;
    try {
      const result = await invoke<GetStatusResult>("bridge_get_status", {
        deviceId: request.deviceId,
      });
      outcome = { ok: true, status: result.status };
    } catch (error) {
      outcome = { ok: false, failure: toBridgeFailure(error) };
    }
    // `state` is read when the answer arrives, so an older read cannot overwrite a newer one.
    update(finishStatusRead(state, request.requestId, outcome));
  }

  async function discover(): Promise<void> {
    if (!canDiscover(state)) {
      return;
    }
    update(startDiscovery(state));
    try {
      const result = await invoke<DiscoverDevicesResult>("bridge_discover_devices");
      update(finishDiscovery(state, result.devices));
    } catch (error) {
      update(failDiscovery(state, toBridgeFailure(error)));
    }
  }

  elements.discoverButton.addEventListener("click", () => {
    void discover();
  });
  elements.refreshButton.addEventListener("click", () => {
    const next = refreshStatus(state);
    update(next.state);
    if (next.request !== null) {
      void readStatus(next.request);
    }
  });

  void checkBackendStatus(elements.backendStatus);
  render();
}

window.addEventListener("DOMContentLoaded", () => {
  start({
    backendStatus: byId("backend-status"),
    discoverButton: byId<HTMLButtonElement>("discover-button"),
    devicesMessage: byId("devices-message"),
    devicesList: byId("devices-list"),
    refreshButton: byId<HTMLButtonElement>("refresh-button"),
    selectedSummary: byId("selected-summary"),
    statusBody: byId("status-body"),
  });
});
