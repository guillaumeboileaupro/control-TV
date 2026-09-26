// How the volume slider and the mute button drive the state, without a DOM.
//
// Moving a slider produces a burst of events: dozens while dragging, one per key press, and a
// held key repeats. Each of those would be a network round trip to the TV if it were sent, so the
// remote works locally while the control moves (a draft: no request) and sends once, after the
// control has settled. At most one command is ever in flight (see `idle` in model.ts), so a
// second gesture cannot queue behind the first, and a level the TV already reports is not sent.
// `main.ts` only connects DOM events to the functions below; the timer is injected so the
// behaviour is the same code under test and in the application.

import type { AppState } from "./model.ts";
import type { CommandRequest } from "./playback.ts";
import { setVolumeDraft, startMute, startVolume } from "./sound.ts";

// How long the control must stay still before its command is sent. A pointer release is a clear
// end of a gesture, so a short wait is enough. A key press is not: the first press of a held key
// is followed by a pause (commonly 500 to 660 ms) before it starts repeating, and the wait must
// outlast that pause or the hold would be sent as two commands. Whatever is driving the control
// is unknown until it declares itself, so the longer, safe wait is the default.
export const VOLUME_SETTLE_POINTER_MS = 300;
export const VOLUME_SETTLE_KEYBOARD_MS = 800;

export interface Timers {
  set(callback: () => void, delayMs: number): number;
  clear(handle: number): void;
}

export interface Settler {
  // (Re)starts the wait: `onSettled` runs once, `delayMs` after the last touch (by default the
  // delay the settler was created with).
  touch(delayMs?: number): void;
  // Forgets a wait in progress: nothing runs until the next touch.
  cancel(): void;
}

export function createSettler(delayMs: number, timers: Timers, onSettled: () => void): Settler {
  let handle: number | null = null;
  return {
    touch(waitMs = delayMs): void {
      if (handle !== null) {
        timers.clear(handle);
      }
      handle = timers.set(() => {
        handle = null;
        onSettled();
      }, waitMs);
    },
    cancel(): void {
      if (handle !== null) {
        timers.clear(handle);
        handle = null;
      }
    },
  };
}

export interface SoundControllerDeps {
  getState(): AppState;
  setState(next: AppState): void;
  // Performs the I/O for one command that the state has already marked as pending.
  run(request: CommandRequest): void;
  timers: Timers;
}

export interface SoundController {
  // What is driving the volume control: a pointer (mouse, touch, pen) or the keyboard. It only
  // chooses how long to wait after the control stops moving; it never blocks a command.
  volumePointerDown(): void;
  volumeKeyDown(): void;
  // The volume control moved: update the draft, send nothing, and forget any wait in progress
  // (the control is still moving).
  volumeInput(percent: number): void;
  // The volume control was released or a key step was made: send the draft once it has settled.
  volumeChange(): void;
  // The mute button was pressed.
  mute(): void;
}

export function createSoundController(deps: SoundControllerDeps): SoundController {
  const commit = (): void => {
    const next = startVolume(deps.getState());
    deps.setState(next.state);
    if (next.request !== null) {
      deps.run(next.request);
    }
  };
  const settler = createSettler(VOLUME_SETTLE_KEYBOARD_MS, deps.timers, commit);
  let driver: "pointer" | "keyboard" = "keyboard";

  return {
    volumePointerDown(): void {
      driver = "pointer";
    },
    volumeKeyDown(): void {
      driver = "keyboard";
    },
    volumeInput(percent: number): void {
      settler.cancel();
      deps.setState(setVolumeDraft(deps.getState(), percent));
    },
    volumeChange(): void {
      settler.touch(driver === "pointer" ? VOLUME_SETTLE_POINTER_MS : VOLUME_SETTLE_KEYBOARD_MS);
    },
    mute(): void {
      // No need to cancel a wait for a volume draft: pressing mute starts a command, which drops
      // the draft, and a settled wait with no draft sends nothing. A refused press leaves the
      // draft and its wait alone.
      const next = startMute(deps.getState());
      deps.setState(next.state);
      if (next.request !== null) {
        deps.run(next.request);
      }
    },
  };
}
