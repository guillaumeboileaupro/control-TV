// Tests for the pure UI model, run with Node's built-in runner (`npm test`). They use
// hand-written device and status values: they say nothing about a real Chromecast, only
// about how the UI reasons over what the backend reports.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  canDiscover,
  canRefresh,
  describeFailure,
  describeStatus,
  failDiscovery,
  finishDiscovery,
  finishStatusRead,
  formatClock,
  initialState,
  refreshStatus,
  selectDevice,
  startDiscovery,
  toBridgeFailure,
  type AppState,
  type Device,
  type DeviceStatus,
} from "./model.ts";

function device(id: string, friendlyName = `Device ${id}`): Device {
  return { id, friendlyName, host: "192.0.2.10", port: 8009, kind: "cast", modelName: null };
}

function discovered(...devices: Device[]): AppState {
  return finishDiscovery(startDiscovery(initialState()), devices);
}

const FULL_STATUS: DeviceStatus = {
  deviceId: "a",
  connection: "connected",
  observedAt: "2026-09-26T10:00:00+00:00",
  receiver: {
    appId: "CC1AD845",
    appName: "Default Media Receiver",
    volumeLevel: 0.5,
    muted: false,
    standby: null,
  },
  media: {
    playbackState: "playing",
    contentId: "http://media.example/movie.mp4",
    contentType: "video/mp4",
    title: "Movie",
    positionSeconds: 65,
    durationSeconds: 7325,
    supportsSeek: true,
  },
};

const at = (iso: string): string => `at ${iso}`;

describe("selection", () => {
  test("starts with no selection and nothing to refresh", () => {
    const state = initialState();

    assert.equal(state.selected, null);
    assert.deepEqual(state.status, { kind: "none" });
    assert.equal(canRefresh(state), false);
  });

  test("selects by stable id and starts a status read for exactly that id", () => {
    const { state, request } = selectDevice(discovered(device("a"), device("b")), "b");

    assert.equal(state.selected?.id, "b");
    assert.deepEqual(request, { requestId: 1, deviceId: "b" });
    assert.deepEqual(state.status, { kind: "loading", requestId: 1 });
  });

  test("never selects by display name: two devices sharing a name stay distinct", () => {
    const state = discovered(device("a", "Living room"), device("b", "Living room"));

    const second = selectDevice(state, "b");

    assert.equal(second.state.selected?.id, "b");
    assert.equal(second.request?.deviceId, "b");
  });

  test("a display name is not an id and selects nothing", () => {
    const { state, request } = selectDevice(discovered(device("a", "Living room")), "Living room");

    assert.equal(request, null);
    assert.equal(state.selected, null);
  });

  test("an id the backend did not just report is ignored", () => {
    const { state, request } = selectDevice(discovered(device("a")), "zzz");

    assert.equal(request, null);
    assert.equal(state.selected, null);
  });

  test("changing the selection starts a new read for the new device", () => {
    const first = selectDevice(discovered(device("a"), device("b")), "a");
    const settled = finishStatusRead(first.state, 1, { ok: true, status: FULL_STATUS });

    const second = selectDevice(settled, "b");

    assert.equal(second.state.selected?.id, "b");
    assert.deepEqual(second.request, { requestId: 2, deviceId: "b" });
  });

  test("re-selecting the device already being read does not queue a second read", () => {
    const first = selectDevice(discovered(device("a")), "a");

    const again = selectDevice(first.state, "a");

    assert.equal(again.request, null);
    assert.equal(again.state, first.state);
  });
});

describe("status reads", () => {
  test("a successful read for the selected device becomes the shown status", () => {
    const { state } = selectDevice(discovered(device("a")), "a");

    const next = finishStatusRead(state, 1, { ok: true, status: FULL_STATUS });

    assert.deepEqual(next.status, { kind: "ready", status: FULL_STATUS });
  });

  test("a failed read is kept with its code and message", () => {
    const { state } = selectDevice(discovered(device("a")), "a");
    const failure = { code: "device_unavailable", message: "asleep" };

    const next = finishStatusRead(state, 1, { ok: false, failure });

    assert.deepEqual(next.status, { kind: "failed", failure });
  });

  test("a slow answer for the previous selection never overwrites the current one", () => {
    const first = selectDevice(discovered(device("a"), device("b")), "a");
    const second = selectDevice(first.state, "b");
    assert.equal(second.request?.requestId, 2);

    const afterStale = finishStatusRead(second.state, 1, { ok: true, status: FULL_STATUS });

    assert.deepEqual(afterStale.status, { kind: "loading", requestId: 2 });
    assert.equal(afterStale.selected?.id, "b");
  });

  test("an answer that arrives after the selection was dropped is ignored", () => {
    const first = selectDevice(discovered(device("a")), "a");
    const rediscovered = finishDiscovery(
      startDiscovery({ ...first.state, status: { kind: "none" } }),
      [],
    );

    const next = finishStatusRead(rediscovered, 1, { ok: true, status: FULL_STATUS });

    assert.equal(next, rediscovered);
  });

  test("a status that names a different device is rejected, not shown", () => {
    const { state } = selectDevice(discovered(device("b")), "b");

    const next = finishStatusRead(state, 1, { ok: true, status: FULL_STATUS });

    assert.equal(next.status.kind, "failed");
    if (next.status.kind === "failed") {
      assert.equal(next.status.failure.code, "unexpected_response");
    }
  });

  test("refresh needs a selection", () => {
    const { request } = refreshStatus(discovered(device("a")));

    assert.equal(request, null);
  });

  test("refresh re-reads the selected device once it is no longer loading", () => {
    const first = selectDevice(discovered(device("a")), "a");
    assert.equal(refreshStatus(first.state).request, null);
    const settled = finishStatusRead(first.state, 1, { ok: true, status: FULL_STATUS });

    const { state, request } = refreshStatus(settled);

    assert.deepEqual(request, { requestId: 2, deviceId: "a" });
    assert.deepEqual(state.status, { kind: "loading", requestId: 2 });
  });
});

