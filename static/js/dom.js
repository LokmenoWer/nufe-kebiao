/** 极简 DOM 构建工具：只用 textContent / setAttribute，不拼 HTML 字符串。 */

const BOOLEAN_PROPS = new Set(["checked", "disabled", "selected", "readOnly", "required", "multiple", "hidden"]);

let idCounter = 0;

export function nextId(prefix = "kb") {
  idCounter += 1;
  return `${prefix}-${idCounter}`;
}

function isChild(value) {
  return typeof value === "string" || typeof value === "number" || value instanceof Node;
}

function appendChildren(node, children) {
  for (const child of children) {
    if (Array.isArray(child)) {
      appendChildren(node, child);
    } else if (child !== null && child !== undefined && child !== false) {
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }
}

export function h(tag, props, ...children) {
  let attributes = props;
  if (attributes === null || attributes === undefined
    || isChild(attributes) || Array.isArray(attributes)) {
    children.unshift(attributes);
    attributes = {};
  }
  const node = document.createElement(tag);

  for (const [key, raw] of Object.entries(attributes)) {
    if (raw === null || raw === undefined || raw === false) continue;
    if (key === "class" || key === "className") {
      node.className = String(raw);
    } else if (key === "text") {
      node.textContent = String(raw);
    } else if (key === "style" && typeof raw === "object") {
      for (const [prop, value] of Object.entries(raw)) {
        if (value === null || value === undefined) continue;
        if (prop.startsWith("--") || prop.includes("-")) node.style.setProperty(prop, String(value));
        else node.style[prop] = String(value);
      }
    } else if (key === "dataset" && typeof raw === "object") {
      for (const [name, value] of Object.entries(raw)) {
        if (value !== null && value !== undefined) node.dataset[name] = String(value);
      }
    } else if (key === "attrs" && typeof raw === "object") {
      for (const [name, value] of Object.entries(raw)) {
        if (value !== null && value !== undefined && value !== false) node.setAttribute(name, String(value));
      }
    } else if (key === "on" && typeof raw === "object") {
      for (const [event, handler] of Object.entries(raw)) {
        if (typeof handler === "function") node.addEventListener(event, handler);
      }
    } else if (key.startsWith("on") && typeof raw === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), raw);
    } else if (BOOLEAN_PROPS.has(key)) {
      // 能走到这里说明 raw 不是 false/null/undefined；空字符串也表示“设置该布尔属性”
      node[key] = true;
      node.setAttribute(key.toLowerCase(), "");
    } else if (key === "value") {
      node.value = String(raw);
      node.setAttribute("value", String(raw));
    } else {
      node.setAttribute(key, raw === true ? "" : String(raw));
    }
  }

  appendChildren(node, children);
  return node;
}

export function replace(node, ...children) {
  node.replaceChildren();
  appendChildren(node, children);
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function on(target, type, handler, options) {
  target.addEventListener(type, handler, options);
  return () => target.removeEventListener(type, handler, options);
}

export function debounce(fn, wait = 150) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
}

export function isTypingTarget(target) {
  if (!target || !(target instanceof Element)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}
