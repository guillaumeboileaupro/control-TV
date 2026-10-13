// The TV's own remote channel (the Google TV remote protocol), as plain functions (no DOM, no
// Tauri, no I/O).
//
// A TV whose Cast volume is fixed is heard at the volume the TV itself sets: this panel reaches
// that volume, mute, power and navigation through the TV's own remote channel. Rules:
// - only a TV that advertises the channel gets the panel; a Cast group never does;
// - pairing is explicit: the TV shows a code, the person types it here;
// - a key is sent once per press, never resent; a press whose answer is lost is said to be
//   uncertain, not repeated;
// - what the panel shows of the TV (volume, mute, power) is the TV's own last report, never a
//   value deduced from the key that was pressed;
// - one request at a time, shared with every other request of the app (see `idle`).
// `main.ts` performs the I/O and draws the result; `npm test` covers every rule below.

import { isJsonObject, type JsonObject, type JsonValue } from "./json.ts";
import { describeFailure, idle, type AppState, type BridgeFailure } from "./model.ts";

export type RemoteKeyName =
  | "volume_down"
  | "volume_up"
  | "mute"
  | "power"
  | "up"
  | "down"
  | "left"
  | "right"
  | "ok"
  | "back"
  | "home"
  | "delete"
  | "enter";

export const KEY_LABELS: Record<RemoteKeyName, string> = {
  volume_down: "Volume down",
  volume_up: "Volume up",
  mute: "Mute",
  power: "Power",
  up: "Up",
  down: "Down",
  left: "Left",
  right: "Right",
  ok: "OK",
  back: "Back",
  home: "Home",
  delete: "Backspace",
  enter: "Enter",
};

// The TV's own report, as the control layer relays it. `null` means the TV did not say.
export interface AudibleVolume {
  level: number;
  max: number;
  muted: boolean;
}

export interface RemoteReport {
  isOn: boolean | null;
  // The package name of the app in front on the TV, as the TV reported it.
  currentApp: string | null;
  volume: AudibleVolume | null;
}

export type RemoteAction =
  | { kind: "status" }
  | { kind: "key"; key: RemoteKeyName }
  | { kind: "show_code" }
  | { kind: "pair"; code: string }
  // Text for the TV's active field. It may be a password: it is never put in an outcome.
  | { kind: "text"; text: string }
  | { kind: "list_apps" }
  | { kind: "launch_app"; package: string; label: string };

// `not_tried`: not tried yet. `needed`: the TV answered that it must be paired first.
// `code_shown`: the TV was asked to show a code. `paired`: the TV accepted this app.
export type PairingStep = "not_tried" | "needed" | "code_shown" | "paired";

export interface RemoteOutcome {
  // `done`: the TV reported the expected result. `sent`: sent, effect not reported by the TV.
  // `failed`: not sent, refused, or its delivery is uncertain.
  tone: "done" | "sent" | "failed";
  text: string;
  // The raw code and message, for a collapsed diagnostic disclosure only.
  technical: string | null;
}

// An app installed on the TV, as the TV listed it: its package, and a display name control-TV
// knows for it or null.
export interface TvApp {
  package: string;
  name: string | null;
}

export interface RemoteView {
  report: RemoteReport | null;
  pending: { requestId: number; action: RemoteAction } | null;
  pairing: PairingStep;
  outcome: RemoteOutcome | null;
  // The TV's apps from its last listing; null until listed.
  apps: TvApp[] | null;
  // What the person typed to narrow the app list.
  appFilter: string;
}

export const REMOTE_IDLE: RemoteView = {
  report: null,
  pending: null,
  pairing: "not_tried",
  outcome: null,
  apps: null,
  appFilter: "",
};

export function appLabel(app: TvApp): string {
  return app.name ?? app.package;
}

export interface RemoteRequest {
  requestId: number;
  deviceId: string;
  action: RemoteAction;
}

export type RemoteAnswer = { ok: true; result: JsonValue } | { ok: false; failure: BridgeFailure };

const PAIRING_CODE = /^[0-9a-fA-F]{6}$/;
export const MAX_TEXT_LENGTH = 256;

// What the control layer accepts: 1 to 256 characters, none of them a control character.
function textAllowed(text: string): boolean {
  return (
    text.length > 0 &&
    text.length <= MAX_TEXT_LENGTH &&
    ![...text].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127)
  );
}

