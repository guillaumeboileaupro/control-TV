// The display manager's rules, like GNOME's Settings > Displays: the desktop's screens as the
// bridge reports them (modes, position, orientation, scale, main screen), their arrangement by
// drag and drop with magnetic edges, the Join / Clone / Single screen modes and the layout sent
// back. Pure functions over plain data: display-panel.ts performs the calls and draws the scene.
// Nothing here is applied: the bridge checks, applies, and reverts what is not kept.

import { isJsonObject, type JsonValue } from "./json.ts";

export type Rotation = "normal" | "left" | "right" | "inverted";
export const ROTATIONS: readonly { value: Rotation; label: string }[] = [
  { value: "normal", label: "Landscape" },
  { value: "left", label: "Portrait left" },
  { value: "right", label: "Portrait right" },
  { value: "inverted", label: "Landscape (flipped)" },
];
// xrandr factors: below 1 everything looks larger (125 % is 0.8).
export const SCALES: readonly { factor: number; label: string }[] = [
  { factor: 1, label: "100 %" },
  { factor: 0.8, label: "125 %" },
  { factor: 0.667, label: "150 %" },
  { factor: 0.5, label: "200 %" },
];
// How close (in desktop pixels) an edge must come to another to snap onto it.
export const SNAP_PIXELS = 64;

export interface DisplayModeInfo {
  id: string;
  width: number;
  height: number;
  rate: number;
}

export interface DisplayInfo {
  name: string;
  connected: boolean;
  enabled: boolean;
  mode: string | null;
  x: number;
  y: number;
  rotation: Rotation;
  scale: number;
  primary: boolean;
  modes: DisplayModeInfo[];
}

export interface DisplayLayout {
  displays: DisplayInfo[];
  awaitingConfirmation: boolean;
  revertedByItself: boolean;
}

function isRotation(value: JsonValue | undefined): value is Rotation {
  return value === "normal" || value === "left" || value === "right" || value === "inverted";
}

function parseMode(value: JsonValue): DisplayModeInfo | null {
  if (!isJsonObject(value)) {
    return null;
  }
  const { id, width, height, rate } = value;
  if (
    typeof id !== "string" ||
    typeof width !== "number" ||
    typeof height !== "number" ||
    typeof rate !== "number"
  ) {
    return null;
  }
  return { id, width, height, rate };
}

// The layout a `bridge_get_display_layout` (or apply, keep, revert) answer reports; `null` when
// it has another shape.
export function parseLayout(value: JsonValue): DisplayLayout | null {
  if (!isJsonObject(value) || !Array.isArray(value["displays"])) {
    return null;
  }
  const displays: DisplayInfo[] = [];
  for (const entry of value["displays"]) {
    if (!isJsonObject(entry) || !Array.isArray(entry["modes"])) {
      return null;
    }
    const { name, connected, enabled, mode, x, y, rotation, scale, primary } = entry;
    const modes = entry["modes"].map(parseMode);
    if (
      typeof name !== "string" ||
      typeof enabled !== "boolean" ||
      typeof x !== "number" ||
      typeof y !== "number" ||
      typeof scale !== "number" ||
      !isRotation(rotation) ||
      modes.some((candidate) => candidate === null)
    ) {
      return null;
    }
    displays.push({
      name,
      connected: connected === true,
      enabled,
      mode: typeof mode === "string" ? mode : null,
      x,
      y,
      rotation,
      scale,
      primary: primary === true,
      modes: modes.filter((candidate): candidate is DisplayModeInfo => candidate !== null),
    });
  }
  return {
    displays,
    awaitingConfirmation: value["awaitingConfirmation"] === true,
    revertedByItself: value["revertedByItself"] === true,
  };
}

// The screens the manager shows: the ones that can be turned on (they offer modes).
export function manageable(displays: readonly DisplayInfo[]): DisplayInfo[] {
  return displays.filter((display) => display.modes.length > 0);
}

export function currentMode(display: DisplayInfo): DisplayModeInfo | null {
  return display.modes.find((mode) => mode.id === display.mode) ?? null;
}

// The size the screen takes on the desktop: its mode, turned and scaled.
export function logicalSize(display: DisplayInfo): { width: number; height: number } {
  const mode = currentMode(display);
  if (mode === null) {
    return { width: 0, height: 0 };
  }
  const turned = display.rotation === "left" || display.rotation === "right";
  const width = turned ? mode.height : mode.width;
  const height = turned ? mode.width : mode.height;
  return { width: Math.round(width * display.scale), height: Math.round(height * display.scale) };
}

// The sizes a screen offers, largest first, each once.
export function resolutions(display: DisplayInfo): { width: number; height: number }[] {
  const seen = new Set<string>();
  const sizes: { width: number; height: number }[] = [];
  for (const mode of display.modes) {
    const key = `${mode.width}x${mode.height}`;
    if (!seen.has(key)) {
      seen.add(key);
      sizes.push({ width: mode.width, height: mode.height });
    }
  }
  return sizes.sort((a, b) => b.width * b.height - a.width * a.height);
}

