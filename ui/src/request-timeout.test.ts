import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { settleWithin, type RequestTimers } from "./request-timeout.ts";

function controlledTimers(): {
  timers: RequestTimers;
  expire(): void;
  cleared(): number[];
} {
  let callback: (() => void) | null = null;
  const cleared: number[] = [];
  return {
    timers: {
      set(next): number {
        callback = next;
        return 17;
      },
      clear(handle): void {
        cleared.push(handle);
      },
    },
    expire(): void {
      assert.notEqual(callback, null);
      callback?.();
    },
    cleared: () => cleared,
  };
}

describe("the UI request deadline", () => {
  test("turns a lost host response into the structured timeout the UI already handles", async () => {
    const clock = controlledTimers();
    const never = new Promise<string>(() => undefined);
    const result = settleWithin(never, 25_000, clock.timers);

    clock.expire();

    await assert.rejects(result, {
      code: "bridge_timeout",
      message: "The application host did not return the command result before its deadline.",
    });
    assert.deepEqual(clock.cleared(), [17]);
  });

  test("preserves a host answer and cancels its deadline", async () => {
    const clock = controlledTimers();

    assert.equal(await settleWithin(Promise.resolve("answer"), 25_000, clock.timers), "answer");
    assert.deepEqual(clock.cleared(), [17]);
  });

  test("preserves a host rejection and cancels its deadline", async () => {
    const clock = controlledTimers();
    const failure = { code: "device_unavailable", message: "offline" };

    await assert.rejects(settleWithin(Promise.reject(failure), 25_000, clock.timers), failure);
    assert.deepEqual(clock.cleared(), [17]);
  });
});
