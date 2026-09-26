// Playback and sound control logic for the remote, as plain functions (no DOM, no Tauri, no I/O).
//
// The TV's observed state is the source of truth. Nothing here decides what the TV is doing:
// it only decides which controls that observed state offers, how a command's progress and
// outcome are worded, and when a command may be sent. A command that was sent is not a
// command that worked: `unconfirmed` and `not_checked` are never worded as success, and a
// position the operator is moving the seek control to is a draft, never a reported position.
// `main.ts` performs the I/O and draws the result; `npm test` covers every rule below.

import {
  formatClock,
  idle,
  playbackKind,
  type AppState,
  type BridgeFailure,
  type DeviceStatus,
} from "./model.ts";

export type TransportCommand = "play" | "pause" | "stop";
// Volume and mute are absolute requests: a level to reach, a mute state to reach. The names are
// the control layer's own, so an answer can be matched to the command that was sent.
export type SoundCommand = "set_volume" | "set_muted";
export type CommandName = TransportCommand | "seek" | SoundCommand;

// What a sound command asks the TV for. It is a request, never something the TV reported.
export type SoundTarget = { kind: "volume"; percent: number } | { kind: "mute"; muted: boolean };

// What the control layer answers once a command was sent. `confirmed` means a status observed
// on the TV shows the result; `unconfirmed` means it did not in time (`detail` says what the TV
// did report); `not_checked` means verification was off. `observed` is the last status read.
export type Confirmation = "confirmed" | "unconfirmed" | "not_checked";

export interface CommandResult {
  command: CommandName;
  deviceId: string;
  confirmation: Confirmation;
  detail: string | null;
  observed: DeviceStatus | null;
}

export type CommandView =
  | { kind: "idle" }
  | {
      kind: "pending";
      requestId: number;
      command: CommandName;
      targetSeconds: number | null;
      sound: SoundTarget | null;
    }
  // The TV showed the result. `positionSeconds` is the position it reported (a seek's outcome).
  | { kind: "confirmed"; command: CommandName; positionSeconds: number | null }
  // Sent, but the TV did not show the result in time (or nothing checked): not a success.
  | {
      kind: "sent";
      command: CommandName;
      confirmation: "unconfirmed" | "not_checked";
      detail: string | null;
      targetSeconds: number | null;
      sound: SoundTarget | null;
      // The volume the answer itself reported, in whole percent, when it carried an observation.
      reportedPercent: number | null;
    }
  // Not sent, or refused: an error with the control layer's code.
  | { kind: "failed"; command: CommandName; failure: BridgeFailure };

export interface CommandRequest {
  requestId: number;
  command: CommandName;
  deviceId: string;
  positionSeconds: number | null;
  sound: SoundTarget | null;
}

// The arguments the application command takes, as the Tauri boundary expects them. Only the
// values the operator asked for travel: a volume as a level from 0 to 1 (the slider works in
// whole percent), a mute as the state to reach.
export function commandArguments(request: CommandRequest): Record<string, unknown> {
  const args: Record<string, unknown> = { deviceId: request.deviceId };
  switch (request.command) {
    case "seek":
      args["positionSeconds"] = request.positionSeconds;
      break;
    case "set_volume":
      args["level"] = request.sound?.kind === "volume" ? request.sound.percent / 100 : null;
      break;
    case "set_muted":
      args["muted"] = request.sound?.kind === "mute" ? request.sound.muted : null;
      break;
    default:
      break;
  }
  return args;
}

export type CommandOutcome =
  { ok: true; result: CommandResult } | { ok: false; failure: BridgeFailure };

// --- what the observed state offers ---------------------------------------------------------

export interface SeekDescription {
  max: number;
  // What the slider shows: the draft being composed, else the position the TV reported.
  value: number;
  drafting: boolean;
  valueText: string;
  maxText: string;
}

export interface PrimaryControl {
  command: "play" | "pause";
  label: string;
  busyLabel: string;
}

export interface ControlsDescription {
  // Controls exist only for media on a connected device; "nothing playing" offers none.
  visible: boolean;
  // One toggle rather than two competing buttons: it always offers the opposite of what the
  // TV reports (pause while playing or buffering, play while paused).
  primary: PrimaryControl | null;
  stop: boolean;
  seek: SeekDescription | null;
  // Why seeking is not offered, when transport controls are.
  seekNote: string | null;
  // Why no transport control is offered although there is media (idle or unknown state).
  unavailable: string | null;
  // A request is in flight (a command or a discovery): nothing may be sent, so the controls
  // look unavailable. `pendingCommand` says which command it is, if any.
  busy: boolean;
  pendingCommand: CommandName | null;
}

const HIDDEN: ControlsDescription = {
  visible: false,
  primary: null,
  stop: false,
  seek: null,
  seekNote: null,
  unavailable: null,
  busy: false,
  pendingCommand: null,
};