// The application command and its arguments, as the Tauri boundary expects them.
export function remoteInvocation(request: RemoteRequest): {
  command: string;
  args: JsonObject;
} {
  const deviceId = request.deviceId;
  const action = request.action;
  switch (action.kind) {
    case "status":
      return { command: "bridge_get_remote_status", args: { deviceId } };
    case "key":
      return { command: "bridge_press_remote_key", args: { deviceId, key: action.key } };
    case "show_code":
      return { command: "bridge_start_remote_pairing", args: { deviceId } };
    case "pair":
      return { command: "bridge_finish_remote_pairing", args: { deviceId, code: action.code } };
    case "text":
      return { command: "bridge_type_remote_text", args: { deviceId, text: action.text } };
    case "list_apps":
      return { command: "bridge_list_tv_apps", args: { deviceId } };
    case "launch_app":
      return { command: "bridge_launch_tv_app", args: { deviceId, package: action.package } };
  }
}

// Only a TV that advertises its own remote channel; never a group, never an undetermined one.
export function remoteAvailable(state: AppState): boolean {
  const selected = state.selected;
  return selected !== null && selected.kind !== "group" && selected.androidTvRemote === true;
}

// The TV's apps go through its debugging, a separate service: every TV (a Cast video device), not
// a group or a speaker, whatever discovery saw of its remote channel.
export function appsAvailable(state: AppState): boolean {
  return state.selected !== null && state.selected.kind === "cast";
}

function isAppAction(action: RemoteAction): boolean {
  return action.kind === "list_apps" || action.kind === "launch_app";
}

function keysAllowed(state: AppState): boolean {
  return state.remote.pairing !== "needed" && state.remote.pairing !== "code_shown";
}

// Starts one request on the remote channel, or nothing: when the TV has no channel, another
// request is in flight, a key is pressed while pairing is still needed, or a code is malformed
// (then the panel says so and nothing is sent).
export function startRemote(
  state: AppState,
  action: RemoteAction,
): { state: AppState; request: RemoteRequest | null } {
  const selected = state.selected;
  const available = isAppAction(action) ? appsAvailable(state) : remoteAvailable(state);
  if (selected === null || !available || !idle(state)) {
    return { state, request: null };
  }
  // Apps go through the TV's debugging, not its remote: a pairing step blocks neither.
  const needsRemote =
    action.kind !== "show_code" &&
    action.kind !== "pair" &&
    action.kind !== "list_apps" &&
    action.kind !== "launch_app";
  if (needsRemote && !keysAllowed(state)) {
    return { state, request: null };
  }
  if (action.kind === "text" && !textAllowed(action.text)) {
    const outcome: RemoteOutcome = {
      tone: "failed",
      text: `Type 1 to ${MAX_TEXT_LENGTH} characters, on one line.`,
      technical: null,
    };
    return { state: { ...state, remote: { ...state.remote, outcome } }, request: null };
  }
  let sent: RemoteAction = action;
  if (action.kind === "pair") {
    const code = action.code.trim();
    if (state.remote.pairing !== "code_shown") {
      return { state, request: null };
    }
    if (!PAIRING_CODE.test(code)) {
      const outcome: RemoteOutcome = {
        tone: "failed",
        text: "Type the 6 letters and digits the TV shows.",
        technical: null,
      };
      return { state: { ...state, remote: { ...state.remote, outcome } }, request: null };
    }
    sent = { kind: "pair", code: code.toUpperCase() };
  }
  const requestId = state.nextRequestId;
  return {
    state: {
      ...state,
      remote: { ...state.remote, pending: { requestId, action: sent }, outcome: null },
      nextRequestId: requestId + 1,
    },
    request: { requestId, deviceId: selected.id, action: sent },
  };
}

// A report as the control layer sends it; anything malformed is "the TV did not say".
export function toReport(value: JsonValue | undefined): RemoteReport | null {
  if (!isJsonObject(value)) {
    return null;
  }
  const isOn = typeof value["isOn"] === "boolean" ? value["isOn"] : null;
  const app = value["currentApp"];
  const currentApp = typeof app === "string" && app !== "" ? app : null;
  const raw = value["volume"];
  let volume: AudibleVolume | null = null;
  if (
    isJsonObject(raw) &&
    typeof raw["level"] === "number" &&
    typeof raw["max"] === "number" &&
    typeof raw["muted"] === "boolean" &&
    raw["max"] > 0
  ) {
    volume = { level: raw["level"], max: raw["max"], muted: raw["muted"] };
  }
  return { isOn, currentApp, volume };
}

