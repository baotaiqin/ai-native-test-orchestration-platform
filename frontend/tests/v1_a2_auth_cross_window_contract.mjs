import assert from 'node:assert/strict'
import vm from 'node:vm'
import { webcrypto } from 'node:crypto'
import { createRequire } from 'node:module'
import { buildSync } from '../node_modules/esbuild/lib/main.js'

class SharedStorageHub {
  data = new Map()
  surfaces = new Map()
  changedWrites = 0
  deliveredEvents = 0

  seed(key, value) {
    this.data.set(String(key), String(value))
  }

  register(id) {
    const listeners = []
    const storage = {
      getItem: (key) => this.data.has(String(key)) ? this.data.get(String(key)) : null,
      setItem: (key, value) => this.write(id, String(key), String(value)),
      removeItem: (key) => this.remove(id, String(key)),
    }
    const window = {
      localStorage: storage,
      location: { pathname: '/', assign() {} },
      addEventListener(type, listener) {
        if (type === 'storage') listeners.push(listener)
      },
      clearTimeout,
      setTimeout,
    }
    const surface = { id, listeners, storage, window }
    this.surfaces.set(id, surface)
    return surface
  }

  write(sourceId, key, value) {
    const oldValue = this.data.has(key) ? this.data.get(key) : null
    if (oldValue === value) return
    this.data.set(key, value)
    this.changedWrites += 1
    this.dispatch(sourceId, key, oldValue, value)
  }

  remove(sourceId, key) {
    if (!this.data.has(key)) return
    const oldValue = this.data.get(key)
    this.data.delete(key)
    this.changedWrites += 1
    this.dispatch(sourceId, key, oldValue, null)
  }

  dispatch(sourceId, key, oldValue, newValue) {
    for (const surface of this.surfaces.values()) {
      if (surface.id === sourceId) continue
      setTimeout(() => {
        this.deliveredEvents += 1
        for (const listener of surface.listeners) {
          listener({ key, oldValue, newValue, storageArea: surface.storage })
        }
      }, 0)
    }
  }
}

function account(username, role, displayName = username.toUpperCase()) {
  return { id: `id-${username}`, username, display_name: displayName, roles: [role] }
}

function response(config, data, status = 200) {
  return { data, status, statusText: String(status), headers: {}, config }
}

