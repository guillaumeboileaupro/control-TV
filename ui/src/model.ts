import type { CommandView } from "./playback.ts";

// Application state and presentation logic for the manual UI, as plain functions.
//
// Nothing here touches the DOM, Tauri or the network: `main.ts` performs the I/O and
// feeds the results in, so every rule below (selection by stable id, stale-response
// handling, how a partial status and a failure are worded) is testable on its own with
// `npm test`. Nothing here contains Cast/control logic either: the shared Python control
// layer decides what a device's status is; this module only decides how to show it.

export interface Device {
  id: string;
  friendlyName: string;
  host: string;
  port: number;
  kind: string;
  modelName: string | null;
}

export interface ReceiverStatus {
  appId: string | null;
  appName: string | null;
  volumeLevel: number | null;
  muted: boolean | null;
  standby: boolean | null;
}

export interface MediaStatus {
  playbackState: string;
  contentId: string | null;
  contentType: string | null;
  title: string | null;
  positionSeconds: number | null;
  durationSeconds: number | null;
  supportsSeek: boolean | null;
}

// `null` means the device did not report it - unknown, never "zero" or "off".
export interface DeviceStatus {
  deviceId: string;
  connection: string;
  observedAt: string;
  receiver: ReceiverStatus | null;
  media: MediaStatus | null;
}

// What a rejected Tauri command carries: a stable `code` (a control-layer error code such
// as `device_unavailable`, or one of the shell's own `backend_unavailable`,
// `bridge_timeout`, `bridge_transport`) plus a human-readable `message`.
export interface BridgeFailure {
  code: string;
  message: string;
}

export type DiscoveryView =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done" }
  | { kind: "failed"; failure: BridgeFailure };

export type StatusView =
  | { kind: "none" }
  | { kind: "loading"; requestId: number }
  | { kind: "ready"; status: DeviceStatus }
  | { kind: "failed"; failure: BridgeFailure };

export interface AppState {
  devices: Device[];
  discovery: DiscoveryView;
  // The whole discovered device, not just its id, so the selected-device panel does not
  // depend on the list (which is emptied while a new discovery runs). Its `id` is the
  // stable identifier every status read is made with; `friendlyName` is display-only.
  selected: Device | null;
  status: StatusView;
  // The last playback command (see playback.ts): in flight, refused, or sent and whether the
  // TV then showed the result. Its answer never edits `status`; only a status the TV
  // reported (the one the answer carries) replaces it.
  command: CommandView;
  // A position the operator is moving the seek control to and has not sent yet. It is a
  // request being composed, never a position the TV reported.
  seekDraft: number | null;
  // A volume, in whole percent, the operator is moving the volume control to and has not sent
  // yet. Like the seek draft it is a request being composed, never a level the TV reported.
  volumeDraft: number | null;
  notice: string | null;
  nextRequestId: number;
}

export interface StatusRequest {
  requestId: number;
  deviceId: string;
}

export type StatusOutcome =
  { ok: true; status: DeviceStatus } | { ok: false; failure: BridgeFailure };

export interface DevicesMessageLine {
  text: string;
  // Read out by assistive technology but not drawn: the list appearing is the visual cue.
  announceOnly: boolean;
}

export interface DevicesMessage {
  lines: DevicesMessageLine[];
  failure: BridgeFailure | null;
}

// What the device picker says about the last discovery: a prompt before the first search, a
// failure, why the selection was dropped, that nothing was found, or how many devices were.
export function describeDevicesMessage(state: AppState): DevicesMessage {
  if (state.discovery.kind === "failed") {
    return { lines: [], failure: state.discovery.failure };
  }
  const lines: DevicesMessageLine[] = [];
  if (state.discovery.kind === "idle") {
    lines.push({ text: "Find your TV or Chromecast on this network.", announceOnly: false });
  }
  if (state.discovery.kind === "done") {
    if (state.notice !== null) {
      lines.push({ text: state.notice, announceOnly: false });
    }
    const count = state.devices.length;
    lines.push(
      count === 0
        ? {
            text: "No devices found. Make sure your TV or Chromecast is on and on the same network.",
            announceOnly: false,
          }
        : { text: `${count} ${count === 1 ? "device" : "devices"} found.`, announceOnly: true },
    );
  }
  return { lines, failure: null };
}