function failureOutcome(action: RemoteAction, failure: BridgeFailure): RemoteOutcome {
  const technical = `${failure.code}: ${failure.message}`;
  if (action.kind === "list_apps" || action.kind === "launch_app") {
    if (failure.code === "unsupported_operation") {
      return {
        tone: "failed",
        text: "To list and open the TV's apps, turn on USB debugging on the TV: Settings › System › About, select “Android TV OS build” 7 times, then Settings › System › Developer options › USB debugging. Then list the apps again and allow control-TV when the TV asks. A TV that only offers “Wireless debugging” (with a pairing code) can't list its apps here yet.",
        technical,
      };
    }
    if (failure.code === "pairing_required") {
      return {
        tone: "failed",
        text: "Allow control-TV on the TV when it asks about debugging, then try again.",
        technical,
      };
    }
  }
  switch (failure.code) {
    case "pairing_required":
      return {
        tone: "failed",
        text: "Pair control-TV with this TV first: the TV will show a code to type here.",
        technical,
      };
    case "command_rejected":
      if (action.kind === "pair") {
        return {
          tone: "failed",
          text: "The TV refused that code. Show a new code and try again.",
          technical,
        };
      }
      break;
    case "unsupported_operation":
      return {
        tone: "failed",
        text: "This version of control-TV can't use the TV's own remote.",
        technical,
      };
    // A lost connection may have come after the request was sent: for anything that acts on
    // the TV, the outcome is uncertain, never "try again".
    case "device_unavailable":
    case "timeout":
    case "bridge_timeout":
    case "bridge_transport":
      if (action.kind === "launch_app") {
        return {
          tone: "failed",
          text: `${action.label} may or may not have opened. It was not sent again: check the TV.`,
          technical,
        };
      }
      if (action.kind === "text") {
        return {
          tone: "failed",
          text: "The text may or may not have reached the TV. It was not sent again: check the TV.",
          technical,
        };
      }
      if (action.kind === "key") {
        // The key may have reached the TV: it was not sent again, and the panel says so.
        return {
          tone: "failed",
          text: `${KEY_LABELS[action.key]} may or may not have reached the TV. It was not sent again: check the TV.`,
          technical,
        };
      }
      break;
    default:
      break;
  }
  const described = describeFailure(failure, "reach the TV's remote");
  return { tone: "failed", text: `${described.title}. ${described.hint}`, technical };
}

function keyOutcome(key: RemoteKeyName, result: JsonObject): RemoteOutcome {
  const label = KEY_LABELS[key];
  const detail = typeof result["detail"] === "string" ? result["detail"] : "";
  switch (result["confirmation"]) {
    case "confirmed":
      return { tone: "done", text: `${label}: ${detail}.`, technical: null };
    case "unconfirmed":
      // Some TVs never report their volume on this channel (their sound is set by the TV
      // itself): only the person in front of the TV can tell whether it changed.
      if (toReport(result["after"])?.volume == null) {
        return {
          tone: "sent",
          text: `${label} was sent. This TV doesn't report its volume here: check the TV.`,
          technical: detail === "" ? null : detail,
        };
      }
      return {
        tone: "sent",
        text: `${label} was sent, but the TV didn't report the change.`,
        technical: detail === "" ? null : detail,
      };
    default:
      return {
        tone: "sent",
        text: `${label} was sent. The TV doesn't report what this key does: check the TV.`,
        technical: null,
      };
  }
}

