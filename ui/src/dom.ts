// Small DOM helpers shared by the window's views: elements by id, icons, text, busy state.

export function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (element === null) {
    throw new Error(`missing #${id} element`);
  }
  return element as T;
}

export const SVG_NS = "http://www.w3.org/2000/svg";

export function icon(name: string, className = "icon"): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

export function text(tag: string, className: string, content: string): HTMLElement {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = content;
  return element;
}

export function setBusy(element: HTMLElement, busy: boolean): void {
  if (busy) {
    element.setAttribute("aria-disabled", "true");
  } else {
    element.removeAttribute("aria-disabled");
  }
}

// Playback kinds are model vocabulary; the sprite names the shapes.
export function disclosure(technical: string): HTMLElement {
  const diagnostic = document.createElement("details");
  diagnostic.className = "diagnostic";
  diagnostic.append(text("summary", "", "Details"), text("pre", "", technical));
  return diagnostic;
}

export function setIcon(button: HTMLElement, name: string): void {
  button.querySelector("use")?.setAttribute("href", `#i-${name}`);
}
