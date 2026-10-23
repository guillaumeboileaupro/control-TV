import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { JsonValue } from "./json.ts";

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
  HEALTHY,
  initialState,
  recordServiceAnswer,
  recordServiceFailure,
  refreshStatus,
  selectDevice,
  setHostCapabilities,
  chosenDevice,
  startDiscovery,
  toBridgeFailure,
  type AppState,
  type BridgeFailure,
  type Device,
  type PlaybackKind,
  type DeviceStatus,
  type ServiceHealth,
  type StatusOutcome,
  type StatusRequest,
} from "./model.ts";
import { createFocusReturn, createSoundController } from "./interaction.ts";
import {
  commandArguments,
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
import {
  finishRemote,
  remoteInvocation,
  setAppFilter,
  startRemote,
  type RemoteAction,
  type RemoteAnswer,
  type RemoteRequest,
} from "./remote.ts";
import { describeSound } from "./sound.ts";
import { byId, disclosure, icon, setBusy, setIcon, text } from "./dom.ts";
import { createRemotePanel, type RemotePanelElements } from "./remote-panel.ts";
import { createScreenPanel, type ScreenPanelElements } from "./screen-panel.ts";
import { isTrayAction, trayRequest } from "./tray.ts";

// Every `invoke` below maps onto one application command, which reaches the devices
// through the shared control layer. This file performs the I/O and draws the DOM; the
// rules (selection by stable id, one request in flight, which controls the observed state
// offers, wording) live in model.ts and playback.ts. It must never grow its own
// device/control logic. It sends a command only when the operator uses a control the observed
// state offers, never resends one, and never shows a state the TV did not report.
// Internal component names never appear in the normal view; raw error text lives only in a
// collapsed diagnostic disclosure.

// Waits for the seek control to settle (a drag release, or the last of several key presses)
// before sending one seek, so moving the control never sends a burst of commands.
const SEEK_COMMIT_DELAY_MS = 400;

interface PingResult {
  status: string;
  controlTvVersion: string;
  tvApps: boolean;
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
  remote: RemotePanelElements;
  screen: ScreenPanelElements;
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
  pauseNote: HTMLElement;
  sound: HTMLElement;
  muteButton: HTMLButtonElement;
  volumeInput: HTMLInputElement;
  soundReadout: HTMLElement;
  soundNote: HTMLElement;
  commandFeedback: HTMLElement;
}

const PLAYBACK_ICONS: Record<PlaybackKind, string> = {
  playing: "play",
  paused: "pause",
  buffering: "buffering",
  idle: "idle",
  undetermined: "undetermined",
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

function problem(
  failure: BridgeFailure,
  action: string,
  recovery: { label: string; run: () => void } | null,
): HTMLElement {
  const description = describeFailure(failure, action);
  return problemBlock(description.title, description.hint, description.technical, recovery);
}

function start(elements: Elements): void {
  let state = initialState();
  let health: ServiceHealth = HEALTHY;
  // Numbers every bridge request when it starts, so only a request started after a service
  // problem was recorded can show that the service answers again.
  let requestsStarted = 0;
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
  // A range control disabled by its command loses keyboard focus. Return it after the answer
  // only while the user has not intentionally focused another control in the meantime.
  const seekFocus = createFocusReturn(() => elements.seekInput.focus());
  const volumeFocus = createFocusReturn(() => elements.volumeInput.focus());
  document.addEventListener("focusin", (event) => {
    const target = event.target;
    // Losing focus to the document because a range was disabled is not a new user intention.
    // Every real element other than that range is.
    if (target !== document.body && target !== document.documentElement) {
      if (target !== elements.seekInput) {
        seekFocus.cancel();
      }
      if (target !== elements.volumeInput) {
        volumeFocus.cancel();
      }
    }
  });

  function renderNotice(): void {
    elements.serviceNotice.replaceChildren();
    elements.serviceNotice.hidden = health.failure === null;
    if (health.failure !== null) {
      elements.serviceNotice.append(problem(health.failure, "start", null));
    }
  }

  // Ends a recorded service problem once a later request was answered by the service. The
  // request's own outcome is handled by its caller, unchanged.
  function noteAnswer(request: number, failure: BridgeFailure | null): void {
    const next = recordServiceAnswer(health, request, failure);
    if (next !== health) {
      health = next;
      renderNotice();
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
    if (description.byline !== null) {
      parts.push(text("p", "byline", description.byline));
    }

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

  // Quiet facts under the controls. Volume and mute are not among them: the sound control shows
  // them, and saying the same thing twice would only add noise. Nothing to say draws nothing.
  function renderFacts(description: ReturnType<typeof describeStatus>): HTMLElement | null {
    const facts = document.createElement("ul");
    facts.className = "facts";
    const add = (iconName: string, label: string): void => {
      const item = document.createElement("li");
      item.className = "fact";
      item.append(icon(iconName), label);
      facts.append(item);
    };
    if (description.standby) {
      add("idle", "In standby");
    }
    if (description.application !== null) {
      add("app", description.application);
    }
    return facts.childElementCount === 0 ? null : facts;
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
            const facts = renderFacts(description);
            if (facts !== null) {
              contextFacts.append(facts);
            }
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
        seekFocus.remember();
      }
      el.seekInput.disabled = controls.busy;
      if (!controls.busy) {
        seekFocus.restore();
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
    el.pauseNote.hidden = controls.pauseNote === null;
    el.pauseNote.replaceChildren();
    if (controls.pauseNote !== null) {
      el.pauseNote.append(icon("info"), controls.pauseNote);
    }
    el.seekNote.hidden = controls.seekNote === null;
    el.seekNote.replaceChildren();
    if (controls.seekNote !== null) {
      el.seekNote.append(icon("info"), controls.seekNote);
    }

    renderFeedback();
    if (controlsHadFocus && el.controls.hidden) {
      (el.refreshButton.hidden ? el.pickerSummary : el.refreshButton).focus();
    } else if (active === el.primaryButton && el.primaryButton.hidden && !el.stopButton.hidden) {
      // The toggle went away (the TV says this media can't be paused): keep keyboard focus on
      // the controls rather than dropping it to the page.
      el.stopButton.focus();
    }
  }

  // Updates the static sound controls in place from what the observed state offers. The slider
  // shows the level being sent or composed, else the TV's; the mute button offers the opposite
  // of the mute state the TV reported and never remembers a state of its own.
  function renderSound(): void {
    const el = elements;
    const sound = describeSound(state);
    const active = document.activeElement;
    const soundHadFocus = active !== null && el.sound.contains(active);

    el.sound.hidden = !sound.visible;

    const mute = sound.mute;
    el.muteButton.hidden = mute === null;
    if (mute !== null) {
      const muting = sound.pendingCommand === "set_muted";
      el.muteButton.setAttribute("aria-label", mute.action);
      el.muteButton.title = mute.action;
      setIcon(el.muteButton, muting ? "buffering" : mute.muted ? "volume-off" : "volume");
      setBusy(el.muteButton, sound.busy);
      el.muteButton.classList.toggle("is-muted", mute.muted);
      el.muteButton.classList.toggle("is-loading", muting);
      el.muteButton.setAttribute("aria-busy", String(muting));
    }

    const volume = sound.volume;
    el.volumeInput.hidden = volume === null;
    if (volume !== null) {
      el.volumeInput.value = String(volume.value);
      el.volumeInput.setAttribute("aria-valuetext", `${volume.value} percent`);
      // The slider is disabled while a request runs, which drops keyboard focus; give it back
      // once it is enabled again.
      if (sound.busy && active === el.volumeInput) {
        volumeFocus.remember();
      }
      el.volumeInput.disabled = sound.busy;
      if (!sound.busy) {
        volumeFocus.restore();
      }
    }

    el.soundReadout.textContent = sound.readout;
    // With a slider the line is kept (blank) so composing a level never moves what is below;
    // without one there is nothing to protect and a blank line would only be a gap.
    el.soundReadout.hidden = volume === null && sound.readout === "";
    el.soundReadout.classList.toggle("is-empty", sound.readout === "");
    el.soundNote.hidden = sound.note === null;
    el.soundNote.replaceChildren();
    if (sound.note !== null) {
      el.soundNote.append(icon("info"), sound.note);
    }

    if (soundHadFocus && el.sound.hidden) {
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
    renderSound();
    remotePanel.render(state);
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

  // Drawn again when the selected TV changes: the stream needs one.
  let screenPanel: { refresh(): void; render(): void } | null = null;

  function update(next: AppState): void {
    state = next;
    render();
    screenPanel?.render();
  }

  function onSelect(deviceId: string): void {
    const next = selectDevice(state, deviceId);
    update(next.state);
    const chosen = chosenDevice(next);
    if (chosen !== null) {
      // The explicit choice becomes the home-screen widget's TV (Android only; nothing is
      // sent to any TV). A failure to store it never affects the window.
      void invoke("remember_selection", chosen).catch(() => undefined);
    }
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
    const number = ++requestsStarted;
    let outcome: StatusOutcome;
    try {
      const result = await invoke<GetStatusResult>("bridge_get_status", {
        deviceId: request.deviceId,
      });
      outcome = { ok: true, status: result.status };
    } catch (error) {
      outcome = { ok: false, failure: toBridgeFailure(error) };
    }
    noteAnswer(number, outcome.ok ? null : outcome.failure);
    // `state` is read when the answer arrives, so a late answer cannot overwrite a newer read.
    update(finishStatusRead(state, request.requestId, outcome));
  }

  async function runCommand(request: CommandRequest): Promise<void> {
    const number = ++requestsStarted;
    let outcome: CommandOutcome;
    try {
      const answer = await invoke<CommandAnswer>(
        `bridge_${request.command}`,
        commandArguments(request),
      );
      outcome = { ok: true, result: answer.result };
    } catch (error) {
      outcome = { ok: false, failure: toBridgeFailure(error) };
    }
    noteAnswer(number, outcome.ok ? null : outcome.failure);
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

  async function runRemote(request: RemoteRequest): Promise<void> {
    const number = ++requestsStarted;
    const { command, args } = remoteInvocation(request);
    let answer: RemoteAnswer;
    try {
      answer = { ok: true, result: await invoke<JsonValue>(command, args) };
    } catch (error) {
      answer = { ok: false, failure: toBridgeFailure(error) };
    }
    noteAnswer(number, answer.ok ? null : answer.failure);
    // `state` is read when the answer arrives, so a late answer cannot overwrite anything.
    update(finishRemote(state, request.requestId, answer));
  }

  function onRemote(action: RemoteAction): void {
    const next = startRemote(state, action);
    update(next.state);
    if (next.request !== null) {
      void runRemote(next.request);
    }
  }

  async function discover(): Promise<void> {
    if (!canDiscover(state)) {
      return;
    }
    elements.picker.open = true;
    update(startDiscovery(state));
    const number = ++requestsStarted;
    try {
      const result = await invoke<DiscoverDevicesResult>("bridge_discover_devices");
      noteAnswer(number, null);
      update(finishDiscovery(state, result.devices));
    } catch (error) {
      const failure = toBridgeFailure(error);
      noteAnswer(number, failure);
      update(failDiscovery(state, failure));
    }
  }

  async function checkBackend(): Promise<void> {
    const number = ++requestsStarted;
    try {
      const result = await invoke<PingResult>("bridge_ping");
      update(setHostCapabilities(state, result.tvApps === true));
      noteAnswer(number, null);
    } catch (error) {
      const next = recordServiceFailure(health, toBridgeFailure(error), requestsStarted);
      if (next !== health) {
        health = next;
        renderNotice();
      }
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
  // Moving the volume control only composes a level; one command is sent once it has settled.
  const remotePanel = createRemotePanel(elements.remote, {
    run: onRemote,
    filterApps: (filter) => update(setAppFilter(state, filter)),
    focusElsewhere: () =>
      (elements.refreshButton.hidden ? elements.pickerSummary : elements.refreshButton).focus(),
  });

  const sound = createSoundController({
    getState: () => state,
    setState: update,
    run: (request) => {
      void runCommand(request);
    },
    timers: {
      set: (callback, delayMs) => window.setTimeout(callback, delayMs),
      clear: (handle) => window.clearTimeout(handle),
    },
  });
  elements.volumeInput.addEventListener("pointerdown", () => sound.volumePointerDown());
  // The release is heard on the window: it may happen away from the control after a drag.
  window.addEventListener("pointerup", () => sound.volumePointerUp());
  window.addEventListener("pointercancel", () => sound.volumePointerUp());
  elements.volumeInput.addEventListener("keyup", () => sound.volumeKeyUp());
  elements.volumeInput.addEventListener("keydown", () => sound.volumeKeyDown());
  elements.volumeInput.addEventListener("input", () =>
    sound.volumeInput(Number(elements.volumeInput.value)),
  );
  elements.volumeInput.addEventListener("change", () => sound.volumeChange());
  elements.muteButton.addEventListener("click", () => sound.mute());
  elements.picker.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && elements.picker.open) {
      elements.picker.open = false;
      elements.pickerSummary.focus();
    }
  });

  // The tray's quick controls run as the window's own actions (see tray.ts); when one cannot
  // run, nothing is sent and the window comes to the front so the person sees why.
  void listen<JsonValue>("tray-action", (event) => {
    if (!isTrayAction(event.payload)) {
      return;
    }
    const request = trayRequest(state, event.payload);
    if (request.kind === "transport") {
      onTransport(request.command);
    } else if (request.kind === "remote") {
      onRemote(request.action);
    } else {
      void invoke("show_main_window").catch(() => undefined);
    }
  }).catch(() => undefined);

  // The screen stream has its own small state (screen-panel.ts); its calls are the shell's
  // commands like every other request.
  screenPanel = createScreenPanel(elements.screen, {
    call: (command, args) => invoke<JsonValue>(command, args),
    selectedTv: () => state.selected?.id ?? null,
    timers: {
      set: (callback, delayMs) => window.setTimeout(callback, delayMs),
      clear: (handle) => window.clearTimeout(handle),
    },
  });

  render();
  void checkBackend();
  screenPanel.refresh();
}

window.addEventListener("DOMContentLoaded", () => {
  start({
    screen: {
      panel: byId("screen-panel"),
      mode: byId("screen-mode"),
      preview: byId("screen-preview"),
      display: {
        scene: byId("display-scene"),
        arrangement: byId("display-arrangement"),
        settings: byId("display-settings"),
        title: byId("display-title"),
        enabled: byId<HTMLInputElement>("display-enabled"),
        primary: byId<HTMLInputElement>("display-primary"),
        resolution: byId<HTMLSelectElement>("display-resolution"),
        rate: byId<HTMLSelectElement>("display-rate"),
        rotation: byId<HTMLSelectElement>("display-rotation"),
        scale: byId<HTMLSelectElement>("display-scale"),
        apply: byId<HTMLButtonElement>("display-apply"),
        cancel: byId<HTMLButtonElement>("display-cancel"),
        keep: byId("display-keep"),
        keepText: byId("display-keep-text"),
        keepButton: byId<HTMLButtonElement>("display-keep-button"),
        revertButton: byId<HTMLButtonElement>("display-revert"),
        message: byId("display-message"),
      },
      screenField: byId("screen-field"),
      screen: byId<HTMLSelectElement>("screen-select"),
      size: byId<HTMLSelectElement>("screen-size"),
      fps: byId<HTMLSelectElement>("screen-fps"),
      extension: byId("screen-extension"),
      refresh: byId<HTMLSelectElement>("screen-refresh"),
      ask: byId<HTMLButtonElement>("screen-ask"),
      consent: byId("screen-consent"),
      consentText: byId("screen-consent-text"),
      confirm: byId<HTMLButtonElement>("screen-confirm"),
      cancel: byId<HTMLButtonElement>("screen-cancel"),
      stop: byId<HTMLButtonElement>("screen-stop"),
      status: byId("screen-status"),
      error: byId("screen-error"),
    },
    remote: {
      tvRemote: byId("tv-remote"),
      remotePairing: byId("remote-pairing"),
      remotePairingText: byId("remote-pairing-text"),
      remoteCode: byId<HTMLInputElement>("remote-code"),
      remotePair: byId<HTMLButtonElement>("remote-pair"),
      remoteShowCode: byId<HTMLButtonElement>("remote-show-code"),
      remoteRead: byId<HTMLButtonElement>("remote-read"),
      remoteTextForm: byId<HTMLFormElement>("remote-text-form"),
      remoteText: byId<HTMLInputElement>("remote-text"),
      remoteTextSend: byId<HTMLButtonElement>("remote-text-send"),
      remoteAppsList: byId<HTMLButtonElement>("remote-apps-list"),
      remoteAppsPairing: byId("remote-apps-pairing"),
      remoteAppsPairingForm: byId<HTMLFormElement>("remote-apps-pairing-form"),
      remoteAppsPairingEndpoint: byId<HTMLInputElement>("remote-apps-pairing-endpoint"),
      remoteAppsPairingCode: byId<HTMLInputElement>("remote-apps-pairing-code"),
      remoteAppsPair: byId<HTMLButtonElement>("remote-apps-pair"),
      remoteKeys: byId("remote-keys"),
      remoteAppsFilter: byId<HTMLInputElement>("remote-apps-filter"),
      remoteApps: byId("remote-apps"),
      remoteReadout: byId("remote-readout"),
      remoteFeedback: byId("remote-feedback"),
    },
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
    pauseNote: byId("pause-note"),
    sound: byId("sound"),
    muteButton: byId<HTMLButtonElement>("mute-button"),
    volumeInput: byId<HTMLInputElement>("volume-input"),
    soundReadout: byId("sound-readout"),
    soundNote: byId("sound-note"),
    commandFeedback: byId("command-feedback"),
  });
});
