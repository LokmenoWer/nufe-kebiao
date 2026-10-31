/** 与后端 JSON API 通信：CSRF、错误信息、超时、401 统一处理。 */

export class ApiError extends Error {
  constructor(message, status = 0, body = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

let csrfToken = null;
let unauthorizedHandler = null;

export function setCsrfToken(token) {
  csrfToken = typeof token === "string" && token ? token : null;
}

export function getCsrfToken() {
  return csrfToken;
}

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = typeof handler === "function" ? handler : null;
}

function detailOf(payload) {
  if (!payload || typeof payload !== "object") return "";
  if (typeof payload.detail === "string") return payload.detail;
  if (Array.isArray(payload.detail)) {
    return payload.detail.map((item) => (item && typeof item.msg === "string" ? item.msg : "")).filter(Boolean).join("；");
  }
  if (typeof payload.message === "string") return payload.message;
  return "";
}

const STATUS_MESSAGES = {
  400: "请求有误，请检查后重试",
  401: "登录已失效，请重新登录",
  403: "请求被拒绝，请刷新页面后重试",
  404: "没有找到对应数据",
  409: "正在处理中，请稍后再试",
  422: "请求参数不正确",
  429: "操作太频繁，请稍后再试",
  500: "服务暂时不可用，请稍后重试",
  502: "服务暂时不可用，请稍后重试",
  503: "服务暂时不可用，请稍后重试",
};

export async function request(path, { method = "GET", body, timeoutMs } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken && method !== "GET") headers["X-CSRF-Token"] = csrfToken;

  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    timeoutMs ?? (method === "GET" ? 25_000 : 120_000),
  );

  const options = {
    method,
    headers,
    credentials: "same-origin",
    signal: controller.signal,
    cache: "no-store",
  };
  if (body !== undefined) options.body = JSON.stringify(body);

  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error?.name === "AbortError") throw new ApiError("请求超时，请稍后重试", 0);
    throw new ApiError("网络连接失败，请检查网络后重试", 0);
  } finally {
    window.clearTimeout(timeout);
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const message = detailOf(payload) || STATUS_MESSAGES[response.status] || `请求失败（${response.status}）`;
    if (response.status === 401) {
      if (csrfToken) {
        setCsrfToken(null);
        unauthorizedHandler?.();
      }
    }
    throw new ApiError(message, response.status, payload);
  }
  return payload;
}

export function get(path, options = {}) {
  return request(path, { ...options, method: "GET" });
}

export function post(path, body = {}, options = {}) {
  return request(path, { ...options, method: "POST", body });
}

/** 拼查询串，自动跳过 null/undefined/空字符串。 */
export function withQuery(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") query.set(key, String(value));
  }
  const tail = query.toString();
  return tail ? `${path}?${tail}` : path;
}

// 统一命名空间，视图里可以 api.get / api.post
export const api = { request, get, post, withQuery, setCsrfToken, getCsrfToken, setUnauthorizedHandler, ApiError };
