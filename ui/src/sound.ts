// Volume and mute for the remote, as plain functions (no DOM, no Tauri, no I/O).
//
// The TV's observed state is the source of truth. The volume control shows what the TV reported,
// except while the operator is composing a level (a draft: a request, not yet sent) or a level is
// on its way (sending: sent, not yet shown by the TV); each is worded as what it is. Mute is not
// kept on the remote at all: the button offers the opposite of the mute state the TV reported, and
// a press asks for that absolute state. A missing report is never a default: a volume or mute
// state the TV did not report offers no control, and says so.
// `main.ts` performs the I/O and draws the result; `npm test` covers every rule below.

import { idle, type AppState } from "./model.ts";
import { begin, type CommandName, type CommandRequest } from "./playback.ts";

export const MAX_PERCENT = 100;

// A whole percent within [0, 100]; the control moves in one-percent steps.
export function clampPercent(value: number): number {
  return Math.min(Math.max(Math.round(value), 0), MAX_PERCENT);
}

// `observed`: the level the TV reported. `draft`: a level being composed, not sent. `sending`: a
// level that was sent and that the TV has not shown yet.
export type VolumePhase = "observed" | "draft" | "sending";

export interface VolumeControl {
  // The whole percent the control shows: the level being sent, else the draft, else the TV's.
  value: number;
  // What the TV reported, in whole percent.
  observed: number;
  phase: VolumePhase;
}

export interface MuteControl {
  // As the TV reports it.
  muted: boolean;
  // What a press asks for: always the opposite of what the TV reports.
  action: "Mute" | "Unmute";
}

export interface SoundDescription {
  // Sound controls exist for a connected device, whether or not media is playing.
  visible: boolean;
  // Null when the TV did not report its volume: there is nothing honest to draw a slider on.
  volume: VolumeControl | null;
  // Null when the TV did not report whether it is muted: the button could not know what to offer.
  mute: MuteControl | null;
  // The one line under the controls: what the TV reports, what is being composed or sent. Empty
  // when there is nothing to say.
  readout: string;
  // Why a control is not offered.
  note: string | null;
  // A request is in flight (a command or a discovery): nothing may be sent, so the controls look
  // unavailable. `pendingCommand` says which command it is, if any.
  busy: boolean;
  pendingCommand: CommandName | null;
}

const HIDDEN: SoundDescription = {
  visible: false,
  volume: null,
  mute: null,
  readout: "",
  note: null,
  busy: false,
  pendingCommand: null,
};

function percentOf(level: number): number {
  return Math.round(level * MAX_PERCENT);
}

export function describeSound(state: AppState): SoundDescription {
  if (state.selected === null || state.status.kind !== "ready") {
    return HIDDEN;
  }
  const status = state.status.status;
  if (status.connection !== "connected") {
    return HIDDEN;
  }
  const level = status.receiver?.volumeLevel ?? null;
  const muted = status.receiver?.muted ?? null;
  const busy = !idle(state);
  const pending = state.command.kind === "pending" ? state.command : null;
  const pendingSound = pending?.sound ?? null;

  let volume: VolumeControl | null = null;
  if (level !== null) {
    const observed = percentOf(level);
    if (pendingSound?.kind === "volume") {
      volume = { value: pendingSound.percent, observed, phase: "sending" };
    } else if (state.volumeDraft !== null && clampPercent(state.volumeDraft) !== observed) {
      volume = { value: clampPercent(state.volumeDraft), observed, phase: "draft" };
    } else {
      volume = { value: observed, observed, phase: "observed" };
    }
  }
  const mute: MuteControl | null =
    muted === null ? null : { muted, action: muted ? "Unmute" : "Mute" };

  let readout = "";
  if (pendingSound?.kind === "mute") {
    readout = pendingSound.muted ? "Muting…" : "Unmuting…";
  } else if (volume === null) {
    readout = muted === true ? "Muted" : "";
  } else if (volume.phase === "sending") {
    readout = `Setting volume to ${volume.value}%…`;
  } else if (volume.phase === "draft") {
    readout = `Set volume to ${volume.value}%`;
  } else {
    readout = muted === true ? `Volume ${volume.observed}% · Muted` : `Volume ${volume.observed}%`;
  }

  let note: string | null = null;
  if (volume === null && mute === null) {
    note = "The TV didn't report its volume or mute state, so sound controls aren't available.";
  } else if (volume === null) {
    note = "The TV didn't report its volume.";
  } else if (mute === null) {
    note = "The TV didn't report whether it is muted.";
  }

  return {
    visible: true,
    volume,
    mute,
    readout,
    note,
    busy,
    pendingCommand: pending === null ? null : pending.command,
  };
}

// Composes a volume: moves the draft, sends nothing. Ignored unless the TV reported a volume and
// nothing is in flight. A level equal to the one the TV reports is no draft at all.
export function setVolumeDraft(state: AppState, percent: number | null): AppState {
  if (percent === null) {
    return state.volumeDraft === null ? state : { ...state, volumeDraft: null };
  }
  const volume = describeSound(state).volume;
  if (volume === null || !idle(state) || !Number.isFinite(percent)) {
    return state;
  }
  const next = clampPercent(percent) === volume.observed ? null : clampPercent(percent);
  return state.volumeDraft === next ? state : { ...state, volumeDraft: next };
}

// Sends the composed volume as one command. Nothing is sent when there is no draft, no volume
// was reported, something is in flight, or the draft equals the level the TV already reports.
export function startVolume(state: AppState): {
  state: AppState;
  request: CommandRequest | null;
} {
  const draft = state.volumeDraft;
  const volume = describeSound(state).volume;
  if (draft === null || volume === null || !idle(state)) {
    return { state, request: null };
  }
  const target = clampPercent(draft);
  if (target === volume.observed) {
    return { state: { ...state, volumeDraft: null }, request: null };
  }
  return (
    begin(state, "set_volume", null, { kind: "volume", percent: target }) ?? {
      state,
      request: null,
    }
  );
}

// Mutes or unmutes: asks for the opposite of the mute state the TV reported, as an absolute state
// (never a toggle). Nothing is sent when that state was not reported or something is in flight,
// so a second click while the first runs sends nothing.
export function startMute(state: AppState): {
  state: AppState;
  request: CommandRequest | null;
} {
  const mute = describeSound(state).mute;
  if (mute === null || !idle(state)) {
    return { state, request: null };
  }
  return (
    begin(state, "set_muted", null, { kind: "mute", muted: !mute.muted }) ?? {
      state,
      request: null,
    }
  );
}
