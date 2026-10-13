// Tests for the TV remote panel logic, run with Node's built-in runner (`npm test`). Hand-written
// states and answers only: they say nothing about a real TV, only how the panel reasons over what
// the control layer answers.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { DEVICE, readyWith, statusOf } from "./fixtures.ts";
import {
  finishDiscovery,
  idle,
  refreshStatus,
  selectDevice,
  startDiscovery,
  type AppState,
  type Device,
} from "./model.ts";
import { startTransport } from "./playback.ts";
import {
  appLabel,
  describeRemote,
  describeReport,
  finishRemote,
  remoteInvocation,
  setAppFilter,
  startRemote,
  toApps,
  toReport,
  type RemoteAction,
  type RemoteAnswer,
  type RemoteRequest,
} from "./remote.ts";

const TV: Device = { ...DEVICE, androidTvRemote: true };

function withTv(device: Device = TV): AppState {
  const ready = readyWith(statusOf());
  return { ...ready, selected: device, devices: [device] };
}

function run(
  state: AppState,
  action: RemoteAction,
  answer: RemoteAnswer,
): { state: AppState; request: RemoteRequest } {
  const started = startRemote(state, action);
  assert.notEqual(started.request, null, "the request should have started");
  const request = started.request as RemoteRequest;
  return { state: finishRemote(started.state, request.requestId, answer), request };
}

const report = (level: number, muted = false) => ({
  isOn: true,
  currentApp: null,
  volume: { level, max: 100, muted },
});

const keyAnswer = (confirmation: string, after: unknown, detail = "the TV reports volume 11/100") =>
  ({
    ok: true,
    result: {
      result: { key: "volume_up", deviceId: "a", confirmation, detail, before: report(10), after },
    },
  }) as const;

const failure = (code: string, message = "detail"): RemoteAnswer => ({
  ok: false,
  failure: { code, message },
});

describe("which TV gets the panel", () => {
  test("only a TV that advertises its own remote channel", () => {
    assert.equal(describeRemote(withTv()).visible, true);
    for (const androidTvRemote of [false, null, undefined]) {
      const state = withTv({ ...DEVICE, androidTvRemote });
      assert.equal(describeRemote(state).visible, false);
      assert.equal(startRemote(state, { kind: "status" }).request, null);
    }
  });

  test("never a Cast group, even one that reports the channel", () => {
    const group = withTv({ ...TV, kind: "group" });

    assert.equal(describeRemote(group).visible, false);
    assert.equal(startRemote(group, { kind: "key", key: "volume_up" }).request, null);
  });
});

describe("requests", () => {
  test("each action maps onto its application command with only what it needs", () => {
    const base = { requestId: 1, deviceId: "a" };
    assert.deepEqual(remoteInvocation({ ...base, action: { kind: "status" } }), {
      command: "bridge_get_remote_status",
      args: { deviceId: "a" },
    });
    assert.deepEqual(remoteInvocation({ ...base, action: { kind: "key", key: "mute" } }), {
      command: "bridge_press_remote_key",
      args: { deviceId: "a", key: "mute" },
    });
    assert.deepEqual(remoteInvocation({ ...base, action: { kind: "show_code" } }), {
      command: "bridge_start_remote_pairing",
      args: { deviceId: "a" },
    });
    assert.deepEqual(remoteInvocation({ ...base, action: { kind: "pair", code: "A1B2C3" } }), {
      command: "bridge_finish_remote_pairing",
      args: { deviceId: "a", code: "A1B2C3" },
    });
  });

  test("one request at a time, shared with every other request of the app", () => {
    const started = startRemote(withTv(), { kind: "key", key: "volume_up" });

    assert.equal(idle(started.state), false);
    assert.equal(startRemote(started.state, { kind: "key", key: "volume_up" }).request, null);
    assert.equal(startTransport(started.state, "pause").request, null);
    assert.equal(refreshStatus(started.state).request, null);
    assert.equal(describeRemote(started.state).keysEnabled, false);
    assert.equal(describeRemote(started.state).progress, "Sending Volume up…");
    assert.equal(describeRemote(started.state).pendingKey, "volume_up");
  });

  test("no key while another app request runs", () => {
    const pending = startTransport(withTv(), "pause").state;

    assert.equal(startRemote(pending, { kind: "key", key: "volume_up" }).request, null);
  });

  test("a late or duplicate answer is ignored", () => {
    const started = startRemote(withTv(), { kind: "status" });
    const request = started.request as RemoteRequest;

    assert.equal(
      finishRemote(started.state, request.requestId + 1, { ok: true, result: {} }),
      started.state,
    );
    const done = finishRemote(started.state, request.requestId, {
      ok: true,
      result: { remote: report(3) },
    });
    assert.equal(finishRemote(done, request.requestId, { ok: true, result: {} }), done);
  });
});

