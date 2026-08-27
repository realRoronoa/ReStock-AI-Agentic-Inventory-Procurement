/// <reference types="vite/client" />

/**
 * Typed environment variables.
 *
 * Declared so a typo in `import.meta.env.VITE_API_BSAE_URL` is a compile error
 * rather than a silent `undefined` that falls back to same-origin and only
 * shows up as 404s at runtime.
 *
 * Everything here is PUBLIC: Vite inlines VITE_-prefixed values into the
 * bundle. No credential may ever be added to this interface.
 */
interface ImportMetaEnv {
  /** Backend base URL. Empty means same-origin (dev proxy handles it). */
  readonly VITE_API_BASE_URL?: string
  /** Dev-proxy target. Consumed by vite.config.ts only, never by app code. */
  readonly VITE_DEV_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