function seekReason(media: NonNullable<DeviceStatus["media"]>): string | null {
  if (media.supportsSeek === false) {
    return "Seeking isn't available for this media.";
  }
  if (media.supportsSeek === null) {
    return "The TV didn't say whether this media can be seeked.";
  }
  if (media.durationSeconds === null || !(media.durationSeconds > 0)) {
    return "Seeking needs the media's length, which the TV didn't report.";
  }
  if (media.positionSeconds === null) {
    return "Seeking needs the current position, which the TV didn't report.";
  }
  return null;
}

// The largest position the control may go to: the whole seconds of the reported duration.
function seekMax(media: NonNullable<DeviceStatus["media"]>): number {
  return Math.floor(media.durationSeconds ?? 0);
}

export function describeControls(state: AppState): ControlsDescription {
  if (state.selected === null || state.status.kind !== "ready") {
    return HIDDEN;
  }
  const status = state.status.status;
  const media = status.media;
  if (status.connection !== "connected" || media === null) {
    return HIDDEN;
  }
  // Busy is any request in flight, not only a command: while a discovery runs the controls stay
  // on screen but every interaction is refused, so they must look unavailable, not enabled.
  const busy = !idle(state);
  const pendingCommand = state.command.kind === "pending" ? state.command.command : null;
  const kind = playbackKind(media.playbackState);

  if (kind === "idle" || kind === "unknown") {
    return {
      ...HIDDEN,
      visible: true,
      busy,
      pendingCommand,
      unavailable:
        kind === "idle"
          ? "The TV is idle, so playback controls aren't available."
          : "The TV didn't report its playback state, so playback controls aren't available.",
    };
  }

  const primary: PrimaryControl =
    kind === "paused"
      ? { command: "play", label: "Play", busyLabel: "Starting…" }
      : { command: "pause", label: "Pause", busyLabel: "Pausing…" };

  const reason = seekReason(media);
  let seek: SeekDescription | null = null;
  if (reason === null) {
    const max = seekMax(media);
    const observed = clampSeek(media.positionSeconds ?? 0, max);
    const drafting = state.seekDraft !== null;
    const value = state.seekDraft !== null ? clampSeek(state.seekDraft, max) : observed;
    seek = { max, value, drafting, valueText: formatClock(value), maxText: formatClock(max) };
  }
  return {
    visible: true,
    primary,
    stop: true,
    seek,
    seekNote: reason,
    unavailable: null,
    busy,
    pendingCommand,
  };
}

// A whole number of seconds within [0, max]; the control moves in one-second steps.
export function clampSeek(seconds: number, max: number): number {
  return Math.min(Math.max(Math.round(seconds), 0), Math.max(max, 0));
}

// --- sending a command ---------------------------------------------------------------------

// Starts a command: it becomes the one request in flight, and any position or volume still being
// composed is dropped (it was never sent, so nothing pretends it was).
export function begin(
  state: AppState,
  command: CommandName,
  positionSeconds: number | null,
  sound: SoundTarget | null = null,
): { state: AppState; request: CommandRequest } | null {
  if (state.selected === null) {
    return null;
  }
  const requestId = state.nextRequestId;
  return {
    state: {
      ...state,
      command: {
        kind: "pending",
        requestId,
        command,
        targetSeconds: positionSeconds,
        sound,
      },
      seekDraft: null,
      volumeDraft: null,
      nextRequestId: requestId + 1,
    },
    request: { requestId, command, deviceId: state.selected.id, positionSeconds, sound },
  };
}

// Play, pause or stop, only if the observed state offers it right now and nothing else is in
// flight. A click on a control the state no longer offers (stale, or a second click while a
// command runs) sends nothing.
export function startTransport(
  state: AppState,
  command: TransportCommand,
): { state: AppState; request: CommandRequest | null } {
  const controls = describeControls(state);
  const offered = command === "stop" ? controls.stop : controls.primary?.command === command;
  if (!idle(state) || !offered) {
    return { state, request: null };
  }
  return begin(state, command, null) ?? { state, request: null };
}

// Composes a seek: moves the draft, sends nothing. Ignored unless seeking is offered.
export function setSeekDraft(state: AppState, seconds: number | null): AppState {
  if (seconds === null) {
    return state.seekDraft === null ? state : { ...state, seekDraft: null };
  }
  const seek = describeControls(state).seek;
  if (seek === null || !idle(state) || !Number.isFinite(seconds)) {
    return state;
  }
  return { ...state, seekDraft: clampSeek(seconds, seek.max) };
}

