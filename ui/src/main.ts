import { invoke } from "@tauri-apps/api/core";

import {
  canDiscover,
  canRefresh,
  canSelect,
  describeDevice,
  describeDevicesMessage,
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
  type BridgeFailure,
  type Device,
  type PlaybackKind,
  type DeviceStatus,
  type StatusOutcome,
  type StatusRequest,
} from "./model.ts";

// Every `invoke` below maps onto one application command, which reaches the devices
// through the shared control layer. This file performs the I/O and draws the DOM; the
// rules (selection by stable id, one request in flight, wording) live in model.ts. It must
// never grow its own device/control logic, and nothing here sends a command to a device:
// after discovery the UI only reads status. Internal component names never appear in the
// normal view; raw error text lives only in a collapsed diagnostic disclosure.

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
  serviceNotice: HTMLElement;
  picker: HTMLDetailsElement;
  pickerSummary: HTMLElement;
  discoverButton: HTMLButtonElement;
  discoverLabel: HTMLElement;
  devicesMessage: HTMLElement;
  devicesList: HTMLElement;
  context: HTMLElement;
  contextBody: HTMLElement;
  observed: HTMLElement;
  refreshButton: HTMLButtonElement;
}

function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (element === null) {
    throw new Error(`missing #${id} element`);
  }
  return element as T;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function icon(name: string, className = "icon"): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

function text(tag: string, className: string, content: string): HTMLElement {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = content;
  return element;
}

function setBusy(element: HTMLElement, busy: boolean): void {
  if (busy) {
    element.setAttribute("aria-disabled", "true");
  } else {
    element.removeAttribute("aria-disabled");
  }
}

// Playback kinds are model vocabulary; the sprite names the shapes.
const PLAYBACK_ICONS: Record<PlaybackKind, string> = {
  playing: "play",
  paused: "pause",
  buffering: "buffering",
  idle: "idle",
  unknown: "unknown",
};

const KIND_ICONS: Record<string, string> = { audio: "volume" };

// A problem the person can act on: plain wording first, the raw code and message only in a
// collapsed disclosure, and one recovery button.
function problem(
  failure: BridgeFailure,
  action: string,
  recovery: { label: string; run: () => void } | null,
): HTMLElement {
  const description = describeFailure(failure, action);
  const block = document.createElement("div");
  block.className = "problem";

  const title = text("p", "problem-title", "");
  title.append(icon("alert"), description.title);
  block.append(title, text("p", "problem-hint", description.hint));

  if (recovery !== null) {
    const button = text("button", "btn", recovery.label);
    (button as HTMLButtonElement).type = "button";
    button.addEventListener("click", recovery.run);
    block.append(button);
  }

  const diagnostic = document.createElement("details");
  diagnostic.className = "diagnostic";
  diagnostic.append(text("summary", "", "Details"), text("pre", "", description.technical));
  block.append(diagnostic);
  return block;
}

