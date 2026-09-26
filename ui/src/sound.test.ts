// Tests for the pure volume and mute logic, run with Node's built-in runner (`npm test`). They use
// hand-written states and answers: they say nothing about a real Chromecast, only about how the
// remote reasons over what the backend reports.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { commandResult, DEVICE, readyWith, statusOf } from "./fixtures.ts";
import {
  finishDiscovery,
  finishStatusRead,
  initialState,
  refreshStatus,
  selectDevice,
  startDiscovery,
  type AppState,
} from "./model.ts";
import {
  commandArguments,
  describeCommandFailure,
  describeCommandFeedback,
  describeControls,
  finishCommand,
  setSeekDraft,
  startSeek,
  startTransport,
} from "./playback.ts";
import { clampPercent, describeSound, setVolumeDraft, startMute, startVolume } from "./sound.ts";

const at45 = () => readyWith(statusOf());
const muted = () => readyWith(statusOf({ receiver: { muted: true } }));

function pendingVolume(percent: number): { state: AppState; requestId: number } {
  const { state, request } = startVolume(setVolumeDraft(at45(), percent));
  assert.ok(request);
  return { state, requestId: request.requestId };
}

function pendingMute(from: AppState = at45()): { state: AppState; requestId: number } {
  const { state, request } = startMute(from);
  assert.ok(request);
  return { state, requestId: request.requestId };
}

describe("what the observed state offers", () => {
  test("no device selected: nothing is offered", () => {
    assert.equal(describeSound(initialState()).visible, false);
  });

  test("a status that is loading or failed offers nothing", () => {
    const found = finishDiscovery(startDiscovery(initialState()), [DEVICE]);
    const { state: loading, request } = selectDevice(found, "a");
    assert.ok(request);
    const failed = finishStatusRead(loading, request.requestId, {
      ok: false,
      failure: { code: "device_unavailable", message: "asleep" },
    });

    assert.equal(describeSound(loading).visible, false);
    assert.equal(describeSound(failed).visible, false);
  });

  test("a disconnected device offers nothing", () => {
    assert.equal(
      describeSound(
        readyWith(statusOf({ connection: "disconnected", receiver: null, media: null })),
      ).visible,
      false,
    );
  });

  test("sound is offered with nothing playing: volume does not depend on media", () => {
    const sound = describeSound(readyWith(statusOf({ media: null })));

    assert.equal(sound.visible, true);
    assert.notEqual(sound.volume, null);
    assert.notEqual(sound.mute, null);
  });

  test("the level and mute state the TV reported are what is shown", () => {
    const sound = describeSound(at45());

    assert.deepEqual(sound.volume, { value: 45, observed: 45, phase: "observed" });
    assert.deepEqual(sound.mute, { muted: false, action: "Mute" });
    assert.equal(sound.readout, "Volume 45%");
    assert.equal(sound.note, null);
    assert.equal(sound.busy, false);
    assert.equal(sound.pendingCommand, null);
  });

  test("a level is worded in whole percent, rounded", () => {
    const cases: [number, number][] = [
      [0, 0],
      [0.004, 0],
      [0.456, 46],
      [0.994, 99],
      [1, 100],
    ];
    for (const [level, percent] of cases) {
      const sound = describeSound(readyWith(statusOf({ receiver: { volumeLevel: level } })));
      assert.equal(sound.volume?.observed, percent, String(level));
      assert.equal(sound.readout, `Volume ${percent}%`);
    }
  });

  test("a muted TV offers unmute, says muted, and keeps the level it reported", () => {
    const sound = describeSound(muted());

    assert.deepEqual(sound.mute, { muted: true, action: "Unmute" });
    assert.equal(sound.volume?.observed, 45);
    assert.equal(sound.readout, "Volume 45% · Muted");
  });

  test("a volume the TV did not report offers no slider and says so, never zero", () => {
    const sound = describeSound(readyWith(statusOf({ receiver: { volumeLevel: null } })));

    assert.equal(sound.volume, null);
    assert.notEqual(sound.mute, null);
    assert.equal(sound.note, "The TV didn't report its volume.");
    assert.equal(sound.readout, "");
  });

  test("a mute state the TV did not report offers no mute button and says so, never 'not muted'", () => {
    const sound = describeSound(readyWith(statusOf({ receiver: { muted: null } })));

    assert.equal(sound.mute, null);
    assert.equal(sound.volume?.observed, 45);
    assert.equal(sound.note, "The TV didn't report whether it is muted.");
    assert.equal(sound.readout, "Volume 45%");
  });

  test("neither reported: the group explains itself and offers nothing to press", () => {
    const partial = describeSound(
      readyWith(statusOf({ receiver: { volumeLevel: null, muted: null } })),
    );
    const none = describeSound(readyWith(statusOf({ receiver: null })));

    for (const sound of [partial, none]) {
      assert.equal(sound.visible, true);
      assert.equal(sound.volume, null);
      assert.equal(sound.mute, null);
      assert.match(sound.note ?? "", /volume or mute state/);
      assert.equal(sound.readout, "");
    }
  });

  test("a muted TV with no reported level still says it is muted", () => {
    const sound = describeSound(
      readyWith(statusOf({ receiver: { volumeLevel: null, muted: true } })),
    );

    assert.equal(sound.readout, "Muted");
    assert.equal(sound.mute?.action, "Unmute");
  });
});

