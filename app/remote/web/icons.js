// Line icons on a 24x24 grid, drawn like the desktop's app/ui/studio/icons.py
// (same strokes, round caps). Each value is the inner SVG markup.

const F = 'fill="currentColor" stroke="none"';

export const ICONS = {
  play: `<path ${F} d="M7.5 4.8 19.5 12 7.5 19.2Z"/>`,
  stop: `<rect ${F} x="6.5" y="6.5" width="11" height="11" rx="2"/>`,
  window: `<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 4v16M4 12h16"/>`,
  you: `<circle cx="12" cy="7.5" r="3.8"/><path d="M4.5 21c0-5.5 3.5-7.5 7.5-7.5s7.5 2 7.5 7.5"/>`,
  plus: `<path d="M12 5v14M5 12h14"/>`,
  minus: `<path d="M5 12h14"/>`,
  close: `<path d="M6 6l12 12M18 6 6 18"/>`,
  trash: `<path d="M4 6.5h16M6 6.5l1 14h10l1-14M9 6.5l.5-3h5l.5 3M10 10.5l.3 6.5M14 10.5l-.3 6.5"/>`,
  chevron_down: `<path d="M6 9l6 6 6-6"/>`,
  chevron_up: `<path d="M6 15l6-6 6 6"/>`,
  chevron_right: `<path d="M9 6l6 6-6 6"/>`,
  chevron_left: `<path d="M15 6l-6 6 6 6"/>`,
  cloud_rain: `<path d="M7.6 15.2h9.6a3.3 3.3 0 0 0 .3-6.6 4.6 4.6 0 0 0-8.9-.6 3.6 3.6 0 0 0-1 7.2Z"/><path d="M8.5 17.8l-1 3M12.5 17.8l-1 3M16.5 17.8l-1 3"/>`,
  droplet: `<path d="M12 3s-6.5 7.5-6.5 11.5A6.5 6.5 0 0 0 12 21a6.5 6.5 0 0 0 6.5-6.5C18.5 10.5 12 3 12 3Z"/>`,
  plan: `<rect x="3.5" y="3.5" width="17" height="17" rx="2"/><path d="M3.5 10h17M10 10v10.5"/>`,
  headphones: `<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="13.5" width="5" height="7" rx="2"/><rect x="16" y="13.5" width="5" height="7" rx="2"/>`,
  volume: `<path d="M4 9.5h4l4.5-4v13L8 14.5H4Z"/><path d="M15 9.2a4 4 0 0 1 0 5.6M17.2 6.6a8 8 0 0 1 0 10.8"/>`,
  house: `<path d="M3 11.5 12 4l9 7.5M5.5 9.8V20h13V9.8M10 20v-5.5h4V20"/>`,
  pencil: `<path d="M15.5 4.5l4 4-11 11h-4v-4Z"/><path d="M13 7l4 4"/>`,
  sliders: `<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="15" cy="6" r="2.2" fill="var(--icon-bg, var(--surface))"/><circle cx="8" cy="12" r="2.2" fill="var(--icon-bg, var(--surface))"/><circle cx="13" cy="18" r="2.2" fill="var(--icon-bg, var(--surface))"/>`,
  info: `<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><circle ${F} cx="12" cy="7.8" r="1"/>`,
  help: `<circle cx="12" cy="12" r="8.5"/><path d="M9.3 9.6C9.3 7.9 10.6 7 12 7c1.6 0 2.8 1 2.8 2.5 0 2.1-2.8 2.3-2.8 4.3"/><circle ${F} cx="12" cy="16.8" r="1"/>`,
  link: `<path d="M14 4h6v6M20 4l-9 9M17.5 13.5v6h-13v-13h6"/>`,
  bolt: `<path d="M13.5 2.5 6 13.5h5.5L10 21.5 18 10h-5.5z"/>`,
  wind: `<path d="M3 9h11c3 0 3.5-4 1-4.5M3 13.5h15c3.5 0 3.5 5 .5 5.5M3 17.5h8"/>`,
  speaker: `<rect x="6" y="2.5" width="12" height="19" rx="2.5"/><circle cx="12" cy="14.5" r="3.6"/><circle ${F} cx="12" cy="7" r="1"/>`,
  phone: `<rect x="6.5" y="2.5" width="11" height="19" rx="2.5"/><path d="M10.5 18.5h3"/>`,
  refresh: `<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.8 4.2v4.5h-4.5"/>`,
  compass: `<circle cx="12" cy="12" r="8.5"/><path ${F} d="M12 5.8l2.4 6.2H9.6Z"/><path d="M9.6 12 12 18.2l2.4-6.2"/>`,
  wifi_off: `<path d="M3 3l18 18M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5-2.6M14.5 10.6A10 10 0 0 1 19 13M2 9.5a15 15 0 0 1 4.5-2.9M11 5.6A15 15 0 0 1 22 9.5"/><circle ${F} cx="12" cy="19.5" r="1"/>`,
  check: `<path d="M5 12.5l4.5 4.5L19 7.5"/>`,
  lock: `<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>`,
  rotate: `<path d="M4.5 12a7.5 7.5 0 0 1 12.8-5.3L19.5 9"/><path d="M19.8 4.2v4.8H15M19.5 12a7.5 7.5 0 0 1-12.8 5.3L4.5 15"/><path d="M4.2 19.8V15H9"/>`,
};

export function svg(name, cls = "") {
  const body = ICONS[name] || ICONS.info;
  return `<svg class="svg ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}

/** Fill every <span data-icon="name"> under root with its SVG. */
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    const name = el.getAttribute("data-icon");
    if (el.dataset.iconDone === name) return;
    el.innerHTML = svg(name);
    el.dataset.iconDone = name;
  });
}

export function setIcon(el, name) {
  el.setAttribute("data-icon", name);
  el.innerHTML = svg(name);
  el.dataset.iconDone = name;
}
