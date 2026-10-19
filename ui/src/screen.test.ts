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
  follows,
  initialScreenState,
  parseScreens,
  parseStream,
  startStop,
  withScreens,
  type ScreenPanelState,
} from "./screen.ts";

const LISTED: JsonValue = {
  screens: [
    { name: "eDP-1", width: 1920, height: 1080, primary: true },
    { name: "HDMI-1", width: 2560, height: 1440, primary: false },
  ],
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

function available(): ScreenPanelState {
  return withScreens(initialScreenState(), parseScreens(LISTED));
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
    assert.equal(parseScreens(null), null);
    assert.equal(parseScreens({ screens: "eDP-1" }), null);
    assert.equal(parseScreens({ screens: [1] }), null);
    assert.equal(parseScreens({ screens: [{ name: "eDP-1", width: "wide", height: 1 }] }), null);
  });

  test("a refreshed list keeps the chosen screen while it is still there", () => {
    const chosen = choose(available(), { screen: "HDMI-1" });
    assert.equal(withScreens(chosen, parseScreens(LISTED)).choice.screen, "HDMI-1");
    assert.equal(withScreens(chosen, []).choice.screen, null);
  });

  test("with no screen to duplicate, extending is still offered", () => {
    const none = withScreens(initialScreenState(), []);
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
    const extend = describeScreen(askConsent(choose(available(), { mode: "extend" }), true), true);
    assert.match(extend.consentText, /virtual monitor is added/);
  });

  test("a changed choice or a cancel withdraws the consent step", () => {
    assert.equal(choose(confirming(), { fps: 60 }).confirming, false);
    assert.equal(cancelConsent(confirming()).confirming, false);
  });

  test("the confirmed start carries the choice and the consent", () => {
    const chosen = askConsent(choose(available(), { size: "1280x720", fps: 25 }), true);
    const next = consentedStart(chosen, "tv-1");
    assert.deepEqual(next.request, {
      command: "bridge_start_screen_stream",
      args: {
        deviceId: "tv-1",
        mode: "duplicate",
        screen: "eDP-1",
        width: 1280,
        height: 720,
        fps: 25,
        consent: true,
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
    assert.equal(view.errorText, "the stream ended by itself");
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
    assert.equal(describeScreen(refused, true).errorText, "a screen stream is already running");
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
});
