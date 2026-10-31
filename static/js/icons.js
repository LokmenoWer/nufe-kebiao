/** 内联 SVG 图标（stroke 风格）。全部通过 DOM 创建，不引入外部图标库。 */

const PATHS = {
  "chevron-left": ["M15 18l-6-6 6-6"],
  "chevron-right": ["M9 18l6-6-6-6"],
  "chevron-down": ["M6 9l6 6 6-6"],
  refresh: ["M21 12a9 9 0 1 1-2.64-6.36", "M21 4v5h-5"],
  x: ["M18 6L6 18", "M6 6l12 12"],
  copy: ["M9 9h10v10H9z", "M5 15V5h10"],
  logout: ["M15 4h4v16h-4", "M10 8l-4 4 4 4", "M6 12h10"],
  pin: ["M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z",
    "M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"],
  user: ["M20 21a8 8 0 0 0-16 0", "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"],
  calendar: ["M5 5h14v15H5z", "M5 10h14", "M9 3v4", "M15 3v4"],
  clock: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M12 7v5l3 2"],
  moon: ["M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z"],
  sun: ["M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z", "M12 2v2", "M12 20v2", "M4.9 4.9l1.4 1.4",
    "M17.7 17.7l1.4 1.4", "M2 12h2", "M20 12h2", "M4.9 19.1l1.4-1.4", "M17.7 6.3l1.4-1.4"],
  settings: ["M4 7h16", "M4 17h16", "M9 4v6", "M15 14v6"],
  external: ["M14 4h6v6", "M20 4l-9 9", "M18 14v5H5V6h5"],
  check: ["M20 6L9 17l-5-5"],
  alert: ["M12 3l9 17H3z", "M12 9v5", "M12 17h.01"],
  info: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M12 11v5", "M12 8h.01"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 14h10l1-14", "M10 11v6", "M14 11v6"],
  qr: ["M4 4h6v6H4z", "M14 4h6v6h-6z", "M4 14h6v6H4z", "M14 14h2v2h-2z",
    "M18 14h2v2h-2z", "M14 18h2v2h-2z", "M18 18h2v2h-2z"],
  key: ["M14 7a4 4 0 1 0-3.7 5.4L4 19v2h2l2-2v-2h2v-2h1.6A4 4 0 0 0 14 7z", "M16.5 5.5h.01"],
  eye: ["M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  "eye-off": ["M3 3l18 18", "M10.6 6.2A9.8 9.8 0 0 1 12 6c6.5 0 10 6 10 6a17.6 17.6 0 0 1-3.4 3.9",
    "M6.2 6.7C3.5 8.5 2 12 2 12s3.5 6 10 6c1.5 0 2.8-.3 4-.8", "M9.9 9.9a3 3 0 0 0 4.2 4.2"],
  grid: ["M4 4h7v7H4z", "M13 4h7v7h-7z", "M4 13h7v7H4z", "M13 13h7v7h-7z"],
  list: ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
  "arrow-right": ["M5 12h14", "M13 6l6 6-6 6"],
  book: ["M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2z", "M4 17h15"],
  layers: ["M12 3l9 5-9 5-9-5z", "M3 13l9 5 9-5", "M3 17l9 5 9-5"],
  sparkles: ["M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8z",
    "M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"],
};

export function icon(name, { className = "" } = {}) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.9");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", `icon${className ? ` ${className}` : ""}`);
  for (const d of PATHS[name] || []) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

export const REPO_URL = "https://github.com/LokmenoWer/nufe-kebiao";