describe("the volume draft", () => {
  test("moving the control composes a draft and sends nothing", () => {
    const next = setVolumeDraft(at45(), 60);

    assert.equal(next.volumeDraft, 60);
    assert.equal(next.command.kind, "idle");
    const sound = describeSound(next);
    assert.deepEqual(sound.volume, { value: 60, observed: 45, phase: "draft" });
    assert.equal(sound.readout, "Set volume to 60%");
  });

  test("a draft never changes the level the TV reported", () => {
    const next = setVolumeDraft(at45(), 90);

    assert.equal(next.status.kind, "ready");
    assert.equal(describeSound(next).volume?.observed, 45);
    assert.equal(
      next.status.kind === "ready" ? next.status.status.receiver?.volumeLevel : null,
      0.45,
    );
  });

  test("a draft is a whole percent within 0 to 100", () => {
    assert.equal(setVolumeDraft(at45(), 150).volumeDraft, 100);
    assert.equal(setVolumeDraft(at45(), -5).volumeDraft, 0);
    assert.equal(setVolumeDraft(at45(), 60.4).volumeDraft, 60);
    assert.equal(setVolumeDraft(at45(), 60.6).volumeDraft, 61);
    assert.equal(clampPercent(Number.MAX_VALUE), 100);
  });

  test("a level that is not a number is ignored", () => {
    const state = at45();

    assert.equal(setVolumeDraft(state, Number.NaN), state);
    assert.equal(setVolumeDraft(state, Number.POSITIVE_INFINITY), state);
  });

  test("the level the TV already reports is no draft, even after moving away and back", () => {
    assert.equal(setVolumeDraft(at45(), 45).volumeDraft, null);
    assert.equal(setVolumeDraft(setVolumeDraft(at45(), 70), 45).volumeDraft, null);
    assert.equal(
      describeSound(setVolumeDraft(setVolumeDraft(at45(), 70), 45)).volume?.phase,
      "observed",
    );
  });

  test("a draft can be cancelled", () => {
    assert.equal(setVolumeDraft(setVolumeDraft(at45(), 60), null).volumeDraft, null);
    const idleState = at45();
    assert.equal(setVolumeDraft(idleState, null), idleState);
  });

  test("nothing is composed without a device or without a reported volume", () => {
    assert.equal(setVolumeDraft(initialState(), 30).volumeDraft, null);
    const unknown = readyWith(statusOf({ receiver: { volumeLevel: null } }));
    assert.equal(setVolumeDraft(unknown, 30).volumeDraft, null);
  });

  test("nothing is composed while any request is in flight", () => {
    const { state: sending } = pendingVolume(60);
    const { state: pausing } = startTransport(at45(), "pause") as { state: AppState };
    const discovering = startDiscovery(at45());
    const reading = refreshStatus(at45()).state;

    for (const busy of [sending, pausing, discovering, reading]) {
      assert.equal(setVolumeDraft(busy, 30).volumeDraft, null);
    }
  });
});

