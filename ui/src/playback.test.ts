// Tests for the pure playback logic, run with Node's built-in runner (`npm test`). They use
// hand-written states and answers: they say nothing about a real Chromecast, only about how
// the remote reasons over what the backend reports.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  canDiscover,
  canRefresh,
  canSelect,
  finishDiscovery,
  finishStatusRead,
  initialState,
  refreshStatus,
  selectDevice,
  startDiscovery,
  type AppState,
  type Device,
  type DeviceStatus,
  type MediaStatus,
} from "./model.ts";
import {
  clampSeek,
  describeCommandFailure,
  describeCommandFeedback,
  describeControls,
  finishCommand,
  setSeekDraft,
  startSeek,
  startTransport,
  type CommandName,
  type CommandResult,
  type Confirmation,
} from "./playback.ts";

const DEVICE: Device = {
  id: "a",
  friendlyName: "Device a",
  host: "192.0.2.10",
  port: 8009,
  kind: "cast",
  modelName: null,
};

const MEDIA: MediaStatus = {
  playbackState: "playing",
  contentId: "http://media.example/movie.mp4",
  contentType: "video/mp4",
  title: "Movie",
  positionSeconds: 212,
  durationSeconds: 596.4,
  supportsSeek: true,
};

function statusWith(media: Partial<MediaStatus> | null, connection = "connected"): DeviceStatus {
  return {
    deviceId: "a",
    connection,
    observedAt: "2026-09-26T10:00:00+00:00",
    receiver: { appId: null, appName: null, volumeLevel: 0.5, muted: false, standby: null },
    media: media === null ? null : { ...MEDIA, ...media },
  };
}

// A selected device whose status read has completed with `status`.
function ready(status: DeviceStatus): AppState {
  const found = finishDiscovery(startDiscovery(initialState()), [DEVICE]);
  const { state, request } = selectDevice(found, "a");
  assert.ok(request);
  return finishStatusRead(state, request.requestId, { ok: true, status });
}

const playing = () => ready(statusWith({ playbackState: "playing" }));
const paused = () => ready(statusWith({ playbackState: "paused" }));

function result(
  command: CommandName,
  confirmation: Confirmation,
  observed: DeviceStatus | null,
  detail: string | null = null,
): CommandResult {
  return { command, deviceId: "a", confirmation, detail, observed };
}

function pendingPause(): { state: AppState; requestId: number } {
  const { state, request } = startTransport(playing(), "pause");
  assert.ok(request);
  return { state, requestId: request.requestId };
}