// Applies the answer of the request that is still pending; every other answer is ignored.
export function finishRemote(state: AppState, requestId: number, answer: RemoteAnswer): AppState {
  const pending = state.remote.pending;
  if (pending === null || pending.requestId !== requestId) {
    return state;
  }
  const action = pending.action;
  const remote: RemoteView = { ...state.remote, pending: null };
  if (!answer.ok) {
    const failure = answer.failure;
    let pairing = remote.pairing;
    if (action.kind === "list_apps" || action.kind === "launch_app") {
      // The apps' own "allow control-TV on the TV" is not the remote's pairing.
      return { ...state, remote: { ...remote, outcome: failureOutcome(action, failure) } };
    }
    if (failure.code === "pairing_required") {
      pairing = "needed";
    } else if (action.kind === "pair" && failure.code === "command_rejected") {
      pairing = "needed"; // the TV dropped that pairing attempt: a new code is needed
    }
    return { ...state, remote: { ...remote, pairing, outcome: failureOutcome(action, failure) } };
  }
  const result = isJsonObject(answer.result) ? answer.result : {};
  switch (action.kind) {
    case "status":
      return {
        ...state,
        remote: { ...remote, pairing: "paired", report: toReport(result["remote"]), outcome: null },
      };
    case "key": {
      const pressed = isJsonObject(result["result"]) ? result["result"] : {};
      return {
        ...state,
        remote: {
          ...remote,
          pairing: "paired",
          report: toReport(pressed["after"]),
          outcome: keyOutcome(action.key, pressed),
        },
      };
    }
    case "show_code":
      // The pairing area says what to do next; no second message repeats it.
      return { ...state, remote: { ...remote, pairing: "code_shown", outcome: null } };
    case "pair":
      return {
        ...state,
        remote: {
          ...remote,
          pairing: "paired",
          outcome: { tone: "done", text: "Paired with this TV.", technical: null },
        },
      };
    case "text": {
      const typed = isJsonObject(result["result"]) ? result["result"] : {};
      const fieldReported = typed["fieldReported"] === true;
      return {
        ...state,
        remote: {
          ...remote,
          pairing: "paired",
          report: toReport(typed["observed"]) ?? remote.report,
          outcome: {
            tone: "sent",
            text: fieldReported
              ? "Text sent to the TV's text field: check the TV."
              : "Text sent, but the TV didn't report an open text field. Open a search or text field on the TV, then send again.",
            technical: null,
          },
        },
      };
    }
    case "list_apps": {
      const apps = toApps(result["apps"]);
      return {
        ...state,
        remote: {
          ...remote,
          apps,
          outcome:
            apps.length === 0
              ? { tone: "sent", text: "The TV listed no app it can open.", technical: null }
              : null,
        },
      };
    }
    case "launch_app": {
      const launched = isJsonObject(result["result"]) ? result["result"] : {};
      const label = action.label;
      let outcome: RemoteOutcome;
      switch (launched["confirmation"]) {
        case "confirmed":
          outcome = { tone: "done", text: `${label} is open on the TV.`, technical: null };
          break;
        case "unconfirmed":
          outcome = {
            tone: "sent",
            text: `${label} was sent, but the TV reports another app in front.`,
            technical: typeof launched["detail"] === "string" ? launched["detail"] : null,
          };
          break;
        default:
          outcome = { tone: "sent", text: `${label} was sent: check the TV.`, technical: null };
      }
      return {
        ...state,
        remote: {
          ...remote,
          report: withFrontApp(remote.report, launched["frontApp"]),
          outcome,
        },
      };
    }
  }
}

// The report with the app the TV said it has in front, when it said one.
function withFrontApp(
  report: RemoteReport | null,
  front: JsonValue | undefined,
): RemoteReport | null {
  if (typeof front !== "string" || front === "") {
    return report;
  }
  return { isOn: report?.isOn ?? null, volume: report?.volume ?? null, currentApp: front };
}

// An app list as the control layer sends it; malformed entries are left out, never guessed.
export function toApps(value: JsonValue | undefined): TvApp[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const apps: TvApp[] = [];
  for (const entry of value) {
    if (isJsonObject(entry) && typeof entry["package"] === "string" && entry["package"] !== "") {
      const name = typeof entry["name"] === "string" && entry["name"] !== "" ? entry["name"] : null;
      apps.push({ package: entry["package"], name });
    }
  }
  return apps;
}

// Narrows the app list as the person types; sends nothing.
export function setAppFilter(state: AppState, filter: string): AppState {
  return state.remote.appFilter === filter
    ? state
    : { ...state, remote: { ...state.remote, appFilter: filter } };
}

