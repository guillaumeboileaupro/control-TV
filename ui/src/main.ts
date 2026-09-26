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
import {
  describeCommandFeedback,
  describeControls,
  finishCommand,
  setSeekDraft,
  startSeek,
  startTransport,
  type CommandOutcome,
  type CommandRequest,
  type CommandResult,
} from "./playback.ts";

// Every `invoke` below maps onto one application command, which reaches the devices
// through the shared control layer. This file performs the I/O and draws the DOM; the
// rules (selection by stable id, one request in flight, which controls the observed state
// offers, wording) live in model.ts and playback.ts. It must never grow its own
// device/control logic. It sends a playback command only when the operator uses a control the
// observed state offers, never resends one, and never shows a state the TV did not report.
// Internal component names never appear in the normal view; raw error text lives only in a
// collapsed diagnostic disclosure.

// Waits for the seek control to settle (a drag release, or the last of several key presses)
// before sending one seek, so moving the control never sends a burst of commands.
const SEEK_COMMIT_DELAY_MS = 400;

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

interface CommandAnswer {
  result: CommandResult;
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
  contextNow: HTMLElement;
  contextFacts: HTMLElement;
  observed: HTMLElement;
  refreshButton: HTMLButtonElement;
  controls: HTMLElement;
  seek: HTMLElement;
  seekInput: HTMLInputElement;
  seekLabel: HTMLElement;
  primaryButton: HTMLButtonElement;
  primaryLabel: HTMLElement;
  stopButton: HTMLButtonElement;
  stopLabel: HTMLElement;
  controlsNote: HTMLElement;
  seekNote: HTMLElement;
  commandFeedback: HTMLElement;
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
function problemBlock(
  title: string,
  hint: string,
  technical: string,
  recovery: { label: string; run: () => void } | null,
): HTMLElement {
  const block = document.createElement("div");
  block.className = "problem";

  const heading = text("p", "problem-title", "");
  heading.append(icon("alert"), title);
  block.append(heading, text("p", "problem-hint", hint));

  if (recovery !== null) {
    const button = text("button", "btn", recovery.label);
    (button as HTMLButtonElement).type = "button";
    button.addEventListener("click", recovery.run);
    block.append(button);
  }

  block.append(disclosure(technical));
  return block;
}

function disclosure(technical: string): HTMLElement {
  const diagnostic = document.createElement("details");
  diagnostic.className = "diagnostic";
  diagnostic.append(text("summary", "", "Details"), text("pre", "", technical));
  return diagnostic;
}

function problem(
  failure: BridgeFailure,
  action: string,
  recovery: { label: string; run: () => void } | null,
): HTMLElement {
  const description = describeFailure(failure, action);
  return problemBlock(description.title, description.hint, description.technical, recovery);
}

function setIcon(button: HTMLElement, name: string): void {
  button.querySelector("use")?.setAttribute("href", `#i-${name}`);
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
  let feedbackKey = "";
  let seekTimer: number | undefined;
  // The seek slider is disabled while a command runs, which drops keyboard focus; remember
  // that it had it so focus can be given back once it is enabled again.
  let seekWantsFocus = false;

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

  function renderPlayback(
    description: ReturnType<typeof describeStatus>,
    withProgress: boolean,
  ): HTMLElement[] {
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

      if (withProgress && description.position.fraction !== null) {
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
    const { context, contextNow, contextFacts, observed, refreshButton } = elements;
    const active = document.activeElement;
    const bodyHadFocus = active !== null && contextNow.contains(active);
    context.hidden = state.selected === null;
    setBusy(refreshButton, !canRefresh(state));
    const loading = state.status.kind === "loading";
    refreshButton.classList.toggle("is-loading", loading);
    refreshButton.setAttribute("aria-busy", String(loading));

    const key = JSON.stringify([state.selected?.id ?? null, state.status]);
    if (key !== contextKey) {
      contextKey = key;
      contextNow.replaceChildren();
      contextFacts.replaceChildren();
      observed.textContent = "";
      switch (state.status.kind) {
        case "none":
          break;
        case "loading":
          contextNow.append(text("p", "message", "Reading status…"));
          break;
        case "failed": {
          const failure = state.status.failure;
          const recovery = describeFailure(failure).recovery === "discover" ? "discover" : "retry";
          contextNow.append(
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
            // The slider replaces the read-only progress bar when seeking is offered.
            contextNow.append(
              ...renderPlayback(description, describeControls(state).seek === null),
            );
            contextFacts.append(renderFacts(description));
          }
          if (description.note !== null) {
            const line = text("p", "note", "");
            line.append(icon("info"), description.note);
            contextNow.append(line);
          }
          break;
        }
      }
    }
    // Keep keyboard focus in the status area when the control that had it is replaced: a
    // recovery button hands it to the refresh button while a read runs.
    if (bodyHadFocus && active !== null && !contextNow.contains(active)) {
      refreshButton.focus();
    }
  }

  // Updates the static playback controls in place from what the observed state offers.
  function renderControls(): void {
    const el = elements;
    const controls = describeControls(state);
    const active = document.activeElement;
    const controlsHadFocus = active !== null && el.controls.contains(active);

    el.controls.hidden = !controls.visible;

    const primary = controls.primary;
    el.primaryButton.hidden = primary === null;
    if (primary !== null) {
      const pending = controls.pendingCommand === primary.command;
      el.primaryLabel.textContent = pending ? primary.busyLabel : primary.label;
      setIcon(el.primaryButton, pending ? "buffering" : primary.command);
      setBusy(el.primaryButton, controls.busy);
      el.primaryButton.classList.toggle("is-loading", pending);
      el.primaryButton.setAttribute("aria-busy", String(pending));
    }

    const stopping = controls.pendingCommand === "stop";
    el.stopButton.hidden = !controls.stop;
    el.stopLabel.textContent = stopping ? "Stopping…" : "Stop";
    setIcon(el.stopButton, stopping ? "buffering" : "stop");
    setBusy(el.stopButton, controls.busy);
    el.stopButton.classList.toggle("is-loading", stopping);
    el.stopButton.setAttribute("aria-busy", String(stopping));

    const seek = controls.seek;
    el.seek.hidden = seek === null;
    if (seek !== null) {
      el.seekInput.max = String(seek.max);
      el.seekInput.value = String(seek.value);
      el.seekInput.setAttribute("aria-valuetext", `${seek.valueText} of ${seek.maxText}`);
      if (controls.busy && active === el.seekInput) {
        seekWantsFocus = true;
      }
      el.seekInput.disabled = controls.busy;
      if (!controls.busy && seekWantsFocus) {
        seekWantsFocus = false;
        el.seekInput.focus();
      }
      // A draft is a request being composed, worded as such; the position the TV reported
      // stays in the state row above.
      el.seekLabel.classList.toggle("is-empty", !seek.drafting);
      el.seekLabel.textContent = seek.drafting ? `Go to ${seek.valueText}` : "";
    }

    el.controlsNote.hidden = controls.unavailable === null;
    el.controlsNote.replaceChildren();
    if (controls.unavailable !== null) {
      el.controlsNote.append(icon("info"), controls.unavailable);
    }
    el.seekNote.hidden = controls.seekNote === null;
    el.seekNote.replaceChildren();
    if (controls.seekNote !== null) {
      el.seekNote.append(icon("info"), controls.seekNote);
    }

    renderFeedback();
    if (controlsHadFocus && el.controls.hidden) {
      (el.refreshButton.hidden ? el.pickerSummary : el.refreshButton).focus();
    }
  }

  // The command's progress and outcome. Redrawn only when it changed: it is a live region.
  function renderFeedback(): void {
    const { commandFeedback } = elements;
    const feedback = describeCommandFeedback(state);
    const key = JSON.stringify(feedback);
    if (key === feedbackKey) {
      return;
    }
    const hadFocus = commandFeedback.contains(document.activeElement);
    feedbackKey = key;
    commandFeedback.replaceChildren();
    for (const line of feedback.lines) {
      if (line.announceOnly) {
        commandFeedback.append(text("p", "sr-only", line.text));
        continue;
      }
      const row = text("p", `feedback feedback-${line.tone}`, "");
      row.classList.toggle("is-loading", state.command.kind === "pending");
      row.append(
        icon(state.command.kind === "pending" ? "buffering" : "info"),
        text("span", "", line.text),
      );
      commandFeedback.append(row);
    }
    if (feedback.failure !== null) {
      const { title, hint, technical, recovery } = feedback.failure;
      commandFeedback.append(
        problemBlock(
          title,
          hint,
          technical,
          recovery === "discover"
            ? { label: "Find devices", run: () => void discover() }
            : { label: "Check state", run: onRefresh },
        ),
      );
    } else if (feedback.checkState) {
      const actions = document.createElement("div");
      actions.className = "feedback-actions";
      const check = text("button", "btn", "Check state");
      (check as HTMLButtonElement).type = "button";
      check.addEventListener("click", onRefresh);
      actions.append(check);
      if (feedback.detail !== null) {
        actions.append(disclosure(feedback.detail));
      }
      commandFeedback.append(actions);
    }
    // The message that had focus (a recovery button) is gone: keep focus on the controls.
    if (hadFocus && !commandFeedback.contains(document.activeElement)) {
      elements.refreshButton.focus();
    }
  }

  function render(): void {
    // The list is rebuilt on every state change; put keyboard focus back on the same device.
    const focusedDeviceId = document.activeElement?.getAttribute("data-device-id") ?? null;
    const refreshHadFocus = document.activeElement === elements.refreshButton;
    renderSummary();
    renderDevices();
    renderContext();
    renderControls();
    // One recovery action at a time: a failed status read, or a command outcome that offers
    // its own labelled button, makes the refresh icon a repeat of it, so it steps aside and
    // hands over keyboard focus if it had it.
    elements.refreshButton.hidden =
      state.status.kind === "failed" ||
      state.command.kind === "sent" ||
      state.command.kind === "failed";
    if (refreshHadFocus && elements.refreshButton.hidden) {
      (
        elements.contextNow.querySelector<HTMLElement>(".problem .btn") ??
        elements.commandFeedback.querySelector<HTMLElement>("button")
      )?.focus();
    }
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

  async function runCommand(request: CommandRequest): Promise<void> {
    let outcome: CommandOutcome;
    try {
      const args: Record<string, unknown> = { deviceId: request.deviceId };
      if (request.command === "seek") {
        args["positionSeconds"] = request.positionSeconds;
      }
      const answer = await invoke<CommandAnswer>(`bridge_${request.command}`, args);
      outcome = { ok: true, result: answer.result };
    } catch (error) {
      outcome = { ok: false, failure: toBridgeFailure(error) };
    }
    // `state` is read when the answer arrives, so a late answer cannot overwrite anything.
    update(finishCommand(state, request.requestId, outcome));
  }

  function onTransport(command: "play" | "pause" | "stop"): void {
    const next = startTransport(state, command);
    update(next.state);
    if (next.request !== null) {
      void runCommand(next.request);
    }
  }

  function commitSeek(): void {
    const next = startSeek(state);
    update(next.state);
    if (next.request !== null) {
      void runCommand(next.request);
    }
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
  elements.primaryButton.addEventListener("click", () => {
    const command = describeControls(state).primary?.command;
    if (command !== undefined) {
      onTransport(command);
    }
  });
  elements.stopButton.addEventListener("click", () => onTransport("stop"));
  elements.seekInput.addEventListener("input", () => {
    window.clearTimeout(seekTimer);
    update(setSeekDraft(state, Number(elements.seekInput.value)));
  });
  elements.seekInput.addEventListener("change", () => {
    window.clearTimeout(seekTimer);
    seekTimer = window.setTimeout(commitSeek, SEEK_COMMIT_DELAY_MS);
  });
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
    contextNow: byId("context-now"),
    contextFacts: byId("context-facts"),
    observed: byId("observed"),
    refreshButton: byId<HTMLButtonElement>("refresh-button"),
    controls: byId("controls"),
    seek: byId("seek"),
    seekInput: byId<HTMLInputElement>("seek-input"),
    seekLabel: byId("seek-label"),
    primaryButton: byId<HTMLButtonElement>("primary-button"),
    primaryLabel: byId("primary-label"),
    stopButton: byId<HTMLButtonElement>("stop-button"),
    stopLabel: byId("stop-label"),
    controlsNote: byId("controls-note"),
    seekNote: byId("seek-note"),
    commandFeedback: byId("command-feedback"),
  });
});
