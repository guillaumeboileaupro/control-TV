// Tests for how the volume slider and the mute button drive the state, run with Node's built-in
// runner (`npm test`). The timer is a fake clock the test advances by hand, so what is asserted
// here is exactly the number of commands a gesture produces and when, not something timing luck
// could change. Nothing here talks to a device.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { commandResult, readyWith, statusOf } from "./fixtures.ts";
import {
  createSettler,
  createSoundController,
  VOLUME_SETTLE_KEYBOARD_MS,
  VOLUME_SETTLE_POINTER_MS,
  type Timers,
} from "./interaction.ts";
import { finishDiscovery, refreshStatus, startDiscovery, type AppState } from "./model.ts";
import {
  commandArguments,
  finishCommand,
  startTransport,
  type CommandRequest,
} from "./playback.ts";
import { describeSound } from "./sound.ts";

// The wait used when nothing has said what drives the control: the keyboard's.
const SETTLE = VOLUME_SETTLE_KEYBOARD_MS;

class FakeTimers implements Timers {
  now = 0;
  private next = 1;
  private readonly tasks = new Map<number, { at: number; callback: () => void }>();

  set(callback: () => void, delayMs: number): number {
    const handle = this.next++;
    this.tasks.set(handle, { at: this.now + delayMs, callback });
    return handle;
  }

  clear(handle: number): void {
    this.tasks.delete(handle);
  }

  // Runs every task that falls due within `ms`, in order, as time passes.
  advance(ms: number): void {
    const end = this.now + ms;
    for (;;) {
      const due = [...this.tasks.entries()]
        .filter(([, task]) => task.at <= end)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (due === undefined) {
        break;
      }
      this.tasks.delete(due[0]);
      this.now = due[1].at;
      due[1].callback();
    }
    this.now = end;
  }

  get waiting(): number {
    return this.tasks.size;
  }
}

function harness(initial: AppState = readyWith(statusOf())) {
  let state = initial;
  const sent: CommandRequest[] = [];
  const timers = new FakeTimers();
  const controller = createSoundController({
    getState: () => state,
    setState: (next) => {
      state = next;
    },
    run: (request) => {
      sent.push(request);
    },
    timers,
  });
  return {
    controller,
    sent,
    timers,
    get state(): AppState {
      return state;
    },
    set state(next: AppState) {
      state = next;
    },
    // The TV answers the command that is in flight with `observed`.
    answer(confirmation: "confirmed" | "unconfirmed", observed = statusOf()): void {
      const request = sent[sent.length - 1];
      assert.ok(request);
      state = finishCommand(state, request.requestId, {
        ok: true,
        result: commandResult(request.command, confirmation, observed),
      });
    },
  };
}

describe("the settle timer", () => {
  test("runs once, a fixed delay after the last touch", () => {
    const timers = new FakeTimers();
    let runs = 0;
    const settler = createSettler(400, timers, () => {
      runs += 1;
    });

    settler.touch();
    timers.advance(300);
    settler.touch();
    timers.advance(300);
    settler.touch();
    timers.advance(399);
    assert.equal(runs, 0);
    timers.advance(1);
    assert.equal(runs, 1);
    timers.advance(10_000);
    assert.equal(runs, 1);
  });

  test("a cancelled wait never runs, and a later touch starts a new one", () => {
    const timers = new FakeTimers();
    let runs = 0;
    const settler = createSettler(400, timers, () => {
      runs += 1;
    });

    settler.touch();
    settler.cancel();
    timers.advance(1_000);
    assert.equal(runs, 0);
    settler.cancel();
    settler.touch();
    timers.advance(400);
    assert.equal(runs, 1);
  });

  test("never holds more than one wait, however often it is touched", () => {
    const timers = new FakeTimers();
    const settler = createSettler(400, timers, () => undefined);

    for (let i = 0; i < 500; i += 1) {
      settler.touch();
      assert.equal(timers.waiting, 1);
    }
  });
});