function start(elements: Elements): void {
  let state = initialState();
  let serviceFailure: BridgeFailure | null = null;
  // The device message and the status area are live regions: rebuilding one whose content
  // did not change would make assistive technology read it out again, so each is redrawn only
  // when what it says has changed. The message and the list have separate keys: the list also
  // changes with whether selection is allowed, which flips around every status read while the
  // message (for example the announced device count) does not.
  let messageKey = "";
  let listKey = "";
  let contextKey = "";

  function renderNotice(): void {
    elements.serviceNotice.replaceChildren();
    elements.serviceNotice.hidden = serviceFailure === null;
    if (serviceFailure !== null) {
      elements.serviceNotice.append(problem(serviceFailure, "start", null));
    }
  }

  function renderSummary(): void {
    const selected = state.selected;
    const label = document.createElement("span");
    label.className = "summary-text";
    label.append(text("span", "summary-title", selected?.friendlyName ?? "Choose a device"));
    if (selected !== null && state.status.kind === "ready") {
      label.append(
        text("span", "summary-sub", describeStatus(state.status.status).connectionLabel),
      );
    }
    elements.pickerSummary.replaceChildren(icon("tv"), label, icon("chevron", "icon chevron"));
  }

  function renderDevices(): void {
    const { devicesList, devicesMessage } = elements;

    const message = describeDevicesMessage(state);
    const nextMessageKey = JSON.stringify(message);
    if (nextMessageKey !== messageKey) {
      messageKey = nextMessageKey;
      devicesMessage.replaceChildren();
      if (message.failure !== null) {
        devicesMessage.append(problem(message.failure, "find devices", null));
      }
      for (const line of message.lines) {
        devicesMessage.append(text("p", line.announceOnly ? "sr-only" : "message-line", line.text));
      }
    }

    const busy = !canSelect(state);
    const nextListKey = JSON.stringify([state.devices, state.selected?.id ?? null, busy]);
    if (nextListKey === listKey) {
      return;
    }
    listKey = nextListKey;
    devicesList.replaceChildren();

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
      setBusy(button, busy);

      const labels = document.createElement("span");
      labels.className = "device-text";
      labels.append(
        text("span", "device-name", device.friendlyName),
        text("span", "device-sub", describeDevice(device, state.devices).subtitle),
      );
      button.append(icon(KIND_ICONS[device.kind] ?? "tv"), labels);
      if (isSelected) {
        button.append(icon("check", "icon check"));
      }
      button.addEventListener("click", () => onSelect(device.id));
      item.appendChild(button);
      devicesList.appendChild(item);
    }
  }

  function renderPlayback(description: ReturnType<typeof describeStatus>): HTMLElement[] {
    const parts: HTMLElement[] = [];
    const headline = text("h2", "headline", description.headline);
    if (!description.hasMedia) {
      headline.classList.add("is-empty");
    }
    parts.push(headline);

    if (description.playback !== null && description.position !== null) {
      const row = document.createElement("p");
      row.className = "playback";
      const state = text("span", "playback-state", "");
      state.append(icon(PLAYBACK_ICONS[description.playback.kind]), description.playback.label);
      row.append(state, text("span", "", description.position.text));
      parts.push(row);

      if (description.position.fraction !== null) {
        const bar = document.createElement("progress");
        bar.className = "progress";
        bar.max = 1;
        bar.value = description.position.fraction;
        bar.setAttribute("aria-label", "Playback position");
        parts.push(bar);
      }
    }
    return parts;
  }

  function renderFacts(description: ReturnType<typeof describeStatus>): HTMLElement {
    const facts = document.createElement("ul");
    facts.className = "facts";
    const add = (iconName: string, label: string): void => {
      const item = document.createElement("li");
      item.className = "fact";
      item.append(icon(iconName), label);
      facts.append(item);
    };
    add(description.muted === true ? "volume-off" : "volume", description.soundText);
    if (description.standby) {
      add("idle", "In standby");
    }
    if (description.application !== null) {
      add("app", description.application);
    }
    return facts;
  }

  function renderContext(): void {
    const { context, contextBody, observed, refreshButton } = elements;
    const active = document.activeElement;
    const refreshHadFocus = active === refreshButton;
    const bodyHadFocus = active !== null && contextBody.contains(active);
    context.hidden = state.selected === null;
    setBusy(refreshButton, !canRefresh(state));
    const loading = state.status.kind === "loading";
    refreshButton.classList.toggle("is-loading", loading);
    refreshButton.setAttribute("aria-busy", String(loading));
    // A failure carries its own labelled recovery button; the icon would only repeat it.
    refreshButton.hidden = state.status.kind === "failed";

    const key = JSON.stringify([state.selected?.id ?? null, state.status]);
    if (key !== contextKey) {
      contextKey = key;
      contextBody.replaceChildren();
      observed.textContent = "";
      switch (state.status.kind) {
        case "none":
          break;
        case "loading":
          contextBody.append(text("p", "message", "Reading status…"));
          break;
        case "failed": {
          const failure = state.status.failure;
          const recovery = describeFailure(failure).recovery === "discover" ? "discover" : "retry";
          contextBody.append(
            problem(
              failure,
              "read this device's status",
              recovery === "discover"
                ? { label: "Find devices", run: () => void discover() }
                : { label: "Try again", run: onRefresh },
            ),
          );
          break;
        }
        case "ready": {
          const description = describeStatus(state.status.status);
          observed.textContent = `Updated ${description.observedAt}`;
          if (description.connected) {
            contextBody.append(...renderPlayback(description), renderFacts(description));
          }
          if (description.note !== null) {
            const line = text("p", "note", "");
            line.append(icon("info"), description.note);
            contextBody.append(line);
          }
          break;
        }
      }
    }
    // Keep keyboard focus in the status area when the control that had it is replaced: a
    // recovery button hands it to the refresh button while a read runs, and back again if
    // the read fails once more.
    if (refreshHadFocus && refreshButton.hidden) {
      contextBody.querySelector<HTMLElement>(".problem .btn")?.focus();
    } else if (bodyHadFocus && active !== null && !contextBody.contains(active)) {
      refreshButton.focus();
    }
  }

  function render(): void {
    // The list is rebuilt on every state change; put keyboard focus back on the same device.
    const focusedDeviceId = document.activeElement?.getAttribute("data-device-id") ?? null;
    renderSummary();
    renderDevices();
    renderContext();
    const searching = state.discovery.kind === "running";
    elements.discoverLabel.textContent = searching ? "Searching…" : "Find devices";
    elements.discoverButton
      .querySelector("use")
      ?.setAttribute("href", searching ? "#i-buffering" : "#i-search");
    setBusy(elements.discoverButton, !canDiscover(state));
    elements.discoverButton.classList.toggle("is-loading", searching);
    elements.discoverButton.setAttribute("aria-busy", String(searching));
    if (focusedDeviceId !== null) {
      elements.devicesList
        .querySelector<HTMLElement>(`[data-device-id="${CSS.escape(focusedDeviceId)}"]`)
        ?.focus();
    }
  }

  function update(next: AppState): void {
    state = next;
    render();
  }

  function onSelect(deviceId: string): void {
    const next = selectDevice(state, deviceId);
    update(next.state);
    if (next.request !== null) {
      // The chosen device is now the row you see; collapse the list and keep focus nearby.
      elements.picker.open = false;
      elements.pickerSummary.focus();
      void readStatus(next.request);
    }
  }

  function onRefresh(): void {
    const next = refreshStatus(state);
    update(next.state);
    if (next.request !== null) {
      void readStatus(next.request);
    }
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
    // `state` is read when the answer arrives, so a late answer cannot overwrite a newer read.
    update(finishStatusRead(state, request.requestId, outcome));
  }

  async function discover(): Promise<void> {
    if (!canDiscover(state)) {
      return;
    }
    elements.picker.open = true;
    update(startDiscovery(state));
    try {
      const result = await invoke<DiscoverDevicesResult>("bridge_discover_devices");
      update(finishDiscovery(state, result.devices));
    } catch (error) {
      update(failDiscovery(state, toBridgeFailure(error)));
    }
  }

  async function checkBackend(): Promise<void> {
    try {
      await invoke<PingResult>("bridge_ping");
    } catch (error) {
      serviceFailure = toBridgeFailure(error);
      renderNotice();
    }
  }

  elements.discoverButton.addEventListener("click", () => {
    void discover();
  });
  elements.refreshButton.addEventListener("click", onRefresh);
  elements.picker.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && elements.picker.open) {
      elements.picker.open = false;
      elements.pickerSummary.focus();
    }
  });

  render();
  void checkBackend();
}

window.addEventListener("DOMContentLoaded", () => {
  start({
    serviceNotice: byId("service-notice"),
    picker: byId<HTMLDetailsElement>("picker"),
    pickerSummary: byId("picker-summary"),
    discoverButton: byId<HTMLButtonElement>("discover-button"),
    discoverLabel: byId("discover-label"),
    devicesMessage: byId("devices-message"),
    devicesList: byId("devices-list"),
    context: byId("context"),
    contextBody: byId("context-body"),
    observed: byId("observed"),
    refreshButton: byId<HTMLButtonElement>("refresh-button"),
  });
});