describe("keys and what the TV reports", () => {
  test("a confirmed volume key shows the TV's own new volume", () => {
    const { state } = run(
      withTv(),
      { kind: "key", key: "volume_up" },
      keyAnswer("confirmed", report(11)),
    );
    const view = describeRemote(state);

    assert.equal(view.readout, "TV volume 11 of 100");
    assert.deepEqual(view.outcome, {
      tone: "done",
      text: "Volume up: the TV reports volume 11/100.",
      technical: null,
    });
    assert.equal(state.remote.pairing, "paired");
    assert.equal(idle(state), true);
  });

  test("an unconfirmed key is sent, not done, and shows what the TV still reports", () => {
    const { state } = run(
      withTv(),
      { kind: "key", key: "volume_up" },
      keyAnswer("unconfirmed", report(100), "sent; the TV still reports volume 100/100"),
    );
    const view = describeRemote(state);

    assert.equal(view.outcome?.tone, "sent");
    assert.equal(view.outcome?.text, "Volume up was sent, but the TV didn't report the change.");
    assert.equal(view.readout, "TV volume 100 of 100");
  });

  test("a TV that never reports its volume leaves the check to the person", () => {
    const { state } = run(
      withTv(),
      { kind: "key", key: "volume_up" },
      keyAnswer(
        "unconfirmed",
        { isOn: true, volume: null },
        "sent; the TV did not report its volume",
      ),
    );

    assert.equal(
      describeRemote(state).outcome?.text,
      "Volume up was sent. This TV doesn't report its volume here: check the TV.",
    );
    assert.equal(describeRemote(state).outcome?.tone, "sent");
  });

  test("a key whose effect the TV does not report asks the person to look", () => {
    const { state } = run(
      withTv(),
      { kind: "key", key: "power" },
      keyAnswer("not_checked", { isOn: null, volume: null }),
    );

    assert.equal(
      describeRemote(state).outcome?.text,
      "Power was sent. The TV doesn't report what this key does: check the TV.",
    );
    assert.equal(describeRemote(state).readout, "");
  });

  test("a key whose answer is lost is uncertain and never resent", () => {
    for (const code of ["timeout", "bridge_timeout", "bridge_transport"]) {
      const { state } = run(withTv(), { kind: "key", key: "volume_up" }, failure(code));

      assert.equal(
        describeRemote(state).outcome?.text,
        "Volume up may or may not have reached the TV. It was not sent again: check the TV.",
      );
      assert.equal(state.remote.pending, null);
    }
  });

  test("the reported state is worded from the TV's report only", () => {
    assert.equal(describeReport(null), "");
    assert.equal(describeReport(report(7, true)), "TV volume 7 of 100 · Muted");
    assert.equal(
      describeReport({ isOn: false, currentApp: null, volume: null }),
      "The TV reports it is off",
    );
    assert.equal(describeReport({ isOn: null, currentApp: null, volume: null }), "");
  });

  test("a malformed report is the TV saying nothing", () => {
    assert.equal(toReport(null), null);
    assert.deepEqual(toReport({ isOn: "yes", volume: { level: 3, max: 0, muted: false } }), {
      isOn: null,
      currentApp: null,
      volume: null,
    });
    assert.deepEqual(toReport({ isOn: true, volume: { level: "3", max: 100, muted: false } }), {
      isOn: true,
      currentApp: null,
      volume: null,
    });
  });

  test("reading the TV's volume shows its report and sends no key", () => {
    const { state, request } = run(
      withTv(),
      { kind: "status" },
      { ok: true, result: { remote: report(25, true) } },
    );

    assert.equal(request.action.kind, "status");
    assert.equal(describeRemote(state).readout, "TV volume 25 of 100 · Muted");
    assert.equal(describeRemote(state).outcome, null);
  });
});