async function drainStorageEvents(rounds = 8) {
  for (let index = 0; index < rounds; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
}

const entry = `
  import { createPinia, setActivePinia } from 'pinia'
  import { useAuthStore } from '@/stores/auth'
  import { http } from '@/api/http'
  export { createPinia, setActivePinia, useAuthStore, http }
`
const bundle = buildSync({
  stdin: { contents: entry, resolveDir: process.cwd(), sourcefile: 'v1-a2-r2-contract-entry.ts' },
  absWorkingDir: process.cwd(),
  tsconfig: 'tsconfig.app.json',
  bundle: true,
  write: false,
  format: 'cjs',
  platform: 'node',
}).outputFiles[0].text

const hub = new SharedStorageHub()
const initialEpoch = 'epoch-account-a'
hub.seed('auth_identity_epoch', initialEpoch)
hub.seed('access_token', 'same-token')
hub.seed('current_user', JSON.stringify({
  ...account('account-a', 'ADMIN', 'Account A'),
  __identity_epoch: initialEpoch,
}))

function createWindowRuntime(id) {
  const surface = hub.register(id)
  const module = { exports: {} }
  const context = vm.createContext({
    AbortController,
    Buffer,
    URL,
    URLSearchParams,
    clearTimeout,
    console,
    crypto: webcrypto,
    exports: module.exports,
    localStorage: surface.storage,
    module,
    process,
    queueMicrotask,
    require: createRequire(import.meta.url),
    setTimeout,
    TextDecoder,
    TextEncoder,
    window: surface.window,
  })
  new vm.Script(bundle, { filename: `v1-a2-r2-${id}.cjs` }).runInContext(context)
  const runtime = module.exports
  runtime.setActivePinia(runtime.createPinia())
  return { id, runtime, store: runtime.useAuthStore() }
}

const first = createWindowRuntime('window-a')
const second = createWindowRuntime('window-b')
let serverUser = account('account-a', 'ADMIN', 'Account A')
let pendingLogoutResolve
let slowLoginResolve
const meRequests = { 'window-a': 0, 'window-b': 0 }

function installAdapter(client) {
  client.runtime.http.defaults.adapter = async (config) => {
    const body = typeof config.data === 'string' && config.data ? JSON.parse(config.data) : config.data
    if (config.url === '/auth/me') {
      meRequests[client.id] += 1
      return response(config, { ...serverUser, roles: [...serverUser.roles] })
    }
    if (config.url === '/auth/logout') {
      return new Promise((resolve) => {
        pendingLogoutResolve = () => resolve(response(config, { revoked: true }))
      })
    }
    if (config.url === '/auth/login' && body.username === 'account-a') {
      return new Promise((resolve) => {
        slowLoginResolve = () => resolve(response(config, {
          access_token: 'same-token',
          token_type: 'bearer',
          expires_in: 3600,
          user: account('account-a', 'ADMIN', 'Account A'),
        }))
      })
    }
    if (config.url === '/auth/login' && body.username === 'account-b') {
      serverUser = account('account-b', 'ADMIN', 'Account B')
      return response(config, {
        access_token: 'same-token',
        token_type: 'bearer',
        expires_in: 3600,
        user: { ...serverUser, roles: [...serverUser.roles] },
      })
    }
    throw new Error(`unexpected request ${config.method} ${config.url}`)
  }
}

installAdapter(first)
installAdapter(second)

assert.deepEqual(await Promise.all([
  first.store.ensureCurrentUser(),
  second.store.ensureCurrentUser(),
]), [true, true])
assert.equal(hub.changedWrites, 0, 'unchanged /auth/me data must not be written back')
assert.deepEqual(meRequests, { 'window-a': 1, 'window-b': 1 })
assert.equal(first.store.identityEpoch, initialEpoch)
assert.equal(second.store.identityEpoch, initialEpoch)

const stalePrivilegedLogout = second.store.logoutFromServer()
serverUser = account('account-a', 'USER', 'Account A (restricted)')
assert.equal(await first.store.refreshUser(), true)
await drainStorageEvents()

assert.equal(first.store.user.roles[0], 'USER')
assert.equal(second.store.user.roles[0], 'USER', 'role changes must propagate to the peer window')
assert.equal(first.store.identityEpoch, initialEpoch, 'same-account refresh must keep its epoch')
assert.equal(second.store.identityEpoch, initialEpoch)
assert.equal(hub.changedWrites, 1, 'a real profile change must be persisted exactly once')
assert.deepEqual(meRequests, { 'window-a': 2, 'window-b': 2 })

pendingLogoutResolve()
const staleLogoutResult = await stalePrivilegedLogout
assert.equal(staleLogoutResult.revoked, true)
assert.equal(staleLogoutResult.local_cleared, false)
assert.equal(second.store.user.username, 'account-a', 'role propagation must invalidate an older privileged write')

const settledWrites = hub.changedWrites
const settledRequests = { ...meRequests }
await drainStorageEvents(12)
assert.equal(hub.changedWrites, settledWrites, 'storage propagation must converge without an echo write')
assert.deepEqual(meRequests, settledRequests, 'storage propagation must converge without repeated /auth/me calls')

const staleAccountALogin = first.store.signIn({ username: 'account-a', password: 'slow-password' })
assert.equal(await second.store.signIn({ username: 'account-b', password: 'fast-password' }), true)
const accountBEpoch = second.store.identityEpoch
slowLoginResolve()
assert.equal(await staleAccountALogin, false, 'late A login must not overwrite newer B with identical token text')
await drainStorageEvents()

assert.equal(first.store.user.username, 'account-b')
assert.equal(second.store.user.username, 'account-b')
assert.equal(first.store.identityEpoch, accountBEpoch)
assert.equal(second.store.identityEpoch, accountBEpoch)
assert.notEqual(accountBEpoch, initialEpoch, 'a real login must rotate the epoch')

const finalWrites = hub.changedWrites
const finalRequests = { ...meRequests }
await drainStorageEvents(12)
assert.equal(hub.changedWrites, finalWrites)
assert.deepEqual(meRequests, finalRequests)

console.log(JSON.stringify({
  status: 'passed',
  checks: {
    unchanged_refresh_keeps_epoch_without_storage_write: true,
    cross_window_refresh_converges: true,
    role_change_propagates_once: true,
    stale_privileged_write_invalidated: true,
    same_token_a_b_a_login_isolated: true,
  },
  me_requests: meRequests,
  changed_storage_writes: hub.changedWrites,
  delivered_storage_events: hub.deliveredEvents,
}))