describe("sending a volume", () => {
  test("sends exactly the draft, once, and marks it as sending, not as done", () => {
    const { state, request } = startVolume(setVolumeDraft(at45(), 60));

    assert.deepEqual(
      request && { command: request.command, deviceId: request.deviceId, sound: request.sound },
      { command: "set_volume", deviceId: "a", sound: { kind: "volume", percent: 60 } },
    );
    assert.deepEqual(state.command, {
      kind: "pending",
      requestId: request?.requestId,
      command: "set_volume",
      targetSeconds: null,
      sound: { kind: "volume", percent: 60 },
    });
    assert.equal(state.volumeDraft, null);
    const sound = describeSound(state);
    assert.deepEqual(sound.volume, { value: 60, observed: 45, phase: "sending" });
    assert.equal(sound.readout, "Setting volume to 60%…");
    assert.equal(sound.busy, true);
    assert.equal(sound.pendingCommand, "set_volume");
  });

  test("the request carries the level as a fraction the control layer takes", () => {
    const cases: [number, number][] = [
      [0, 0],
      [7, 0.07],
      [29, 0.29],
      [60, 0.6],
      [100, 1],
    ];
    for (const [percent, level] of cases) {
      const { request } = startVolume(setVolumeDraft(at45(), percent));
      assert.ok(request);
      assert.deepEqual(commandArguments(request), { deviceId: "a", level });
    }
  });

  test("nothing is sent without a draft, or for the level the TV already reports", () => {
    assert.equal(startVolume(at45()).request, null);
    const forced: AppState = { ...at45(), volumeDraft: 45 };
    const next = startVolume(forced);
    assert.equal(next.request, null);
    assert.equal(next.state.volumeDraft, null);
  });

  test("nothing is sent while another request is in flight, and the draft is kept", () => {
    const drafted = setVolumeDraft(at45(), 60);
    const discovering: AppState = { ...drafted, discovery: { kind: "running" } };

    const next = startVolume(discovering);

    assert.equal(next.request, null);
    assert.equal(next.state.volumeDraft, 60);
  });

  test("a second send while one is in flight sends nothing", () => {
    const { state } = pendingVolume(60);

    assert.equal(startVolume({ ...state, volumeDraft: 70 }).request, null);
    assert.equal(startMute(state).request, null);
    assert.equal(startTransport(state, "pause").request, null);
  });

  test("any command that starts drops a volume being composed", () => {
    const drafted = setVolumeDraft(at45(), 60);

    const pause = startTransport(drafted, "pause");
    const mute = startMute(drafted);

    assert.equal(pause.state.volumeDraft, null);
    assert.equal(mute.state.volumeDraft, null);
  });

  test("a volume command drops a seek being composed, and neither draft outlives a new selection", () => {
    const both = setSeekDraft(setVolumeDraft(at45(), 60), 300);
    assert.equal(both.volumeDraft, 60);
    assert.equal(both.seekDraft, 300);

    assert.equal(startVolume(both).state.seekDraft, null);
    assert.equal(startSeek(both).state.volumeDraft, null);
    const reselected = selectDevice(finishDiscovery(both, [DEVICE]), "a").state;
    assert.equal(reselected.volumeDraft, null);
    assert.equal(reselected.seekDraft, null);
  });

  test("a new discovery and a refresh drop the draft", () => {
    const drafted = setVolumeDraft(at45(), 60);

    assert.equal(startDiscovery(drafted).volumeDraft, null);
    assert.equal(refreshStatus(drafted).state.volumeDraft, null);
  });

  test("a selection dropped by a discovery drops the draft too", () => {
    const drafted = setVolumeDraft(at45(), 60);

    const next = finishDiscovery({ ...drafted, discovery: { kind: "running" } }, []);

    assert.equal(next.volumeDraft, null);
    assert.equal(next.selected, null);
  });
});

