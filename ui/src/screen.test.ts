// Tests for the "Screen to TV" panel logic, run with Node's built-in runner (`npm test`).
// Hand-written answers only: they say nothing about a real screen or TV, only how the panel
// reasons over what the control layer answers.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { JsonValue } from "./json.ts";
import {
  askConsent,
  cancelConsent,
  choose,
  consentedStart,
  describeScreen,
  finishPoll,
  finishScreen,
  finishWebRtc,
  follows,
  initialScreenState,
  moveExtension,
  parseListing,
  parseReceiverCapabilities,
  parseStream,
  previewScreens,
  reconcileStreams,
  screenRequestDeadlineMs,
  selectTransport,
  shouldApplyPoll,
  startStop,
  withScreens,
  type ScreenPanelState,
} from "./screen.ts";

const LISTED: JsonValue = {
  screens: [
    { name: "eDP-1", width: 1920, height: 1080, x: 0, y: 0, primary: true },
    { name: "HDMI-1", width: 2560, height: 1440, x: 1920, y: -180, primary: false },
  ],
  extension: { output: "HDMI-2", sizes: [[1920, 1080]], refreshRates: [60, 50] },
};

function stream(state: string, error: string | null = null): JsonValue {
  return {
    stream: {
      state,
      mode: "duplicate",
      screen: "eDP-1",
      width: 1920,
      height: 1080,
      fps: 30,
      error,
    },
  };
}

const LISTED_SCREENS = [
  { name: "eDP-1", width: 1920, height: 1080, x: 0, y: 0, primary: true },
  { name: "HDMI-1", width: 2560, height: 1440, x: 1920, y: -180, primary: false },
];

function available(): ScreenPanelState {
  return withScreens(initialScreenState(), parseListing(LISTED));
}

function confirming(): ScreenPanelState {
  return askConsent(available(), true);
}

describe("availability", () => {
  test("the panel is shown only where the screens could be listed", () => {
    assert.equal(describeScreen(initialScreenState(), true).visible, false);
    assert.equal(describeScreen(withScreens(initialScreenState(), null), true).visible, false);
    const shown = available();
    assert.equal(describeScreen(shown, true).visible, true);
    assert.equal(shown.choice.screen, "eDP-1");
  });

  test("an answer of another shape is not a list of screens", () => {
    assert.equal(parseListing(null), null);
    assert.equal(parseListing({ screens: "eDP-1" }), null);
    assert.equal(parseListing({ screens: [1] }), null);
    assert.equal(parseListing({ screens: [{ name: "eDP-1", width: "wide", height: 1 }] }), null);
    assert.equal(
      parseListing({
        screens: [{ name: "eDP-1", width: 0, height: 1080, x: 0, y: 0, primary: true }],
      }),
      null,
    );
    assert.equal(
      parseListing({
        screens: [{ name: "eDP-1", width: 1920, height: 1080, x: 0.5, y: 0, primary: true }],
      }),
      null,
    );
  });

  test("a refreshed list keeps the chosen screen while it is still there", () => {
    const chosen = choose(available(), { screen: "HDMI-1" });
    assert.equal(withScreens(chosen, parseListing(LISTED)).choice.screen, "HDMI-1");
    assert.equal(withScreens(chosen, { screens: [], extensionOutput: null }).choice.screen, null);
  });

  test("the preview preserves real coordinates and resolution proportions", () => {
    const preview = previewScreens(LISTED_SCREENS);
    assert.equal(preview.length, 2);
    assert.equal(
      preview[1]?.previewX,
      (preview[0]?.previewX ?? 0) + (preview[0]?.previewWidth ?? 0),
    );
    assert.equal((preview[1]?.previewWidth ?? 0) / (preview[1]?.previewHeight ?? 1), 2560 / 1440);
    assert.ok((preview[1]?.previewY ?? 0) < (preview[0]?.previewY ?? 0));
  });

  test("with no screen to duplicate, extending is still offered", () => {
    const none = withScreens(initialScreenState(), { screens: [], extensionOutput: "HDMI-2" });
    const view = describeScreen(none, true);
    assert.equal(view.canAsk, false);
    assert.equal(view.statusText, "No screen can be duplicated: extend instead.");
    assert.equal(describeScreen(choose(none, { mode: "extend" }), true).canAsk, true);
  });
});

