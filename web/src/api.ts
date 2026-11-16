/** HTTP 客户端：浏览器同源 / vite 代理 / Tauri 注入三者兼容。 */

declare global {
  interface Window {
    __CG_API__?: string
  }
}

const BASE = (typeof window !== 'undefined' && window.__CG_API__) || ''

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (e) {
    throw new ApiError(0, `无法连接本地服务：${(e as Error).message}`)
  }
  const text = await res.text()
  let data: any = {}
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { error: text.slice(0, 400) }
    }
  }
  if (!res.ok || data?.ok === false) {
    throw new ApiError(res.status, data?.error || `请求失败（${res.status}）`)
  }
  return data as T
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  put: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body ?? {}) }),
}

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export const qs = (params: Record<string, string>) => {
  const s = new URLSearchParams(params).toString()
  return s ? `?${s}` : ''
}
