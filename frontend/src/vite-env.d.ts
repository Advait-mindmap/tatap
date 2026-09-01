/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the planner API lives. Defaults to localhost:8000 in development. */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
