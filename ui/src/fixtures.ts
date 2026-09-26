// Hand-written devices, statuses and answers shared by the sound and interaction tests. They say
// nothing about a real Chromecast, only what the remote does with what a backend reports.

import {
  finishDiscovery,
  finishStatusRead,
  initialState,
  selectDevice,
  startDiscovery,
  type AppState,
  type Device,
  type DeviceStatus,
  type MediaStatus,
  type ReceiverStatus,
} from "./model.ts";
import type { CommandName, CommandResult, Confirmation } from "./playback.ts";

export const DEVICE: Device = {
  id: "a",
  friendlyName: "Device a",
  host: "192.0.2.10",
  port: 8009,
  kind: "cast",
  modelName: null,
};

export const MEDIA: MediaStatus = {
  playbackState: "playing",
  contentId: "http://media.example/movie.mp4",
  contentType: "video/mp4",
  title: "Movie",
  positionSeconds: 212,
  durationSeconds: 596.4,
  supportsSeek: true,
};

const RECEIVER: ReceiverStatus = {
  appId: null,
  appName: null,
  volumeLevel: 0.45,
  muted: false,
  standby: null,
};

export interface StatusOptions {
  // `null` is a TV that reported no receiver state at all; a partial object overrides fields.
  receiver?: Partial<ReceiverStatus> | null;
  // `null` is nothing playing.
  media?: Partial<MediaStatus> | null;
  connection?: string;
}

export function statusOf(options: StatusOptions = {}): DeviceStatus {
  const connection = options.connection ?? "connected";
  const media = options.media === undefined ? MEDIA : options.media;
  const receiver = options.receiver === undefined ? RECEIVER : options.receiver;
  return {
    deviceId: "a",
    connection,
    observedAt: "2026-09-26T10:00:00+00:00",
    receiver: receiver === null ? null : { ...RECEIVER, ...receiver },
    media: media === null ? null : { ...MEDIA, ...media },
  };
}

// A selected device whose status read has completed with `status`.
export function readyWith(status: DeviceStatus): AppState {
  const found = finishDiscovery(startDiscovery(initialState()), [DEVICE]);
  const { state, request } = selectDevice(found, "a");
  if (request === null) {
    throw new Error("the fixture device could not be selected");
  }
  return finishStatusRead(state, request.requestId, { ok: true, status });
}

export function commandResult(
  command: CommandName,
  confirmation: Confirmation,
  observed: DeviceStatus | null,
  detail: string | null = null,
): CommandResult {
  return { command, deviceId: "a", confirmation, detail, observed };
}