describe("discovery", () => {
  test("starting a discovery empties the list but keeps the selection", () => {
    const selected = selectDevice(discovered(device("a")), "a").state;

    const running = startDiscovery(
      finishStatusRead(selected, 1, { ok: true, status: FULL_STATUS }),
    );

    assert.deepEqual(running.devices, []);
    assert.equal(running.selected?.id, "a");
    assert.equal(canRefresh(running), false);
  });

  test("the selection survives a rediscovery that reports the same stable id again", () => {
    const selected = selectDevice(discovered(device("a", "Old name")), "a").state;
    const settled = finishStatusRead(selected, 1, { ok: true, status: FULL_STATUS });

    const next = finishDiscovery(startDiscovery(settled), [device("a", "New name"), device("b")]);

    assert.equal(next.selected?.id, "a");
    assert.equal(next.selected?.friendlyName, "New name");
    assert.equal(next.status.kind, "ready");
    assert.equal(next.notice, null);
  });

  test("a selected device missing from the rediscovery is dropped with an explanation", () => {
    const selected = selectDevice(discovered(device("a")), "a").state;
    const settled = finishStatusRead(selected, 1, { ok: true, status: FULL_STATUS });

    const next = finishDiscovery(startDiscovery(settled), [device("b")]);

    assert.equal(next.selected, null);
    assert.deepEqual(next.status, { kind: "none" });
    assert.match(next.notice ?? "", /not found by the latest discovery/);
  });

  test("a failed discovery keeps the selection and reports the failure", () => {
    const selected = selectDevice(discovered(device("a")), "a").state;
    const settled = finishStatusRead(selected, 1, { ok: true, status: FULL_STATUS });
    const failure = { code: "discovery_failed", message: "no network" };

    const next = failDiscovery(startDiscovery(settled), failure);

    assert.equal(next.selected?.id, "a");
    assert.deepEqual(next.discovery, { kind: "failed", failure });
  });

  test("discovery and status reads are never offered at the same time", () => {
    const loading = selectDevice(discovered(device("a")), "a").state;
    assert.equal(canDiscover(loading), false);

    const settled = finishStatusRead(loading, 1, { ok: true, status: FULL_STATUS });
    assert.equal(canDiscover(settled), true);

    const running = startDiscovery(settled);
    assert.equal(canDiscover(running), false);
    assert.equal(selectDevice(running, "a").request, null);
  });
});

describe("failure wording", () => {
  test("unwraps the structured failure the shell sends", () => {
    assert.deepEqual(toBridgeFailure({ code: "timeout", message: "slow" }), {
      code: "timeout",
      message: "slow",
    });
  });

  test("wraps a plain string or Error rejection from Tauri itself", () => {
    assert.deepEqual(toBridgeFailure("command not found"), {
      code: "unexpected",
      message: "command not found",
    });
    assert.deepEqual(toBridgeFailure(new Error("boom")), { code: "unexpected", message: "boom" });
    assert.equal(toBridgeFailure(null).code, "unexpected");
  });

  const cases: [string, string][] = [
    ["backend_unavailable", "backend_unavailable"],
    ["bridge_transport", "backend_unavailable"],
    ["bridge_timeout", "backend_timeout"],
    ["device_unavailable", "device_unavailable"],
    ["device_not_found", "device_unknown"],
    ["timeout", "device_timeout"],
    ["internal_error", "error"],
    ["invalid_argument", "error"],
  ];
  for (const [code, kind] of cases) {
    test(`classifies ${code} as ${kind}`, () => {
      assert.equal(describeFailure({ code, message: "m" }).kind, kind);
    });
  }

  test("an unknown device tells the operator how to recover", () => {
    const description = describeFailure({ code: "device_not_found", message: "not discovered" });

    assert.match(description.detail, /Discover devices/);
  });

  test("an unclassified error names the action that failed", () => {
    const failure = { code: "internal_error", message: "boom" };

    assert.equal(describeFailure(failure).title, "Could not read the device status");
    assert.equal(describeFailure(failure, "discover devices").title, "Could not discover devices");
  });

  test("an unclassified error still shows its code", () => {
    assert.match(describeFailure({ code: "weird", message: "m" }).detail, /\(weird\)/);
  });
});

