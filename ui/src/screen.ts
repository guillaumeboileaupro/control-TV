// The "Screen to TV" panel's rules: which screens can be streamed, the person's choice (duplicate
// one screen or extend the desktop onto the TV: size, refresh rate and where the TV's screen sits,
// frame rate), the explicit consent step and what the panel shows for each state of the stream. Pure functions over plain data: the panel
// (screen-panel.ts) performs the calls and draws the DOM.
//
// The stream runs in the control layer, which needs the owner's explicit consent for every start
// and keeps one stream at a time; the state shown is the one it reports, never assumed.

import { isJsonObject, type JsonValue } from "./json.ts";
import type { BridgeFailure } from "./model.ts";

export type ScreenMode = "duplicate" | "extend";
export type StreamStateName = "idle" | "starting" | "running" | "stopping" | "failed";

// "auto" is the largest size the TV path takes (Full HD).
export const SIZES = ["auto", "1280x720", "1920x1080"] as const;
export type SizeName = (typeof SIZES)[number];
const PIXELS: Record<SizeName, { width: number; height: number }> = {
  auto: { width: 1920, height: 1080 },
  "1280x720": { width: 1280, height: 720 },
  "1920x1080": { width: 1920, height: 1080 },
};
// The extended screen's refresh rates and sides, as measured on the desktop (30 Hz refused).
export const REFRESH_RATES = [60, 50] as const;
export type RefreshRate = (typeof REFRESH_RATES)[number];
export const POSITIONS = ["left", "right", "above", "below"] as const;
export type Placement = (typeof POSITIONS)[number];
// 60 is not offered: the TV's Cast receiver did not keep up with 1080p60 (hardware test).
export const RATES = [25, 30] as const;
export type Rate = (typeof RATES)[number];

export interface ScreenOutput {
  name: string;
  width: number;
  height: number;
  x: number;
  y: number;
  primary: boolean;
  virtual?: boolean;
}

export interface PreviewScreen extends ScreenOutput {
  previewX: number;
  previewY: number;
  previewWidth: number;
  previewHeight: number;
}

export interface StreamStatus {
  state: StreamStateName;
  mode: ScreenMode | null;
  screen: string | null;
  error: string | null;
}

export interface ScreenChoice {
  mode: ScreenMode;
  screen: string | null;
  size: SizeName;
  fps: Rate;
  refresh: RefreshRate;
  position: Placement;
  x: number | null;
  y: number | null;
}

// What `bridge_list_screens` reports: the screens to duplicate, and the free video output the
// extension would use (null: the extension is not available until one is enabled).
export interface Listing {
  screens: ScreenOutput[];
  extensionOutput: string | null;
}

export type Availability =
  { kind: "unknown" } | ({ kind: "available" } & Listing) | { kind: "unavailable" };

export interface ScreenPanelState {
  availability: Availability;
  stream: StreamStatus;
  choice: ScreenChoice;
  // The consent step is shown for the current choice; nothing has been sent yet.
  confirming: boolean;
  // A request is in flight: the controls wait for its answer.
  pending: boolean;
  // The last request the control layer refused or could not answer.
  failure: BridgeFailure | null;
}

export const IDLE_STREAM: StreamStatus = { state: "idle", mode: null, screen: null, error: null };

export function initialScreenState(): ScreenPanelState {
  return {
    availability: { kind: "unknown" },
    stream: IDLE_STREAM,
    choice: {
      mode: "duplicate",
      screen: null,
      size: "auto",
      fps: 30,
      refresh: 60,
      position: "right",
      x: null,
      y: null,
    },
    confirming: false,
    pending: false,
    failure: null,
  };
}

const STATES: readonly StreamStateName[] = ["idle", "starting", "running", "stopping", "failed"];

function isMode(value: JsonValue | undefined): value is ScreenMode {
  return value === "duplicate" || value === "extend";
}

