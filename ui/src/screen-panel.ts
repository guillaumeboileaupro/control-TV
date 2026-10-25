// The "Screen to TV" panel: duplicate one screen or extend the desktop onto the TV (resolution,
// refresh rate and where the TV's screen sits, chosen in the extension menu), after an explicit
// consent step, and stop. The panel owns its elements and its own small state (see
// screen.ts); every call goes through `call`, which `main.ts` maps onto the shell's commands.
// It is shown only where this computer's screen can be captured (an X11 desktop): elsewhere
// (Android, Wayland) the listing fails and the panel stays hidden.

import { createDisplayPanel, type DisplayPanelElements } from "./display-panel.ts";
import { setBusy } from "./dom.ts";
import type { JsonValue } from "./json.ts";
import { toBridgeFailure, type BridgeFailure } from "./model.ts";
import {
  pendingExtension,
  RATES,
  REFRESH_RATES,
  SIZES,
  askConsent,
  arrangementScreens,
  cancelConsent,
  choose,
  consentedStart,
  describeScreen,
  finishPoll,
  finishScreen,
  finishWebRtc,
  follows,
  initialScreenState,
  moveExtension,
  parseListing,
  parseReceiverCapabilities,
  previewScreens,
  startStop,
  selectTransport,
  shouldApplyPoll,
  withScreens,
  type Rate,
  type RefreshRate,
  type ScreenMode,
  type ScreenPanelState,
  type SizeName,
  type TransportChoice,
} from "./screen.ts";

// How often the panel asks for the stream's state while it starts, runs or stops.
const FOLLOW_INTERVAL_MS = 2000;