describe("consent", () => {
  test("nothing is sent before the person confirms, and a TV is needed", () => {
    assert.equal(describeScreen(available(), false).canAsk, false);
    assert.equal(describeScreen(available(), false).statusText, "Choose a TV first.");
    assert.equal(askConsent(available(), false).confirming, false);
    assert.equal(consentedStart(available(), "tv-1").request, null);
    assert.equal(consentedStart(confirming(), null).request, null);
  });

  test("the consent step says what becomes visible and how the stream ends", () => {
    const duplicate = describeScreen(confirming(), true);
    assert.equal(duplicate.confirming, true);
    assert.match(duplicate.consentText, /screen eDP-1 will be visible on the TV/);
    assert.match(duplicate.consentText, /only on your home network/);
    assert.match(duplicate.consentText, /one hour, on Stop, or when you quit/);
    const extend = describeScreen(
      askConsent(choose(available(), { mode: "extend", position: "above", refresh: 50 }), true),
      true,
    );
    assert.match(
      extend.consentText,
      /second screen at desktop coordinates 1920, 0 \(1920×1080, 50 Hz\)/,
    );
  });

  test("a changed choice or a cancel withdraws the consent step", () => {
    assert.equal(choose(confirming(), { fps: 25 }).confirming, false);
    assert.equal(cancelConsent(confirming()).confirming, false);
  });

  test("the confirmed start carries the choice and the consent", () => {
    const chosen = askConsent(choose(available(), { size: "1280x720", fps: 25 }), true);
    const next = consentedStart(chosen, "tv-1");
    assert.deepEqual(next.request, {
      transport: "auto",
      command: "bridge_start_screen_stream",
      args: {
        deviceId: "tv-1",
        mode: "duplicate",
        screen: "eDP-1",
        width: 1280,
        height: 720,
        fps: 25,
        consent: true,
        refresh: 60,
        position: "right",
      },
    });
    assert.equal(next.state.pending, true);
    assert.equal(describeScreen(next.state, true).canChoose, false);
    const extend = consentedStart(
      askConsent(choose(available(), { mode: "extend" }), true),
      "tv-1",
    );
    assert.equal(extend.request?.args.screen, null);
  });
});

describe("the stream", () => {
  test("receiver capabilities select WebRTC only when confirmed", () => {
    const capabilities = parseReceiverCapabilities({
      capabilities: {
        deviceAvailable: true,
        hlsAvailable: true,
        remoteAccessible: true,
        webrtcConfirmed: true,
        reason: null,
      },
    });
    assert.notEqual(capabilities, null);
    assert.equal(selectTransport("auto", capabilities!), "webrtc");
    assert.equal(selectTransport("hls", capabilities!), "hls");
    assert.equal(selectTransport("webrtc", { ...capabilities!, webrtcConfirmed: false }), null);
    assert.equal(parseReceiverCapabilities({ capabilities: { hlsAvailable: true } }), null);
  });

  test("WebRTC states are mapped without exposing pairing credentials", () => {
    const sent = {
      ...consentedStart(confirming(), "tv-1").state,
      transportUsed: "webrtc" as const,
    };
    const starting = finishWebRtc(sent, {
      ok: true,
      result: { state: "awaiting_receiver", pairingToken: "must-not-be-rendered" },
    });
    assert.equal(starting.stream.state, "starting");
    assert.equal(starting.transportUsed, "webrtc");
    assert.equal(describeScreen(starting, true).statusText, "Starting the WebRTC stream…");
    const idle = finishWebRtc(starting, { ok: true, result: { state: "idle", error: null } });
    assert.equal(idle.transportUsed, null);
  });

  test("a poll started before Stop can never replace the stop result", () => {
    assert.equal(shouldApplyPoll(4, 5, false, "webrtc", null), false);
    assert.equal(shouldApplyPoll(5, 5, true, "webrtc", "webrtc"), false);
    assert.equal(shouldApplyPoll(5, 5, false, "webrtc", "webrtc"), true);
  });

  test("a started stream is followed until it is idle again", () => {
    const sent = consentedStart(confirming(), "tv-1").state;
    const started = finishScreen(sent, { ok: true, result: stream("starting") });
    assert.equal(started.pending, false);
    assert.equal(follows(started), true);
    const running = finishPoll(started, stream("running"));
    const view = describeScreen(running, true);
    assert.equal(view.statusText, "Streaming to the TV.");
    assert.equal(view.canStop, true);
    assert.equal(view.canChoose, false);
    assert.equal(follows(finishPoll(running, stream("idle"))), false);
    assert.equal(finishPoll(running, "garbled"), running);
  });

  test("stop is sent once, then the reported state is shown", () => {
    const running = finishPoll(available(), stream("running"));
    const stopping = startStop(running);
    assert.equal(stopping.send, true);
    assert.equal(startStop(stopping.state).send, false);
    const answered = finishScreen(stopping.state, { ok: true, result: stream("stopping") });
    assert.equal(describeScreen(answered, true).canStop, false);
    assert.equal(describeScreen(answered, true).statusText, "Stopping the stream…");
    assert.equal(startStop(available()).send, false);
  });

  test("a failed stream shows its error and is dismissed with stop", () => {
    const failed = finishPoll(available(), stream("failed", "the stream ended by itself"));
    const view = describeScreen(failed, true);
    assert.equal(view.errorText, "The stream ended by itself.");
    assert.equal(choose(failed, { fps: 25 }), failed);
    assert.equal(view.stopLabel, "Dismiss");
    assert.equal(view.canAsk, false);
    assert.equal(startStop(failed).send, true);
  });

  test("a refused request is shown and the choice can be made again", () => {
    const sent = consentedStart(confirming(), "tv-1").state;
    const refused = finishScreen(sent, {
      ok: false,
      failure: { code: "invalid_argument", message: "a screen stream is already running" },
    });
    assert.equal(describeScreen(refused, true).errorText, "A screen stream is already running.");
    assert.equal(describeScreen(refused, true).canChoose, true);
    const unreadable = finishScreen(sent, { ok: true, result: { stream: { state: "lost" } } });
    assert.equal(unreadable.failure?.message, "The stream state could not be read.");
  });

  test("the reported stream is read without guessing", () => {
    assert.deepEqual(parseStream({ stream: { state: "idle", mode: "mirror", screen: 3 } }), {
      state: "idle",
      mode: null,
      screen: null,
      error: null,
    });
    assert.equal(parseStream(null), null);
  });

  test("an unreadable WebRTC answer ends the request instead of leaving the controls waiting", () => {
    const sent = {
      ...consentedStart(confirming(), "tv-1").state,
      transportUsed: "webrtc" as const,
    };
    const unreadable: JsonValue[] = [{ state: "reconnecting" }, { stream: "lost" }, null];
    for (const result of unreadable) {
      const answered = finishWebRtc(sent, { ok: true, result });
      assert.equal(answered.pending, false);
      assert.equal(answered.failure?.message, "The stream state could not be read.");
      assert.equal(describeScreen(answered, true).canChoose, true);
    }
  });
});

