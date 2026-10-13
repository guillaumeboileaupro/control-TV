// Tests for the tray's quick controls, run with Node's built-in runner (`npm test`). Hand-written
// states only: the tray decides nothing the window would not decide itself.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { DEVICE, readyWith, statusOf } from "./fixtures.ts";
import { initialState, type AppState } from "./model.ts";
import { startTransport } from "./playback.ts";
import { isTrayAction, trayRequest } from "./tray.ts";

const playing = (): AppState => readyWith(statusOf());
const withRemote = (state: AppState): AppState => ({
  ...state,
  selected: { ...DEVICE, androidTvRemote: true },
});

describe("tray quick controls", () => {
  test("play / pause offers the opposite of what the TV reports, like the window", () => {
    assert.deepEqual(trayRequest(playing(), "play_pause"), {
      kind: "transport",
      command: "pause",
    });
    const paused = readyWith(statusOf({ media: { playbackState: "paused" } }));
    assert.deepEqual(trayRequest(paused, "play_pause"), { kind: "transport", command: "play" });
  });

  test("stop only when the window offers it", () => {
    assert.deepEqual(trayRequest(playing(), "stop"), { kind: "transport", command: "stop" });
    const nothing = readyWith(statusOf({ media: null }));
    assert.equal(trayRequest(nothing, "stop").kind, "show");
    assert.equal(trayRequest(nothing, "play_pause").kind, "show");
  });

  test("the TV's own mute and volume go through its remote keys", () => {
    assert.deepEqual(trayRequest(withRemote(playing()), "tv_mute"), {
      kind: "remote",
      action: { kind: "key", key: "mute" },
    });
    assert.deepEqual(trayRequest(withRemote(playing()), "tv_volume_up"), {
      kind: "remote",
      action: { kind: "key", key: "volume_up" },
    });
    assert.deepEqual(trayRequest(withRemote(playing()), "tv_volume_down"), {
      kind: "remote",
      action: { kind: "key", key: "volume_down" },
    });
  });

  test("a TV without its remote channel gets no key: the window is shown instead", () => {
    assert.deepEqual(trayRequest(playing(), "tv_mute"), {
      kind: "show",
      reason: "the TV's remote is not available",
    });
  });

  test("nothing selected or a request in flight sends nothing", () => {
    assert.deepEqual(trayRequest(initialState(), "play_pause"), {
      kind: "show",
      reason: "no TV is selected",
    });
    const busy = startTransport(playing(), "pause").state;
    for (const action of ["play_pause", "stop", "tv_mute"] as const) {
      assert.deepEqual(trayRequest(withRemote(busy), action), {
        kind: "show",
        reason: "another request is in progress",
      });
    }
  });

  test("only the five tray identifiers are accepted from the event", () => {
    for (const ok of ["play_pause", "stop", "tv_mute", "tv_volume_down", "tv_volume_up"]) {
      assert.equal(isTrayAction(ok), true);
    }
    for (const bad of ["set_volume", "", null, 3, "quit"]) {
      assert.equal(isTrayAction(bad), false);
    }
  });
});
