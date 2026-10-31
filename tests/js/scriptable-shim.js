/**
 * 极简的 Scriptable 运行时替身：把小组件脚本在 Node 里跑一遍，记录它画出了什么。
 * 只实现脚本用到的 API；目的是在没有 iPhone 的情况下确认各尺寸 / 模式都能渲染、不抛错。
 */
import fs from "node:fs";
import vm from "node:vm";

class Node {
  constructor(kind) {
    this.kind = kind;
    this.children = [];
  }

  addText(text) {
    const t = new WidgetText(text);
    this.children.push(t);
    return t;
  }

  addStack() {
    const s = new WidgetStack();
    this.children.push(s);
    return s;
  }

  addSpacer(length) {
    this.children.push({ kind: "spacer", length });
  }

  setPadding(...p) {
    this.padding = p;
  }

  texts() {
    const out = [];
    const walk = (n) => {
      if (n.kind === "text") out.push(n.text);
      for (const c of n.children || []) walk(c);
    };
    walk(this);
    return out;
  }
}

class WidgetText {
  constructor(text) {
    this.kind = "text";
    this.text = text;
  }

  centerAlignText() {}
}

class WidgetStack extends Node {
  constructor() {
    super("stack");
  }

  layoutVertically() {}

  layoutHorizontally() {}

  centerAlignContent() {}

  topAlignContent() {}
}

class ListWidget extends Node {
  constructor() {
    super("widget");
  }

  async presentMedium() {
    this.presented = "medium";
  }

  async presentLarge() {
    this.presented = "large";
  }
}

class Color {
  constructor(hex, alpha = 1) {
    this.hex = hex;
    this.alpha = alpha;
  }

  static dynamic(light, dark) {
    return { light, dark };
  }
}

const Font = new Proxy({}, { get: (_t, name) => (size) => ({ name, size }) });

class Size {
  constructor(w, h) {
    this.w = w;
    this.h = h;
  }
}

export async function runWidgetScript({
  payload, status = 200, family = "medium", parameter = "", url, nowMs, fail = false, cache,
}) {
  let source = fs.readFileSync(new URL("../../scriptable/nufe-kebiao-widget.js", import.meta.url), "utf8");
  if (url !== undefined) source = source.replace("__WIDGET_URL__", url);
  const files = cache || new Map();
  let result = null;
  let done;
  const finished = new Promise((resolve) => { done = resolve; });

  class Request {
    constructor(u) {
      this.url = u;
    }

    async loadJSON() {
      if (fail) throw new Error("offline");
      this.response = { statusCode: status };
      return JSON.parse(JSON.stringify(payload));
    }
  }

  const fm = {
    cacheDirectory: () => "/cache",
    joinPath: (a, b) => `${a}/${b}`,
    writeString: (p, s) => files.set(p, s),
    readString: (p) => files.get(p),
    fileExists: (p) => files.has(p),
  };

  const RealDate = Date;
  const FakeDate = class extends RealDate {
    constructor(...a) {
      super(...(a.length ? a : [nowMs ?? RealDate.now()]));
    }

    static now() {
      return nowMs ?? RealDate.now();
    }
  };

  const context = {
    ListWidget, Color, Font, Size, Request,
    FileManager: { local: () => fm },
    Script: {
      setWidget: (w) => { result = w; },
      complete: () => done(),
    },
    config: { runsInWidget: true, widgetFamily: family },
    args: { widgetParameter: parameter },
    Date: FakeDate,
    JSON, Math, Number, String, Object, Array, Promise, console,
  };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  await finished;
  return { widget: result, files };
}