function isStateName(value: JsonValue | undefined): value is StreamStateName {
  return typeof value === "string" && (STATES as readonly string[]).includes(value);
}

// What a `bridge_list_screens` answer reports; `null` when it has another shape.
export function parseListing(value: JsonValue): Listing | null {
  if (!isJsonObject(value) || !Array.isArray(value["screens"])) {
    return null;
  }
  const extension = value["extension"];
  const output = isJsonObject(extension) ? extension["output"] : undefined;
  const screens: ScreenOutput[] = [];
  for (const entry of value["screens"]) {
    if (!isJsonObject(entry)) {
      return null;
    }
    const { name, width, height, x, y, primary } = entry;
    if (
      typeof name !== "string" ||
      typeof width !== "number" ||
      typeof height !== "number" ||
      typeof x !== "number" ||
      typeof y !== "number" ||
      !Number.isInteger(width) ||
      !Number.isInteger(height) ||
      !Number.isInteger(x) ||
      !Number.isInteger(y) ||
      width <= 0 ||
      height <= 0
    ) {
      return null;
    }
    screens.push({ name, width, height, x, y, primary: primary === true });
  }
  return { screens, extensionOutput: typeof output === "string" ? output : null };
}

export function previewScreens(screens: readonly ScreenOutput[]): PreviewScreen[] {
  if (screens.length === 0) {
    return [];
  }
  const left = Math.min(...screens.map((screen) => screen.x));
  const top = Math.min(...screens.map((screen) => screen.y));
  const right = Math.max(...screens.map((screen) => screen.x + screen.width));
  const bottom = Math.max(...screens.map((screen) => screen.y + screen.height));
  const scale = Math.min(560 / (right - left), 220 / (bottom - top));
  return screens.map((screen) => ({
    ...screen,
    previewX: 20 + (screen.x - left) * scale,
    previewY: 20 + (screen.y - top) * scale,
    previewWidth: screen.width * scale,
    previewHeight: screen.height * scale,
  }));
}

// The stream state an answer of the screen stream commands reports; `null` otherwise.
export function parseStream(value: JsonValue): StreamStatus | null {
  const stream = isJsonObject(value) ? value["stream"] : undefined;
  if (!isJsonObject(stream) || !isStateName(stream["state"])) {
    return null;
  }
  const { mode, screen, error } = stream;
  return {
    state: stream["state"],
    mode: isMode(mode) ? mode : null,
    screen: typeof screen === "string" ? screen : null,
    error: typeof error === "string" ? error : null,
  };
}

export function withScreens(state: ScreenPanelState, listing: Listing | null): ScreenPanelState {
  if (listing === null) {
    return { ...state, availability: { kind: "unavailable" } };
  }
  const { screens } = listing;
  const kept = screens.some((screen) => screen.name === state.choice.screen);
  const screen = kept ? state.choice.screen : (screens[0]?.name ?? null);
  const primary = screens.find((candidate) => candidate.primary) ?? screens[0];
  const placed =
    state.choice.x === null && primary !== undefined
      ? { x: primary.x + primary.width, y: primary.y }
      : { x: state.choice.x, y: state.choice.y };
  return {
    ...state,
    availability: { kind: "available", ...listing },
    choice: { ...state.choice, screen, ...placed },
  };
}

export function arrangementScreens(state: ScreenPanelState): ScreenOutput[] {
  if (state.availability.kind !== "available") {
    return [];
  }
  const screens = [...state.availability.screens];
  if (
    state.choice.mode === "extend" &&
    state.availability.extensionOutput !== null &&
    state.choice.x !== null &&
    state.choice.y !== null
  ) {
    screens.push({
      name: state.availability.extensionOutput,
      ...PIXELS[state.choice.size],
      x: state.choice.x,
      y: state.choice.y,
      primary: false,
      virtual: true,
    });
  }
  return screens;
}

