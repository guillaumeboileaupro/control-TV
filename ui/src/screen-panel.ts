// The "Screen to TV" panel: duplicate one screen or extend the desktop onto the TV (resolution,
// refresh rate and where the TV's screen sits, chosen in the extension menu), after an explicit
// consent step, and stop. The panel owns its elements and its own small state (see
// screen.ts); every call goes through `call`, which `main.ts` maps onto the shell's commands.
// It is shown only where this computer's screen can be captured (an X11 desktop): elsewhere
// (Android, Wayland) the listing fails and the panel stays hidden.

import { setBusy } from "./dom.ts";
import type { JsonValue } from "./json.ts";
import { toBridgeFailure, type BridgeFailure } from "./model.ts";
import {
  POSITIONS,
  RATES,
  REFRESH_RATES,
  SIZES,
  askConsent,
  cancelConsent,
  choose,
  consentedStart,
  describeScreen,
  finishPoll,
  finishScreen,
  follows,
  initialScreenState,
  parseListing,
  startStop,
  withScreens,
  type Placement,
  type Rate,
  type RefreshRate,
  type ScreenMode,
  type ScreenPanelState,
  type SizeName,
} from "./screen.ts";

// How often the panel asks for the stream's state while it starts, runs or stops.
const FOLLOW_INTERVAL_MS = 2000;

export interface ScreenPanelElements {
  panel: HTMLElement;
  mode: HTMLElement;
  screenField: HTMLElement;
  screen: HTMLSelectElement;
  size: HTMLSelectElement;
  fps: HTMLSelectElement;
  // The extension menu: refresh rate and the arrangement of the TV's screen around the main one.
  extension: HTMLElement;
  refresh: HTMLSelectElement;
  layout: HTMLElement;
  ask: HTMLButtonElement;
  consent: HTMLElement;
  consentText: HTMLElement;
  confirm: HTMLButtonElement;
  cancel: HTMLButtonElement;
  stop: HTMLButtonElement;
  status: HTMLElement;
  error: HTMLElement;
}

export type ScreenCall = (command: string, args: Record<string, JsonValue>) => Promise<JsonValue>;

export interface ScreenPanelPorts {
  call: ScreenCall;
  // The selected TV's stable id, or null.
  selectedTv(): string | null;
  timers: {
    set(callback: () => void, delayMs: number): number;
  };
}

function isMode(value: string | undefined): value is ScreenMode {
  return value === "duplicate" || value === "extend";
}

function isSize(value: string): value is SizeName {
  return (SIZES as readonly string[]).includes(value);
}

function isRate(value: number): value is Rate {
  return (RATES as readonly number[]).includes(value);
}

function isRefresh(value: number): value is RefreshRate {
  return (REFRESH_RATES as readonly number[]).includes(value);
}

function isPlacement(value: string | undefined): value is Placement {
  return (POSITIONS as readonly (string | undefined)[]).includes(value);
}

