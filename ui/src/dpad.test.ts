// Tests for the directional pad's five click zones, run with Node's built-in runner (`npm test`).
// They read the real markup and stylesheet: each zone is a button the remote panel wires to its
// own key, and the five round zones sit inside the ring without overlapping, so a click always
// reaches exactly one key.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, test } from "node:test";

import { createRemotePanel, type RemotePanelElements } from "./remote-panel.ts";
import type { RemoteAction } from "./remote.ts";

const UI_ROOT = process.cwd();
const HTML = readFileSync(resolve(UI_ROOT, "index.html"), "utf8");
const CSS = readFileSync(resolve(UI_ROOT, "src/styles.css"), "utf8");
const ZONES = ["up", "left", "ok", "right", "down"] as const;

function padMarkup(): string {
  const start = HTML.indexOf('<div class="dpad"');
  assert.ok(start >= 0, "the pad is in index.html");
  return HTML.slice(start, HTML.indexOf("</div>", start));
}

function rule(selector: string): Map<string, string> {
  const start = CSS.indexOf(`\n${selector} {`);
  assert.ok(start >= 0, `${selector} is styled`);
  const body = CSS.slice(CSS.indexOf("{", start) + 1, CSS.indexOf("}", start));
  const declarations = new Map<string, string>();
  for (const declaration of body.replace(/\/\*[\s\S]*?\*\//g, "").split(";")) {
    const colon = declaration.indexOf(":");
    if (colon > 0) {
      declarations.set(declaration.slice(0, colon).trim(), declaration.slice(colon + 1).trim());
    }
  }
  return declarations;
}

// A percentage of the pad, resolving `var(--name)` against the pad's own custom properties.
function percent(value: string | undefined): number {
  assert.ok(value !== undefined);
  const variable = /^var\((--[a-z-]+)\)$/.exec(value);
  const literal = variable === null ? value : rule(".dpad").get(variable[1]);
  assert.ok(literal !== undefined && literal.endsWith("%"), `${value} is a % of the pad`);
  return Number.parseFloat(literal);
}

interface Disc {
  x: number;
  y: number;
  r: number;
}

function zone(name: (typeof ZONES)[number]): Disc {
  const own = rule(name === "ok" ? ".dpad-ok" : `.dpad-${name}`);
  const size = percent(name === "ok" ? own.get("width") : rule(".dpad-key").get("width"));
  const left = own.has("left") ? percent(own.get("left")) : 100 - percent(own.get("right")) - size;
  const top = own.has("top") ? percent(own.get("top")) : 100 - percent(own.get("bottom")) - size;
  return { x: left + size / 2, y: top + size / 2, r: size / 2 };
}

class FakeButton extends EventTarget {
  constructor(readonly dataset: Record<string, string>) {
    super();
  }
}

function wiredPad(sent: RemoteAction[]): FakeButton[] {
  const keys = [...padMarkup().matchAll(/data-remote-key="([a-z_]+)"/g)].map((match) => match[1]);
  const buttons = keys.map((key) => new FakeButton({ remoteKey: key }));
  const tvRemote = { querySelectorAll: () => buttons };
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
  });
  return buttons;
}

describe("the directional pad", () => {
  test("has exactly five buttons, in the TV remote the panel wires", () => {
    const pad = padMarkup();
    const keys = [...pad.matchAll(/data-remote-key="([a-z_]+)"/g)].map((match) => match[1]);
    assert.deepEqual(keys, [...ZONES]);
    assert.equal([...pad.matchAll(/<button\b/g)].length, 5);
    assert.equal([...pad.matchAll(/type="button"/g)].length, 5);
    for (const label of ["Up", "Left", "Right", "Down"]) {
      assert.ok(pad.includes(`aria-label="${label}"`), `${label} is named for screen readers`);
    }
    const remote = HTML.indexOf('id="tv-remote"');
    assert.ok(remote >= 0 && remote < HTML.indexOf('<div class="dpad"'));
  });

  test("a click on each zone sends that key once, and only that key", () => {
    const sent: RemoteAction[] = [];
    const buttons = wiredPad(sent);
    for (const button of buttons) {
      button.dispatchEvent(new Event("click"));
    }
    assert.deepEqual(
      sent,
      ZONES.map((key) => ({ kind: "key", key })),
    );
  });

  test("the five zones are round, inside the ring and never overlap", () => {
    assert.equal(rule(".dpad").get("position"), "relative");
    assert.equal(rule(".dpad-key").get("position"), "absolute");
    assert.equal(rule(".dpad-ok").get("position"), "absolute");
    const discs = ZONES.map(zone);
    for (const disc of discs) {
      assert.ok(Math.hypot(disc.x - 50, disc.y - 50) + disc.r <= 50, "inside the round pad");
    }
    for (const [i, a] of discs.entries()) {
      for (const b of discs.slice(i + 1)) {
        assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= a.r + b.r, "zones never overlap");
      }
    }
  });

  test("the four arrows are identical and symmetric around OK in the centre", () => {
    const [up, left, ok, right, down] = ZONES.map(zone);
    assert.deepEqual([ok.x, ok.y], [50, 50]);
    const arrows = [up, left, right, down];
    assert.ok(arrows.every((disc) => disc.r === up.r));
    const distances = arrows.map((disc) => Math.hypot(disc.x - 50, disc.y - 50));
    assert.ok(distances.every((distance) => Math.abs(distance - distances[0]) < 1e-9));
    assert.deepEqual([up.x, down.x, left.y, right.y], [50, 50, 50, 50]);
  });
});