export function initialState(): AppState {
  return {
    devices: [],
    discovery: { kind: "idle" },
    selected: null,
    status: { kind: "none" },
    command: { kind: "idle" },
    seekDraft: null,
    volumeDraft: null,
    notice: null,
    nextRequestId: 1,
  };
}

// The backend is single-flight (one request at a time) and every request's timeout starts
// before it gets its turn, so anything queued behind a slow request eats into its own time
// budget - and rapid clicks would queue without bound. The UI therefore keeps at most one
// request in flight: while a discovery, a status read or a command (playback, volume or mute)
// runs, it offers none of discovery, device selection, refresh or another command.
export function idle(state: AppState): boolean {
  return (
    state.discovery.kind !== "running" &&
    state.status.kind !== "loading" &&
    state.command.kind !== "pending"
  );
}

export function canDiscover(state: AppState): boolean {
  return idle(state);
}

export function canSelect(state: AppState): boolean {
  return idle(state);
}

export function canRefresh(state: AppState): boolean {
  return state.selected !== null && idle(state);
}

export function startDiscovery(state: AppState): AppState {
  return {
    ...state,
    devices: [],
    discovery: { kind: "running" },
    command: { kind: "idle" },
    seekDraft: null,
    volumeDraft: null,
    notice: null,
  };
}

// The selection survives a new discovery only if the backend reports that same stable id
// again; otherwise it is dropped with an explanation instead of silently pointing at a
// device the backend no longer lists.
export function finishDiscovery(state: AppState, devices: Device[]): AppState {
  const next: AppState = { ...state, devices, discovery: { kind: "done" } };
  if (state.selected === null) {
    return next;
  }
  const selectedId = state.selected.id;
  const still = devices.find((device) => device.id === selectedId);
  if (still !== undefined) {
    return { ...next, selected: still };
  }
  return {
    ...next,
    selected: null,
    status: { kind: "none" },
    command: { kind: "idle" },
    seekDraft: null,
    volumeDraft: null,
    notice: "The selected device was not found by the latest discovery. Select a device again.",
  };
}

export function failDiscovery(state: AppState, failure: BridgeFailure): AppState {
  return { ...state, devices: [], discovery: { kind: "failed", failure } };
}

function beginStatusRead(
  state: AppState,
  selected: Device,
): { state: AppState; request: StatusRequest } {
  const requestId = state.nextRequestId;
  return {
    state: {
      ...state,
      selected,
      status: { kind: "loading", requestId },
      command: { kind: "idle" },
      seekDraft: null,
      volumeDraft: null,
      notice: null,
      nextRequestId: requestId + 1,
    },
    request: { requestId, deviceId: selected.id },
  };
}

// Selects by the device's stable id, never by its display name (two devices can share a
// friendly name). An id the backend did not just report is ignored, and so is any
// selection while a request is already in flight (see `canSelect`).
export function selectDevice(
  state: AppState,
  deviceId: string,
): { state: AppState; request: StatusRequest | null } {
  const device = state.devices.find((candidate) => candidate.id === deviceId);
  if (device === undefined || !canSelect(state)) {
    return { state, request: null };
  }
  return beginStatusRead(state, device);
}

export function refreshStatus(state: AppState): {
  state: AppState;
  request: StatusRequest | null;
} {
  if (!canRefresh(state) || state.selected === null) {
    return { state, request: null };
  }
  return beginStatusRead(state, state.selected);
}

