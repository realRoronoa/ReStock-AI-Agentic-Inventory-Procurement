/**
 * The only place in the frontend that performs HTTP.
 *
 * Components and hooks call typed functions in the sibling modules; nothing
 * calls `fetch` directly. That gives one place to handle the backend's error
 * envelope, one place to configure the base URL, and one place to audit if the
 * API contract ever changes.
 */

import type { ApiErrorBody } from '@/types/api'

/**
 * Base URL for the backend.
 *
 * Empty by default, which makes requests same-origin and lets the Vite dev
 * proxy (see vite.config.ts) forward `/api` to the backend — so there is no
 * CORS to configure in development.
 *
 * Set `VITE_API_BASE_URL` when the backend lives elsewhere. Never put a secret
 * in a `VITE_`-prefixed variable: everything so prefixed is compiled into the
 * bundle and is public.
 */
export const API_BASE_URL = (
  (typeof process !== 'undefined' && process.env?.SMOKE_BASE_URL) ||
  import.meta.env?.VITE_API_BASE_URL ||
  (typeof window === 'undefined' ? 'http://127.0.0.1:8000' : '')
).replace(/\/$/, '')

/** Requests slower than this are almost certainly not coming back. */
const DEFAULT_TIMEOUT_MS = 30_000

/**
 * A failed request, with the backend's structured error attached.
 *
 * `code` is the thing to branch on. Components should switch on codes like
 * `PAYOUT_ALREADY_REQUESTED` or `DAILY_SPEND_LIMIT_EXCEEDED` rather than
 * matching message text.
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown> | undefined
  readonly requestId: string | undefined

  constructor(args: {
    status: number
    code: string
    message: string
    details?: Record<string, unknown>
    requestId?: string
  }) {
    super(args.message)
    this.name = 'ApiError'
    this.status = args.status
    this.code = args.code
    this.details = args.details
    this.requestId = args.requestId
  }

  /** True for problems a retry might fix. Never true for 4xx business rules. */
  get isRetryable(): boolean {
    return this.status === 0 || this.status >= 500
  }

  /** The request reached the backend but a rule refused it. */
  get isBusinessRule(): boolean {
    return this.status >= 400 && this.status < 500
  }

  /**
   * A duplicate or already-handled action, e.g. approving twice.
   *
   * Worth distinguishing in the UI: a 409 on approve usually means the system
   * protected the merchant, not that something went wrong.
   */
  get isConflict(): boolean {
    return this.status === 409
  }
}

/** Thrown when the network or the browser gave up before a response. */
export class NetworkError extends ApiError {
  constructor(message: string) {
    super({ status: 0, code: 'NETWORK_ERROR', message })
    this.name = 'NetworkError'
  }
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null) return false
  const candidate = (value as { error?: unknown }).error
  return (
    typeof candidate === 'object' &&
    candidate !== null &&
    typeof (candidate as { code?: unknown }).code === 'string'
  )
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown
  try {
    body = await response.json()
  } catch {
    body = undefined
  }

  if (isApiErrorBody(body)) {
    return new ApiError({
      status: response.status,
      code: body.error.code,
      message: body.error.message,
      details: body.error.details,
      requestId: body.error.request_id,
    })
  }

  // A response that is not the documented envelope means something upstream of
  // the application answered — a proxy, a gateway, a 502 page. Say so plainly
  // rather than showing the caller a fragment of HTML.
  return new ApiError({
    status: response.status,
    code: `HTTP_${response.status}`,
    message:
      response.status >= 500
        ? 'The server could not complete this request. Please try again.'
        : `Request failed with status ${response.status}.`,
  })
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  /** Serialised as JSON. Omit entirely for endpoints that take no body. */
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
  signal?: AbortSignal
  timeoutMs?: number
}

function buildUrl(
  path: string,
  query: RequestOptions['query'],
): string {
  const url = `${API_BASE_URL}${path}`
  if (!query) return url

  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    // Skip undefined/null so callers can pass optional filters directly
    // without assembling the query string themselves.
    if (value === undefined || value === null || value === '') continue
    params.append(key, String(value))
  }

  const serialised = params.toString()
  return serialised ? `${url}?${serialised}` : url
}

/**
 * Perform a request and parse the result.
 *
 * @throws ApiError on any non-2xx response, or NetworkError on a transport
 *   failure or timeout.
 */
export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = 'GET',
    body,
    query,
    signal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  } = options

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)

  // Honour a caller-supplied signal (TanStack Query passes one on unmount)
  // alongside our own timeout.
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
  } catch (cause) {
    if (signal?.aborted) {
      // Deliberate cancellation, not a failure. Re-throw so TanStack Query
      // recognises it and does not surface an error state.
      throw cause
    }
    throw new NetworkError(
      controller.signal.aborted
        ? 'The request timed out. Check that the backend is running.'
        : 'Could not reach the server. Check your connection.',
    )
  } finally {
    clearTimeout(timeout)
  }

  if (!response.ok) {
    throw await toApiError(response)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export const apiClient = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),

  post: <T>(path: string, options?: Omit<RequestOptions, 'method'>) =>
    request<T>(path, { ...options, method: 'POST' }),
}
