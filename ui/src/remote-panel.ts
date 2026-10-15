// The TV remote panel: the TV's own keys, pairing and text, and its installed apps.
//
// The panel owns its elements, its redraw keys and its event wiring; it holds no application
// state and sends nothing itself: every action goes to `actions.run`, and `render` draws what
// `describeRemote` says of the state `main.ts` hands in.

import { disclosure, icon, setBusy, text } from "./dom.ts";
import { appLogo } from "./app-logos.ts";
import type { AppState } from "./model.ts";
import {
  appHue,
  appInitial,
  appLabel,
  describeRemote,
  type RemoteAction,
  type RemoteDescription,
  type RemoteKeyName,
} from "./remote.ts";

export interface RemotePanelElements {
  tvRemote: HTMLElement;
  remoteKeys: HTMLElement;
  remotePairing: HTMLElement;
  remotePairingText: HTMLElement;
  remoteCode: HTMLInputElement;
  remotePair: HTMLButtonElement;
  remoteShowCode: HTMLButtonElement;
  remoteRead: HTMLButtonElement;
  remoteTextForm: HTMLFormElement;
  remoteText: HTMLInputElement;
  remoteTextSend: HTMLButtonElement;
  remoteAppsList: HTMLButtonElement;
  remoteAppsFilter: HTMLInputElement;
  remoteApps: HTMLElement;
  remoteReadout: HTMLElement;
  remoteFeedback: HTMLElement;
}

export interface RemotePanelActions {
  // Runs one remote action (it is sent only if the state allows it).
  run(action: RemoteAction): void;
  // Narrows the app list; sends nothing.
  filterApps(filter: string): void;
  // Moves keyboard focus out of the panel when the panel hides while it had it.
  focusElsewhere(): void;
}

