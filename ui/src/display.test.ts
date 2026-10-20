// Tests for the display manager rules (`npm test`): hand-written layouts only, nothing applied.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  arrangementOf,
  clone,
  commonResolutions,
  join,
  logicalSize,
  manageable,
  move,
  normalize,
  overlapping,
  parseLayout,
  problem,
  ratesAtSize,
  resolutions,
  sameLayout,
  setEnabled,
  setMode,
  setPrimary,
  setResolution,
  setRotation,
  setScale,
  single,
  snap,
  toRequest,
  type DisplayInfo,
} from "./display.ts";

const LAPTOP: DisplayInfo = {
  name: "eDP-1",
  connected: true,
  enabled: true,
  mode: "0x48",
  x: 0,
  y: 0,
  rotation: "normal",
  scale: 1,
  primary: true,
  modes: [
    { id: "0x48", width: 1920, height: 1080, rate: 60 },
    { id: "0x49", width: 1920, height: 1080, rate: 47.99 },
    { id: "0x53", width: 1280, height: 720, rate: 60 },
  ],
};
const TV: DisplayInfo = {
  name: "HDMI-1",
  connected: true,
  enabled: false,
  mode: null,
  x: 0,
  y: 0,
  rotation: "normal",
  scale: 1,
  primary: false,
  modes: [
    { id: "0x1a8", width: 1920, height: 1080, rate: 59.96 },
    { id: "0x1a9", width: 1920, height: 1080, rate: 49.93 },
    { id: "0x54", width: 1024, height: 768, rate: 60 },
  ],
};
const ABSENT: DisplayInfo = { ...TV, name: "DP-1", connected: false, modes: [] };
const THREE: DisplayInfo = {
  ...TV,
  name: "DP-2",
  modes: [{ id: "0x60", width: 2560, height: 1440, rate: 60 }],
};

function joinedPair(): DisplayInfo[] {
  return setEnabled([LAPTOP, TV, ABSENT], "HDMI-1", true);
}

describe("reading the layout", () => {
  test("the bridge's answer is read without guessing", () => {
    const layout = parseLayout({
      displays: [
        {
          name: "eDP-1",
          connected: true,
          enabled: true,
          mode: "0x48",
          x: 0,
          y: 0,
          rotation: "normal",
          scale: 1,
          primary: true,
          modes: [{ id: "0x48", width: 1920, height: 1080, rate: 60 }],
        },
      ],
      awaitingConfirmation: true,
      revertedByItself: false,
    });
    assert.equal(layout?.displays[0]?.name, "eDP-1");
    assert.equal(layout?.awaitingConfirmation, true);
    assert.equal(parseLayout(null), null);
    assert.equal(parseLayout({ displays: [{ name: "x", modes: [] }] }), null);
    assert.equal(parseLayout({ displays: [{ ...LAPTOP, modes: [{ id: 1 }] }] }), null);
    assert.equal(parseLayout({ displays: ["x"] }), null);
  });

  test("only screens that offer modes are managed", () => {
    assert.deepEqual(
      manageable([LAPTOP, TV, ABSENT]).map((display) => display.name),
      ["eDP-1", "HDMI-1"],
    );
  });

  test("a screen's size on the desktop follows its mode, orientation and scale", () => {
    assert.deepEqual(logicalSize(LAPTOP), { width: 1920, height: 1080 });
    assert.deepEqual(logicalSize({ ...LAPTOP, rotation: "left" }), { width: 1080, height: 1920 });
    assert.deepEqual(logicalSize({ ...LAPTOP, scale: 0.5 }), { width: 960, height: 540 });
    assert.deepEqual(logicalSize(TV), { width: 0, height: 0 });
  });
});

describe("resolutions and refresh rates", () => {
  test("each size is offered once, largest first, with its rates", () => {
    assert.deepEqual(resolutions(LAPTOP), [
      { width: 1920, height: 1080 },
      { width: 1280, height: 720 },
    ]);
    assert.deepEqual(
      ratesAtSize(LAPTOP).map((mode) => mode.rate),
      [60, 47.99],
    );
    assert.deepEqual(ratesAtSize(TV), []);
  });

  test("a new size keeps the rate when it can, a rate is a mode", () => {
    const smaller = setResolution([LAPTOP], "eDP-1", 1280, 720);
    assert.equal(smaller[0]?.mode, "0x53");
    const slower = setMode([LAPTOP], "eDP-1", "0x49");
    assert.equal(slower[0]?.mode, "0x49");
    assert.equal(setMode([LAPTOP], "eDP-1", "0xff")[0]?.mode, "0x48");
    assert.equal(setResolution([LAPTOP], "eDP-1", 800, 600)[0]?.mode, "0x48");
  });

  test("orientation and scale are set; an unknown scale is ignored", () => {
    assert.equal(setRotation([LAPTOP], "eDP-1", "right")[0]?.rotation, "right");
    assert.equal(setScale([LAPTOP], "eDP-1", 0.8)[0]?.scale, 0.8);
    assert.equal(setScale([LAPTOP], "eDP-1", 0.9)[0]?.scale, 1);
  });
});

