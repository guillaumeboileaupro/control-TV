// The status-area icon (tray) as plain functions (no DOM, no Tauri, no I/O).
//
// The tray is a second view over the window: it owns no state and no control logic. Each of its
// quick controls is the window's own action, decided here from what the window shows: the same
// selected TV, the same single request in flight, the same controls the TV's state offers. When
// the action cannot run, nothing is sent and the window is brought to the front instead, so the
// person sees why. `main.ts` performs the I/O.

import type { JsonValue } from "./json.ts";
import { idle, type AppState } from "./model.ts";
import { describeControls } from "./playback.ts";
import { describeRemote, type RemoteAction } from "./remote.ts";

// The TV's own remote keys the tray offers, by tray identifier (the window's key names).
const KEYS = {
  tv_mute: "mute",
  tv_volume_down: "volume_down",
  tv_volume_up: "volume_up",
} as const;

type TrayKey = keyof typeof KEYS;
export type TrayAction =
  "select_tv" | "play_pause" | "stop" | "duplicate_screen" | "stop_stream" | TrayKey;

// Every action of the tray's menu (`src-tauri/src/tray.rs`, `ACTIONS`), in the same order.
export const TRAY_ACTIONS: readonly TrayAction[] = [
  "select_tv",
  "play_pause",
  "stop",
  "tv_mute",
  "tv_volume_down",
  "tv_volume_up",
  "duplicate_screen",
  "stop_stream",
];

export type TrayRequest =
  | { kind: "transport"; command: "play" | "pause" | "stop" }
  | { kind: "remote"; action: RemoteAction }
  | { kind: "select_tv" }
  | { kind: "duplicate_screen" }
  | { kind: "stop_stream" }
  | { kind: "show"; reason: string };

export function isTrayAction(value: JsonValue): value is TrayAction {
  return typeof value === "string" && (TRAY_ACTIONS as readonly string[]).includes(value);
}

// What a tray click does now: the window's own action, or nothing and the window shown.
export function trayRequest(state: AppState, action: TrayAction, streaming = false): TrayRequest {
  if (action === "select_tv") {
    return { kind: "select_tv" };
  }
  if (state.selected === null) {
    return { kind: "show", reason: "no TV is selected" };
  }
  if (!idle(state)) {
    return { kind: "show", reason: "another request is in progress" };
  }
  if (action === "play_pause") {
    const primary = describeControls(state).primary;
    return primary === null
      ? { kind: "show", reason: "nothing to play or pause" }
      : { kind: "transport", command: primary.command };
  }
  if (action === "stop") {
    return describeControls(state).stop
      ? { kind: "transport", command: "stop" }
      : { kind: "show", reason: "nothing to stop" };
  }
  if (action === "duplicate_screen") {
    return streaming
      ? { kind: "show", reason: "screen streaming is already active" }
      : { kind: "duplicate_screen" };
  }
  if (action === "stop_stream") {
    return streaming
      ? { kind: "stop_stream" }
      : { kind: "show", reason: "screen streaming is not active" };
  }
  const remote = describeRemote(state);
  if (!remote.remoteVisible || !remote.keysEnabled) {
    return { kind: "show", reason: "the TV's remote is not available" };
  }
  return { kind: "remote", action: { kind: "key", key: KEYS[action] } };
}
