const DEFAULT_BASE_URL = 'http://127.0.0.1:8000'

/** Base URL of the backend API, configurable via VITE_API_BASE_URL. */
export const apiBaseUrl: string =
  import.meta.env.VITE_API_BASE_URL?.trim() || DEFAULT_BASE_URL
