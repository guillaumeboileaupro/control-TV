// Application state and presentation logic for the manual UI, as plain functions.
//
// Nothing here touches the DOM, Tauri or the network: `main.ts` performs the I/O and
// feeds the results in, so every rule below (selection by stable id, stale-response
// handling, what "incomplete" means, how a failure is worded) is testable on its own with
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
  notice: string | null;
  nextRequestId: number;
}

export interface StatusRequest {
  requestId: number;
  deviceId: string;
}

export type StatusOutcome =
  { ok: true; status: DeviceStatus } | { ok: false; failure: BridgeFailure };

export function initialState(): AppState {
  return {
    devices: [],
    discovery: { kind: "idle" },
    selected: null,
    status: { kind: "none" },
    notice: null,
    nextRequestId: 1,
  };
}

// The backend is single-flight (one request at a time), so a discovery queued behind a
// status read, or a status read queued behind a discovery, would eat into the other's
// time budget. Rather than queue them, the UI does not offer one while the other runs.
export function canDiscover(state: AppState): boolean {
  return state.discovery.kind !== "running" && state.status.kind !== "loading";
}

export function canRefresh(state: AppState): boolean {
  return (
    state.selected !== null && state.discovery.kind !== "running" && state.status.kind !== "loading"
  );
}

export function startDiscovery(state: AppState): AppState {
  return { ...state, devices: [], discovery: { kind: "running" }, notice: null };
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
      notice: null,
      nextRequestId: requestId + 1,
    },
    request: { requestId, deviceId: selected.id },
  };
}

// Selects by the device's stable id, never by its display name (two devices can share a
// friendly name). An id the backend did not just report is ignored.
export function selectDevice(
  state: AppState,
  deviceId: string,
): { state: AppState; request: StatusRequest | null } {
  const device = state.devices.find((candidate) => candidate.id === deviceId);
  if (device === undefined || state.discovery.kind === "running") {
    return { state, request: null };
  }
  if (state.status.kind === "loading" && state.selected?.id === deviceId) {
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

// A response only applies if it answers the read that is still the current one. A
// slower, older read (the operator changed the selection meanwhile, or a new discovery
// dropped it) must never overwrite what is shown for the current selection.
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
  title: string;
  detail: string;
}

// `action` completes "Could not ..." for a failure that is not classified more precisely.
export function describeFailure(
  failure: BridgeFailure,
  action = "read the device status",
): FailureDescription {
  switch (failure.code) {
    case "backend_unavailable":
    case "bridge_transport":
      return {
        kind: "backend_unavailable",
        title: "Control backend unavailable",
        detail: failure.message,
      };
    case "bridge_timeout":
      return {
        kind: "backend_timeout",
        title: "Control backend not responding",
        detail: failure.message,
      };
    case "device_unavailable":
      return {
        kind: "device_unavailable",
        title: "Device unavailable",
        detail: failure.message,
      };
    case "device_not_found":
      return {
        kind: "device_unknown",
        title: "Device not known to the backend",
        detail: `${failure.message}. Run “Discover devices” again, then select the device.`,
      };
    case "timeout":
      return {
        kind: "device_timeout",
        title: "The device did not answer in time",
        detail: failure.message,
      };
    default:
      return {
        kind: "error",
        title: `Could not ${action}`,
        detail: `${failure.message} (${failure.code})`,
      };
  }
}

export interface StatusRow {
  label: string;
  value: string;
}

export interface StatusDescription {
  rows: StatusRow[];
  notes: string[];
  incomplete: boolean;
}

const NOT_REPORTED = "Not reported";

function capitalize(value: string): string {
  return value.length === 0 ? value : value.charAt(0).toUpperCase() + value.slice(1);
}

function yesNo(value: boolean | null): string {
  if (value === null) {
    return NOT_REPORTED;
  }
  return value ? "Yes" : "No";
}

export function formatClock(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) {
    return NOT_REPORTED;
  }
  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = String(whole % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

function formatPosition(media: MediaStatus): string {
  if (media.positionSeconds === null) {
    return NOT_REPORTED;
  }
  const position = formatClock(media.positionSeconds);
  return media.durationSeconds === null
    ? position
    : `${position} / ${formatClock(media.durationSeconds)}`;
}

function formatVolume(receiver: ReceiverStatus | null): string {
  if (receiver === null || receiver.volumeLevel === null) {
    return NOT_REPORTED;
  }
  return `${Math.round(receiver.volumeLevel * 100)}%`;
}

function defaultFormatTime(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleTimeString();
}

// What the status says, worded for display. A field the device did not report is shown
// as "Not reported" and the status is flagged incomplete; a missing media session is a
// state of its own (nothing loaded), not an incompleteness.
export function describeStatus(
  status: DeviceStatus,
  formatTime: (iso: string) => string = defaultFormatTime,
): StatusDescription {
  const rows: StatusRow[] = [
    { label: "Connection", value: capitalize(status.connection) },
    { label: "Observed at", value: formatTime(status.observedAt) },
  ];
  const notes: string[] = [];

  if (status.connection !== "connected") {
    notes.push("Receiver and media state are only available on a connected device.");
    return { rows, notes, incomplete: false };
  }

  const receiver = status.receiver;
  rows.push({
    label: "Application",
    value: receiver?.appName ?? receiver?.appId ?? NOT_REPORTED,
  });
  if (receiver !== null && receiver.standby !== null) {
    rows.push({ label: "Standby", value: yesNo(receiver.standby) });
  }
  rows.push({ label: "Volume", value: formatVolume(receiver) });
  rows.push({ label: "Muted", value: yesNo(receiver?.muted ?? null) });

  let incomplete = receiver === null || receiver.volumeLevel === null || receiver.muted === null;

  const media = status.media;
  if (media === null) {
    notes.push("No active media session reported.");
  } else {
    rows.push({ label: "Playback", value: capitalize(media.playbackState) });
    if (media.title !== null) {
      rows.push({ label: "Title", value: media.title });
    }
    rows.push({ label: "Content", value: media.contentId ?? NOT_REPORTED });
    if (media.contentType !== null) {
      rows.push({ label: "Content type", value: media.contentType });
    }
    rows.push({ label: "Position", value: formatPosition(media) });
    incomplete = incomplete || media.playbackState === "unknown" || media.positionSeconds === null;
  }

  if (incomplete) {
    notes.push(
      "Status incomplete: the device did not report every field. Missing values are shown as “Not reported”, never as zero or off.",
    );
  }
  return { rows, notes, incomplete };
}