describe("the stream both hosts report", () => {
  test("a WebRTC session running in the Rust engine is shown, never as idle", () => {
    const opened = reconcileStreams(available(), stream("running"), { state: "streaming" });
    assert.equal(opened.stream.state, "running");
    assert.equal(opened.stream.mode, "duplicate");
    assert.equal(opened.stream.screen, "eDP-1");
    assert.equal(opened.transportUsed, "webrtc");
    const view = describeScreen(opened, true);
    assert.equal(view.statusText, "Streaming to the TV with WebRTC.");
    assert.equal(view.canChoose, false);
    assert.equal(view.canAsk, false);
    assert.equal(view.canStop, true);
    assert.equal(follows(opened), true);
  });

  test("the mode and screen shown are the ones streamed, not an older choice", () => {
    const extending: JsonValue = {
      stream: { state: "running", mode: "extend", screen: null, error: null },
    };
    const shown = reconcileStreams(available(), extending, { state: "streaming" });
    assert.equal(shown.choice.mode, "extend");
    assert.equal(describeScreen(shown, true).extending, true);
    const other: JsonValue = {
      stream: { state: "running", mode: "duplicate", screen: "HDMI-1", error: null },
    };
    const duplicating = reconcileStreams(choose(available(), { mode: "extend" }), other, {
      state: "idle",
    });
    assert.equal(duplicating.choice.mode, "duplicate");
    assert.equal(duplicating.choice.screen, "HDMI-1");
    assert.equal(duplicating.confirming, false);
  });

  test("an engine waiting for its receiver or reconnecting is a start still in progress", () => {
    const waiting = reconcileStreams(available(), stream("starting"), {
      state: "awaiting_receiver",
    });
    assert.equal(waiting.stream.state, "starting");
    assert.equal(describeScreen(waiting, true).canAsk, false);
  });

  test("a failed engine session is shown with its error and dismissed through WebRTC", () => {
    const failed = reconcileStreams(available(), stream("idle"), {
      state: "failed",
      error: "the receiver stopped answering",
    });
    assert.equal(failed.transportUsed, "webrtc");
    assert.equal(describeScreen(failed, true).errorText, "The receiver stopped answering.");
    assert.equal(describeScreen(failed, true).stopLabel, "Dismiss");
  });

  test("with the engine idle, the control layer's stream is the HLS one", () => {
    const hls = reconcileStreams(available(), stream("running"), { state: "idle" });
    assert.equal(hls.transportUsed, "hls");
    assert.equal(describeScreen(hls, true).statusText, "Streaming to the TV with HLS.");
    const idle = reconcileStreams({ ...hls, transportUsed: "webrtc" }, stream("idle"), {
      state: "idle",
    });
    assert.equal(idle.stream.state, "idle");
    assert.equal(idle.transportUsed, null);
    assert.equal(describeScreen(idle, true).canChoose, true);
  });

  test("a side that did not answer neither invents nor ends a stream", () => {
    const running = {
      ...finishPoll(available(), stream("running")),
      transportUsed: "webrtc" as const,
    };
    assert.equal(reconcileStreams(running, null, null), running);
    assert.equal(reconcileStreams(running, "garbled", { state: "idle" }), running);
    assert.equal(reconcileStreams(running, stream("running"), null).transportUsed, "webrtc");
    assert.equal(reconcileStreams(available(), stream("running"), null).transportUsed, "hls");
  });

  test("a failure stays visible while the real state is shown", () => {
    const sent = consentedStart(confirming(), "tv-1").state;
    const lost = finishScreen(sent, {
      ok: false,
      failure: { code: "bridge_timeout", message: "the host did not answer in time" },
    });
    const shown = reconcileStreams(lost, stream("running"), { state: "idle" });
    assert.equal(shown.stream.state, "running");
    assert.equal(shown.pending, false);
    assert.equal(describeScreen(shown, true).errorText, "The host did not answer in time.");
    assert.equal(describeScreen(shown, true).canStop, true);
  });
});