export function createScreenPanel(
  el: ScreenPanelElements,
  ports: ScreenPanelPorts,
): { refresh(): void; render(): void } {
  let state: ScreenPanelState = initialScreenState();
  let followTimer: number | null = null;

  function update(next: ScreenPanelState): void {
    state = next;
    render();
    if (follows(state) && followTimer === null) {
      followTimer = ports.timers.set(poll, FOLLOW_INTERVAL_MS);
    }
  }

  async function answer(
    command: string,
    args: Record<string, JsonValue>,
  ): Promise<{ ok: true; result: JsonValue } | { ok: false; failure: BridgeFailure }> {
    try {
      return { ok: true, result: await ports.call(command, args) };
    } catch (error) {
      return { ok: false, failure: toBridgeFailure(error) };
    }
  }

  async function poll(): Promise<void> {
    followTimer = null;
    if (state.pending || !follows(state)) {
      if (follows(state)) {
        update(state);
      }
      return;
    }
    const reply = await answer("bridge_get_screen_stream", {});
    update(reply.ok ? finishPoll(state, reply.result) : state);
  }

  async function refresh(): Promise<void> {
    const listed = await answer("bridge_list_screens", {});
    let next = withScreens(state, listed.ok ? parseListing(listed.result) : null);
    if (next.availability.kind === "available") {
      const current = await answer("bridge_get_screen_stream", {});
      next = current.ok ? finishPoll(next, current.result) : next;
    }
    update(next);
  }

  function render(): void {
    const view = describeScreen(state, ports.selectedTv() !== null);
    el.panel.hidden = !view.visible;
    for (const button of el.mode.querySelectorAll<HTMLButtonElement>("[data-screen-mode]")) {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset["screenMode"] === state.choice.mode),
      );
      setBusy(button, !view.canChoose);
    }
    el.screenField.hidden = state.choice.mode !== "duplicate";
    const screens = state.availability.kind === "available" ? state.availability.screens : [];
    const key = JSON.stringify(screens);
    if (el.screen.dataset["key"] !== key) {
      el.screen.dataset["key"] = key;
      el.screen.replaceChildren(
        ...screens.map((screen) => {
          const label = `${screen.name} (${screen.width}×${screen.height}${screen.primary ? ", main" : ""})`;
          return new Option(label, screen.name);
        }),
      );
    }
    el.screen.value = state.choice.screen ?? "";
    el.size.value = state.choice.size;
    el.fps.value = String(state.choice.fps);
    el.refresh.value = String(state.choice.refresh);
    for (const field of [el.screen, el.size, el.fps, el.refresh]) {
      field.disabled = !view.canChoose;
    }
    el.extension.hidden = !view.extending;
    for (const slot of el.layout.querySelectorAll<HTMLButtonElement>("[data-position]")) {
      const chosen = slot.dataset["position"] === state.choice.position;
      slot.setAttribute("aria-pressed", String(chosen));
      slot.textContent = chosen ? "TV" : "";
      setBusy(slot, !view.canChoose);
    }
    // Starting is offered only from the choice; a running or failed stream offers Stop/Dismiss.
    el.ask.hidden = view.confirming || state.stream.state !== "idle";
    setBusy(el.ask, !view.canAsk);
    el.ask.textContent =
      state.choice.mode === "duplicate" ? "Duplicate on the TV" : "Extend to the TV";
    el.consent.hidden = !view.confirming;
    el.consentText.textContent = view.consentText;
    el.stop.hidden = !view.canStop && state.stream.state !== "stopping";
    setBusy(el.stop, !view.canStop);
    el.stop.textContent = view.stopLabel;
    el.status.textContent = view.statusText;
    el.error.hidden = view.errorText === null;
    el.error.textContent = view.errorText ?? "";
  }

  async function start(): Promise<void> {
    const next = consentedStart(state, ports.selectedTv());
    update(next.state);
    if (next.request !== null) {
      const { command, args } = next.request;
      update(finishScreen(state, await answer(command, { ...args })));
    }
  }

  async function stop(): Promise<void> {
    const next = startStop(state);
    update(next.state);
    if (next.send) {
      update(finishScreen(state, await answer("bridge_stop_screen_stream", {})));
    }
  }

  for (const button of el.mode.querySelectorAll<HTMLButtonElement>("[data-screen-mode]")) {
    const mode = button.dataset["screenMode"];
    if (isMode(mode)) {
      button.addEventListener("click", () => update(choose(state, { mode })));
    }
  }
  el.screen.addEventListener("change", () => update(choose(state, { screen: el.screen.value })));
  el.size.addEventListener("change", () => {
    if (isSize(el.size.value)) {
      update(choose(state, { size: el.size.value }));
    }
  });
  el.fps.addEventListener("change", () => {
    const fps = Number(el.fps.value);
    if (isRate(fps)) {
      update(choose(state, { fps }));
    }
  });
  el.refresh.addEventListener("change", () => {
    const refresh = Number(el.refresh.value);
    if (isRefresh(refresh)) {
      update(choose(state, { refresh }));
    }
  });
  for (const slot of el.layout.querySelectorAll<HTMLButtonElement>("[data-position]")) {
    const position = slot.dataset["position"];
    if (isPlacement(position)) {
      slot.addEventListener("click", () => update(choose(state, { position })));
    }
  }
  el.ask.addEventListener("click", () => {
    update(askConsent(state, ports.selectedTv() !== null));
    if (state.confirming) {
      el.confirm.focus();
    }
  });
  el.cancel.addEventListener("click", () => {
    update(cancelConsent(state));
    el.ask.focus();
  });
  el.confirm.addEventListener("click", () => void start());
  el.stop.addEventListener("click", () => void stop());

  return { refresh: () => void refresh(), render };
}
