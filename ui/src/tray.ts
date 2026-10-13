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

export type TrayAction = "play_pause" | "stop" | "tv_mute" | "tv_volume_down" | "tv_volume_up";

export type TrayRequest =
  | { kind: "transport"; command: "play" | "pause" | "stop" }
  | { kind: "remote"; action: RemoteAction }
  | { kind: "show"; reason: string };

const KEYS = {
  tv_mute: "mute",
  tv_volume_down: "volume_down",
  tv_volume_up: "volume_up",
} as const;

export function isTrayAction(value: JsonValue): value is TrayAction {
  return (
    value === "play_pause" ||
    value === "stop" ||
    value === "tv_mute" ||
    value === "tv_volume_down" ||
    value === "tv_volume_up"
  );
}

// What a tray click does now: the window's own action, or nothing and the window shown.
export function trayRequest(state: AppState, action: TrayAction): TrayRequest {
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
  const remote = describeRemote(state);
  if (!remote.remoteVisible || !remote.keysEnabled) {
    return { kind: "show", reason: "the TV's remote is not available" };
  }
  return { kind: "remote", action: { kind: "key", key: KEYS[action] } };
}