export interface RemoteDescription {
  visible: boolean;
  // The remote's own keys, pairing and text: only for a TV that advertises its remote channel.
  remoteVisible: boolean;
  // The TV's report can be read: nothing in flight and no pairing step in the way.
  readEnabled: boolean;
  // Keys can be pressed: nothing in flight and no pairing step in the way.
  keysEnabled: boolean;
  // `null`: no pairing step to show. Otherwise what the pairing area offers.
  pairing: { codeEntry: boolean; text: string } | null;
  // What the TV last reported, worded; empty when it reported nothing yet.
  readout: string;
  // The request in flight, worded, or null.
  progress: string | null;
  pendingKey: RemoteKeyName | null;
  // Text for the TV's field is on its way.
  sendingText: boolean;
  // The TV's apps matching the filter, sorted by label; null until the TV listed them.
  apps: TvApp[] | null;
  appsEnabled: boolean;
  listingApps: boolean;
  launchEnabled: boolean;
  pendingApp: string | null;
  busy: boolean;
  outcome: RemoteOutcome | null;
}

const HIDDEN: RemoteDescription = {
  visible: false,
  remoteVisible: false,
  readEnabled: false,
  keysEnabled: false,
  pairing: null,
  readout: "",
  progress: null,
  pendingKey: null,
  sendingText: false,
  apps: null,
  appsEnabled: false,
  listingApps: false,
  launchEnabled: false,
  pendingApp: null,
  busy: false,
  outcome: null,
};

function progressOf(action: RemoteAction): string {
  switch (action.kind) {
    case "status":
      return "Reading the TV's state…";
    case "key":
      return `Sending ${KEY_LABELS[action.key]}…`;
    case "show_code":
      return "Asking the TV to show a code…";
    case "pair":
      return "Pairing…";
    case "text":
      return "Sending text…";
    case "list_apps":
      return "Listing the TV's apps… If the TV asks, allow control-TV.";
    case "launch_app":
      return `Opening ${action.label}…`;
  }
}

function filteredApps(apps: TvApp[] | null, filter: string): TvApp[] | null {
  if (apps === null) {
    return null;
  }
  const needle = filter.trim().toLowerCase();
  return apps
    .filter(
      (app) =>
        needle === "" ||
        appLabel(app).toLowerCase().includes(needle) ||
        app.package.toLowerCase().includes(needle),
    )
    .sort((a, b) => appLabel(a).localeCompare(appLabel(b)));
}

export function describeReport(report: RemoteReport | null): string {
  if (report === null) {
    return "";
  }
  const parts: string[] = [];
  if (report.volume !== null) {
    const { level, max, muted } = report.volume;
    parts.push(`TV volume ${level} of ${max}`);
    if (muted) {
      parts.push("Muted");
    }
  }
  if (report.isOn === false) {
    parts.push("The TV reports it is off");
  }
  if (report.currentApp !== null) {
    parts.push(`TV app: ${report.currentApp}`);
  }
  return parts.join(" · ");
}

export function describeRemote(state: AppState): RemoteDescription {
  if (!remoteAvailable(state) && !appsAvailable(state)) {
    return HIDDEN;
  }
  const remote = state.remote;
  const busy = !idle(state);
  let pairing: RemoteDescription["pairing"] = null;
  if (remote.pairing === "needed") {
    pairing = { codeEntry: false, text: "Pair control-TV with this TV to use its own remote." };
  } else if (remote.pairing === "code_shown") {
    pairing = { codeEntry: true, text: "Type the code the TV shows." };
  }
  const pending = remote.pending;
  return {
    visible: true,
    remoteVisible: remoteAvailable(state),
    readEnabled: !busy && keysAllowed(state),
    keysEnabled: !busy && keysAllowed(state),
    pairing,
    readout: describeReport(remote.report),
    progress: pending === null ? null : progressOf(pending.action),
    pendingKey: pending !== null && pending.action.kind === "key" ? pending.action.key : null,
    sendingText: pending !== null && pending.action.kind === "text",
    apps: filteredApps(remote.apps, remote.appFilter),
    appsEnabled: !busy,
    listingApps: pending !== null && pending.action.kind === "list_apps",
    launchEnabled: !busy,
    pendingApp:
      pending !== null && pending.action.kind === "launch_app" ? pending.action.package : null,
    busy,
    outcome: remote.outcome,
  };
}