describe("the volume slider", () => {
  test("a drag of many events sends one command, with the last level, shortly after release", () => {
    const h = harness();

    h.controller.volumePointerDown();
    for (let percent = 46; percent <= 80; percent += 1) {
      h.controller.volumeInput(percent);
      h.timers.advance(10);
    }
    h.controller.volumeChange();
    assert.equal(h.sent.length, 0, "nothing is sent while the control moves or just after release");
    h.timers.advance(VOLUME_SETTLE_POINTER_MS - 1);
    assert.equal(h.sent.length, 0);
    h.timers.advance(1);

    assert.equal(h.sent.length, 1);
    assert.deepEqual(h.sent[0]?.sound, { kind: "volume", percent: 80 });
    assert.deepEqual(commandArguments(h.sent[0]!), { deviceId: "a", level: 0.8 });
    assert.equal(h.state.command.kind, "pending");
  });

  test("moving the control only composes a draft: the reported level is untouched until the TV says", () => {
    const h = harness();

    h.controller.volumeInput(70);

    assert.equal(h.sent.length, 0);
    assert.equal(describeSound(h.state).volume?.observed, 45);
    assert.equal(describeSound(h.state).volume?.value, 70);
    assert.equal(describeSound(h.state).readout, "Set volume to 70%");
  });

  test("a held key, with its initial pause and then fast repeats, sends one command", () => {
    const h = harness();

    h.controller.volumeKeyDown();
    h.controller.volumeInput(46);
    h.controller.volumeChange();
    h.timers.advance(660); // the pause before a held key starts repeating
    assert.equal(h.sent.length, 0, "the first press is not sent before the repeats begin");
    for (let step = 2; step <= 40; step += 1) {
      h.controller.volumeKeyDown();
      h.controller.volumeInput(45 + step);
      h.controller.volumeChange();
      h.timers.advance(33);
    }
    assert.equal(h.sent.length, 0, "not while the key is still repeating");
    h.timers.advance(SETTLE);

    assert.equal(h.sent.length, 1);
    assert.deepEqual(h.sent[0]?.sound, { kind: "volume", percent: 85 });
  });

  test("the keyboard wait outlasts a typical key-repeat pause, and a pointer's does not need to", () => {
    assert.ok(VOLUME_SETTLE_KEYBOARD_MS > 660);
    assert.ok(VOLUME_SETTLE_POINTER_MS < VOLUME_SETTLE_KEYBOARD_MS);
  });

  test("what drives the control chooses the wait, and the last driver wins", () => {
    const h = harness();

    h.controller.volumePointerDown();
    h.controller.volumeKeyDown();
    h.controller.volumeInput(50);
    h.controller.volumeChange();
    h.timers.advance(VOLUME_SETTLE_POINTER_MS);
    assert.equal(h.sent.length, 0, "the keyboard drove it last, so the longer wait applies");
    h.timers.advance(SETTLE - VOLUME_SETTLE_POINTER_MS);
    assert.equal(h.sent.length, 1);
  });

  test("with no driver declared, the safe longer wait is used", () => {
    const h = harness();

    h.controller.volumeInput(50);
    h.controller.volumeChange();
    h.timers.advance(VOLUME_SETTLE_POINTER_MS);
    assert.equal(h.sent.length, 0);
    h.timers.advance(SETTLE);
    assert.equal(h.sent.length, 1);
  });

  test("a single key press sends one command", () => {
    const h = harness();

    h.controller.volumeInput(46);
    h.controller.volumeChange();
    h.timers.advance(SETTLE);

    assert.equal(h.sent.length, 1);
    assert.deepEqual(h.sent[0]?.sound, { kind: "volume", percent: 46 });
  });

  test("moving again before it settles postpones the command instead of sending twice", () => {
    const h = harness();

    h.controller.volumeInput(60);
    h.controller.volumeChange();
    h.timers.advance(300);
    h.controller.volumeInput(65);
    h.timers.advance(SETTLE + 100);
    assert.equal(h.sent.length, 0, "the control was moving again, so the wait was forgotten");
    h.controller.volumeChange();
    h.timers.advance(SETTLE);

    assert.equal(h.sent.length, 1);
    assert.deepEqual(h.sent[0]?.sound, { kind: "volume", percent: 65 });
  });

  test("moving away and back to the level the TV reports sends nothing", () => {
    const h = harness();

    h.controller.volumeInput(80);
    h.controller.volumeInput(45);
    h.controller.volumeChange();
    h.timers.advance(SETTLE * 3);

    assert.equal(h.sent.length, 0);
    assert.equal(h.state.command.kind, "idle");
  });

  test("a change with nothing composed sends nothing", () => {
    const h = harness();

    h.controller.volumeChange();
    h.timers.advance(SETTLE * 3);

    assert.equal(h.sent.length, 0);
  });

  test("while the command runs, the control cannot queue another one", () => {
    const h = harness();
    h.controller.volumeInput(60);
    h.controller.volumeChange();
    h.timers.advance(SETTLE);
    assert.equal(h.sent.length, 1);

    for (let percent = 61; percent <= 100; percent += 1) {
      h.controller.volumeInput(percent);
      h.controller.volumeChange();
      h.timers.advance(50);
    }
    h.timers.advance(SETTLE * 3);

    assert.equal(h.sent.length, 1, "no second command while the first is in flight");
    assert.equal(h.state.volumeDraft, null);
  });

  test("once the first command is answered, the next gesture sends the next command", () => {
    const h = harness();
    h.controller.volumeInput(60);
    h.controller.volumeChange();
    h.timers.advance(SETTLE);
    h.answer("confirmed", statusOf({ receiver: { volumeLevel: 0.6 } }));

    h.controller.volumeInput(30);
    h.controller.volumeChange();
    h.timers.advance(SETTLE);

    assert.equal(h.sent.length, 2);
    assert.deepEqual(h.sent[1]?.sound, { kind: "volume", percent: 30 });
  });

  test("continuous movement is bounded: one command per settled gesture, never one per event", () => {
    const h = harness();
    let level = 45;
    let events = 0;

    for (let gesture = 0; gesture < 3; gesture += 1) {
      for (let step = 0; step < 100; step += 1) {
        level = level >= 100 ? 10 : level + 1;
        h.controller.volumeInput(level);
        h.controller.volumeChange();
        events += 2;
        h.timers.advance(20);
        assert.ok(h.timers.waiting <= 1, "at most one timer is ever waiting");
      }
      h.timers.advance(SETTLE);
      h.answer("confirmed", statusOf({ receiver: { volumeLevel: level / 100 } }));
    }

    assert.equal(events, 600);
    assert.equal(h.sent.length, 3);
  });

  test("a draft dropped by a discovery or a refresh is not sent when the wait ends", () => {
    for (const drop of [
      (state: AppState) => startDiscovery(state),
      (state: AppState) => refreshStatus(state).state,
    ]) {
      const h = harness();
      h.controller.volumeInput(60);
      h.controller.volumeChange();
      h.state = drop(h.state);

      h.timers.advance(SETTLE * 3);

      assert.equal(h.sent.length, 0);
    }
  });

  test("a draft for a device that is no longer listed is not sent", () => {
    const h = harness();
    h.controller.volumeInput(60);
    h.controller.volumeChange();
    h.state = finishDiscovery({ ...h.state, discovery: { kind: "running" } }, []);

    h.timers.advance(SETTLE * 3);

    assert.equal(h.sent.length, 0);
  });

  test("nothing is composed or sent when the TV did not report a volume", () => {
    const h = harness(readyWith(statusOf({ receiver: { volumeLevel: null } })));

    h.controller.volumeInput(60);
    h.controller.volumeChange();
    h.timers.advance(SETTLE * 3);

    assert.equal(h.sent.length, 0);
    assert.equal(h.state.volumeDraft, null);
  });

  test("nothing is composed while a discovery runs", () => {
    const h = harness();
    h.state = { ...h.state, discovery: { kind: "running" } };

    h.controller.volumeInput(60);
    h.controller.volumeChange();
    h.timers.advance(SETTLE * 3);

    assert.equal(h.sent.length, 0);
  });

  test("a level the TV did not confirm is not resent on its own", () => {
    const h = harness();
    h.controller.volumeInput(60);
    h.controller.volumeChange();
    h.timers.advance(SETTLE);
    h.answer("unconfirmed");

    h.timers.advance(60_000);

    assert.equal(h.sent.length, 1);
    assert.equal(h.state.command.kind, "sent");
  });
});

