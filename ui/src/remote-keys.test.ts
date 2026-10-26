// Every key of the window's TV remote, the original ones and the Google TV keys added since, goes
// through the same path (`npm test`): it is labelled, it starts one request carrying exactly its
// own key, every key is unavailable while it is sent, and its outcome names it. The keys are read
// from the real markup, so a key added to the window without these behaviours fails here.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, test } from "node:test";

import { DEVICE, readyWith, statusOf } from "./fixtures.ts";
import type { AppState, Device } from "./model.ts";
import {
  describeRemote,
  finishRemote,
  KEY_LABELS,
  remoteInvocation,
  startRemote,
  type RemoteAnswer,
  type RemoteKeyName,
  type RemoteRequest,
} from "./remote.ts";

const HTML = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const WINDOW_KEYS = [
  ...new Set([...HTML.matchAll(/data-remote-key="([a-z0-9_]+)"/g)].map((m) => m[1])),
];
const TV: Device = { ...DEVICE, androidTvRemote: true };

function withTv(): AppState {
  return { ...readyWith(statusOf()), tvAppsAvailable: true, selected: TV, devices: [TV] };
}

function isKey(name: string): name is RemoteKeyName {
  return Object.prototype.hasOwnProperty.call(KEY_LABELS, name);
}

function sent(key: RemoteKeyName): RemoteAnswer {
  const silent = { isOn: null, currentApp: null, volume: null };
  return {
    ok: true,
    result: {
      result: {
        key,
        deviceId: TV.id,
        confirmation: "not_checked",
        detail: "",
        before: silent,
        after: silent,
      },
    },
  };
}

describe("every key of the TV remote", () => {
  test("the window holds the whole Google TV remote, each key labelled", () => {
    for (const key of ["assistant", "input", "settings", "info", "guide", "digit_0", "digit_9"]) {
      assert.ok(WINDOW_KEYS.includes(key), `${key} is in the window`);
    }
    assert.deepEqual([...WINDOW_KEYS].sort(), Object.keys(KEY_LABELS).sort());
    for (const key of WINDOW_KEYS) {
      assert.ok(isKey(key) && KEY_LABELS[key].trim() !== "", `${key} has a label`);
    }
  });

  test("each key sends one request with exactly its own key", () => {
    for (const key of WINDOW_KEYS) {
      assert.ok(isKey(key));
      const started = startRemote(withTv(), { kind: "key", key });
      assert.notEqual(started.request, null, key);
      assert.deepEqual(remoteInvocation(started.request as RemoteRequest), {
        command: "bridge_press_remote_key",
        args: { deviceId: TV.id, key },
      });
    }
  });

  test("while one key is sent, every key is unavailable and only that one shows it", () => {
    for (const key of WINDOW_KEYS) {
      assert.ok(isKey(key));
      const started = startRemote(withTv(), { kind: "key", key });
      const remote = describeRemote(started.state);
      assert.equal(remote.pendingKey, key);
      assert.equal(remote.keysEnabled, false, `${key}: the other keys wait`);
      assert.equal(startRemote(started.state, { kind: "key", key: "home" }).request, null);
    }
  });

  test("each key's outcome names it and gives the keys back", () => {
    for (const key of WINDOW_KEYS) {
      assert.ok(isKey(key));
      const started = startRemote(withTv(), { kind: "key", key });
      const request = started.request as RemoteRequest;
      const done = describeRemote(finishRemote(started.state, request.requestId, sent(key)));
      assert.ok(
        done.outcome?.text.startsWith(`${KEY_LABELS[key]} was sent`),
        `${key}: ${done.outcome?.text}`,
      );
      assert.equal(done.pendingKey, null);
      assert.equal(done.keysEnabled, true);
    }
  });

  test("no key is offered for a TV without the remote channel", () => {
    const castOnly = { ...withTv(), selected: { ...DEVICE, androidTvRemote: false } };
    for (const key of WINDOW_KEYS) {
      assert.ok(isKey(key));
      assert.equal(startRemote(castOnly, { kind: "key", key }).request, null, key);
    }
  });
});
