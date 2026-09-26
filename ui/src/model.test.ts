// Tests for the pure UI model, run with Node's built-in runner (`npm test`). They use
// hand-written device and status values: they say nothing about a real Chromecast, only
// about how the UI reasons over what the backend reports.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  canDiscover,
  canRefresh,
  canSelect,
  describeDevice,
  describeDevicesMessage,
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

  test("changing the selection starts a new read once the previous one has settled", () => {
    const first = selectDevice(discovered(device("a"), device("b")), "a");
    const settled = finishStatusRead(first.state, 1, { ok: true, status: FULL_STATUS });

    const second = selectDevice(settled, "b");

    assert.equal(second.state.selected?.id, "b");
    assert.deepEqual(second.request, { requestId: 2, deviceId: "b" });
  });

  test("no selection is offered while a status read is in flight", () => {
    const loading = selectDevice(discovered(device("a"), device("b")), "a");

    const other = selectDevice(loading.state, "b");
    const same = selectDevice(loading.state, "a");

    assert.equal(canSelect(loading.state), false);
    assert.equal(other.request, null);
    assert.equal(other.state, loading.state);
    assert.equal(same.request, null);
    assert.equal(same.state, loading.state);
  });

  test("rapid alternating clicks leave exactly one read in flight", () => {
    let { state } = selectDevice(discovered(device("a"), device("b")), "a");
    let started = 1;

    for (const id of ["b", "a", "b", "a", "b"]) {
      const next = selectDevice(state, id);
      if (next.request !== null) {
        started += 1;
      }
      state = next.state;
    }

    assert.equal(started, 1);
    assert.equal(state.selected?.id, "a");
    assert.deepEqual(state.status, { kind: "loading", requestId: 1 });
    assert.equal(state.nextRequestId, 2);
  });

  test("selection is offered again once the read settles, whatever its outcome", () => {
    const loading = selectDevice(discovered(device("a"), device("b")), "a").state;
    const failure = { code: "timeout", message: "slow" };

    assert.equal(canSelect(finishStatusRead(loading, 1, { ok: true, status: FULL_STATUS })), true);
    assert.equal(canSelect(finishStatusRead(loading, 1, { ok: false, failure })), true);
  });

  test("selection is not offered while a discovery runs", () => {
    const running = startDiscovery(discovered(device("a")));

    assert.equal(canSelect(running), false);
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

  test("an answer for an older read never overwrites the read now in flight", () => {
    const first = selectDevice(discovered(device("a"), device("b")), "a");
    const failure = { code: "timeout", message: "slow" };
    const settled = finishStatusRead(first.state, 1, { ok: false, failure });
    const second = selectDevice(settled, "b");
    assert.equal(second.request?.requestId, 2);

    const afterLate = finishStatusRead(second.state, 1, { ok: true, status: FULL_STATUS });

    assert.deepEqual(afterLate.status, { kind: "loading", requestId: 2 });
    assert.equal(afterLate.selected?.id, "b");
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

describe("device wording", () => {
  test("a device is described by its kind, without a network address", () => {
    const tv = device("a", "Living room");
    const speaker = { ...device("b", "Kitchen"), kind: "audio" };

    assert.deepEqual(describeDevice(tv, [tv, speaker]), {
      name: "Living room",
      subtitle: "Cast device",
    });
    assert.equal(describeDevice(speaker, [tv, speaker]).subtitle, "Speaker");
    assert.equal(describeDevice({ ...tv, kind: "group" }, [tv]).subtitle, "Group");
    assert.equal(describeDevice({ ...tv, kind: "weird" }, [tv]).subtitle, "Device");
  });

  test("the address is shown only when two devices share a name", () => {
    const first = device("a", "Living room");
    const second = { ...device("b", "Living room"), host: "192.0.2.11" };
    const other = device("c", "Kitchen");

    assert.equal(
      describeDevice(first, [first, second, other]).subtitle,
      "Cast device · 192.0.2.10:8009",
    );
    assert.equal(
      describeDevice(second, [first, second, other]).subtitle,
      "Cast device · 192.0.2.11:8009",
    );
    assert.equal(describeDevice(other, [first, second, other]).subtitle, "Cast device");
  });
});

describe("device picker message", () => {
  const texts = (state: AppState) =>
    describeDevicesMessage(state).lines.map((line) => [line.text, line.announceOnly]);

  test("prompts before the first search", () => {
    assert.deepEqual(texts(initialState()), [
      ["Find your TV or Chromecast on this network.", false],
    ]);
  });

  test("says nothing while a search is running", () => {
    assert.deepEqual(describeDevicesMessage(startDiscovery(initialState())), {
      lines: [],
      failure: null,
    });
  });

  test("announces, without drawing, how many devices were found", () => {
    assert.deepEqual(texts(discovered(device("a"))), [["1 device found.", true]]);
    assert.deepEqual(texts(discovered(device("a"), device("b"))), [["2 devices found.", true]]);
  });

  test("tells the operator when nothing was found", () => {
    const [[message, announceOnly]] = texts(discovered());

    assert.match(String(message), /No devices found/);
    assert.equal(announceOnly, false);
  });

  test("explains a dropped selection before anything else", () => {
    const selected = selectDevice(discovered(device("a")), "a").state;
    const settled = finishStatusRead(selected, 1, { ok: true, status: FULL_STATUS });

    const next = finishDiscovery(startDiscovery(settled), [device("b")]);

    const lines = describeDevicesMessage(next).lines;
    assert.match(lines[0]?.text ?? "", /not found by the latest discovery/);
    assert.equal(lines[0]?.announceOnly, false);
    assert.equal(lines[1]?.text, "1 device found.");
  });

  test("a kept selection carries no notice", () => {
    const selected = selectDevice(discovered(device("a")), "a").state;
    const settled = finishStatusRead(selected, 1, { ok: true, status: FULL_STATUS });

    const next = finishDiscovery(startDiscovery(settled), [device("a")]);

    assert.deepEqual(texts(next), [["1 device found.", true]]);
  });

  test("reports a failed discovery as a failure, not as text", () => {
    const failure = { code: "discovery_failed", message: "no route" };

    const message = describeDevicesMessage(failDiscovery(startDiscovery(initialState()), failure));

    assert.deepEqual(message, { lines: [], failure });
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

  const cases: [string, string, "retry" | "discover"][] = [
    ["backend_unavailable", "backend_unavailable", "retry"],
    ["bridge_transport", "backend_unavailable", "retry"],
    ["bridge_timeout", "backend_timeout", "retry"],
    ["device_unavailable", "device_unavailable", "retry"],
    ["device_not_found", "device_unknown", "discover"],
    ["timeout", "device_timeout", "retry"],
    ["internal_error", "error", "retry"],
    ["invalid_argument", "error", "retry"],
  ];
  for (const [code, kind, recovery] of cases) {
    test(`classifies ${code} as ${kind} and recovers by ${recovery}`, () => {
      const description = describeFailure({ code, message: "m" });

      assert.equal(description.kind, kind);
      assert.equal(description.recovery, recovery);
    });
  }

  test("an unknown device is recovered by finding devices again", () => {
    const description = describeFailure({ code: "device_not_found", message: "not discovered" });

    assert.match(description.hint, /Find devices again/);
  });

  test("a failed discovery points at the network", () => {
    const description = describeFailure({ code: "discovery_failed", message: "no route" });

    assert.equal(description.title, "Couldn't find devices");
    assert.match(description.hint, /network/);
    assert.equal(description.recovery, "retry");
  });

  test("an unclassified error names the action that failed", () => {
    const failure = { code: "internal_error", message: "boom" };

    assert.equal(describeFailure(failure).title, "Couldn't read this device's status");
    assert.equal(describeFailure(failure, "find devices").title, "Couldn't find devices");
  });

  test("keeps the raw code and message for the diagnostic view only", () => {
    const description = describeFailure({ code: "weird", message: "m" });

    assert.equal(description.technical, "weird: m");
    assert.doesNotMatch(`${description.title} ${description.hint}`, /weird/);
  });

  test("the everyday wording never names an internal component", () => {
    const internal = /python|bridge|tauri|cast ?transport|control ?service|mcp|rust/i;
    const codes = [
      "backend_unavailable",
      "bridge_transport",
      "bridge_timeout",
      "device_unavailable",
      "device_not_found",
      "timeout",
      "internal_error",
    ];

    for (const code of codes) {
      const description = describeFailure({ code, message: "Python control bridge failed" });

      assert.doesNotMatch(`${description.title} ${description.hint}`, internal, code);
    }
  });
});

describe("status wording", () => {
  test("a complete status leads with what is playing", () => {
    const description = describeStatus(FULL_STATUS, at);

    assert.equal(description.connected, true);
    assert.equal(description.connectionLabel, "Connected");
    assert.equal(description.observedAt, "at 2026-09-26T10:00:00+00:00");
    assert.equal(description.headline, "Movie");
    assert.equal(description.hasMedia, true);
    assert.deepEqual(description.playback, { kind: "playing", label: "Playing" });
    assert.equal(description.position?.text, "1:05 / 2:02:05");
    assert.equal(description.volumeText, "Volume 50%");
    assert.equal(description.mutedText, "Not muted");
    assert.equal(description.soundText, "Volume 50% · Not muted");
    assert.equal(description.application, "Default Media Receiver");
    assert.equal(description.standby, false);
    assert.equal(description.note, null);
  });

  test("progress is a fraction of the duration, clamped, and absent without a duration", () => {
    const media = FULL_STATUS.media!;
    const fractionOf = (positionSeconds: number | null, durationSeconds: number | null) =>
      describeStatus({ ...FULL_STATUS, media: { ...media, positionSeconds, durationSeconds } }, at)
        .position?.fraction;

    assert.equal(fractionOf(50, 200), 0.25);
    assert.equal(fractionOf(500, 200), 1);
    assert.equal(fractionOf(50, null), null);
    assert.equal(fractionOf(50, 0), null);
  });

  test("a title is preferred, then the content identity, then an honest fallback", () => {
    const media = FULL_STATUS.media!;
    const headlineOf = (title: string | null, contentId: string | null) =>
      describeStatus({ ...FULL_STATUS, media: { ...media, title, contentId } }, at).headline;

    assert.equal(headlineOf("Movie", "http://x/y.mp4"), "Movie");
    assert.equal(headlineOf(null, "http://x/y.mp4"), "http://x/y.mp4");
    assert.equal(headlineOf(null, null), "Unidentified media");
  });

  test("a partial status says what was not reported instead of inventing values", () => {
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

    assert.equal(description.application, null);
    assert.equal(description.volumeText, "Volume not reported");
    assert.equal(description.muted, null);
    assert.equal(description.mutedText, "Mute state not reported");
    assert.equal(description.soundText, "Volume not reported · Mute state not reported");
    assert.deepEqual(description.playback, { kind: "unknown", label: "State unknown" });
    assert.equal(description.position?.text, "Position not reported");
    assert.equal(description.position?.fraction, null);
    assert.equal(description.note, null);
  });

  test("a connected device without a receiver block says so field by field", () => {
    const description = describeStatus({ ...FULL_STATUS, receiver: null, media: null }, at);

    assert.equal(description.volumeText, "Volume not reported");
    assert.equal(description.mutedText, "Mute state not reported");
    assert.equal(description.application, null);
  });

  test("no media session is its own state: nothing playing, volume still shown", () => {
    const description = describeStatus({ ...FULL_STATUS, media: null }, at);

    assert.equal(description.headline, "Nothing playing");
    assert.equal(description.volumeText, "Volume 50%");
    assert.equal(description.hasMedia, false);
    assert.equal(description.playback, null);
    assert.equal(description.position, null);
    assert.equal(description.note, null);
  });

  test("a device that is not connected offers no receiver or media state", () => {
    const description = describeStatus(
      { ...FULL_STATUS, connection: "disconnected", receiver: null, media: null },
      at,
    );

    assert.equal(description.connected, false);
    assert.equal(description.connectionLabel, "Not connected");
    assert.equal(description.headline, "");
    assert.equal(description.playback, null);
    assert.match(description.note ?? "", /isn't connected/);
  });

  test("standby is only called out when the device reported it", () => {
    const reported = { ...FULL_STATUS, receiver: { ...FULL_STATUS.receiver!, standby: true } };
    const notStandby = { ...FULL_STATUS, receiver: { ...FULL_STATUS.receiver!, standby: false } };

    assert.equal(describeStatus(reported, at).standby, true);
    assert.equal(describeStatus(notStandby, at).standby, false);
    assert.equal(describeStatus(FULL_STATUS, at).standby, false);
  });

  test("muted true and false are distinct from not reported", () => {
    const muted = { ...FULL_STATUS, receiver: { ...FULL_STATUS.receiver!, muted: true } };

    assert.equal(describeStatus(muted, at).mutedText, "Muted");
    assert.equal(describeStatus(FULL_STATUS, at).mutedText, "Not muted");
  });

  test("a receiver app id is shown when there is no app name", () => {
    const idOnly = {
      ...FULL_STATUS,
      receiver: { ...FULL_STATUS.receiver!, appName: null, appId: "CC1AD845" },
    };

    assert.equal(describeStatus(idOnly, at).application, "CC1AD845");
  });

  test("maps every playback state the device can report", () => {
    const media = FULL_STATUS.media!;
    const kindOf = (playbackState: string) =>
      describeStatus({ ...FULL_STATUS, media: { ...media, playbackState } }, at).playback?.kind;

    assert.equal(kindOf("playing"), "playing");
    assert.equal(kindOf("paused"), "paused");
    assert.equal(kindOf("buffering"), "buffering");
    assert.equal(kindOf("idle"), "idle");
    assert.equal(kindOf("something-new"), "unknown");
  });

  test("formats clock values, and nothing for a missing or invalid one", () => {
    assert.equal(formatClock(0), "0:00");
    assert.equal(formatClock(59.9), "0:59");
    assert.equal(formatClock(3600), "1:00:00");
    assert.equal(formatClock(null), "");
    assert.equal(formatClock(-1), "");
    assert.equal(formatClock(Number.POSITIVE_INFINITY), "");
  });
});
