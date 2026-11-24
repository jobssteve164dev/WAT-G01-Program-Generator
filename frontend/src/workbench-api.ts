export class RequestError extends Error {}

export async function requestResponse(path: string, onSessionExpired: () => void, options?: RequestInit): Promise<Response> {
  const response = await fetch(path, options);
  if (response.status === 401) {
    if (!options?.signal?.aborted) onSessionExpired();
    throw new RequestError("登录已过期，请重新登录。");
  }
  if (!response.ok) {
    const { detail, message } = await response.json().catch(() => ({})) as { detail?: unknown; message?: unknown };
    throw new RequestError(typeof detail === "string" ? detail : typeof message === "string" ? message : "操作未完成，请检查填写内容后重试。");
  }
  return response;
}

export async function requestData<T>(path: string, onSessionExpired: () => void, options?: RequestInit): Promise<T> {
  return (await requestResponse(path, onSessionExpired, options)).json() as Promise<T>;
}

export function jsonRequest(method: "POST" | "PUT", payload: object): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}

export function messageFor(error: unknown): string {
  return error instanceof RequestError ? error.message : "暂时无法连接工作台，请重试。";
}

export function positiveNumber(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!/^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(trimmed) || !Number.isFinite(value) || value <= 0) {
    throw new RequestError("电压、电流和量程请填写正数，可使用科学计数法。");
  }
  return value;
}

export async function downloadFile(path: string, fallback: string, onSessionExpired: () => void, options?: RequestInit) {
  const response = await requestResponse(path, onSessionExpired, options);
  const data = await response.blob();
  if (options?.signal?.aborted) return;
  const disposition = response.headers.get("Content-Disposition");
  const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const filename = encoded ? decodeURIComponent(encoded) : disposition?.match(/filename="([^"]+)"/i)?.[1];
  const link = document.createElement("a");
  link.download = filename ?? fallback;
  link.href = URL.createObjectURL(data);
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