// A response only applies if it answers the read that is still the current one. Only one
// read is ever in flight, so this is a safeguard rather than an everyday path: a late or
// duplicate answer must never overwrite what is shown for the current selection.
export function finishStatusRead(
  state: AppState,
  requestId: number,
  outcome: StatusOutcome,
): AppState {
  if (state.status.kind !== "loading" || state.status.requestId !== requestId) {
    return state;
  }
  if (!outcome.ok) {
    return { ...state, status: { kind: "failed", failure: outcome.failure } };
  }
  if (state.selected === null || outcome.status.deviceId !== state.selected.id) {
    return {
      ...state,
      status: {
        kind: "failed",
        failure: {
          code: "unexpected_response",
          message: "The backend answered with the status of a different device.",
        },
      },
    };
  }
  return { ...state, status: { kind: "ready", status: outcome.status } };
}

// Anything a rejected `invoke` can carry: the structured failure the shell sends, or a
// plain string/Error from Tauri itself (unknown command, argument decoding, ...).
export function toBridgeFailure(error: unknown): BridgeFailure {
  if (typeof error === "object" && error !== null) {
    const candidate = error as { code?: unknown; message?: unknown };
    if (typeof candidate.code === "string" && typeof candidate.message === "string") {
      return { code: candidate.code, message: candidate.message };
    }
    if (error instanceof Error) {
      return { code: "unexpected", message: error.message };
    }
  }
  return { code: "unexpected", message: String(error) };
}

export type FailureKind =
  | "backend_unavailable"
  | "backend_timeout"
  | "device_unavailable"
  | "device_unknown"
  | "device_timeout"
  | "error";

export interface FailureDescription {
  kind: FailureKind;
  // Plain language for the person using the remote; never internal component names.
  title: string;
  hint: string;
  // What the recovery button should do: read the status again, or find devices again.
  recovery: "retry" | "discover";
  // Raw code and message, for a collapsed diagnostic disclosure only.
  technical: string;
}

// `action` completes "Couldn't ..." for a failure that is not classified more precisely.
export function describeFailure(
  failure: BridgeFailure,
  action = "read this device's status",
): FailureDescription {
  const technical = `${failure.code}: ${failure.message}`;
  switch (failure.code) {
    case "backend_unavailable":
    case "bridge_transport":
      return {
        kind: "backend_unavailable",
        title: "The app's background service isn't available",
        hint: "Restart control-TV. If this keeps happening, the details can help diagnose it.",
        recovery: "retry",
        technical,
      };
    case "bridge_timeout":
      return {
        kind: "backend_timeout",
        title: "The app's background service isn't responding",
        hint: "Wait a moment, then try again.",
        recovery: "retry",
        technical,
      };
    case "device_unavailable":
      return {
        kind: "device_unavailable",
        title: "Can't reach this device",
        hint: "Make sure it is on and connected to the same network, then try again.",
        recovery: "retry",
        technical,
      };
    case "device_not_found":
      return {
        kind: "device_unknown",
        title: "This device needs to be found again",
        hint: "Find devices again, then select it.",
        recovery: "discover",
        technical,
      };
    case "discovery_failed":
      return {
        kind: "error",
        title: "Couldn't find devices",
        hint: "Check your network connection, then try again.",
        recovery: "retry",
        technical,
      };
    case "timeout":
      return {
        kind: "device_timeout",
        title: "This device didn't answer in time",
        hint: "It may be asleep or busy. Try again.",
        recovery: "retry",
        technical,
      };
    default:
      return {
        kind: "error",
        title: `Couldn't ${action}`,
        hint: "Try again.",
        recovery: "retry",
        technical,
      };
  }
}

const KIND_LABELS: Record<string, string> = {
  cast: "Cast device",
  group: "Group",
  audio: "Speaker",
};

export interface DeviceDescription {
  name: string;
  // The kind of device; the network address is added only when two devices share a name,
  // because then it is the one thing that tells them apart.
  subtitle: string;
}

export function describeDevice(device: Device, all: Device[]): DeviceDescription {
  const kind = KIND_LABELS[device.kind] ?? "Device";
  const shared = all.some(
    (other) => other.id !== device.id && other.friendlyName === device.friendlyName,
  );
  return {
    name: device.friendlyName,
    subtitle: shared ? `${kind} · ${device.host}:${device.port}` : kind,
  };
}