describe("several screens", () => {
  test("a screen turned on takes its preferred mode to the right of the others", () => {
    const pair = joinedPair();
    const tv = pair.find((display) => display.name === "HDMI-1");
    assert.equal(tv?.enabled, true);
    assert.equal(tv?.mode, "0x1a8");
    assert.equal(tv?.x, 1920);
    assert.equal(arrangementOf(pair), "join");
  });

  test("the main screen is unique and moves when its screen is turned off", () => {
    const pair = setPrimary(joinedPair(), "HDMI-1");
    assert.deepEqual(
      pair.map((display) => display.primary),
      [false, true, false],
    );
    const off = setEnabled(pair, "HDMI-1", false);
    assert.equal(off.find((display) => display.name === "eDP-1")?.primary, true);
    assert.deepEqual(setEnabled(off, "eDP-1", false), off); // the last screen stays on
    assert.deepEqual(setPrimary(off, "HDMI-1"), off); // a screen that is off cannot be main
    assert.deepEqual(setEnabled(off, "DP-1", true), off); // no modes, never on
    assert.deepEqual(setEnabled(off, "nope", true), off);
  });

  test("three screens join side by side", () => {
    const joined = join([LAPTOP, TV, THREE]);
    assert.deepEqual(
      joined.map((display) => [display.name, display.x, display.y]),
      [
        ["eDP-1", 0, 0],
        ["HDMI-1", 1920, 0],
        ["DP-2", 3840, 0],
      ],
    );
    assert.equal(problem(joined), null);
  });
});

describe("dragging and magnetic edges", () => {
  test("an edge near another snaps beside it or aligned with it", () => {
    const pair = joinedPair();
    assert.deepEqual(snap(pair, "HDMI-1", 1950, 30), { x: 1920, y: 0 }); // right of, tops aligned
    assert.deepEqual(snap(pair, "HDMI-1", -1890, 1050), { x: -1920, y: 1080 }); // left, below
    assert.deepEqual(snap(pair, "HDMI-1", 500, 1100), { x: 500, y: 1080 }); // below, offset kept
  });

  test("far from every edge the screen stays where it was dropped, in whole pixels", () => {
    assert.deepEqual(snap(joinedPair(), "HDMI-1", 2500.4, 400.6), { x: 2500, y: 401 });
    assert.deepEqual(snap(joinedPair(), "nope", 1.6, 2.2), { x: 2, y: 2 });
  });

  test("moving then normalizing keeps the desktop at 0,0 and finds overlaps", () => {
    const left = normalize(move(joinedPair(), "HDMI-1", -1920, 200));
    assert.deepEqual(
      left
        .filter((display) => display.enabled)
        .map((display) => [display.name, display.x, display.y]),
      [
        ["eDP-1", 1920, 0],
        ["HDMI-1", 0, 200],
      ],
    );
    const over = move(joinedPair(), "HDMI-1", 1000, 0);
    assert.equal(overlapping(over), true);
    assert.equal(problem(over), "Two screens overlap: drag one beside the other.");
    assert.deepEqual(normalize([ABSENT]), [ABSENT]);
  });
});

describe("Join, Clone and Single screen", () => {
  test("clone uses the largest size every screen offers, at 0,0", () => {
    assert.deepEqual(commonResolutions([LAPTOP, TV]), [{ width: 1920, height: 1080 }]);
    const cloned = clone([LAPTOP, TV, ABSENT]);
    assert.ok(cloned !== null);
    assert.deepEqual(
      cloned.filter((display) => display.enabled).map((display) => [display.mode, display.x]),
      [
        ["0x48", 0],
        ["0x1a8", 0],
      ],
    );
    assert.equal(arrangementOf(cloned), "clone");
    assert.equal(overlapping(cloned), false);
    assert.equal(clone([LAPTOP, THREE]), null);
    assert.deepEqual(commonResolutions([]), []);
  });

  test("single screen keeps one screen on as the main one", () => {
    const only = single(joinedPair(), "HDMI-1");
    assert.deepEqual(
      only.map((display) => [display.name, display.enabled, display.primary]),
      [
        ["eDP-1", false, false],
        ["HDMI-1", true, true],
        ["DP-1", false, false],
      ],
    );
    assert.equal(arrangementOf(only), "single");
    assert.deepEqual(single(only, "DP-1"), only);
  });
});

describe("what is sent", () => {
  test("every screen is sent, on or off, and an unchanged layout is the same", () => {
    const pair = joinedPair();
    assert.deepEqual(toRequest(pair)[2], { name: "DP-1", enabled: false });
    assert.deepEqual(toRequest(pair)[1], {
      name: "HDMI-1",
      enabled: true,
      mode: "0x1a8",
      x: 1920,
      y: 0,
      rotation: "normal",
      scale: 1,
      primary: false,
    });
    assert.equal(sameLayout(pair, joinedPair()), true);
    assert.equal(sameLayout(pair, [LAPTOP, TV, ABSENT]), false);
  });

  test("a layout with no screen on or no main screen is not applied", () => {
    assert.equal(problem([{ ...LAPTOP, enabled: false }]), "Keep at least one screen on.");
    assert.equal(problem([{ ...LAPTOP, primary: false }]), "Choose the main screen.");
  });
});
