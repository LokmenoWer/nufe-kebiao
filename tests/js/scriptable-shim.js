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

  addDate(date) {
    const d = new WidgetDate(date);
    this.children.push(d);
    return d;
  }

  addSpacer(length) {
    this.children.push({ kind: "spacer", length });
  }

  setPadding(...p) {
    this.padding = p;
  }

  /** 文本按出现顺序；倒计时写成 “[timer→ISO]” / “[relative→ISO]”。 */
  texts() {
    const out = [];
    const walk = (n) => {
      if (n.kind === "text") out.push(n.text);
      if (n.kind === "date") out.push(`[${n.style}→${n.date.toISOString()}]`);
      for (const c of n.children || []) walk(c);
    };
    walk(this);
    return out;
  }

  urls() {
    const out = [];
    const walk = (n) => {
      if (n.url) out.push(n.url);
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

class WidgetDate {
  constructor(date) {
    this.kind = "date";
    this.date = date;
    this.style = "date";
  }

  applyTimerStyle() {
    this.style = "timer";
  }

  applyRelativeStyle() {
    this.style = "relative";
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
    ListWidget.onPresent?.(this);
  }

  async presentLarge() {
    this.presented = "large";
    ListWidget.onPresent?.(this);
  }

  async presentSmall() {
    this.presented = "small";
    ListWidget.onPresent?.(this);
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
  inApp = false, choose = [], scriptBody, scriptStatus = 200,
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

    async loadString() {
      if (fail) throw new Error("offline");
      this.response = { statusCode: scriptStatus };
      return scriptBody;
    }
  }

  const presented = [];
  ListWidget.onPresent = (w) => presented.push(w);
  const alerts = [];
  const choices = [...choose];
  class Alert {
    constructor() {
      this.actions = [];
      alerts.push(this);
    }

    addAction(label) {
      this.actions.push(label);
    }

    addCancelAction(label) {
      this.cancel = label;
    }

    async presentSheet() {
      const want = choices.shift();
      return want === undefined ? -1 : this.actions.indexOf(want);
    }

    async present() {
      return 0;
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
    FileManager: { local: () => fm, iCloud: () => { throw new Error("iCloud disabled"); } },
    Alert,
    Safari: { open: (u) => presented.push(`safari:${u}`) },
    module: { filename: "/Documents/南财课表.js" },
    Script: {
      setWidget: (w) => { result = w; },
      complete: () => done(),
    },
    config: { runsInWidget: !inApp, widgetFamily: inApp ? undefined : family },
    args: { widgetParameter: parameter },
    Date: FakeDate,
    JSON, Math, Number, String, Object, Array, Promise, console,
  };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  await finished;
  return { widget: result, files, alerts, presented };
}
