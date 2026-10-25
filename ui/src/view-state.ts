// Shared state shapes for the application model and its playback/remote reducers.
// This leaf module deliberately contains no reducer logic, DOM access or I/O: model.ts owns
// AppState, while playback.ts and remote.ts transform it without importing each other.

export interface BridgeFailure {
  code: string;
  message: string;
}

export type TransportCommand = "play" | "pause" | "stop";
export type SoundCommand = "set_volume" | "set_muted";
export type CommandName = TransportCommand | "seek" | SoundCommand;

export type SoundTarget = { kind: "volume"; percent: number } | { kind: "mute"; muted: boolean };

export type CommandView =
  | { kind: "idle" }
  | {
      kind: "pending";
      requestId: number;
      command: CommandName;
      targetSeconds: number | null;
      sound: SoundTarget | null;
    }
  | { kind: "confirmed"; command: CommandName; positionSeconds: number | null }
  | {
      kind: "sent";
      command: CommandName;
      confirmation: "unconfirmed" | "not_checked";
      detail: string | null;
      targetSeconds: number | null;
      sound: SoundTarget | null;
      reportedPercent: number | null;
    }
  | { kind: "failed"; command: CommandName; failure: BridgeFailure };

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
  | "enter"
  | "digit_0"
  | "digit_1"
  | "digit_2"
  | "digit_3"
  | "digit_4"
  | "digit_5"
  | "digit_6"
  | "digit_7"
  | "digit_8"
  | "digit_9"
  | "info"
  | "guide"
  | "input"
  | "subtitles"
  | "audio_track"
  | "settings"
  | "channel_up"
  | "channel_down"
  | "red"
  | "green"
  | "yellow"
  | "blue"
  | "play_pause"
  | "stop"
  | "rewind"
  | "fast_forward"
  | "previous"
  | "next"
  | "record"
  | "assistant";

export interface AudibleVolume {
  level: number;
  max: number;
  muted: boolean;
}

export interface RemoteReport {
  isOn: boolean | null;
  currentApp: string | null;
  volume: AudibleVolume | null;
}

export type RemoteAction =
  | { kind: "status" }
  | { kind: "key"; key: RemoteKeyName }
  | { kind: "show_code" }
  | { kind: "pair"; code: string }
  | { kind: "text"; text: string }
  | { kind: "list_apps" }
  | { kind: "pair_apps"; pairingEndpoint: string; code: string }
  | { kind: "open_apps_port" }
  | { kind: "launch_app"; package: string; label: string };

export type PairingStep = "not_tried" | "needed" | "code_shown" | "paired";

export interface RemoteOutcome {
  tone: "done" | "sent" | "failed";
  text: string;
  technical: string | null;
}

export interface TvApp {
  package: string;
  name: string | null;
}

export interface RemoteView {
  report: RemoteReport | null;
  pending: { requestId: number; action: RemoteAction } | null;
  pairing: PairingStep;
  outcome: RemoteOutcome | null;
  apps: TvApp[] | null;
  appFilter: string;
  appsPairingNeeded: boolean;
}

export const REMOTE_IDLE: RemoteView = {
  report: null,
  pending: null,
  pairing: "not_tried",
  outcome: null,
  apps: null,
  appFilter: "",
  appsPairingNeeded: false,
};