// The refresh rates a screen offers at its current size.
export function ratesAtSize(display: DisplayInfo): DisplayModeInfo[] {
  const mode = currentMode(display);
  return mode === null
    ? []
    : display.modes.filter(
        (candidate) => candidate.width === mode.width && candidate.height === mode.height,
      );
}

function update(
  displays: readonly DisplayInfo[],
  name: string,
  change: (display: DisplayInfo) => DisplayInfo,
): DisplayInfo[] {
  return displays.map((display) => (display.name === name ? change(display) : display));
}

// A new size, keeping the refresh rate when the screen offers it there.
export function setResolution(
  displays: readonly DisplayInfo[],
  name: string,
  width: number,
  height: number,
): DisplayInfo[] {
  return update(displays, name, (display) => {
    const rate = currentMode(display)?.rate;
    const sized = display.modes.filter((mode) => mode.width === width && mode.height === height);
    const chosen = sized.find((mode) => mode.rate === rate) ?? sized[0];
    return chosen === undefined ? display : { ...display, mode: chosen.id };
  });
}

export function setMode(displays: readonly DisplayInfo[], name: string, id: string): DisplayInfo[] {
  return update(displays, name, (display) =>
    display.modes.some((mode) => mode.id === id) ? { ...display, mode: id } : display,
  );
}

export function setRotation(
  displays: readonly DisplayInfo[],
  name: string,
  rotation: Rotation,
): DisplayInfo[] {
  return update(displays, name, (display) => ({ ...display, rotation }));
}

export function setScale(
  displays: readonly DisplayInfo[],
  name: string,
  scale: number,
): DisplayInfo[] {
  return SCALES.some((option) => option.factor === scale)
    ? update(displays, name, (display) => ({ ...display, scale }))
    : [...displays];
}

// One main screen, among the ones that are on.
export function setPrimary(displays: readonly DisplayInfo[], name: string): DisplayInfo[] {
  const target = displays.find((display) => display.name === name);
  if (target === undefined || !target.enabled) {
    return [...displays];
  }
  return displays.map((display) => ({ ...display, primary: display.name === name }));
}

