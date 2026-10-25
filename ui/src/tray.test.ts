// Tests for the tray's quick controls, run with Node's built-in runner (`npm test`). Hand-written
// states only: the tray decides nothing the window would not decide itself.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { DEVICE, readyWith, statusOf } from "./fixtures.ts";
import { initialState, type AppState } from "./model.ts";
import { startTransport } from "./playback.ts";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { isTrayAction, TRAY_ACTIONS, trayRequest } from "./tray.ts";

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

  test("selecting a TV remains available when none is connected", () => {
    assert.deepEqual(trayRequest(initialState(), "select_tv"), { kind: "select_tv" });
  });

  test("screen actions follow the real streaming state", () => {
    assert.deepEqual(trayRequest(playing(), "duplicate_screen"), { kind: "duplicate_screen" });
    assert.deepEqual(trayRequest(playing(), "duplicate_screen", true), {
      kind: "show",
      reason: "screen streaming is already active",
    });
    assert.deepEqual(trayRequest(playing(), "stop_stream", true), { kind: "stop_stream" });
    assert.deepEqual(trayRequest(playing(), "stop_stream"), {
      kind: "show",
      reason: "screen streaming is not active",
    });
  });
});

describe("the tray's whole menu", () => {
  test("the window handles exactly the actions the tray menu shows, in its order", () => {
    const rust = readFileSync(resolve(process.cwd(), "../src-tauri/src/tray.rs"), "utf8");
    const block = rust.slice(rust.indexOf("pub(crate) const ACTIONS"), rust.indexOf("];"));
    const ids = [...block.matchAll(/\("([a-z_]+)",/g)].map((match) => match[1]);
    assert.deepEqual(ids, [...TRAY_ACTIONS]);
  });

  test("only the menu's identifiers are actions", () => {
    for (const action of TRAY_ACTIONS) assert.ok(isTrayAction(action), action);
    for (const other of ["set_volume", "text", "", "mute", 3, null]) {
      assert.equal(isTrayAction(other), false, String(other));
    }
  });
});
