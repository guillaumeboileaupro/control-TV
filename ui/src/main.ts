import { invoke } from "@tauri-apps/api/core";

// Application shell skeleton. Every function here maps directly onto one Tauri command
// declared in src-tauri/src/lib.rs, which forwards to src/control_tv/bridge.py - the
// single place that actually talks to Chromecast. This file must never grow its own
// device/control logic; it only renders whatever the backend reports.

interface PingResult {
  status: string;
  controlTvVersion: string;
}

interface Device {
  id: string;
  friendlyName: string;
  host: string;
  port: number;
  kind: string;
  modelName: string | null;
}

interface DiscoverDevicesResult {
  devices: Device[];
}

function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (element === null) {
    throw new Error(`missing #${id} element`);
  }
  return element as T;
}

async function checkBackendStatus(statusEl: HTMLElement): Promise<void> {
  try {
    const result = await invoke<PingResult>("bridge_ping");
    statusEl.textContent = `Control backend ready (control-tv ${result.controlTvVersion}).`;
    statusEl.classList.remove("status-error");
  } catch (error) {
    statusEl.textContent = `Control backend unavailable: ${String(error)}`;
    statusEl.classList.add("status-error");
  }
}

function renderDevices(listEl: HTMLElement, messageEl: HTMLElement, devices: Device[]): void {
  listEl.replaceChildren();
  if (devices.length === 0) {
    messageEl.textContent = "No devices found on the network.";
    return;
  }
  messageEl.textContent = "";
  for (const device of devices) {
    const item = document.createElement("li");
    item.className = "device";
    // Selecting a device (and everything after it: state, controls, volume) is the
    // next slice; for now every discovered device is only displayed, not selectable.
    item.textContent = `${device.friendlyName} — ${device.host}:${device.port} (${device.kind})`;
    listEl.appendChild(item);
  }
}

async function discoverDevices(listEl: HTMLElement, messageEl: HTMLElement): Promise<void> {
  messageEl.textContent = "Discovering…";
  listEl.replaceChildren();
  try {
    const result = await invoke<DiscoverDevicesResult>("bridge_discover_devices");
    renderDevices(listEl, messageEl, result.devices);
  } catch (error) {
    messageEl.textContent = `Discovery failed: ${String(error)}`;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const statusEl = byId<HTMLParagraphElement>("backend-status");
  const discoverButton = byId<HTMLButtonElement>("discover-button");
  const devicesList = byId<HTMLUListElement>("devices-list");
  const devicesMessage = byId<HTMLParagraphElement>("devices-message");

  void checkBackendStatus(statusEl);
  discoverButton.addEventListener("click", () => {
    void discoverDevices(devicesList, devicesMessage);
  });
});