describe("the mute button", () => {
  test("a press sends one command asking for the opposite of what the TV reported", () => {
    const h = harness();

    h.controller.mute();

    assert.equal(h.sent.length, 1);
    assert.deepEqual(h.sent[0]?.sound, { kind: "mute", muted: true });
    assert.deepEqual(commandArguments(h.sent[0]!), { deviceId: "a", muted: true });
  });

  test("rapid presses send one command, not one per press", () => {
    const h = harness();

    for (let press = 0; press < 10; press += 1) {
      h.controller.mute();
    }

    assert.equal(h.sent.length, 1);
  });

  test("after the TV confirms, the next press asks for the opposite again", () => {
    const h = harness();
    h.controller.mute();
    h.answer("confirmed", statusOf({ receiver: { muted: true } }));

    h.controller.mute();

    assert.equal(h.sent.length, 2);
    assert.deepEqual(h.sent[1]?.sound, { kind: "mute", muted: false });
  });

  test("after a mute the TV did not show, the button still offers what the TV reported", () => {
    const h = harness();
    h.controller.mute();
    h.answer("unconfirmed");

    h.controller.mute();

    assert.deepEqual(h.sent[1]?.sound, { kind: "mute", muted: true });
  });

  test("nothing is sent when the mute state was not reported", () => {
    const h = harness(readyWith(statusOf({ receiver: { muted: null } })));

    h.controller.mute();

    assert.equal(h.sent.length, 0);
  });

  test("pressing mute while a volume is being composed sends the mute and drops the draft", () => {
    const h = harness();
    h.controller.volumeInput(70);
    h.controller.volumeChange();

    h.controller.mute();
    h.timers.advance(SETTLE * 3);

    assert.equal(h.sent.length, 1);
    assert.equal(h.sent[0]?.command, "set_muted");
    assert.equal(h.state.volumeDraft, null);
  });

  test("a press refused because something runs leaves the volume being composed alone", () => {
    const h = harness();
    h.controller.volumeInput(70);
    h.controller.volumeChange();
    h.state = { ...h.state, discovery: { kind: "running" } };
    h.controller.mute();

    assert.equal(h.sent.length, 0);
    assert.equal(h.state.volumeDraft, 70);
  });

  test("a mute and a playback command never run together", () => {
    const h = harness();
    h.controller.mute();

    const pause = startTransport(h.state, "pause");

    assert.equal(pause.request, null);
    assert.equal(h.sent.length, 1);
  });
});