describe("request deadlines", () => {
  test("each stream command outlives the shell's own bound on it", () => {
    // The shell bounds the bridge's screen commands at 15 s, and a WebRTC start at about 140 s.
    for (const command of [
      "bridge_list_screens",
      "bridge_get_screen_stream",
      "bridge_start_screen_stream",
      "bridge_stop_screen_stream",
      "bridge_get_screen_receiver_capabilities",
      "bridge_apply_display_layout",
    ]) {
      assert.ok(screenRequestDeadlineMs(command) > 15_000, command);
    }
    for (const command of ["start_webrtc_stream", "stop_webrtc_stream", "get_webrtc_stream"]) {
      assert.ok(screenRequestDeadlineMs(command) > 140_000, command);
    }
  });
});

describe("the extension menu", () => {
  test("extending is offered only with a free video output", () => {
    const none = withScreens(initialScreenState(), {
      screens: LISTED_SCREENS,
      extensionOutput: null,
    });
    const extend = choose(none, { mode: "extend" });
    const view = describeScreen(extend, true);
    assert.equal(view.extending, true);
    assert.equal(view.canExtend, false);
    assert.equal(view.canAsk, false);
    assert.match(view.statusText, /free video output enabled first/);
    assert.equal(describeScreen(choose(available(), { mode: "extend" }), true).canExtend, true);
  });

  test("the chosen size, refresh rate and side are sent with the consent", () => {
    const chosen = askConsent(
      choose(available(), { mode: "extend", size: "1280x720", refresh: 50, position: "left" }),
      true,
    );
    const request = consentedStart(chosen, "tv-1").request;
    assert.equal(request?.args.width, 1280);
    assert.equal(request?.args.refresh, 50);
    assert.equal(request?.args.position, "left");
    assert.equal(request?.args.x, 1920);
    assert.equal(request?.args.y, 0);
    const auto = consentedStart(askConsent(choose(available(), { mode: "extend" }), true), "tv-1");
    assert.equal(auto.request?.args.width, 1920);
    assert.equal(auto.request?.args.height, 1080);
  });

  test("dragging uses desktop coordinates and magnetically aligns nearby edges", () => {
    const extend = choose(available(), { mode: "extend", size: "1280x720" });
    const free = moveExtension(extend, 700, 250);
    assert.equal(free.choice.x, 700);
    assert.equal(free.choice.y, 250);
    const snapped = moveExtension(extend, 1900, 30);
    assert.equal(snapped.choice.x, 1920);
    assert.equal(snapped.choice.y, 0);
    const negative = moveExtension(extend, -1260, 370);
    assert.equal(negative.choice.x, -1280);
    assert.equal(negative.choice.y, 360);
  });

  test("an answer without an extension block reports no free output", () => {
    assert.deepEqual(parseListing({ screens: [] }), { screens: [], extensionOutput: null });
  });
});