// Sends the composed seek. Nothing is sent when there is no draft, seeking is not offered,
// something is in flight, or the draft equals the position the TV already reports.
export function startSeek(state: AppState): {
  state: AppState;
  request: CommandRequest | null;
} {
  const draft = state.seekDraft;
  const seek = describeControls(state).seek;
  if (draft === null || seek === null || !idle(state)) {
    return { state, request: null };
  }
  const target = clampSeek(draft, seek.max);
  const observed =
    state.status.kind === "ready" ? (state.status.status.media?.positionSeconds ?? null) : null;
  if (observed !== null && Math.round(observed) === target) {
    return { state: { ...state, seekDraft: null }, request: null };
  }
  return begin(state, "seek", target) ?? { state, request: null };
}

// Applies the answer to the command that is in flight; any other answer is ignored. The TV's
// state changes only through the status the answer carries (a real observation). A command that
// was sent but not confirmed keeps the last observation and says so; it is never shown as done.
export function finishCommand(
  state: AppState,
  requestId: number,
  outcome: CommandOutcome,
): AppState {
  const view = state.command;
  if (view.kind !== "pending" || view.requestId !== requestId) {
    return state;
  }
  if (!outcome.ok) {
    return {
      ...state,
      command: { kind: "failed", command: view.command, failure: outcome.failure },
    };
  }
  const { result } = outcome;
  const wrong =
    state.selected === null ||
    result.deviceId !== state.selected.id ||
    result.command !== view.command ||
    (result.observed !== null && result.observed.deviceId !== state.selected.id);
  if (wrong) {
    return {
      ...state,
      command: {
        kind: "failed",
        command: view.command,
        failure: {
          code: "unexpected_response",
          message: "The backend answered for a different device or command.",
        },
      },
    };
  }
  const status =
    result.observed === null ? state.status : ({ kind: "ready", status: result.observed } as const);
  if (result.confirmation === "confirmed") {
    return {
      ...state,
      status,
      command: {
        kind: "confirmed",
        command: view.command,
        positionSeconds: result.observed?.media?.positionSeconds ?? null,
      },
    };
  }
  return {
    ...state,
    status,
    command: {
      kind: "sent",
      command: view.command,
      confirmation: result.confirmation,
      detail: result.detail,
      targetSeconds: view.targetSeconds,
      sound: view.sound,
      reportedPercent: reportedPercent(result.observed),
    },
  };
}

function reportedPercent(status: DeviceStatus | null): number | null {
  const level = status?.receiver?.volumeLevel ?? null;
  return level === null ? null : Math.round(level * 100);
}

// --- wording -------------------------------------------------------------------------------

const PENDING_TEXT: Record<TransportCommand, string> = {
  play: "Starting playback…",
  pause: "Pausing…",
  stop: "Stopping…",
};

const CONFIRMED_TEXT: Record<TransportCommand, string> = {
  play: "Playing.",
  pause: "Paused.",
  stop: "Stopped.",
};

// What a command in flight is worded as. Only a seek has no other place on screen that shows
// what was requested; a transport button and the sound readout already say it.
function pendingText(
  command: CommandName,
  sound: SoundTarget | null,
  seconds: number | null,
): string {
  switch (command) {
    case "seek":
      return `Seeking to ${seekText(seconds)}…`;
    case "set_volume":
      return sound?.kind === "volume" ? `Setting volume to ${sound.percent}%…` : "Setting volume…";
    case "set_muted":
      return sound?.kind === "mute" && !sound.muted ? "Unmuting…" : "Muting…";
    default:
      return PENDING_TEXT[command];
  }
}

// What a confirmed command is worded as: the position or level is the one the TV reported.
function confirmedText(
  state: AppState,
  view: { command: CommandName; positionSeconds: number | null },
): string {
  const receiver = state.status.kind === "ready" ? state.status.status.receiver : null;
  switch (view.command) {
    case "seek":
      return `Position ${seekText(view.positionSeconds)}.`;
    case "set_volume": {
      const percent = reportedPercent(state.status.kind === "ready" ? state.status.status : null);
      return percent === null ? "Volume changed." : `Volume ${percent}%.`;
    }
    case "set_muted":
      return receiver?.muted === true
        ? "Muted."
        : receiver?.muted === false
          ? "Unmuted."
          : "Mute changed.";
    default:
      return CONFIRMED_TEXT[view.command];
  }
}

// The subject of "… sent, but …": what the operator asked for.
function sentSubject(command: CommandName, sound: SoundTarget | null): string {
  switch (command) {
    case "seek":
      return "Seek";
    case "set_volume":
      return "Volume";
    case "set_muted":
      return sound?.kind === "mute" && !sound.muted ? "Unmute" : "Mute";
    default:
      return "Command";
  }
}

export interface CommandFailureDescription {
  title: string;
  hint: string;
  // What the recovery button does: read the state again (never resend), or find devices again.
  recovery: "check" | "discover";
  // Raw code and message for a collapsed diagnostic disclosure only.
  technical: string;
}