describe("pairing", () => {
  test("a TV that must be paired first hides the keys and offers to show a code", () => {
    const { state } = run(withTv(), { kind: "key", key: "volume_up" }, failure("pairing_required"));
    const view = describeRemote(state);

    assert.equal(state.remote.pairing, "needed");
    assert.equal(view.keysEnabled, false);
    assert.deepEqual(view.pairing, {
      codeEntry: false,
      text: "Pair control-TV with this TV to use its own remote.",
    });
    assert.equal(startRemote(state, { kind: "key", key: "volume_up" }).request, null);
    assert.equal(startRemote(state, { kind: "status" }).request, null);
    assert.equal(view.readEnabled, false);
  });

  test("showing a code, then typing it, pairs", () => {
    const needed = run(withTv(), { kind: "status" }, failure("pairing_required")).state;
    const shown = run(
      needed,
      { kind: "show_code" },
      { ok: true, result: { pairing: "code_shown" } },
    );

    assert.deepEqual(describeRemote(shown.state).pairing, {
      codeEntry: true,
      text: "Type the code the TV shows.",
    });
    assert.equal(describeRemote(shown.state).outcome, null);
    const paired = run(
      shown.state,
      { kind: "pair", code: " a1b2c3 " },
      {
        ok: true,
        result: { pairing: "paired" },
      },
    );

    assert.deepEqual(paired.request.action, { kind: "pair", code: "A1B2C3" });
    assert.equal(paired.state.remote.pairing, "paired");
    assert.equal(describeRemote(paired.state).pairing, null);
    assert.equal(describeRemote(paired.state).keysEnabled, true);
    assert.equal(describeRemote(paired.state).outcome?.text, "Paired with this TV.");
  });

  test("a malformed code is refused here and nothing is sent", () => {
    const shown = { ...withTv(), remote: { ...withTv().remote, pairing: "code_shown" as const } };

    for (const code of ["12345", "1234567", "GHIJKL", ""]) {
      const started = startRemote(shown, { kind: "pair", code });
      assert.equal(started.request, null);
      assert.equal(
        started.state.remote.outcome?.text,
        "Type the 6 letters and digits the TV shows.",
      );
    }
  });

  test("a code is only sent after the TV was asked to show one", () => {
    assert.equal(startRemote(withTv(), { kind: "pair", code: "A1B2C3" }).request, null);
  });

  test("a refused code asks for a new one", () => {
    const shown = { ...withTv(), remote: { ...withTv().remote, pairing: "code_shown" as const } };
    const { state } = run(shown, { kind: "pair", code: "FFFFFF" }, failure("command_rejected"));

    assert.equal(state.remote.pairing, "needed");
    assert.equal(
      describeRemote(state).outcome?.text,
      "The TV refused that code. Show a new code and try again.",
    );
  });

  test("a version without the channel says so", () => {
    const { state } = run(withTv(), { kind: "status" }, failure("unsupported_operation"));

    assert.equal(
      describeRemote(state).outcome?.text,
      "This version of control-TV can't use the TV's own remote.",
    );
  });

  test("any other failure is worded by the shared failure descriptions", () => {
    const { state } = run(withTv(), { kind: "status" }, failure("device_unavailable"));

    assert.equal(
      describeRemote(state).outcome?.text,
      "Can't reach this device. Make sure it is on and connected to the same network, then try again.",
    );
    assert.equal(describeRemote(state).outcome?.technical, "device_unavailable: detail");
  });
});

describe("the panel belongs to one TV", () => {
  test("selecting another TV starts from nothing; refreshing the same TV keeps it", () => {
    const other: Device = { ...TV, id: "b", friendlyName: "Device b" };
    const paired = run(
      withTv(),
      { kind: "status" },
      { ok: true, result: { remote: report(5) } },
    ).state;
    const listed = finishDiscovery(startDiscovery(paired), [TV, other]);

    const refreshed = refreshStatus(listed).state;
    assert.equal(refreshed.remote.pairing, "paired");

    const switched = selectDevice(listed, "b").state;
    assert.equal(switched.remote.pairing, "unknown");
    assert.equal(switched.remote.report, null);
  });

  test("a TV that disappears from discovery takes its panel state with it", () => {
    const paired = run(
      withTv(),
      { kind: "status" },
      { ok: true, result: { remote: report(5) } },
    ).state;

    const gone = finishDiscovery(startDiscovery(paired), []);

    assert.equal(gone.remote.report, null);
    assert.equal(gone.remote.pairing, "unknown");
  });
});

