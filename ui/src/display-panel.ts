// The display manager in the "Screen to TV" card, like GNOME's Settings > Displays: numbered
// screens dragged on a scene with magnetic edges, the selected screen's resolution, refresh rate,
// orientation, scale and main-screen setting, the Join / Clone / Single screen modes, and Apply,
// Cancel and Revert. An applied layout must be kept within the countdown, or the bridge reverts
// it by itself. Before an extension starts, the TV's screen-to-be is shown too and can be dragged
// into place (its position goes with the start); it is not applied from here.

import { setBusy } from "./dom.ts";
import type { JsonValue } from "./json.ts";
import { toBridgeFailure } from "./model.ts";
import {
  ROTATIONS,
  SCALES,
  arrangementOf,
  clone,
  currentMode,
  join,
  logicalSize,
  manageable,
  move,
  normalize,
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

const KEEP_SECONDS = 20;
const SCENE_PADDING = 12;

export interface PendingTv {
  name: string;
  width: number;
  height: number;
  x: number;
  y: number;
}

export interface DisplayPanelElements {
  scene: HTMLElement;
  arrangement: HTMLElement; // the Join / Clone / Single buttons
  settings: HTMLElement;
  title: HTMLElement;
  enabled: HTMLInputElement;
  primary: HTMLInputElement;
  resolution: HTMLSelectElement;
  rate: HTMLSelectElement;
  rotation: HTMLSelectElement;
  scale: HTMLSelectElement;
  apply: HTMLButtonElement;
  cancel: HTMLButtonElement;
  keep: HTMLElement;
  keepText: HTMLElement;
  keepButton: HTMLButtonElement;
  revertButton: HTMLButtonElement;
  message: HTMLElement;
}

export interface DisplayPanelPorts {
  call(command: string, args: Record<string, JsonValue>): Promise<JsonValue>;
  timers: {
    set(callback: () => void, delayMs: number): number;
    clear(handle: number): void;
  };
  // The TV's screen before the extension starts (null once it is a real screen, or not
  // extending), and where to put it.
  pendingTv(): PendingTv | null;
  placeTv(x: number, y: number): void;
}

interface Item {
  name: string; // the first screen of the tile
  number: number;
  // Screens set as clones share one tile, like Settings > Displays: "1 | 2".
  label: string;
  cloned: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
  pending: boolean;
}

export function createDisplayPanel(
  el: DisplayPanelElements,
  ports: DisplayPanelPorts,
): { refresh(): void; render(): void } {
  let applied: DisplayInfo[] = [];
  let draft: DisplayInfo[] = [];
  let selected: string | null = null;
  let busy = false;
  let keepLeft = 0;
  let keepTimer: number | null = null;
  let message = "";

  async function answer(command: string, args: Record<string, JsonValue> = {}): Promise<void> {
    busy = true;
    render();
    try {
      const layout = parseLayout(await ports.call(command, args));
      if (layout === null) {
        message = "The screens could not be read.";
      } else {
        applied = layout.displays;
        draft = layout.displays;
        if (layout.revertedByItself) {
          message = "The last change was not kept, so it was reverted.";
        }
        if (layout.awaitingConfirmation) {
          startCountdown();
        } else {
          stopCountdown();
        }
      }
    } catch (error) {
      message = toBridgeFailure(error).message;
      await reread();
    } finally {
      busy = false;
      render();
    }
  }

  async function reread(): Promise<void> {
    try {
      const layout = parseLayout(await ports.call("bridge_get_display_layout", {}));
      if (layout !== null) {
        applied = layout.displays;
        draft = layout.displays;
      }
    } catch {
      // the message already says what failed
    }
  }

  function startCountdown(): void {
    stopCountdown();
    keepLeft = KEEP_SECONDS;
    const tick = (): void => {
      keepLeft -= 1;
      if (keepLeft <= 0) {
        keepTimer = null;
        void answer("bridge_get_display_layout"); // the bridge reverted it by itself
        return;
      }
      render();
      keepTimer = ports.timers.set(tick, 1000);
    };
    keepTimer = ports.timers.set(tick, 1000);
  }

  function stopCountdown(): void {
    if (keepTimer !== null) {
      ports.timers.clear(keepTimer);
      keepTimer = null;
    }
    keepLeft = 0;
  }

  function edit(next: DisplayInfo[]): void {
    draft = next;
    message = "";
    render();
  }

  function items(): Item[] {
    const shown: Item[] = [];
    manageable(draft)
      .filter((display) => display.enabled)
      .forEach((display, index) => {
        const size = logicalSize(display);
        const same = shown.find(
          (item) =>
            item.x === display.x &&
            item.y === display.y &&
            item.width === size.width &&
            item.height === size.height,
        );
        if (same !== undefined) {
          same.label = `${same.label} | ${index + 1}`;
          same.cloned = true;
          return;
        }
        shown.push({
          name: display.name,
          number: index + 1,
          label: String(index + 1),
          cloned: false,
          x: display.x,
          y: display.y,
          ...size,
          pending: false,
        });
      });
    const tv = ports.pendingTv();
    if (tv !== null && !draft.some((display) => display.name === tv.name && display.enabled)) {
      const number = shown.length + 1;
      shown.push({ ...tv, number, label: String(number), cloned: false, pending: true });
    }
    return shown;
  }

  function sceneScale(shown: readonly Item[]): { scale: number; left: number; top: number } {
    const left = Math.min(...shown.map((item) => item.x));
    const top = Math.min(...shown.map((item) => item.y));
    const right = Math.max(...shown.map((item) => item.x + item.width));
    const bottom = Math.max(...shown.map((item) => item.y + item.height));
    const width = Math.max(1, el.scene.clientWidth - 2 * SCENE_PADDING);
    const height = Math.max(1, el.scene.clientHeight - 2 * SCENE_PADDING);
    return { scale: Math.min(width / (right - left), height / (bottom - top)), left, top };
  }

  // The pending TV is snapped against the real screens like a screen of the draft.
  function snapPending(tv: PendingTv, x: number, y: number): { x: number; y: number } {
    const ghost: DisplayInfo = {
      name: tv.name,
      connected: true,
      enabled: false,
      mode: "pending",
      x,
      y,
      rotation: "normal",
      scale: 1,
      primary: false,
      modes: [{ id: "pending", width: tv.width, height: tv.height, rate: 60 }],
    };
    return snap([...draft.filter((display) => display.name !== tv.name), ghost], tv.name, x, y);
  }

  function drawScene(): void {
    const shown = items();
    if (shown.length === 0) {
      el.scene.replaceChildren();
      return;
    }
    const { scale, left, top } = sceneScale(shown);
    el.scene.replaceChildren(
      ...shown.map((item) => {
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "display-tile";
        tile.classList.toggle("is-pending", item.pending);
        tile.classList.toggle("is-selected", item.name === selected);
        const primary = draft.find((display) => display.name === item.name)?.primary === true;
        tile.classList.toggle("is-primary", primary);
        tile.setAttribute("aria-pressed", String(item.name === selected));
        tile.setAttribute(
          "aria-label",
          `${item.label}: ${item.pending ? "TV (when extending)" : item.name}` +
            (item.cloned ? " (mirrored)" : "") +
            (primary ? ", main screen" : "") +
            `, ${item.width}×${item.height} at ${item.x}, ${item.y}`,
        );
        const place = (x: number, y: number): void => {
          tile.style.left = `${SCENE_PADDING + (x - left) * scale}px`;
          tile.style.top = `${SCENE_PADDING + (y - top) * scale}px`;
        };
        place(item.x, item.y);
        tile.style.width = `${Math.max(24, item.width * scale)}px`;
        tile.style.height = `${Math.max(18, item.height * scale)}px`;
        const number = document.createElement("span");
        number.className = "display-number";
        number.textContent = item.label;
        const name = document.createElement("span");
        name.className = "display-name";
        name.textContent = item.pending ? "TV" : item.name;
        tile.append(number, name);
        tile.addEventListener("click", () => {
          if (!item.pending) {
            selected = item.name;
            render();
          }
        });
        tile.addEventListener("keydown", (event) => {
          const steps: Record<string, [number, number]> = {
            ArrowLeft: [-1, 0],
            ArrowRight: [1, 0],
            ArrowUp: [0, -1],
            ArrowDown: [0, 1],
          };
          const step = steps[event.key];
          if (step === undefined || busy || item.cloned) {
            return;
          }
          event.preventDefault();
          drop(item, item.x + step[0] * 100, item.y + step[1] * 100);
        });
        tile.addEventListener("pointerdown", (event) => {
          if (busy || keepTimer !== null || item.cloned) {
            return; // mirrored screens are not arranged
          }
          const startX = event.clientX;
          const startY = event.clientY;
          let at = { x: item.x, y: item.y };
          tile.setPointerCapture(event.pointerId);
          tile.classList.add("is-dragging");
          const moving = (pointer: PointerEvent): void => {
            const x = item.x + (pointer.clientX - startX) / scale;
            const y = item.y + (pointer.clientY - startY) / scale;
            at = item.pending
              ? snapPending(item, x, y)
              : snap(move(draft, item.name, x, y), item.name, x, y);
            place(at.x, at.y);
          };
          const finish = (): void => {
            tile.removeEventListener("pointermove", moving);
            tile.removeEventListener("pointerup", finish);
            tile.removeEventListener("pointercancel", finish);
            tile.classList.remove("is-dragging");
            drop(item, at.x, at.y);
          };
          tile.addEventListener("pointermove", moving);
          tile.addEventListener("pointerup", finish);
          tile.addEventListener("pointercancel", finish);
        });
        return tile;
      }),
    );
  }

  function drop(item: Item, x: number, y: number): void {
    if (item.pending) {
      const tv = ports.pendingTv();
      if (tv !== null) {
        const at = snapPending(tv, x, y);
        ports.placeTv(at.x, at.y);
      }
      return;
    }
    selected = item.name;
    const at = snap(draft, item.name, x, y);
    edit(normalize(move(draft, item.name, at.x, at.y)));
  }

  function fill(
    select: HTMLSelectElement,
    options: readonly [string, string][],
    value: string,
  ): void {
    const key = JSON.stringify(options);
    if (select.dataset["key"] !== key) {
      select.dataset["key"] = key;
      select.replaceChildren(
        ...options.map(([optionValue, label]) => new Option(label, optionValue)),
      );
    }
    select.value = value;
  }

  function drawSettings(): void {
    const display = draft.find((candidate) => candidate.name === selected) ?? null;
    el.settings.hidden = display === null;
    if (display === null) {
      return;
    }
    el.title.textContent = display.name;
    el.enabled.checked = display.enabled;
    el.primary.checked = display.primary;
    const mode = currentMode(display);
    fill(
      el.resolution,
      resolutions(display).map(({ width, height }) => [
        `${width}x${height}`,
        `${width} × ${height}`,
      ]),
      mode === null ? "" : `${mode.width}x${mode.height}`,
    );
    fill(
      el.rate,
      ratesAtSize(display).map((rate) => [rate.id, `${rate.rate.toFixed(2)} Hz`]),
      display.mode ?? "",
    );
    fill(
      el.rotation,
      ROTATIONS.map(({ value, label }) => [value, label]),
      display.rotation,
    );
    fill(
      el.scale,
      SCALES.map(({ factor, label }) => [String(factor), label]),
      String(display.scale),
    );
    const locked = busy || keepTimer !== null;
    for (const field of [el.resolution, el.rate, el.rotation, el.scale]) {
      field.disabled = locked || !display.enabled;
    }
    el.enabled.disabled = locked;
    el.primary.disabled = locked || !display.enabled || display.primary;
  }

  function render(): void {
    if (selected === null || !draft.some((display) => display.name === selected)) {
      selected = draft.find((display) => display.primary)?.name ?? null;
    }
    drawScene();
    drawSettings();
    const locked = busy || keepTimer !== null;
    const mode = arrangementOf(draft);
    for (const button of el.arrangement.querySelectorAll<HTMLButtonElement>("[data-arrangement]")) {
      button.setAttribute("aria-pressed", String(button.dataset["arrangement"] === mode));
      setBusy(button, locked || manageable(draft).length < 2);
    }
    const changed = !sameLayout(draft, applied);
    const blocked = problem(draft);
    el.apply.disabled = false;
    setBusy(el.apply, locked || !changed || blocked !== null);
    setBusy(el.cancel, locked || !changed);
    el.keep.hidden = keepTimer === null;
    el.keepText.textContent = `Keep these display settings? Reverting in ${keepLeft} s.`;
    const shown = changed && blocked !== null ? blocked : message;
    el.message.hidden = shown === "";
    el.message.textContent = shown;
  }

  for (const button of el.arrangement.querySelectorAll<HTMLButtonElement>("[data-arrangement]")) {
    button.addEventListener("click", () => {
      if (busy || keepTimer !== null || manageable(draft).length < 2) {
        return;
      }
      const wanted = button.dataset["arrangement"];
      if (wanted === "join") {
        edit(join(draft));
      } else if (wanted === "clone") {
        const cloned = clone(draft);
        if (cloned === null) {
          message = "These screens share no resolution: they cannot be cloned.";
          render();
        } else {
          edit(cloned);
        }
      } else if (wanted === "single" && selected !== null) {
        edit(single(draft, selected));
      }
    });
  }
  el.enabled.addEventListener("change", () => {
    if (selected !== null) {
      const next = setEnabled(draft, selected, el.enabled.checked);
      edit(normalize(next));
    }
  });
  el.primary.addEventListener("change", () => {
    if (selected !== null && el.primary.checked) {
      edit(setPrimary(draft, selected));
    }
  });
  el.resolution.addEventListener("change", () => {
    const [width, height] = el.resolution.value.split("x").map(Number);
    if (selected !== null && width !== undefined && height !== undefined) {
      edit(normalize(setResolution(draft, selected, width, height)));
    }
  });
  el.rate.addEventListener("change", () => {
    if (selected !== null) {
      edit(setMode(draft, selected, el.rate.value));
    }
  });
  el.rotation.addEventListener("change", () => {
    const rotation = ROTATIONS.find(({ value }) => value === el.rotation.value)?.value;
    if (selected !== null && rotation !== undefined) {
      edit(normalize(setRotation(draft, selected, rotation)));
    }
  });
  el.scale.addEventListener("change", () => {
    if (selected !== null) {
      edit(normalize(setScale(draft, selected, Number(el.scale.value))));
    }
  });
  el.apply.addEventListener("click", () => {
    if (busy || keepTimer !== null || sameLayout(draft, applied) || problem(draft) !== null) {
      return;
    }
    message = "";
    void answer("bridge_apply_display_layout", { displays: toRequest(normalize(draft)) });
  });
  el.cancel.addEventListener("click", () => {
    if (!busy && keepTimer === null) {
      edit(applied);
    }
  });
  el.keepButton.addEventListener("click", () => {
    stopCountdown();
    void answer("bridge_confirm_display_layout");
  });
  el.revertButton.addEventListener("click", () => {
    stopCountdown();
    void answer("bridge_revert_display_layout");
  });

  return { refresh: () => void answer("bridge_get_display_layout"), render };
}