export function createRemotePanel(
  el: RemotePanelElements,
  actions: RemotePanelActions,
): { render(state: AppState): void } {
  // The feedback and the app list are redrawn only when what they show changed.
  let feedbackKey = "";
  let appsKey = "";

  for (const button of el.tvRemote.querySelectorAll<HTMLButtonElement>("[data-remote-key]")) {
    button.addEventListener("click", () =>
      actions.run({ kind: "key", key: button.dataset["remoteKey"] as RemoteKeyName }),
    );
  }
  el.remoteRead.addEventListener("click", () => actions.run({ kind: "status" }));
  el.remoteAppsList.addEventListener("click", () => actions.run({ kind: "list_apps" }));
  el.remoteAppsFilter.addEventListener("input", () =>
    actions.filterApps(el.remoteAppsFilter.value),
  );
  el.remoteTextForm.addEventListener("submit", (event) => {
    event.preventDefault();
    actions.run({ kind: "text", text: el.remoteText.value });
  });
  el.remoteShowCode.addEventListener("click", () => actions.run({ kind: "show_code" }));
  const pair = (): void => actions.run({ kind: "pair", code: el.remoteCode.value });
  el.remotePair.addEventListener("click", pair);
  el.remoteCode.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      pair();
    }
  });

  // The TV's own remote: keys usable only when nothing else runs and no pairing step is in the
  // way; the pairing area only when the TV asked for it; the TV's last report; the outcome.
  function render(state: AppState): void {
    const remote = describeRemote(state);
    const active = document.activeElement;
    const hadFocus = active !== null && el.tvRemote.contains(active);
    el.tvRemote.hidden = !remote.visible;
    el.remoteKeys.hidden = !remote.remoteVisible;
    if (!remote.visible) {
      if (hadFocus) {
        actions.focusElsewhere();
      }
      return;
    }
    for (const button of el.tvRemote.querySelectorAll<HTMLButtonElement>("[data-remote-key]")) {
      const sending = remote.pendingKey === button.dataset["remoteKey"];
      setBusy(button, !remote.keysEnabled);
      button.classList.toggle("is-loading", sending);
      button.setAttribute("aria-busy", String(sending));
    }
    setBusy(el.remoteRead, !remote.readEnabled);
    setBusy(el.remoteTextSend, !remote.keysEnabled);
    el.remoteTextSend.classList.toggle("is-loading", remote.sendingText);
    renderApps(state, remote);
    el.remoteTextSend.setAttribute("aria-busy", String(remote.sendingText));

    el.remotePairing.hidden = remote.pairing === null;
    if (remote.pairing !== null) {
      el.remotePairingText.textContent = remote.pairing.text;
      el.remoteCode.hidden = !remote.pairing.codeEntry;
      el.remotePair.hidden = !remote.pairing.codeEntry;
      el.remoteShowCode.textContent = remote.pairing.codeEntry
        ? "Show a new code"
        : "Show code on TV";
      setBusy(el.remotePair, remote.busy);
      setBusy(el.remoteShowCode, remote.busy);
      el.remoteCode.disabled = remote.busy;
    }

    el.remoteReadout.textContent = remote.readout;
    el.remoteReadout.classList.toggle("is-empty", remote.readout === "");

    const key = JSON.stringify([remote.progress, remote.outcome]);
    if (key === feedbackKey) {
      return;
    }
    feedbackKey = key;
    el.remoteFeedback.replaceChildren();
    if (remote.progress !== null) {
      const row = text("p", "feedback is-loading", "");
      row.append(icon("buffering"), text("span", "", remote.progress));
      el.remoteFeedback.append(row);
    } else if (remote.outcome !== null) {
      const outcome = remote.outcome;
      const row = text(
        "p",
        `feedback feedback-${outcome.tone === "done" ? "info" : "warning"}`,
        "",
      );
      row.append(
        icon(outcome.tone === "failed" ? "alert" : "info"),
        text("span", "", outcome.text),
      );
      el.remoteFeedback.append(row);
      if (outcome.technical !== null) {
        el.remoteFeedback.append(disclosure(outcome.technical));
      }
    }
  }

  // The TV's apps: the list button, the filter once listed, one button per app. The list is
  // rebuilt only when what it shows changed, keeping focus on the same app.
  function renderApps(state: AppState, remote: RemoteDescription): void {
    setBusy(el.remoteAppsList, !remote.appsEnabled);
    el.remoteAppsList.classList.toggle("is-loading", remote.listingApps);
    el.remoteAppsList.setAttribute("aria-busy", String(remote.listingApps));
    el.remoteAppsList.textContent = remote.apps === null ? "Show TV apps" : "List again";
    el.remoteAppsFilter.hidden = remote.apps === null;
    // The field shows the filter the state holds: another TV starts with an empty one.
    if (el.remoteAppsFilter.value !== state.remote.appFilter) {
      el.remoteAppsFilter.value = state.remote.appFilter;
    }
    const key = JSON.stringify([remote.apps, remote.launchEnabled, remote.pendingApp]);
    if (key === appsKey) {
      return;
    }
    appsKey = key;
    const focused = document.activeElement?.getAttribute("data-package") ?? null;
    el.remoteApps.replaceChildren();
    for (const app of remote.apps ?? []) {
      const item = document.createElement("li");
      const button = text("button", "app-tile", "") as HTMLButtonElement;
      button.type = "button";
      button.dataset["package"] = app.package;
      button.title = app.package;
      button.setAttribute("aria-label", `Open ${appLabel(app)} on the TV`);
      const avatar = text("span", "app-avatar", "");
      avatar.setAttribute("aria-hidden", "true");
      const logo = appLogo(app.package);
      if (logo === null) {
        avatar.textContent = appInitial(appLabel(app));
        avatar.style.setProperty("--hue", String(appHue(app.package)));
      } else {
        avatar.classList.add("has-logo");
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("focusable", "false");
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", logo.path);
        path.setAttribute("fill", `#${logo.hex}`);
        svg.append(path);
        avatar.append(svg);
      }
      button.append(avatar, text("span", "app-name", appLabel(app)));
      const opening = remote.pendingApp === app.package;
      setBusy(button, !remote.launchEnabled);
      button.classList.toggle("is-loading", opening);
      button.setAttribute("aria-busy", String(opening));
      button.addEventListener("click", () =>
        actions.run({ kind: "launch_app", package: app.package, label: appLabel(app) }),
      );
      item.append(button);
      el.remoteApps.append(item);
    }
    if (focused !== null) {
      el.remoteApps.querySelector<HTMLElement>(`[data-package="${CSS.escape(focused)}"]`)?.focus();
    }
  }

  return { render };
}