describe("typing on the TV", () => {
  const typed = (fieldReported: boolean): RemoteAnswer => ({
    ok: true,
    result: {
      result: {
        deviceId: "a",
        confirmation: "not_checked",
        detail: "sent",
        fieldReported,
        observed: { isOn: true, currentApp: "com.example.tv", volume: null },
      },
    },
  });

  test("text goes to its own command, with only the device and the text", () => {
    const started = startRemote(withTv(), { kind: "text", text: "hello world" });

    assert.deepEqual(remoteInvocation(started.request as RemoteRequest), {
      command: "bridge_type_remote_text",
      args: { deviceId: "a", text: "hello world" },
    });
    assert.equal(describeRemote(started.state).sendingText, true);
    assert.equal(describeRemote(started.state).progress, "Sending text…");
  });

  test("sent text asks the person to check, and never repeats the text", () => {
    const { state } = run(withTv(), { kind: "text", text: "s3cret pass" }, typed(true));
    const view = describeRemote(state);

    assert.equal(view.outcome?.tone, "sent");
    assert.equal(view.outcome?.text, "Text sent to the TV's text field: check the TV.");
    assert.equal(JSON.stringify(state.remote).includes("s3cret"), false);
    assert.equal(view.readout, "TV app: com.example.tv");
  });

  test("text sent while the TV reported no open field says how to fix it", () => {
    const { state } = run(withTv(), { kind: "text", text: "abc" }, typed(false));

    assert.equal(
      describeRemote(state).outcome?.text,
      "Text sent, but the TV didn't report an open text field. Open a search or text field on the TV, then send again.",
    );
  });

  test("empty, too long or multi-line text is refused here and nothing is sent", () => {
    for (const text of ["", "a".repeat(257), "two\nlines", "tab\there"]) {
      const started = startRemote(withTv(), { kind: "text", text });
      assert.equal(started.request, null);
      assert.equal(started.state.remote.outcome?.text, "Type 1 to 256 characters, on one line.");
    }
  });

  test("text whose answer is lost is uncertain, never resent and never shown", () => {
    const { state } = run(withTv(), { kind: "text", text: "s3cret" }, failure("bridge_timeout"));

    assert.equal(
      describeRemote(state).outcome?.text,
      "The text may or may not have reached the TV. It was not sent again: check the TV.",
    );
    assert.equal(JSON.stringify(state.remote).includes("s3cret"), false);
  });

  test("no text while pairing is needed", () => {
    const needed = { ...withTv(), remote: { ...withTv().remote, pairing: "needed" as const } };

    assert.equal(startRemote(needed, { kind: "text", text: "abc" }).request, null);
  });

  test("Backspace and Enter are keys of their own", () => {
    const started = startRemote(withTv(), { kind: "key", key: "delete" });

    assert.deepEqual(remoteInvocation(started.request as RemoteRequest).args, {
      deviceId: "a",
      key: "delete",
    });
    assert.equal(describeRemote(started.state).progress, "Sending Backspace…");
  });
});