export function moveExtension(state: ScreenPanelState, x: number, y: number): ScreenPanelState {
  if (!describeScreen(state, true).canChoose || state.choice.mode !== "extend") {
    return state;
  }
  const size = PIXELS[state.choice.size];
  const screens = state.availability.kind === "available" ? state.availability.screens : [];
  const xGuides = screens.flatMap((screen) => [
    screen.x,
    screen.x + screen.width,
    screen.x - size.width,
    screen.x + screen.width - size.width,
  ]);
  const yGuides = screens.flatMap((screen) => [
    screen.y,
    screen.y + screen.height,
    screen.y - size.height,
    screen.y + screen.height - size.height,
  ]);
  const snap = (value: number, guides: readonly number[]): number => {
    const nearest = guides.reduce<number | null>(
      (best, guide) =>
        best === null || Math.abs(guide - value) < Math.abs(best - value) ? guide : best,
      null,
    );
    return nearest !== null && Math.abs(nearest - value) <= 48 ? nearest : Math.round(value);
  };
  return choose(state, { x: snap(x, xGuides), y: snap(y, yGuides) });
}

export function choose(state: ScreenPanelState, choice: Partial<ScreenChoice>): ScreenPanelState {
  // Nothing changes while a stream runs, fails or a request is in flight.
  if (!describeScreen(state, true).canChoose) {
    return state;
  }
  // A changed choice needs a new consent: the step describes exactly what will be shown.
  return { ...state, choice: { ...state.choice, ...choice }, confirming: false, failure: null };
}

function active(stream: StreamStatus): boolean {
  return stream.state === "starting" || stream.state === "running" || stream.state === "stopping";
}

// Whether the panel follows the stream (asks for its state) until it is idle or failed again.
export function follows(state: ScreenPanelState): boolean {
  return active(state.stream);
}

export interface ScreenDescription {
  visible: boolean;
  canChoose: boolean;
  // The extension menu (refresh rate, arrangement) is shown for "extend".
  extending: boolean;
  // A free video output is enabled, so the desktop can be extended.
  canExtend: boolean;
  needsScreen: boolean;
  canAsk: boolean;
  confirming: boolean;
  consentText: string;
  canStop: boolean;
  stopLabel: string;
  statusText: string;
  errorText: string | null;
}

const STATUS_TEXT: Record<StreamStateName, string> = {
  idle: "Not streaming.",
  starting: "Starting the stream…",
  running: "Streaming to the TV.",
  stopping: "Stopping the stream…",
  failed: "The stream stopped.",
};

function consentText(choice: ScreenChoice): string {
  const { width, height } = PIXELS[choice.size];
  const what =
    choice.mode === "duplicate"
      ? `Everything on the screen ${choice.screen ?? ""} will be visible on the TV.`
      : `The TV becomes a second screen at desktop coordinates ${choice.x ?? 0}, ${choice.y ?? 0} ` +
        `(${width}×${height}, ${choice.refresh} Hz) until the stream stops; what you move onto it ` +
        "is visible on the TV, and your main screen is restored afterwards.";
  return (
    `${what} The stream is sent at ${width}×${height}, ${choice.fps} frames per second, only on ` +
    "your home network, with a private address. It stops after one hour, on Stop, or when you quit."
  );
}