describe("mute", () => {
  test("a press asks for the opposite of the state the TV reported, as an absolute state", () => {
    const mute = startMute(at45());
    const unmute = startMute(muted());

    assert.deepEqual(mute.request?.sound, { kind: "mute", muted: true });
    assert.deepEqual(unmute.request?.sound, { kind: "mute", muted: false });
    assert.equal(mute.request?.command, "set_muted");
    assert.deepEqual(commandArguments(mute.request!), { deviceId: "a", muted: true });
    assert.deepEqual(commandArguments(unmute.request!), { deviceId: "a", muted: false });
  });

  test("while it runs, the TV's reported state is still what is shown", () => {
    const { state } = pendingMute();

    const sound = describeSound(state);

    assert.equal(sound.mute?.muted, false);
    assert.equal(sound.mute?.action, "Mute");
    assert.equal(sound.readout, "Muting…");
    assert.equal(sound.pendingCommand, "set_muted");
    assert.equal(sound.busy, true);
  });

  test("unmuting is worded as unmuting", () => {
    const { state } = pendingMute(muted());

    assert.equal(describeSound(state).readout, "Unmuting…");
  });

  test("a second click while the first runs sends nothing (no double command)", () => {
    const { state } = pendingMute();

    assert.equal(startMute(state).request, null);
    assert.equal(startMute(startMute(state).state).request, null);
  });

  test("nothing is sent when the mute state was not reported, or nothing is selected", () => {
    assert.equal(startMute(readyWith(statusOf({ receiver: { muted: null } }))).request, null);
    assert.equal(startMute(initialState()).request, null);
  });

  test("nothing is sent while a discovery or a read runs", () => {
    assert.equal(startMute({ ...at45(), discovery: { kind: "running" } }).request, null);
    assert.equal(startMute(refreshStatus(at45()).state).request, null);
  });

  test("mute does not touch the level", () => {
    const { state } = pendingMute();

    assert.equal(describeSound(state).volume?.observed, 45);
    assert.equal(describeSound(state).volume?.phase, "observed");
  });
});