describe("controls offered by the observed state", () => {
  test("no device selected: nothing is offered", () => {
    assert.equal(describeControls(initialState()).visible, false);
  });

  test("a status that is loading or failed offers nothing", () => {
    const found = finishDiscovery(startDiscovery(initialState()), [DEVICE]);
    const { state: loading, request } = selectDevice(found, "a");
    assert.ok(request);
    const failed = finishStatusRead(loading, request.requestId, {
      ok: false,
      failure: { code: "device_unavailable", message: "asleep" },
    });

    assert.equal(describeControls(loading).visible, false);
    assert.equal(describeControls(failed).visible, false);
  });

  test("a disconnected device offers nothing", () => {
    assert.equal(describeControls(ready(statusWith(null, "disconnected"))).visible, false);
  });

  test("nothing playing offers nothing", () => {
    assert.equal(describeControls(ready(statusWith(null))).visible, false);
  });

  test("playing offers pause, stop and seek, and never play at the same time", () => {
    const controls = describeControls(playing());

    assert.equal(controls.visible, true);
    assert.deepEqual(controls.primary, { command: "pause", label: "Pause", busyLabel: "Pausing…" });
    assert.equal(controls.stop, true);
    assert.notEqual(controls.seek, null);
    assert.equal(controls.unavailable, null);
  });

  test("paused offers play instead of pause", () => {
    const controls = describeControls(paused());

    assert.deepEqual(controls.primary, { command: "play", label: "Play", busyLabel: "Starting…" });
    assert.equal(controls.stop, true);
  });

  test("buffering offers pause and stop", () => {
    const controls = describeControls(ready(statusWith({ playbackState: "buffering" })));

    assert.equal(controls.primary?.command, "pause");
    assert.equal(controls.stop, true);
  });

  test("an idle TV with media offers no transport control and says why", () => {
    const controls = describeControls(ready(statusWith({ playbackState: "idle" })));

    assert.equal(controls.visible, true);
    assert.equal(controls.primary, null);
    assert.equal(controls.stop, false);
    assert.equal(controls.seek, null);
    assert.match(controls.unavailable ?? "", /idle/);
  });

  test("an unreported playback state offers no transport control and says why", () => {
    const controls = describeControls(ready(statusWith({ playbackState: "unknown" })));

    assert.equal(controls.primary, null);
    assert.match(controls.unavailable ?? "", /didn't report its playback state/);
  });
});

describe("seek availability and the draft", () => {
  test("is offered with a seekable media, a duration and a position", () => {
    const seek = describeControls(playing()).seek;

    assert.deepEqual(seek, {
      max: 596,
      value: 212,
      drafting: false,
      valueText: "3:32",
      maxText: "9:56",
    });
  });

  const reasons: [string, Partial<MediaStatus>, RegExp][] = [
    ["media that cannot be seeked", { supportsSeek: false }, /isn't available/],
    ["a TV that did not say", { supportsSeek: null }, /didn't say whether/],
    ["an unknown duration", { durationSeconds: null }, /length/],
    ["a zero duration", { durationSeconds: 0 }, /length/],
    ["an unknown position", { positionSeconds: null }, /current position/],
  ];
  for (const [name, media, reason] of reasons) {
    test(`is not offered for ${name}, while play and stop still are`, () => {
      const controls = describeControls(ready(statusWith(media)));

      assert.equal(controls.seek, null);
      assert.match(controls.seekNote ?? "", reason);
      assert.equal(controls.primary?.command, "pause");
      assert.equal(controls.stop, true);
    });
  }

  test("a draft moves the control but never the position the TV reported", () => {
    const state = setSeekDraft(playing(), 300);

    const seek = describeControls(state).seek;
    assert.equal(seek?.value, 300);
    assert.equal(seek?.drafting, true);
    assert.equal(
      state.status.kind === "ready" ? state.status.status.media?.positionSeconds : null,
      212,
    );
  });

  test("a draft is rounded and kept inside the media", () => {
    assert.equal(setSeekDraft(playing(), 300.6).seekDraft, 301);
    assert.equal(setSeekDraft(playing(), -20).seekDraft, 0);
    assert.equal(setSeekDraft(playing(), 99999).seekDraft, 596);
    assert.equal(clampSeek(5.4, 10), 5);
  });

  test("a draft is ignored when seeking is not offered, invalid, or a command is running", () => {
    const unseekable = ready(statusWith({ supportsSeek: false }));
    assert.equal(setSeekDraft(unseekable, 30).seekDraft, null);
    assert.equal(setSeekDraft(playing(), Number.NaN).seekDraft, null);
    assert.equal(setSeekDraft(pendingPause().state, 30).seekDraft, null);
    assert.equal(setSeekDraft(initialState(), 30).seekDraft, null);
  });

  test("a draft can be cancelled", () => {
    assert.equal(setSeekDraft(setSeekDraft(playing(), 300), null).seekDraft, null);
  });

  test("sending a seek sends exactly the draft and marks it as pending, not as done", () => {
    const { state, request } = startSeek(setSeekDraft(playing(), 300));

    assert.deepEqual(
      request && {
        command: request.command,
        deviceId: request.deviceId,
        at: request.positionSeconds,
      },
      { command: "seek", deviceId: "a", at: 300 },
    );
    assert.deepEqual(state.command, {
      kind: "pending",
      requestId: request?.requestId,
      command: "seek",
      targetSeconds: 300,
      sound: null,
    });
    assert.equal(state.seekDraft, null);
    assert.equal(describeControls(state).seek?.value, 212);
  });

  test("nothing is sent without a draft, or for the position the TV already reports", () => {
    assert.equal(startSeek(playing()).request, null);

    const same = startSeek(setSeekDraft(playing(), 212.2));

    assert.equal(same.request, null);
    assert.equal(same.state.seekDraft, null);
    assert.equal(same.state.command.kind, "idle");
  });

  test("nothing is sent when seeking is not offered", () => {
    const state: AppState = { ...ready(statusWith({ supportsSeek: false })), seekDraft: 30 };

    assert.equal(startSeek(state).request, null);
  });
});

describe("sending transport commands", () => {
  test("play, pause and stop are sent for the selected device when the state offers them", () => {
    const pause = startTransport(playing(), "pause");
    const play = startTransport(paused(), "play");
    const stop = startTransport(playing(), "stop");

    assert.equal(pause.request?.command, "pause");
    assert.equal(pause.request?.deviceId, "a");
    assert.equal(play.request?.command, "play");
    assert.equal(stop.request?.command, "stop");
    assert.equal(pause.state.command.kind, "pending");
  });

  test("a control the observed state does not offer sends nothing", () => {
    assert.equal(startTransport(playing(), "play").request, null);
    assert.equal(startTransport(paused(), "pause").request, null);
    assert.equal(
      startTransport(ready(statusWith({ playbackState: "idle" })), "stop").request,
      null,
    );
    assert.equal(startTransport(ready(statusWith(null)), "stop").request, null);
    assert.equal(startTransport(initialState(), "pause").request, null);
  });

  test("a command in flight blocks every other request", () => {
    const { state } = pendingPause();

    assert.equal(startTransport(state, "stop").request, null);
    assert.equal(startTransport(state, "pause").request, null);
    assert.equal(startSeek({ ...state, seekDraft: 30 }).request, null);
    assert.equal(refreshStatus(state).request, null);
    assert.equal(selectDevice(state, "a").request, null);
    assert.equal(canDiscover(state), false);
    assert.equal(canRefresh(state), false);
    assert.equal(canSelect(state), false);
    assert.equal(describeControls(state).busy, true);
    assert.equal(describeControls(state).pendingCommand, "pause");
  });

  test("controls look busy, not enabled, while a discovery runs, and nothing is sent", () => {
    const searching = startDiscovery(playing());

    const controls = describeControls(searching);

    assert.equal(controls.visible, true);
    assert.equal(controls.busy, true);
    assert.equal(controls.pendingCommand, null);
    assert.equal(startTransport(searching, "pause").request, null);
    assert.equal(startTransport(searching, "stop").request, null);
    assert.equal(setSeekDraft(searching, 30).seekDraft, null);
  });

  test("controls are offered again once the discovery keeps the selection", () => {
    const found = finishDiscovery(startDiscovery(playing()), [DEVICE]);

    assert.equal(describeControls(found).busy, false);
    assert.notEqual(startTransport(found, "pause").request, null);
  });

  test("rapid repeated clicks start exactly one command", () => {
    let state = playing();
    let started = 0;

    for (const command of ["pause", "pause", "stop", "play", "pause", "stop"] as const) {
      const next = startTransport(state, command);
      if (next.request !== null) {
        started += 1;
      }
      state = next.state;
    }

    assert.equal(started, 1);
    assert.equal(state.nextRequestId, 3);
  });

  test("the controls are offered again once the command has been answered", () => {
    const { state, requestId } = pendingPause();

    const answered = finishCommand(state, requestId, {
      ok: false,
      failure: { code: "timeout", message: "slow" },
    });

    assert.equal(describeControls(answered).busy, false);
    assert.notEqual(startTransport(answered, "pause").request, null);
  });
});

describe("command outcomes", () => {
  test("a confirmed command shows the status the TV reported and flips the toggle", () => {
    const { state, requestId } = pendingPause();
    const observed = statusWith({ playbackState: "paused" });

    const next = finishCommand(state, requestId, {
      ok: true,
      result: result("pause", "confirmed", observed),
    });

    assert.deepEqual(next.status, { kind: "ready", status: observed });
    assert.equal(next.command.kind, "confirmed");
    assert.equal(describeControls(next).primary?.command, "play");
  });

  test("a sent but unconfirmed command keeps what the TV last reported and is not success", () => {
    const { state, requestId } = pendingPause();
    const stillPlaying = statusWith({ playbackState: "playing" });

    const next = finishCommand(state, requestId, {
      ok: true,
      result: result("pause", "unconfirmed", stillPlaying, "expected playback paused, but playing"),
    });

    assert.deepEqual(next.status, { kind: "ready", status: stillPlaying });
    assert.equal(next.command.kind, "sent");
    assert.equal(describeControls(next).primary?.command, "pause");
    const feedback = describeCommandFeedback(next);
    assert.equal(feedback.lines[0]?.tone, "warning");
    assert.match(feedback.lines[0]?.text ?? "", /sent, but the TV hasn't shown the change yet/);
    assert.equal(feedback.lines[0]?.announceOnly, false);
    assert.equal(feedback.detail, "expected playback paused, but playing");
    assert.equal(feedback.checkState, true);
  });

  test("an unconfirmed command with no reading leaves the previous status untouched", () => {
    const { state, requestId } = pendingPause();

    const next = finishCommand(state, requestId, {
      ok: true,
      result: result("pause", "unconfirmed", null),
    });

    assert.deepEqual(next.status, state.status);
    assert.equal(next.command.kind, "sent");
  });

  test("a command whose result was not checked is not worded as done", () => {
    const { state, requestId } = pendingPause();

    const next = finishCommand(state, requestId, {
      ok: true,
      result: result("pause", "not_checked", null),
    });

    const line = describeCommandFeedback(next).lines[0];
    assert.match(line?.text ?? "", /result wasn't checked/);
    assert.equal(line?.tone, "warning");
  });

  test("a command that was not sent keeps the status and reports its code", () => {
    const { state, requestId } = pendingPause();
    const failure = { code: "command_rejected", message: "no session" };

    const next = finishCommand(state, requestId, { ok: false, failure });

    assert.deepEqual(next.status, state.status);
    assert.deepEqual(next.command, { kind: "failed", command: "pause", failure });
  });

  test("a confirmed seek reports the position the TV reported, not the requested one", () => {
    const { state, request } = startSeek(setSeekDraft(playing(), 300));
    assert.ok(request);
    const observed = statusWith({ positionSeconds: 298 });

    const next = finishCommand(state, request.requestId, {
      ok: true,
      result: result("seek", "confirmed", observed),
    });

    assert.equal(describeControls(next).seek?.value, 298);
    assert.deepEqual(describeCommandFeedback(next).lines, [
      { text: "Position 4:58.", announceOnly: true, tone: "info" },
    ]);
  });

  test("an unconfirmed seek shows the position the TV still reports", () => {
    const { state, request } = startSeek(setSeekDraft(playing(), 300));
    assert.ok(request);
    const observed = statusWith({ positionSeconds: 212 });

    const next = finishCommand(state, request.requestId, {
      ok: true,
      result: result("seek", "unconfirmed", observed, "position is 212s"),
    });

    assert.equal(describeControls(next).seek?.value, 212);
    assert.match(describeCommandFeedback(next).lines[0]?.text ?? "", /^Seek sent, but/);
  });

  test("a late or duplicate answer never overwrites anything", () => {
    const { state, requestId } = pendingPause();
    const done = finishCommand(state, requestId, {
      ok: true,
      result: result("pause", "confirmed", statusWith({ playbackState: "paused" })),
    });

    assert.equal(
      finishCommand(done, requestId, { ok: false, failure: { code: "x", message: "y" } }),
      done,
    );
    assert.equal(
      finishCommand(state, requestId + 1, { ok: false, failure: { code: "x", message: "y" } }),
      state,
    );
    assert.equal(
      finishCommand(playing(), 1, { ok: false, failure: { code: "x", message: "y" } }).command.kind,
      "idle",
    );
  });

  test("an answer for another device or command is rejected, and its status is not shown", () => {
    const { state, requestId } = pendingPause();
    const foreign = { ...statusWith({ playbackState: "paused" }), deviceId: "b" };

    const otherDevice = finishCommand(state, requestId, {
      ok: true,
      result: { ...result("pause", "confirmed", foreign), deviceId: "b" },
    });
    const otherCommand = finishCommand(state, requestId, {
      ok: true,
      result: result("stop", "confirmed", null),
    });
    const foreignStatus = finishCommand(state, requestId, {
      ok: true,
      result: result("pause", "confirmed", foreign),
    });

    for (const next of [otherDevice, otherCommand, foreignStatus]) {
      assert.equal(next.command.kind, "failed");
      assert.deepEqual(next.status, state.status);
    }
  });

  test("a new read, selection or discovery clears the last command's notice and any draft", () => {
    const { state, requestId } = pendingPause();
    const sent: AppState = {
      ...finishCommand(state, requestId, {
        ok: true,
        result: result("pause", "unconfirmed", null),
      }),
      seekDraft: 40,
    };

    assert.equal(refreshStatus(sent).state.command.kind, "idle");
    assert.equal(refreshStatus(sent).state.seekDraft, null);
    assert.equal(startDiscovery(sent).command.kind, "idle");
    assert.equal(startDiscovery(sent).seekDraft, null);
  });

  test("a dropped selection clears the command too", () => {
    const { state, requestId } = pendingPause();
    const sent = finishCommand(state, requestId, {
      ok: true,
      result: result("pause", "unconfirmed", null),
    });

    const next = finishDiscovery(startDiscovery(sent), []);

    assert.equal(next.selected, null);
    assert.equal(next.command.kind, "idle");
  });
});

describe("feedback while a command runs", () => {
  test("announces what is being done; the button already shows it", () => {
    const cases: [CommandName, string][] = [
      ["play", "Starting playback…"],
      ["pause", "Pausing…"],
      ["stop", "Stopping…"],
    ];
    for (const [command, text] of cases) {
      const base = command === "play" ? paused() : playing();
      const { state } = startTransport(base, command as "play" | "pause" | "stop");

      assert.deepEqual(describeCommandFeedback(state).lines, [
        { text, announceOnly: true, tone: "info" },
      ]);
    }
  });

  test("names the requested position for a seek, as a request", () => {
    const { state } = startSeek(setSeekDraft(playing(), 300));

    const line = describeCommandFeedback(state).lines[0];
    assert.equal(line?.text, "Seeking to 5:00…");
    assert.equal(line?.announceOnly, false);
  });

  test("has nothing to say when idle", () => {
    assert.deepEqual(describeCommandFeedback(playing()).lines, []);
  });

  test("a confirmed transport command is announced but not drawn again", () => {
    const { state, requestId } = pendingPause();
    const next = finishCommand(state, requestId, {
      ok: true,
      result: result("pause", "confirmed", statusWith({ playbackState: "paused" })),
    });

    assert.deepEqual(describeCommandFeedback(next).lines, [
      { text: "Paused.", announceOnly: true, tone: "info" },
    ]);
  });
});

describe("failure wording", () => {
  const cases: [string, string, "check" | "discover"][] = [
    ["device_unavailable", "Can't reach this device", "check"],
    ["device_not_found", "This device needs to be found again", "discover"],
    ["command_rejected", "The TV refused the command", "check"],
    ["timeout", "The TV didn't answer in time", "check"],
    ["backend_unavailable", "The app's background service isn't available", "check"],
    ["bridge_timeout", "The app's background service stopped answering", "check"],
    ["bridge_transport", "The app's background service stopped answering", "check"],
    ["internal_error", "Couldn't send the command", "check"],
  ];
  for (const [code, title, recovery] of cases) {
    test(`${code} is worded plainly and recovers by ${recovery}, never by resending`, () => {
      const description = describeCommandFailure("pause", { code, message: "raw" });

      assert.equal(description.title, title);
      assert.equal(description.recovery, recovery);
      assert.equal(description.technical, `${code}: raw`);
    });
  }

  test("a timeout says the command may or may not have arrived", () => {
    for (const code of ["timeout", "bridge_timeout", "bridge_transport"]) {
      assert.match(
        describeCommandFailure("play", { code, message: "m" }).hint,
        /may or may not have reached it/,
      );
    }
  });

  test("a command that was certainly not sent says so", () => {
    for (const code of ["device_unavailable", "device_not_found", "backend_unavailable"]) {
      assert.match(describeCommandFailure("play", { code, message: "m" }).hint, /wasn't sent/);
    }
  });

  test("seek gets its own wording for an unsupported or invalid position", () => {
    assert.equal(
      describeCommandFailure("seek", { code: "unsupported_operation", message: "m" }).title,
      "Seeking isn't available for this media",
    );
    assert.equal(
      describeCommandFailure("pause", { code: "unsupported_operation", message: "m" }).title,
      "This media can't do that",
    );
    assert.equal(
      describeCommandFailure("seek", { code: "invalid_argument", message: "m" }).title,
      "That position isn't valid",
    );
  });

  test("the everyday wording never names an internal component", () => {
    const internal = /python|bridge|tauri|cast ?transport|control ?service|mcp|rust/i;
    const codes = [
      "device_unavailable",
      "device_not_found",
      "command_rejected",
      "unsupported_operation",
      "timeout",
      "invalid_argument",
      "backend_unavailable",
      "bridge_timeout",
      "bridge_transport",
      "internal_error",
      "weird",
    ];

    for (const command of ["play", "pause", "stop", "seek"] as const) {
      for (const code of codes) {
        const description = describeCommandFailure(command, {
          code,
          message: "Python control bridge failed",
        });

        assert.doesNotMatch(
          `${description.title} ${description.hint}`,
          internal,
          `${command}/${code}`,
        );
      }
    }
  });

  test("a failed command is described from its stored failure", () => {
    const { state, requestId } = pendingPause();
    const next = finishCommand(state, requestId, {
      ok: false,
      failure: { code: "command_rejected", message: "m" },
    });

    const feedback = describeCommandFeedback(next);

    assert.equal(feedback.failure?.title, "The TV refused the command");
    assert.deepEqual(feedback.lines, []);
  });
});