// The control layer's message as a sentence: capital first letter, final full stop.
function sentence(message: string | null): string | null {
  if (message === null || message === "") {
    return null;
  }
  const capital = message.charAt(0).toUpperCase() + message.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

export function describeScreen(state: ScreenPanelState, tvSelected: boolean): ScreenDescription {
  const available = state.availability.kind === "available" ? state.availability : null;
  const screens = available?.screens ?? [];
  const canExtend = (available?.extensionOutput ?? null) !== null;
  const running = active(state.stream);
  const extending = state.choice.mode === "extend";
  const needsScreen = state.choice.mode === "duplicate" && state.choice.screen === null;
  const blocked = needsScreen || (extending && !canExtend);
  const canChoose = !running && !state.pending && state.stream.state !== "failed";
  return {
    visible: state.availability.kind === "available",
    canChoose,
    extending,
    canExtend,
    needsScreen,
    canAsk: canChoose && tvSelected && !blocked && !state.confirming,
    confirming: canChoose && tvSelected && state.confirming,
    consentText: consentText(state.choice),
    canStop:
      !state.pending &&
      (state.stream.state === "starting" ||
        state.stream.state === "running" ||
        state.stream.state === "failed"),
    stopLabel: state.stream.state === "failed" ? "Dismiss" : "Stop",
    statusText:
      !tvSelected && !running
        ? "Choose a TV first."
        : screens.length === 0 && state.choice.mode === "duplicate" && !running
          ? "No screen can be duplicated: extend instead."
          : extending && !canExtend && !running
            ? "Extending needs a free video output enabled first (see the test procedure)."
            : STATUS_TEXT[state.stream.state],
    errorText: sentence(state.failure?.message ?? state.stream.error),
  };
}

export interface ScreenInvocation {
  command: "bridge_start_screen_stream";
  args: {
    deviceId: string;
    mode: ScreenMode;
    screen: string | null;
    width: number;
    height: number;
    fps: number;
    consent: true;
    refresh: RefreshRate;
    position: Placement;
    x?: number;
    y?: number;
  };
}

// Asks for consent for the current choice; nothing is sent.
export function askConsent(state: ScreenPanelState, tvSelected: boolean): ScreenPanelState {
  return describeScreen(state, tvSelected).canAsk
    ? { ...state, confirming: true, failure: null }
    : state;
}

export function cancelConsent(state: ScreenPanelState): ScreenPanelState {
  return { ...state, confirming: false };
}

// The start the person consented to, or `null` when the consent step is not the current one.
export function consentedStart(
  state: ScreenPanelState,
  deviceId: string | null,
): { state: ScreenPanelState; request: ScreenInvocation | null } {
  if (deviceId === null || !describeScreen(state, true).confirming) {
    return { state, request: null };
  }
  const { width, height } = PIXELS[state.choice.size];
  return {
    state: { ...state, confirming: false, pending: true, failure: null },
    request: {
      command: "bridge_start_screen_stream",
      args: {
        deviceId,
        mode: state.choice.mode,
        screen: state.choice.mode === "duplicate" ? state.choice.screen : null,
        width,
        height,
        fps: state.choice.fps,
        consent: true,
        refresh: state.choice.refresh,
        position: state.choice.position,
        ...(state.choice.mode === "extend" && state.choice.x !== null && state.choice.y !== null
          ? { x: state.choice.x, y: state.choice.y }
          : {}),
      },
    },
  };
}

export function startStop(state: ScreenPanelState): { state: ScreenPanelState; send: boolean } {
  if (!describeScreen(state, true).canStop) {
    return { state, send: false };
  }
  return { state: { ...state, pending: true, failure: null }, send: true };
}

// The answer of a start, stop or state request: the reported stream, or the failure.
export function finishScreen(
  state: ScreenPanelState,
  answer: { ok: true; result: JsonValue } | { ok: false; failure: BridgeFailure },
): ScreenPanelState {
  if (!answer.ok) {
    return { ...state, pending: false, failure: answer.failure };
  }
  const stream = parseStream(answer.result);
  if (stream === null) {
    return {
      ...state,
      pending: false,
      failure: { code: "unexpected", message: "The stream state could not be read." },
    };
  }
  return { ...state, pending: false, stream };
}

// A state the panel asked for while following the stream: only the reported stream changes, so
// a late answer never ends a start or stop still in flight. An unreadable answer changes nothing.
export function finishPoll(state: ScreenPanelState, result: JsonValue): ScreenPanelState {
  const stream = parseStream(result);
  return stream === null ? state : { ...state, stream };
}
