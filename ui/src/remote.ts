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
import { describeFailure, idle, type AppState } from "./model.ts";
import type {
  AudibleVolume,
  BridgeFailure,
  RemoteAction,
  RemoteKeyName,
  RemoteOutcome,
  RemoteReport,
  RemoteView,
  TvApp,
} from "./view-state.ts";

export {
  REMOTE_IDLE,
  type AudibleVolume,
  type PairingStep,
  type RemoteAction,
  type RemoteKeyName,
  type RemoteOutcome,
  type RemoteReport,
  type RemoteView,
  type TvApp,
} from "./view-state.ts";

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
  digit_0: "0",
  digit_1: "1",
  digit_2: "2",
  digit_3: "3",
  digit_4: "4",
  digit_5: "5",
  digit_6: "6",
  digit_7: "7",
  digit_8: "8",
  digit_9: "9",
  info: "Info",
  guide: "Guide",
  input: "Input",
  subtitles: "Subtitles",
  audio_track: "Audio track",
  settings: "Settings",
  channel_up: "Channel up",
  channel_down: "Channel down",
  red: "Red",
  green: "Green",
  yellow: "Yellow",
  blue: "Blue",
  play_pause: "Play/Pause",
  stop: "Stop",
  rewind: "Rewind",
  fast_forward: "Fast forward",
  previous: "Previous",
  next: "Next",
  record: "Record",
  assistant: "Google Assistant",
};

export function appLabel(app: TvApp): string {
  return app.name ?? app.package;
}

export interface AppPresentation {
  readonly label: string;
  readonly title: string;
  readonly accessibleLabel: string;
  readonly technicalLabel: boolean;
}

// Keep the package identity available even when a friendly name is shown. The package is the
// authoritative key for logos and launch requests; the display name is never used to guess either.
export function appPresentation(app: TvApp): AppPresentation {
  const label = appLabel(app);
  const identity = `Android package: ${app.package}`;
  return {
    label,
    title: app.name === null ? identity : `${app.name}\n${identity}`,
    accessibleLabel: `Open ${label} on the TV. ${identity}`,
    technicalLabel: app.name === null,
  };
}

export interface RemoteRequest {
  requestId: number;
  deviceId: string;
  action: RemoteAction;
}

export type RemoteAnswer = { ok: true; result: JsonValue } | { ok: false; failure: BridgeFailure };

const PAIRING_CODE = /^[0-9a-fA-F]{6}$/;
const ADB_PAIRING_CODE = /^[0-9]{6}$/;
const ADB_PAIRING_ENDPOINT = /^(?:\[[0-9a-fA-F:]+\]|(?:[0-9]{1,3}\.){3}[0-9]{1,3}):[0-9]{1,5}$/;
export const MAX_TEXT_LENGTH = 256;

// What the control layer accepts: 1 to 256 characters, none of them a control character.
// Characters are counted as the control layer counts them (Python's `len`, Unicode code
// points), not as UTF-16 units: an emoji is one character, not two.
function textAllowed(text: string): boolean {
  const characters = [...text];
  return (
    characters.length > 0 &&
    characters.length <= MAX_TEXT_LENGTH &&
    !characters.some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127)
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
    case "pair_apps":
      return {
        command: "bridge_pair_tv_apps",
        args: { deviceId, pairingEndpoint: action.pairingEndpoint, code: action.code },
      };
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
  return state.tvAppsAvailable && state.selected !== null && state.selected.kind === "cast";
}

function isAppAction(action: RemoteAction): boolean {
  return action.kind === "list_apps" || action.kind === "pair_apps" || action.kind === "launch_app";
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
    action.kind !== "pair_apps" &&
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
  if (action.kind === "pair_apps") {
    const pairingEndpoint = action.pairingEndpoint.trim();
    const code = action.code.trim();
    if (
      (pairingEndpoint !== "" && !ADB_PAIRING_ENDPOINT.test(pairingEndpoint)) ||
      !ADB_PAIRING_CODE.test(code)
    ) {
      const outcome: RemoteOutcome = {
        tone: "failed",
        text: "Type the TV's 6-digit code. Its pairing address is optional unless automatic discovery fails.",
        technical: null,
      };
      return { state: { ...state, remote: { ...state.remote, outcome } }, request: null };
    }
    sent = { kind: "pair_apps", pairingEndpoint, code };
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
  if (isAppAction(action)) {
    if (failure.code === "debugging_off") {
      return {
        tone: "failed",
        text: "No debugging service matches this TV. In Developer options, turn on Wireless debugging (or classic network debugging), keep the TV on the same network, then refresh the app list.",
        technical,
      };
    }
    if (failure.code === "pairing_required") {
      return {
        tone: "failed",
        text: "Enter the temporary code shown by Wireless debugging. The pairing address is discovered automatically when the TV advertises it.",
        technical,
      };
    }
    if (action.kind === "pair_apps" && failure.code === "command_rejected") {
      return {
        tone: "failed",
        text: "That Wireless debugging code was refused or expired. Reopen the pairing-code screen and try the new code.",
        technical,
      };
    }
    // Not the TV's debugging: a Cast group, or a build of control-TV without the app list.
    if (failure.code === "unsupported_operation") {
      return {
        tone: "failed",
        text: "control-TV can't list or open the apps of this device.",
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
      if (key !== "power" && toReport(result["after"])?.volume == null) {
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
    if (isAppAction(action)) {
      // The apps' own "allow control-TV on the TV" is not the remote's pairing.
      return {
        ...state,
        remote: {
          ...remote,
          appsPairingNeeded: failure.code === "pairing_required" || remote.appsPairingNeeded,
          outcome: failureOutcome(action, failure),
        },
      };
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
          appsPairingNeeded: false,
          outcome:
            apps.length === 0
              ? { tone: "sent", text: "The TV listed no app it can open.", technical: null }
              : null,
        },
      };
    }
    case "pair_apps":
      return {
        ...state,
        remote: {
          ...remote,
          appsPairingNeeded: false,
          outcome: {
            tone: "done",
            text: "Wireless debugging paired and connected.",
            technical: null,
          },
        },
      };
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
  appsPairingNeeded: boolean;
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
  appsPairingNeeded: false,
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
    case "pair_apps":
      return "Pairing Wireless debugging…";
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
    appsEnabled: appsAvailable(state) && !busy,
    listingApps: pending !== null && pending.action.kind === "list_apps",
    launchEnabled: appsAvailable(state) && !busy,
    pendingApp:
      pending !== null && pending.action.kind === "launch_app" ? pending.action.package : null,
    appsPairingNeeded: remote.appsPairingNeeded,
    busy,
    outcome: remote.outcome,
  };
}

// The tile's letter: the first letter or digit of the app's label.
export function appInitial(label: string): string {
  const first = /[\p{L}\p{N}]/u.exec(label);
  return first === null ? "?" : first[0].toUpperCase();
}

// A stable colour per app (the same package always gets the same hue), so a tile is easy to
// find again; the letter and the name carry the meaning, never the colour alone.
export function appHue(packageName: string): number {
  let hash = 0;
  for (const char of packageName) {
    hash = (hash * 31 + char.charCodeAt(0)) % 360;
  }
  return hash;
}