describe("the TV's apps", () => {
  const listed = (apps: unknown): RemoteAnswer => ({ ok: true, result: { apps } });
  const APPS = [
    { package: "com.netflix.ninja", name: "Netflix" },
    { package: "com.example.unknown", name: null },
    { package: "com.google.android.youtube.tv", name: "YouTube" },
  ];

  test("listing goes to its own read-only command", () => {
    const started = startRemote(withTv(), { kind: "list_apps" });

    assert.deepEqual(remoteInvocation(started.request as RemoteRequest), {
      command: "bridge_list_tv_apps",
      args: { deviceId: "a" },
    });
    assert.equal(describeRemote(started.state).listingApps, true);
    assert.equal(
      describeRemote(started.state).progress,
      "Listing the TV's apps… If the TV asks, allow control-TV.",
    );
  });

  test("the list shows what the TV listed, sorted by name, the package when no name is known", () => {
    const { state } = run(withTv(), { kind: "list_apps" }, listed(APPS));

    assert.deepEqual(describeRemote(state).apps?.map(appLabel), [
      "com.example.unknown",
      "Netflix",
      "YouTube",
    ]);
    assert.equal(describeRemote(state).outcome, null);
  });

  test("nothing listed yet is not an empty list", () => {
    assert.equal(describeRemote(withTv()).apps, null);
  });

  test("an empty list says so", () => {
    const { state } = run(withTv(), { kind: "list_apps" }, listed([]));

    assert.deepEqual(describeRemote(state).apps, []);
    assert.equal(describeRemote(state).outcome?.text, "The TV listed no app it can open.");
  });

  test("malformed entries are left out, never guessed", () => {
    assert.deepEqual(toApps([{ package: "" }, { name: "x" }, 4, { package: "a.b", name: 3 }]), [
      { package: "a.b", name: null },
    ]);
    assert.deepEqual(toApps("nope"), []);
  });

  test("the filter narrows by name or package and sends nothing", () => {
    const listedState = run(withTv(), { kind: "list_apps" }, listed(APPS)).state;

    const byName = setAppFilter(listedState, "net");
    const byPackage = setAppFilter(listedState, "youtube.tv");

    assert.deepEqual(describeRemote(byName).apps?.map(appLabel), ["Netflix"]);
    assert.deepEqual(describeRemote(byPackage).apps?.map(appLabel), ["YouTube"]);
    assert.equal(idle(byName), true);
    assert.equal(setAppFilter(byName, "net"), byName);
  });

  test("debugging off explains how to turn it on, and does not touch the remote's pairing", () => {
    const { state } = run(withTv(), { kind: "list_apps" }, failure("unsupported_operation"));

    assert.match(describeRemote(state).outcome?.text ?? "", /USB debugging/);
    assert.equal(state.remote.pairing, "unknown");
  });

  test("a TV that asks to allow control-TV says to allow it, and is not a remote pairing", () => {
    const { state } = run(withTv(), { kind: "list_apps" }, failure("pairing_required"));

    assert.equal(
      describeRemote(state).outcome?.text,
      "Allow control-TV on the TV when it asks about debugging, then list the apps again.",
    );
    assert.equal(state.remote.pairing, "unknown");
    assert.equal(describeRemote(state).keysEnabled, true);
  });

  test("listing works even while the remote still needs pairing", () => {
    const needed = { ...withTv(), remote: { ...withTv().remote, pairing: "needed" as const } };

    assert.notEqual(startRemote(needed, { kind: "list_apps" }).request, null);
    assert.equal(
      startRemote(needed, { kind: "launch_app", package: "a.b", label: "A" }).request,
      null,
    );
  });

  const launched = (confirmation: string, front: string | null): RemoteAnswer => ({
    ok: true,
    result: {
      result: {
        package: "com.netflix.ninja",
        deviceId: "a",
        confirmation,
        detail: "d",
        after: { isOn: true, currentApp: front, volume: null },
      },
    },
  });
  const open = { kind: "launch_app" as const, package: "com.netflix.ninja", label: "Netflix" };

  test("opening an app sends only its package to its own command", () => {
    const started = startRemote(withTv(), open);

    assert.deepEqual(remoteInvocation(started.request as RemoteRequest), {
      command: "bridge_launch_tv_app",
      args: { deviceId: "a", package: "com.netflix.ninja" },
    });
    assert.equal(describeRemote(started.state).pendingApp, "com.netflix.ninja");
    assert.equal(describeRemote(started.state).progress, "Opening Netflix…");
  });

  test("an app is open only when the TV reports it in front", () => {
    const done = run(withTv(), open, launched("confirmed", "com.netflix.ninja")).state;
    const other = run(withTv(), open, launched("unconfirmed", "com.other")).state;
    const silent = run(withTv(), open, launched("not_checked", null)).state;

    assert.equal(describeRemote(done).outcome?.text, "Netflix is open on the TV.");
    assert.equal(describeRemote(done).outcome?.tone, "done");
    assert.equal(
      describeRemote(other).outcome?.text,
      "Netflix was sent, but the TV reports another app in front.",
    );
    assert.equal(describeRemote(silent).outcome?.text, "Netflix was sent: check the TV.");
    assert.equal(describeRemote(silent).outcome?.tone, "sent");
  });

  test("an opening whose answer is lost is uncertain and never resent", () => {
    const { state } = run(withTv(), open, failure("bridge_timeout"));

    assert.equal(
      describeRemote(state).outcome?.text,
      "Netflix may or may not have opened. It was not sent again: check the TV.",
    );
  });

  test("another TV starts without the previous TV's apps", () => {
    const other: Device = { ...TV, id: "b", friendlyName: "Device b" };
    const listedState = run(withTv(), { kind: "list_apps" }, listed(APPS)).state;
    const found = finishDiscovery(startDiscovery(listedState), [TV, other]);

    assert.equal(selectDevice(found, "b").state.remote.apps, null);
  });
});
