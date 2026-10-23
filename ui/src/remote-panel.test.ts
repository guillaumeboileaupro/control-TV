import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { clearAppsPairingCodeWhenInactive, consumeAppsPairingCredentials } from "./remote-panel.ts";

describe("TV-app pairing form", () => {
  test("consumes the temporary code while preserving the submitted request", () => {
    const endpoint = { value: "192.0.2.10:40123" };
    const code = { value: "123456" };

    assert.deepEqual(consumeAppsPairingCredentials(endpoint, code), {
      kind: "pair_apps",
      pairingEndpoint: "192.0.2.10:40123",
      code: "123456",
    });
    assert.equal(code.value, "");
    assert.equal(endpoint.value, "192.0.2.10:40123");
  });

  test("clears an unsubmitted code when the pairing area closes", () => {
    const code = { value: "123456" };

    const selected = clearAppsPairingCodeWhenInactive(code, false, "tv-1", "tv-1");

    assert.equal(code.value, "");
    assert.equal(selected, "tv-1");
  });

  test("clears an unsubmitted code when another TV is selected", () => {
    const code = { value: "123456" };

    const selected = clearAppsPairingCodeWhenInactive(code, true, "tv-2", "tv-1");

    assert.equal(code.value, "");
    assert.equal(selected, "tv-2");
  });

  test("keeps an unsubmitted code only while the same TV pairing area stays open", () => {
    const code = { value: "123456" };

    clearAppsPairingCodeWhenInactive(code, true, "tv-1", "tv-1");

    assert.equal(code.value, "123456");
  });
});