const MAYBE_SENT =
  "The command may or may not have reached it. Check the current state before sending it again.";

// A failed command, worded for the person using the remote. Where the command might still have
// been delivered (a timeout), it says so and never offers to resend on its own.
export function describeCommandFailure(
  command: CommandName,
  failure: BridgeFailure,
): CommandFailureDescription {
  const technical = `${failure.code}: ${failure.message}`;
  switch (failure.code) {
    case "device_unavailable":
      return {
        title: "Can't reach this device",
        hint: "The command wasn't sent. Make sure it is on and connected to the same network.",
        recovery: "check",
        technical,
      };
    case "device_not_found":
      return {
        title: "This device needs to be found again",
        hint: "The command wasn't sent. Find devices again, then select it.",
        recovery: "discover",
        technical,
      };
    case "command_rejected":
      return {
        title: "The TV refused the command",
        hint:
          command === "set_volume" || command === "set_muted"
            ? "It received the command but didn't accept it."
            : "It received the command but didn't accept it, for example because this media can't do that right now.",
        recovery: "check",
        technical,
      };
    case "unsupported_operation":
      return {
        title:
          command === "seek"
            ? "Seeking isn't available for this media"
            : command === "set_volume" || command === "set_muted"
              ? "The TV can't do that"
              : "This media can't do that",
        hint: "The command wasn't sent.",
        recovery: "check",
        technical,
      };
    case "timeout":
      return {
        title: "The TV didn't answer in time",
        hint: MAYBE_SENT,
        recovery: "check",
        technical,
      };
    case "invalid_argument":
      return {
        title:
          command === "seek"
            ? "That position isn't valid"
            : command === "set_volume"
              ? "That volume isn't valid"
              : "That request isn't valid",
        hint: "The command wasn't sent. Check the current state and try again.",
        recovery: "check",
        technical,
      };
    case "backend_unavailable":
      return {
        title: "The app's background service isn't available",
        hint: "The command wasn't sent. Restart control-TV.",
        recovery: "check",
        technical,
      };
    case "bridge_timeout":
    case "bridge_transport":
      return {
        title: "The app's background service stopped answering",
        hint: MAYBE_SENT,
        recovery: "check",
        technical,
      };
    default:
      return {
        title: "Couldn't send the command",
        hint: "Check the current state before trying again.",
        recovery: "check",
        technical,
      };
  }
}

export interface CommandFeedbackLine {
  text: string;
  // Read out by assistive technology but not drawn: the observed state already shows it.
  announceOnly: boolean;
  tone: "info" | "warning";
}

export interface CommandFeedback {
  lines: CommandFeedbackLine[];
  // Set for a command that was sent but not confirmed: the raw detail for a disclosure.
  detail: string | null;
  failure: CommandFailureDescription | null;
  // Whether a "check the state" button is offered next to the message.
  checkState: boolean;
}

const NONE: CommandFeedback = { lines: [], detail: null, failure: null, checkState: false };

function seekText(seconds: number | null): string {
  const clock = formatClock(seconds);
  return clock === "" ? "the new position" : clock;
}

export function describeCommandFeedback(state: AppState): CommandFeedback {
  const view = state.command;
  switch (view.kind) {
    case "idle":
      return NONE;
    case "pending":
      return {
        ...NONE,
        lines: [
          {
            text: pendingText(view.command, view.sound, view.targetSeconds),
            // A transport command's progress is already on its button and a sound command's on its
            // readout; only a seek has no other place to show the position that was requested.
            announceOnly: view.command !== "seek",
            tone: "info",
          },
        ],
      };
    case "confirmed":
      return {
        ...NONE,
        lines: [{ text: confirmedText(state, view), announceOnly: true, tone: "info" }],
      };
    case "sent": {
      const what = sentSubject(view.command, view.sound);
      let text =
        view.confirmation === "unconfirmed"
          ? `${what} sent, but the TV hasn't shown the change yet.`
          : `${what} sent. Its result wasn't checked.`;
      // A receiver may settle on a level of its own: when the answer says what the TV reports,
      // say so instead of implying nothing changed.
      if (
        view.confirmation === "unconfirmed" &&
        view.sound?.kind === "volume" &&
        view.reportedPercent !== null &&
        view.reportedPercent !== view.sound.percent
      ) {
        text = `Volume sent, but the TV reports ${view.reportedPercent}%, not ${view.sound.percent}%.`;
      }
      return {
        lines: [{ text, announceOnly: false, tone: "warning" }],
        detail: view.detail,
        failure: null,
        checkState: true,
      };
    }
    case "failed":
      return {
        lines: [],
        detail: null,
        failure: describeCommandFailure(view.command, view.failure),
        checkState: false,
      };
  }
}