export type PlaybackKind = "playing" | "paused" | "buffering" | "idle" | "unknown";

export interface StatusDescription {
  connected: boolean;
  connectionLabel: string;
  observedAt: string;
  // What is on the device: the media's title, else its identity, else "Nothing playing".
  headline: string;
  hasMedia: boolean;
  playback: { kind: PlaybackKind; label: string } | null;
  // `fraction` is null when the duration is unknown, so no progress bar is drawn.
  position: { text: string; fraction: number | null } | null;
  volumeText: string;
  muted: boolean | null;
  mutedText: string;
  // Volume and mute as the single fact a person reads them as: "Volume 45% · Not muted".
  soundText: string;
  // Only what the device reported; null means "say nothing", never a placeholder.
  application: string | null;
  standby: boolean;
  // Set only when there is no receiver/media state to show (the device is not connected).
  note: string | null;
}

const PLAYBACK_LABELS: Record<PlaybackKind, string> = {
  playing: "Playing",
  paused: "Paused",
  buffering: "Buffering",
  idle: "Idle",
  unknown: "State unknown",
};

function capitalize(value: string): string {
  return value.length === 0 ? value : value.charAt(0).toUpperCase() + value.slice(1);
}

function connectionLabel(connection: string): string {
  return connection === "disconnected" ? "Not connected" : capitalize(connection);
}

export function playbackKind(state: string): PlaybackKind {
  return state === "playing" || state === "paused" || state === "buffering" || state === "idle"
    ? state
    : "unknown";
}

export function formatClock(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) {
    return "";
  }
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = String(whole % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

function describePosition(media: MediaStatus): { text: string; fraction: number | null } {
  const position = formatClock(media.positionSeconds);
  if (position === "") {
    return { text: "Position not reported", fraction: null };
  }
  const duration = formatClock(media.durationSeconds);
  const fraction =
    media.positionSeconds !== null && media.durationSeconds !== null && media.durationSeconds > 0
      ? Math.min(1, Math.max(0, media.positionSeconds / media.durationSeconds))
      : null;
  return { text: duration === "" ? position : `${position} / ${duration}`, fraction };
}

function defaultFormatTime(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleTimeString();
}

// What the status says, worded for display. A field the device did not report is worded as
// not reported, never shown as zero or off, so a partial status explains itself field by
// field; a missing media session is a state of its own ("Nothing playing").
export function describeStatus(
  status: DeviceStatus,
  formatTime: (iso: string) => string = defaultFormatTime,
): StatusDescription {
  const connected = status.connection === "connected";
  const common = {
    connected,
    connectionLabel: connectionLabel(status.connection),
    observedAt: formatTime(status.observedAt),
  };

  if (!connected) {
    return {
      ...common,
      headline: "",
      hasMedia: false,
      playback: null,
      position: null,
      volumeText: "",
      muted: null,
      mutedText: "",
      soundText: "",
      application: null,
      standby: false,
      note: "This device isn't connected, so its current state can't be read.",
    };
  }

  const receiver = status.receiver;
  const media = status.media;
  const volumeLevel = receiver?.volumeLevel ?? null;
  const muted = receiver?.muted ?? null;
  const kind = media === null ? null : playbackKind(media.playbackState);
  const volumeText =
    volumeLevel === null ? "Volume not reported" : `Volume ${Math.round(volumeLevel * 100)}%`;
  const mutedText = muted === null ? "Mute state not reported" : muted ? "Muted" : "Not muted";

  return {
    ...common,
    headline:
      media === null ? "Nothing playing" : (media.title ?? media.contentId ?? "Unidentified media"),
    hasMedia: media !== null,
    playback: kind === null ? null : { kind, label: PLAYBACK_LABELS[kind] },
    position: media === null ? null : describePosition(media),
    volumeText,
    muted,
    mutedText,
    soundText: `${volumeText} · ${mutedText}`,
    application: receiver?.appName ?? receiver?.appId ?? null,
    standby: receiver?.standby === true,
    note: null,
  };
}