// Turns a screen on (its first, preferred mode, placed to the right of the others) or off (the
// main screen moves to another one that stays on). The last screen that is on stays on.
export function setEnabled(
  displays: readonly DisplayInfo[],
  name: string,
  enabled: boolean,
): DisplayInfo[] {
  const target = displays.find((display) => display.name === name);
  const on = displays.filter((display) => display.enabled);
  if (target === undefined || target.enabled === enabled || target.modes.length === 0) {
    return [...displays];
  }
  if (!enabled) {
    if (on.length === 1) {
      return [...displays];
    }
    const off = update(displays, name, (display) => ({
      ...display,
      enabled: false,
      primary: false,
    }));
    const next = off.find((display) => display.enabled);
    return target.primary && next !== undefined ? setPrimary(off, next.name) : off;
  }
  const right = Math.max(0, ...on.map((display) => display.x + logicalSize(display).width));
  return update(displays, name, (display) => ({
    ...display,
    enabled: true,
    mode: display.modes[0]?.id ?? null,
    x: right,
    y: 0,
  }));
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

function box(display: DisplayInfo): Box {
  return { x: display.x, y: display.y, ...logicalSize(display) };
}

function nearest(value: number, guides: readonly number[], within: number): number | null {
  let best: number | null = null;
  for (const guide of guides) {
    if (
      Math.abs(guide - value) <= within &&
      (best === null || Math.abs(guide - value) < Math.abs(best - value))
    ) {
      best = guide;
    }
  }
  return best;
}

// Where a dragged screen lands: its edges stick to the other screens' edges (side by side or
// aligned) when they come within `within` desktop pixels; otherwise where it was dropped.
export function snap(
  displays: readonly DisplayInfo[],
  name: string,
  x: number,
  y: number,
  within: number = SNAP_PIXELS,
): { x: number; y: number } {
  const moving = displays.find((display) => display.name === name);
  if (moving === undefined) {
    return { x: Math.round(x), y: Math.round(y) };
  }
  const { width, height } = logicalSize(moving);
  const others = displays.filter((display) => display.enabled && display.name !== name).map(box);
  const xGuides = others.flatMap((other) => [
    other.x + other.width, // to the right of it
    other.x - width, // to the left of it
    other.x, // left edges aligned
    other.x + other.width - width, // right edges aligned
  ]);
  const yGuides = others.flatMap((other) => [
    other.y + other.height,
    other.y - height,
    other.y,
    other.y + other.height - height,
  ]);
  return {
    x: nearest(x, xGuides, within) ?? Math.round(x),
    y: nearest(y, yGuides, within) ?? Math.round(y),
  };
}

export function move(
  displays: readonly DisplayInfo[],
  name: string,
  x: number,
  y: number,
): DisplayInfo[] {
  return update(displays, name, (display) => ({ ...display, x, y }));
}

// The desktop starts at 0,0: every screen that is on is shifted so the top-left one is there.
export function normalize(displays: readonly DisplayInfo[]): DisplayInfo[] {
  const on = displays.filter((display) => display.enabled);
  if (on.length === 0) {
    return [...displays];
  }
  const left = Math.min(...on.map((display) => display.x));
  const top = Math.min(...on.map((display) => display.y));
  return displays.map((display) =>
    display.enabled ? { ...display, x: display.x - left, y: display.y - top } : display,
  );
}

// Two screens that are on overlap without being clones (same place and size).
export function overlapping(displays: readonly DisplayInfo[]): boolean {
  const boxes = displays.filter((display) => display.enabled).map(box);
  return boxes.some((a, index) =>
    boxes.slice(index + 1).some((b) => {
      const clone = a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
      return (
        !clone &&
        a.x < b.x + b.width &&
        b.x < a.x + a.width &&
        a.y < b.y + b.height &&
        b.y < a.y + a.height
      );
    }),
  );
}

export type Arrangement = "join" | "clone" | "single";

export function arrangementOf(displays: readonly DisplayInfo[]): Arrangement {
  const on = displays.filter((display) => display.enabled);
  if (on.length <= 1) {
    return "single";
  }
  const first = on[0] === undefined ? null : box(on[0]);
  const same = on.every((display) => {
    const other = box(display);
    return first !== null && other.x === first.x && other.y === first.y;
  });
  return same ? "clone" : "join";
}

// Join: every screen on, side by side from left to right in their current order.
export function join(displays: readonly DisplayInfo[]): DisplayInfo[] {
  let joined = [...displays];
  for (const display of manageable(displays)) {
    if (!display.enabled) {
      joined = setEnabled(joined, display.name, true);
    }
  }
  const order = joined
    .filter((display) => display.enabled)
    .sort((a, b) => a.x - b.x || a.y - b.y || a.name.localeCompare(b.name));
  let x = 0;
  for (const display of order) {
    joined = move(joined, display.name, x, 0);
    x += logicalSize(joined.find((candidate) => candidate.name === display.name) ?? display).width;
  }
  return joined;
}

// The sizes every manageable screen offers, largest first: what Clone can use.
export function commonResolutions(
  displays: readonly DisplayInfo[],
): { width: number; height: number }[] {
  const screens = manageable(displays);
  const [first, ...rest] = screens;
  if (first === undefined) {
    return [];
  }
  return resolutions(first).filter((size) =>
    rest.every((display) =>
      display.modes.some((mode) => mode.width === size.width && mode.height === size.height),
    ),
  );
}

// Clone: every screen on at 0,0, the same size and orientation, at 100 %; null when no size is
// common to every screen.
export function clone(displays: readonly DisplayInfo[]): DisplayInfo[] | null {
  const size = commonResolutions(displays)[0];
  if (size === undefined) {
    return null;
  }
  let cloned = [...displays];
  for (const display of manageable(displays)) {
    cloned = setEnabled(cloned, display.name, true);
    cloned = setResolution(cloned, display.name, size.width, size.height);
    cloned = update(cloned, display.name, (screen) => ({
      ...screen,
      x: 0,
      y: 0,
      rotation: "normal",
      scale: 1,
    }));
  }
  const main =
    cloned.find((display) => display.primary && display.enabled) ??
    cloned.find((display) => display.enabled);
  return main === undefined ? cloned : setPrimary(cloned, main.name);
}

// Single screen: only `name` on, as the main screen at 0,0.
export function single(displays: readonly DisplayInfo[], name: string): DisplayInfo[] {
  const target = displays.find((display) => display.name === name);
  if (target === undefined || target.modes.length === 0) {
    return [...displays];
  }
  return displays.map((display) =>
    display.name === name
      ? {
          ...display,
          enabled: true,
          primary: true,
          mode: display.mode ?? display.modes[0]?.id ?? null,
          x: 0,
          y: 0,
        }
      : { ...display, enabled: false, primary: false },
  );
}

// Whether two layouts set every screen the same way (nothing to apply).
export function sameLayout(a: readonly DisplayInfo[], b: readonly DisplayInfo[]): boolean {
  return JSON.stringify(toRequest(a)) === JSON.stringify(toRequest(b));
}

// The layout `bridge_apply_display_layout` takes: every screen, on or off.
export function toRequest(displays: readonly DisplayInfo[]): JsonValue[] {
  return displays.map((display): JsonValue => {
    if (!display.enabled) {
      return { name: display.name, enabled: false };
    }
    return {
      name: display.name,
      enabled: true,
      mode: display.mode,
      x: display.x,
      y: display.y,
      rotation: display.rotation,
      scale: display.scale,
      primary: display.primary,
    };
  });
}

// What blocks Apply, in words; null when the layout can be applied.
export function problem(displays: readonly DisplayInfo[]): string | null {
  const on = displays.filter((display) => display.enabled);
  if (on.length === 0) {
    return "Keep at least one screen on.";
  }
  if (on.filter((display) => display.primary).length !== 1) {
    return "Choose the main screen.";
  }
  if (overlapping(displays)) {
    return "Two screens overlap: drag one beside the other.";
  }
  return null;
}
