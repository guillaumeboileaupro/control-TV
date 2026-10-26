// Tests for the directional pad's five click zones, run with Node's built-in runner (`npm test`).
// They read the real markup and stylesheet: each zone is a button the remote panel wires to its
// own key; the four arrows are quarters of the ring (clip-path polygons) and OK a disc above them,
// in a pad clipped to a disc. Every point of the pad, edges and corners included, is hit-tested
// against those shapes: it reaches exactly one key, and nothing outside the pad reaches any.

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

type Point = readonly [number, number];
type Zone = (typeof ZONES)[number];
const ARROWS = ["up", "right", "down", "left"] as const;

// The polygon of an arrow's `clip-path`, in % of the pad.
function quarter(name: (typeof ARROWS)[number]): Point[] {
  const clip = rule(`.dpad-${name}`).get("clip-path") ?? "";
  const inner = /^polygon\((.*)\)$/.exec(clip);
  assert.ok(inner !== null, `.dpad-${name} is clipped to a polygon`);
  return inner[1].split(",").map((pair) => {
    const [x, y] = pair.trim().split(/\s+/).map(percent);
    return [x, y] as const;
  });
}

// Strictly inside a convex polygon (on an edge counts as "edge", not inside).
function within(polygon: Point[], [x, y]: Point): "inside" | "edge" | "outside" {
  let sign = 0;
  for (const [index, [ax, ay]] of polygon.entries()) {
    const [bx, by] = polygon[(index + 1) % polygon.length];
    const cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax);
    if (Math.abs(cross) < 1e-9) {
      const between = Math.min(ax, bx) <= x && x <= Math.max(ax, bx);
      if (between && Math.min(ay, by) <= y && y <= Math.max(ay, by)) return "edge";
      continue;
    }
    if (sign !== 0 && Math.sign(cross) !== sign) return "outside";
    sign = Math.sign(cross);
  }
  return "inside";
}

// The keys a press at `point` (in % of the pad) can reach, as the stylesheet draws them.
function keysAt(point: Point): Zone[] {
  const [x, y] = point;
  const padRadius = Number.parseFloat(
    /^circle\((.*)\)$/.exec(rule(".dpad").get("clip-path") ?? "")?.[1] ?? "",
  );
  if (Math.hypot(x - 50, y - 50) > padRadius) return [];
  const ok = percent(rule(".dpad").get("--ok")) / 2;
  if (Math.hypot(x - 50, y - 50) < ok) return ["ok"]; // OK is drawn above the quarters
  return ARROWS.filter((name) => within(quarter(name), point) !== "outside");
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

  test("the pad is a disc, OK sits above four quarters of it", () => {
    assert.equal(rule(".dpad").get("clip-path"), "circle(50%)");
    assert.equal(rule(".dpad").get("position"), "relative");
    assert.equal(rule(".dpad-key").get("inset"), "0");
    assert.equal(rule(".dpad-ok").get("position"), "absolute");
    assert.equal(rule(".dpad-ok").get("z-index"), "1");
    for (const name of ARROWS) {
      assert.equal(quarter(name).length, 3, `${name} is a triangle from the centre`);
      assert.deepEqual(quarter(name)[0], [50, 50]);
    }
  });

  test("every point of the pad reaches exactly one key: no dead zone, no overlap", () => {
    for (let x = 0; x <= 100; x += 1) {
      for (let y = 0; y <= 100; y += 1) {
        const keys = keysAt([x, y]);
        const inside = Math.hypot(x - 50, y - 50) <= 50;
        const onDiagonal = Math.abs(x - 50) === Math.abs(y - 50) && keys[0] !== "ok";
        if (!inside) {
          assert.deepEqual(keys, [], `(${x}, ${y}) is outside the pad`);
        } else if (onDiagonal && !(x === 50 && y === 50)) {
          // The border between two quarters: either neighbour, never a hole.
          assert.equal(keys.length, 2, `(${x}, ${y}) borders two quarters`);
        } else {
          assert.equal(keys.length, 1, `(${x}, ${y}) reaches ${keys.join("+") || "nothing"}`);
        }
      }
    }
  });

  test("edges, corners and borders reach the expected key", () => {
    const cases: [Point, Zone[]][] = [
      [[50, 0.5], ["up"]], // the very top of the ring
      [[99.5, 50], ["right"]],
      [[50, 99.5], ["down"]],
      [[0.5, 50], ["left"]],
      [[50, 50], ["ok"]],
      [[50, 32.1], ["ok"]], // just inside OK's edge
      [[50, 31.9], ["up"]], // just outside it: the arrow, not a gap
      [[68.1, 50], ["right"]],
      [[50, 68.1], ["down"]],
      [[31.9, 50], ["left"]],
      // Either side of each border between quarters (the diagonals), inside the ring.
      [[80, 19], ["up"]],
      [[80, 21], ["right"]],
      [[81, 80], ["right"]],
      [[80, 81], ["down"]],
      [[20, 81], ["down"]],
      [[20, 79], ["left"]],
      [[19, 20], ["left"]],
      [[20, 19], ["up"]],
      [[2, 2], []], // the square's corners are outside the round pad
      [[98, 2], []],
      [[2, 98], []],
      [[98, 98], []],
    ];
    for (const [point, keys] of cases) {
      assert.deepEqual(keysAt(point), keys, `(${point[0]}, ${point[1]})`);
    }
  });

  test("the four quarters are the same shape turned around the centre", () => {
    const rotate = ([x, y]: Point): Point => [100 - y, x]; // 90° clockwise about (50, 50)
    assert.deepEqual(quarter("up").map(rotate), quarter("right"));
    assert.deepEqual(quarter("right").map(rotate), quarter("down"));
    assert.deepEqual(quarter("down").map(rotate), quarter("left"));
  });
});