describe("what a command's answer changes", () => {
  test("a confirmed volume is shown as the level the TV reported", () => {
    const { state, requestId } = pendingVolume(60);
    const observed = statusOf({ receiver: { volumeLevel: 0.6 } });

    const next = finishCommand(state, requestId, {
      ok: true,
      result: commandResult("set_volume", "confirmed", observed),
    });

    assert.equal(next.command.kind, "confirmed");
    assert.deepEqual(describeSound(next).volume, { value: 60, observed: 60, phase: "observed" });
    assert.equal(describeSound(next).busy, false);
    assert.deepEqual(describeCommandFeedback(next).lines, [
      { text: "Volume 60%.", announceOnly: true, tone: "info" },
    ]);
  });

  test("a confirmed mute is shown as the state the TV reported, and offers the opposite", () => {
    const { state, requestId } = pendingMute();
    const observed = statusOf({ receiver: { muted: true } });

    const next = finishCommand(state, requestId, {
      ok: true,
      result: commandResult("set_muted", "confirmed", observed),
    });

    assert.deepEqual(describeSound(next).mute, { muted: true, action: "Unmute" });
    assert.equal(describeSound(next).readout, "Volume 45% · Muted");
    assert.deepEqual(describeCommandFeedback(next).lines, [
      { text: "Muted.", announceOnly: true, tone: "info" },
    ]);
  });

  test("a confirmed unmute is worded as unmuted", () => {
    const { state, requestId } = pendingMute(muted());

    const next = finishCommand(state, requestId, {
      ok: true,
      result: commandResult("set_muted", "confirmed", statusOf({ receiver: { muted: false } })),
    });

    assert.equal(describeCommandFeedback(next).lines[0]?.text, "Unmuted.");
  });

  test("a volume the TV did not show is sent but not confirmed, and the slider returns to what it reports", () => {
    const { state, requestId } = pendingVolume(60);

    const next = finishCommand(state, requestId, {
      ok: true,
      result: commandResult(
        "set_volume",
        "unconfirmed",
        statusOf(),
        "command sent; expected volume near 0.6, but volume is 0.45",
      ),
    });

    assert.equal(next.command.kind, "sent");
    assert.deepEqual(describeSound(next).volume, { value: 45, observed: 45, phase: "observed" });
    const feedback = describeCommandFeedback(next);
    assert.equal(feedback.lines[0]?.text, "Volume sent, but the TV reports 45%, not 60%.");
    assert.equal(feedback.lines[0]?.tone, "warning");
    assert.equal(feedback.lines[0]?.announceOnly, false);
    assert.equal(feedback.checkState, true);
    assert.equal(feedback.detail, "command sent; expected volume near 0.6, but volume is 0.45");
  });

  test("a receiver that settles on a level of its own is told as such, not as unchanged", () => {
    const { state, requestId } = pendingVolume(50);

    const next = finishCommand(state, requestId, {
      ok: true,
      result: commandResult(
        "set_volume",
        "unconfirmed",
        statusOf({ receiver: { volumeLevel: 0.47 } }),
      ),
    });

    assert.equal(describeSound(next).volume?.observed, 47);
    assert.equal(
      describeCommandFeedback(next).lines[0]?.text,
      "Volume sent, but the TV reports 47%, not 50%.",
    );
  });

  test("a volume whose answer carried no reading keeps the last status and says it is not shown yet", () => {
    const { state, requestId } = pendingVolume(60);

    const next = finishCommand(state, requestId, {
      ok: true,
      result: commandResult("set_volume", "unconfirmed", null, "could not read the TV status"),
    });

    assert.deepEqual(next.status, state.status);
    assert.equal(
      describeCommandFeedback(next).lines[0]?.text,
      "Volume sent, but the TV hasn't shown the change yet.",
    );
  });

  test("a volume that was not checked is not worded as done", () => {
    const { state, requestId } = pendingVolume(60);

    const next = finishCommand(state, requestId, {
      ok: true,
      result: commandResult("set_volume", "not_checked", null),
    });

    assert.equal(next.command.kind, "sent");
    assert.equal(
      describeCommandFeedback(next).lines[0]?.text,
      "Volume sent. Its result wasn't checked.",
    );
    assert.equal(describeSound(next).volume?.observed, 45);
  });

  test("a mute the TV did not show is sent but not confirmed and still offers what the TV reported", () => {
    const { state, requestId } = pendingMute();

    const next = finishCommand(state, requestId, {
      ok: true,
      result: commandResult("set_muted", "unconfirmed", statusOf(), "expected mute on"),
    });

    assert.equal(next.command.kind, "sent");
    assert.deepEqual(describeSound(next).mute, { muted: false, action: "Mute" });
    assert.equal(
      describeCommandFeedback(next).lines[0]?.text,
      "Mute sent, but the TV hasn't shown the change yet.",
    );
  });

  test("an unmute that was not shown or not checked says unmute", () => {
    for (const [confirmation, text] of [
      ["unconfirmed", "Unmute sent, but the TV hasn't shown the change yet."],
      ["not_checked", "Unmute sent. Its result wasn't checked."],
    ] as const) {
      const { state, requestId } = pendingMute(muted());

      const next = finishCommand(state, requestId, {
        ok: true,
        result: commandResult("set_muted", confirmation, null),
      });

      assert.equal(describeCommandFeedback(next).lines[0]?.text, text);
    }
  });

  test("a command that was not sent leaves the reported state alone and returns the controls", () => {
    const { state, requestId } = pendingVolume(60);

    const next = finishCommand(state, requestId, {
      ok: false,
      failure: { code: "device_unavailable", message: "asleep" },
    });

    assert.deepEqual(next.status, state.status);
    assert.equal(next.command.kind, "failed");
    assert.deepEqual(describeSound(next).volume, { value: 45, observed: 45, phase: "observed" });
    assert.equal(describeSound(next).busy, false);
    assert.equal(describeCommandFeedback(next).failure?.title, "Can't reach this device");
  });

  test("every way a sound command can fail is told apart, in plain words", () => {
    const expected: [string, string, string][] = [
      ["device_unavailable", "Can't reach this device", "wasn't sent"],
      ["command_rejected", "The TV refused the command", "didn't accept"],
      ["unsupported_operation", "The TV can't do that", "wasn't sent"],
      ["timeout", "The TV didn't answer in time", "may or may not"],
      ["bridge_timeout", "The app's background service stopped answering", "may or may not"],
      ["backend_unavailable", "The app's background service isn't available", "wasn't sent"],
    ];
    for (const [code, title, hint] of expected) {
      const description = describeCommandFailure("set_volume", { code, message: "m" });
      assert.equal(description.title, title, code);
      assert.match(description.hint, new RegExp(hint), code);
    }
    assert.equal(
      describeCommandFailure("set_volume", { code: "invalid_argument", message: "m" }).title,
      "That volume isn't valid",
    );
    assert.equal(
      describeCommandFailure("set_muted", { code: "invalid_argument", message: "m" }).title,
      "That request isn't valid",
    );
  });

  test("a sound refusal never mentions media it has nothing to do with", () => {
    for (const command of ["set_volume", "set_muted"] as const) {
      const rejected = describeCommandFailure(command, { code: "command_rejected", message: "m" });
      const unsupported = describeCommandFailure(command, {
        code: "unsupported_operation",
        message: "m",
      });

      assert.doesNotMatch(`${rejected.hint} ${unsupported.title}`, /media/i);
    }
  });

  test("a timeout never offers to resend on its own", () => {
    const { state, requestId } = pendingMute();

    const next = finishCommand(state, requestId, {
      ok: false,
      failure: { code: "timeout", message: "no answer" },
    });

    const failure = describeCommandFeedback(next).failure;
    assert.equal(failure?.recovery, "check");
    assert.match(failure?.hint ?? "", /Check the current state before sending it again/);
    assert.equal(describeSound(next).mute?.action, "Mute");
  });

  test("an answer for another command or device is not trusted", () => {
    const { state, requestId } = pendingVolume(60);

    const wrongCommand = finishCommand(state, requestId, {
      ok: true,
      result: commandResult("set_muted", "confirmed", statusOf({ receiver: { muted: true } })),
    });
    const wrongDevice = finishCommand(state, requestId, {
      ok: true,
      result: { ...commandResult("set_volume", "confirmed", statusOf()), deviceId: "b" },
    });

    for (const next of [wrongCommand, wrongDevice]) {
      assert.equal(next.command.kind, "failed");
      assert.equal(describeSound(next).volume?.observed, 45);
      assert.equal(describeCommandFeedback(next).failure?.title, "Couldn't send the command");
    }
  });

  test("a late or unknown answer is ignored", () => {
    const { state, requestId } = pendingVolume(60);

    const ignored = finishCommand(state, requestId + 5, {
      ok: true,
      result: commandResult(
        "set_volume",
        "confirmed",
        statusOf({ receiver: { volumeLevel: 0.6 } }),
      ),
    });

    assert.equal(ignored, state);
  });

  test("checking the state again resynchronises the level and the mute state with the TV", () => {
    const { state, requestId } = pendingVolume(60);
    const sent = finishCommand(state, requestId, {
      ok: true,
      result: commandResult("set_volume", "unconfirmed", statusOf()),
    });

    const refresh = refreshStatus(sent);
    assert.ok(refresh.request);
    const settled = finishStatusRead(refresh.state, refresh.request.requestId, {
      ok: true,
      status: statusOf({ receiver: { volumeLevel: 0.6, muted: true } }),
    });

    assert.equal(settled.command.kind, "idle");
    assert.equal(settled.volumeDraft, null);
    assert.deepEqual(describeSound(settled).volume, { value: 60, observed: 60, phase: "observed" });
    assert.deepEqual(describeSound(settled).mute, { muted: true, action: "Unmute" });
    assert.equal(describeSound(settled).readout, "Volume 60% · Muted");
  });

  test("the TV changing on its own is what is shown after the next read, never a remembered value", () => {
    const first = at45();
    const refresh = refreshStatus(first);
    assert.ok(refresh.request);

    const next = finishStatusRead(refresh.state, refresh.request.requestId, {
      ok: true,
      status: statusOf({ receiver: { volumeLevel: 0.12, muted: true } }),
    });

    assert.equal(describeSound(next).volume?.observed, 12);
    assert.equal(describeSound(next).mute?.muted, true);
  });
});