describe("the arguments a command travels with", () => {
  test("only what the operator asked for is sent, next to the stable device id", () => {
    const base = { requestId: 1, deviceId: "a", positionSeconds: null, sound: null };

    assert.deepEqual(commandArguments({ ...base, command: "play" }), { deviceId: "a" });
    assert.deepEqual(commandArguments({ ...base, command: "pause" }), { deviceId: "a" });
    assert.deepEqual(commandArguments({ ...base, command: "stop" }), { deviceId: "a" });
    assert.deepEqual(commandArguments({ ...base, command: "seek", positionSeconds: 42 }), {
      deviceId: "a",
      positionSeconds: 42,
    });
    assert.deepEqual(
      commandArguments({ ...base, command: "set_volume", sound: { kind: "volume", percent: 35 } }),
      { deviceId: "a", level: 0.35 },
    );
    assert.deepEqual(
      commandArguments({ ...base, command: "set_muted", sound: { kind: "mute", muted: false } }),
      { deviceId: "a", muted: false },
    );
  });

  test("a sound command without its target carries null, which the control layer refuses", () => {
    const base = { requestId: 1, deviceId: "a", positionSeconds: null, sound: null };

    assert.deepEqual(commandArguments({ ...base, command: "set_volume" }), {
      deviceId: "a",
      level: null,
    });
    assert.deepEqual(commandArguments({ ...base, command: "set_muted" }), {
      deviceId: "a",
      muted: null,
    });
  });
});
