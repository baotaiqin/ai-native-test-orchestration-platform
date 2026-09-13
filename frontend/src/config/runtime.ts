interface BrowserRuntimeConfig {
  publicDemoUsername?: unknown
  publicDemoPassword?: unknown
}

declare global {
  interface Window {
    __AI_TEST_RUNTIME_CONFIG__?: BrowserRuntimeConfig
  }
}

export interface PublicDemoCredentials {
  username: string
  password: string
}

function runtimeString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function getPublicDemoCredentials(): PublicDemoCredentials | null {
  const config = window.__AI_TEST_RUNTIME_CONFIG__
  const username = runtimeString(config?.publicDemoUsername)
  const password = runtimeString(config?.publicDemoPassword)
  return username && password ? { username, password } : null
}