describe("status wording", () => {
  const rowsOf = (status: DeviceStatus) =>
    Object.fromEntries(describeStatus(status, at).rows.map((row) => [row.label, row.value]));

  test("a complete status shows receiver and media state", () => {
    const description = describeStatus(FULL_STATUS, at);

    assert.equal(description.incomplete, false);
    assert.deepEqual(description.notes, []);
    assert.deepEqual(rowsOf(FULL_STATUS), {
      Connection: "Connected",
      "Observed at": "at 2026-09-26T10:00:00+00:00",
      Application: "Default Media Receiver",
      Volume: "50%",
      Muted: "No",
      Playback: "Playing",
      Title: "Movie",
      Content: "http://media.example/movie.mp4",
      "Content type": "video/mp4",
      Position: "1:05 / 2:02:05",
    });
  });

  test("a partial status marks unreported fields instead of inventing values", () => {
    const partial: DeviceStatus = {
      ...FULL_STATUS,
      receiver: { appId: null, appName: null, volumeLevel: null, muted: null, standby: null },
      media: {
        playbackState: "unknown",
        contentId: null,
        contentType: null,
        title: null,
        positionSeconds: null,
        durationSeconds: null,
        supportsSeek: null,
      },
    };

    const description = describeStatus(partial, at);
    const rows = rowsOf(partial);

    assert.equal(description.incomplete, true);
    assert.equal(rows["Application"], "Not reported");
    assert.equal(rows["Volume"], "Not reported");
    assert.equal(rows["Muted"], "Not reported");
    assert.equal(rows["Playback"], "Unknown");
    assert.equal(rows["Content"], "Not reported");
    assert.equal(rows["Position"], "Not reported");
    assert.match(description.notes.join(" "), /Status incomplete/);
  });

  test("a connected device without a receiver block is incomplete", () => {
    const description = describeStatus({ ...FULL_STATUS, receiver: null, media: null }, at);

    assert.equal(description.incomplete, true);
  });

  test("no media session is its own state, not an incompleteness", () => {
    const description = describeStatus({ ...FULL_STATUS, media: null }, at);

    assert.equal(description.incomplete, false);
    assert.deepEqual(description.notes, ["No active media session reported."]);
    assert.equal(
      description.rows.some((row) => row.label === "Playback"),
      false,
    );
  });

  test("a device that is not connected shows no receiver or media rows", () => {
    const description = describeStatus(
      { ...FULL_STATUS, connection: "disconnected", receiver: null, media: null },
      at,
    );

    assert.deepEqual(
      description.rows.map((row) => row.label),
      ["Connection", "Observed at"],
    );
    assert.equal(description.incomplete, false);
    assert.match(description.notes[0] ?? "", /only available on a connected device/);
  });

  test("standby is shown only when the device reported it", () => {
    const reported = { ...FULL_STATUS, receiver: { ...FULL_STATUS.receiver!, standby: true } };

    assert.equal(rowsOf(reported)["Standby"], "Yes");
    assert.equal("Standby" in rowsOf(FULL_STATUS), false);
  });

  test("muted true and false are distinct from not reported", () => {
    const muted = { ...FULL_STATUS, receiver: { ...FULL_STATUS.receiver!, muted: true } };

    assert.equal(rowsOf(muted)["Muted"], "Yes");
    assert.equal(rowsOf(FULL_STATUS)["Muted"], "No");
  });

  test("a receiver app id is shown when there is no app name", () => {
    const idOnly = {
      ...FULL_STATUS,
      receiver: { ...FULL_STATUS.receiver!, appName: null, appId: "CC1AD845" },
    };

    assert.equal(rowsOf(idOnly)["Application"], "CC1AD845");
  });

  test("formats clock values", () => {
    assert.equal(formatClock(0), "0:00");
    assert.equal(formatClock(59.9), "0:59");
    assert.equal(formatClock(3600), "1:00:00");
    assert.equal(formatClock(null), "Not reported");
    assert.equal(formatClock(-1), "Not reported");
    assert.equal(formatClock(Number.POSITIVE_INFINITY), "Not reported");
  });
});
