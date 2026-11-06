import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  clearAppsPairingCodeWhenInactive,
  consumeAppsPairingCredentials,
  createRemotePanel,
  type RemotePanelElements,
} from "./remote-panel.ts";
import type { RemoteAction } from "./remote.ts";

class AssistantButton extends EventTarget {
  readonly dataset = { remoteKey: "assistant" };
  readonly attributes = new Map<string, string>();
  readonly classList = { toggle: () => undefined };
  readonly label = { textContent: "Google Assistant" };
  title = "";

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }

  querySelector(): { textContent: string } {
    return this.label;
  }
}

function panelWithAssistant(
  button: AssistantButton,
  sent: RemoteAction[],
  voice: {
    available(): boolean;
    start(): Promise<string | null>;
    stop(): Promise<string | null>;
  },
): void {
  const tvRemote = { querySelectorAll: () => [button] };
  const others = new Proxy({} as Record<string, EventTarget>, {
    get: (cache, name: string) => (cache[name] ??= new EventTarget()),
  });
  const elements = new Proxy(others, {
    get: (target, name: string) => (name === "tvRemote" ? tvRemote : target[name]),
  }) as unknown as RemotePanelElements;
  createRemotePanel(elements, {
    run: (action) => sent.push(action),
    filterApps: () => undefined,
    focusElsewhere: () => undefined,
    voice,
  });
}

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

describe("Android Assistant microphone", () => {
  test("uses push-to-talk only when the Android host offers it", async () => {
    const button = new AssistantButton();
    const sent: RemoteAction[] = [];
    let starts = 0;
    let stops = 0;
    let finish: (() => void) | undefined;
    panelWithAssistant(button, sent, {
      available: () => true,
      start: () => {
        starts++;
        return new Promise((resolve) => {
          finish = () => resolve(null);
        });
      },
      stop: async () => {
        stops++;
        return null;
      },
    });

    button.dispatchEvent(new Event("click"));
    assert.equal(starts, 1);
    assert.deepEqual(sent, []);
    assert.equal(button.attributes.get("aria-pressed"), "true");

    button.dispatchEvent(new Event("click"));
    await Promise.resolve();
    assert.equal(stops, 1);
    finish?.();
    await Promise.resolve();
    assert.equal(button.attributes.get("aria-pressed"), "false");
  });

  test("keeps the existing Assistant key on non-Android hosts", () => {
    const button = new AssistantButton();
    const sent: RemoteAction[] = [];
    panelWithAssistant(button, sent, {
      available: () => false,
      start: async () => null,
      stop: async () => null,
    });

    button.dispatchEvent(new Event("click"));

    assert.deepEqual(sent, [{ kind: "key", key: "assistant" }]);
  });
});