export interface ScreenPanelElements {
  panel: HTMLElement;
  mode: HTMLElement;
  preview: Element;
  // The display manager shown while extending (see display-panel.ts).
  display: DisplayPanelElements;
  screenField: HTMLElement;
  screen: HTMLSelectElement;
  size: HTMLSelectElement;
  fps: HTMLSelectElement;
  transport: HTMLSelectElement;
  // The extension menu: refresh rate and the arrangement of the TV's screen around the main one.
  extension: HTMLElement;
  refresh: HTMLSelectElement;
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
    clear(handle: number): void;
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

function isTransport(value: string): value is TransportChoice {
  return value === "auto" || value === "webrtc" || value === "hls";
}

export function createScreenPanel(
  el: ScreenPanelElements,
  ports: ScreenPanelPorts,
): { refresh(): void; render(): void } {
  let state: ScreenPanelState = initialScreenState();
  let followTimer: number | null = null;
  let operation = 0;
  const displays = createDisplayPanel(el.display, {
    call: ports.call,
    timers: ports.timers,
    pendingTv: () => pendingExtension(state),
    placeTv: (x, y) => update(moveExtension(state, x, y)),
  });

  function update(next: ScreenPanelState): void {
    const before = state;
    state = next;
    render();
    if (follows(state) && followTimer === null) {
      followTimer = ports.timers.set(poll, FOLLOW_INTERVAL_MS);
    }
    // The screens change when an extension starts or stops, or when extending is chosen.
    const extending = state.choice.mode === "extend";
    if (
      extending &&
      (before.stream.state !== state.stream.state || before.choice.mode !== state.choice.mode)
    ) {
      displays.refresh();
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
    const generation = operation;
    const transport = state.transportUsed;
    const reply = await answer(
      transport === "webrtc" ? "get_webrtc_stream" : "bridge_get_screen_stream",
      {},
    );
    if (!shouldApplyPoll(generation, operation, state.pending, transport, state.transportUsed)) {
      return;
    }
    update(
      transport === "webrtc"
        ? finishWebRtc(state, reply)
        : reply.ok
          ? finishPoll(state, reply.result)
          : state,
    );
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
    const preview = previewScreens(arrangementScreens(state));
    el.preview.replaceChildren(
      ...preview.flatMap((screen, index) => {
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        group.classList.add("display-preview-screen");
        if (screen.name === state.choice.screen || screen.virtual === true) {
          group.classList.add("is-selected");
        }
        group.setAttribute("role", "button");
        group.setAttribute("tabindex", view.canChoose ? "0" : "-1");
        group.setAttribute(
          "aria-label",
          screen.virtual === true
            ? `TV display, screen ${index + 1}`
            : `${screen.name}, screen ${index + 1}`,
        );
        group.setAttribute(
          "aria-pressed",
          String(screen.name === state.choice.screen || screen.virtual === true),
        );
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", String(screen.previewX));
        rect.setAttribute("y", String(screen.previewY));
        rect.setAttribute("width", String(screen.previewWidth));
        rect.setAttribute("height", String(screen.previewHeight));
        rect.setAttribute("rx", "8");
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", String(screen.previewX + screen.previewWidth / 2));
        label.setAttribute("y", String(screen.previewY + screen.previewHeight / 2));
        label.textContent = String(index + 1);
        const select = (): void => {
          if (screen.virtual !== true) {
            update(choose(state, { screen: screen.name }));
          }
        };
        group.addEventListener("click", select);
        group.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            select();
          }
        });
        if (screen.virtual === true && state.choice.x !== null && state.choice.y !== null) {
          group.classList.add("is-draggable");
          group.addEventListener("pointerdown", (event) => {
            if (!view.canChoose) {
              return;
            }
            const startClientX = event.clientX;
            const startClientY = event.clientY;
            const startX = state.choice.x ?? 0;
            const startY = state.choice.y ?? 0;
            const scale = screen.previewWidth / screen.width;
            group.setPointerCapture(event.pointerId);
            const moving = (move: PointerEvent): void => {
              group.setAttribute(
                "transform",
                `translate(${move.clientX - startClientX} ${move.clientY - startClientY})`,
              );
            };
            function cleanupDrag(): void {
              group.removeEventListener("pointermove", moving);
              group.removeEventListener("pointerup", finished);
              group.removeEventListener("pointercancel", cancelled);
            }
            function finished(up: PointerEvent): void {
              cleanupDrag();
              update(
                moveExtension(
                  state,
                  startX + (up.clientX - startClientX) / scale,
                  startY + (up.clientY - startClientY) / scale,
                ),
              );
            }
            function cancelled(): void {
              cleanupDrag();
              group.removeAttribute("transform");
            }
            group.addEventListener("pointermove", moving);
            group.addEventListener("pointerup", finished);
            group.addEventListener("pointercancel", cancelled);
          });
        }
        group.append(rect, label);
        return [group];
      }),
    );
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
    el.transport.value = state.choice.transport;
    el.refresh.value = String(state.choice.refresh);
    for (const field of [el.screen, el.size, el.fps, el.transport, el.refresh]) {
      field.disabled = !view.canChoose;
    }
    el.extension.hidden = !view.extending;
    // Starting is offered only from the choice; a running or failed stream offers Stop/Dismiss.
    el.ask.hidden = view.confirming || state.stream.state !== "idle";
    setBusy(el.ask, !view.canAsk);
    el.ask.textContent =
      state.choice.mode === "duplicate" ? "Duplicate on the TV" : "Extend to the TV";
    // The preview picks the screen to duplicate; the display manager replaces it when extending.
    el.preview.toggleAttribute("hidden", state.choice.mode === "extend");
    displays.render();
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
      const generation = ++operation;
      const capabilitiesReply = await answer("bridge_get_screen_receiver_capabilities", {
        deviceId: next.request.args.deviceId,
      });
      if (generation !== operation) {
        return;
      }
      if (!capabilitiesReply.ok) {
        update(finishScreen(state, capabilitiesReply));
        return;
      }
      const capabilities = parseReceiverCapabilities(capabilitiesReply.result);
      const used = capabilities && selectTransport(next.request.transport, capabilities);
      if (used === null) {
        update(
          finishScreen(state, {
            ok: false,
            failure: {
              code: "receiver_unavailable",
              message:
                next.request.transport === "webrtc"
                  ? "WebRTC is not confirmed for the selected TV"
                  : "No screen streaming transport is available for the selected TV",
            },
          }),
        );
        return;
      }
      state = { ...state, transportUsed: used };
      const reply = await answer(
        used === "webrtc" ? "start_webrtc_stream" : next.request.command,
        { ...next.request.args },
      );
      if (generation !== operation) {
        return;
      }
      update(used === "webrtc" ? finishWebRtc(state, reply) : finishScreen(state, reply));
    }
  }

  async function stop(): Promise<void> {
    const next = startStop(state);
    update(next.state);
    if (next.send) {
      const generation = ++operation;
      const webrtc = state.transportUsed === "webrtc";
      const reply = await answer(webrtc ? "stop_webrtc_stream" : "bridge_stop_screen_stream", {});
      if (generation !== operation) {
        return;
      }
      update(webrtc ? finishWebRtc(state, reply) : finishScreen(state, reply));
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
  el.transport.addEventListener("change", () => {
    if (isTransport(el.transport.value)) {
      update(choose(state, { transport: el.transport.value }));
    }
  });
  el.refresh.addEventListener("change", () => {
    const refresh = Number(el.refresh.value);
    if (isRefresh(refresh)) {
      update(choose(state, { refresh }));
    }
  });
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