describe("busy: what looks unavailable", () => {
  test("controls look unavailable, not enabled, while a discovery or a command runs", () => {
    const discovering: AppState = { ...at45(), discovery: { kind: "running" } };
    const commanding = pendingVolume(60).state;

    assert.equal(describeSound(at45()).busy, false);
    for (const state of [discovering, commanding]) {
      assert.equal(describeSound(state).visible, true);
      assert.equal(describeSound(state).busy, true);
    }
  });

  test("while the status is being read the sound group is not drawn at all", () => {
    const reading = refreshStatus(at45()).state;

    assert.equal(reading.status.kind, "loading");
    assert.equal(describeSound(reading).visible, false);
  });

  test("a playback command in flight makes the sound controls unavailable, and the reverse", () => {
    const { state: pausing } = startTransport(at45(), "pause") as { state: AppState };
    assert.equal(describeSound(pausing).busy, true);
    assert.equal(describeSound(pausing).pendingCommand, "pause");
    assert.equal(describeSound(pausing).readout, "Volume 45%");

    const { state: muting } = pendingMute();
    assert.equal(describeControls(muting).busy, true);
    assert.equal(describeControls(muting).pendingCommand, "set_muted");
  });
});

describe("feedback while a sound command runs", () => {
  test("progress is announced to assistive technology, and drawn by the readout instead", () => {
    const volume = describeCommandFeedback(pendingVolume(60).state);
    const mute = describeCommandFeedback(pendingMute().state);
    const unmute = describeCommandFeedback(pendingMute(muted()).state);

    assert.deepEqual(volume.lines, [
      { text: "Setting volume to 60%…", announceOnly: true, tone: "info" },
    ]);
    assert.deepEqual(mute.lines, [{ text: "Muting…", announceOnly: true, tone: "info" }]);
    assert.deepEqual(unmute.lines, [{ text: "Unmuting…", announceOnly: true, tone: "info" }]);
  });

  test("the everyday wording never names an internal component", () => {
    const internal = /python|tauri|bridge|chromecast|pychromecast|transport|rust/i;
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
    for (const command of ["set_volume", "set_muted"] as const) {
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
    const shown = [
      describeSound(at45()),
      describeSound(muted()),
      describeSound(readyWith(statusOf({ receiver: null }))),
      describeSound(readyWith(statusOf({ receiver: { volumeLevel: null } }))),
    ];
    for (const sound of shown) {
      assert.doesNotMatch(`${sound.readout} ${sound.note ?? ""}`, internal);
    }
  });
});
