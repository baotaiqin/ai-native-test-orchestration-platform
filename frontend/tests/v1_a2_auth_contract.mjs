import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { buildSync } from '../node_modules/esbuild/lib/main.js'

class MemoryStorage {
  #data = new Map()

  getItem(key) {
    return this.#data.has(key) ? this.#data.get(key) : null
  }

  setItem(key, value) {
    this.#data.set(String(key), String(value))
  }

  removeItem(key) {
    this.#data.delete(String(key))
  }
}

const storage = new MemoryStorage()
const storageListeners = []
storage.setItem('access_token', 'broken-token')
storage.setItem('current_user', '{broken-json')
globalThis.localStorage = storage
globalThis.window = {
  localStorage: storage,
  location: { pathname: '/', assign() {} },
  addEventListener(type, listener) {
    if (type === 'storage') storageListeners.push(listener)
  },
  clearTimeout,
  setTimeout,
}

const entry = `
  import { createPinia, setActivePinia } from 'pinia'
  import { useAuthStore } from '@/stores/auth'
  import { http } from '@/api/http'
  import * as authApi from '@/api/auth'
  export { createPinia, setActivePinia, useAuthStore, http, authApi }
`
const bundle = buildSync({
  stdin: { contents: entry, resolveDir: process.cwd(), sourcefile: 'v1-a2-contract-entry.ts' },
  absWorkingDir: process.cwd(),
  tsconfig: 'tsconfig.app.json',
  bundle: true,
  write: false,
  format: 'cjs',
  platform: 'node',
})
const bundledModule = { exports: {} }
const executeBundle = new Function('require', 'module', 'exports', bundle.outputFiles[0].text)
executeBundle(createRequire(import.meta.url), bundledModule, bundledModule.exports)
const runtime = bundledModule.exports

assert.equal(storage.getItem('access_token'), null, 'broken user JSON must clear the token safely')
assert.equal(storage.getItem('current_user'), null, 'broken user JSON must be removed safely')
assert.ok(storage.getItem('auth_identity_epoch'), 'broken persisted identity must advance the epoch')

runtime.setActivePinia(runtime.createPinia())
const store = runtime.useAuthStore()
const requests = []
let slowLoginResolve
let logoutResolve
let meRole = 'USER'

function response(config, data, status = 200) {
  return { data, status, statusText: String(status), headers: {}, config }
}

function user(username, role) {
  return { id: `id-${username}`, username, display_name: username.toUpperCase(), roles: [role] }
}

runtime.http.defaults.adapter = async (config) => {
  const body = typeof config.data === 'string' && config.data ? JSON.parse(config.data) : config.data
  requests.push({ method: config.method, url: config.url, params: config.params, body })
  if (config.url === '/auth/login') {
    if (body.username === 'slow-a') {
      return new Promise((resolve) => { slowLoginResolve = () => resolve(response(config, {
        access_token: 'same-token', token_type: 'bearer', expires_in: 3600, user: user('slow-a', 'ADMIN'),
      })) })
    }
    return response(config, {
      access_token: 'same-token', token_type: 'bearer', expires_in: 3600, user: user(body.username, 'ADMIN'),
    })
  }
  if (config.url === '/auth/me') return response(config, user('authoritative', meRole))
  if (config.url === '/auth/logout') {
    return new Promise((resolve) => { logoutResolve = () => resolve(response(config, { revoked: true })) })
  }
  if (config.url === '/auth/change-password') {
    return response(config, { changed: true, reauthentication_required: true })
  }
  if (config.url === '/auth/users' && config.method === 'get') {
    return response(config, { items: [], total: 0, page: config.params.page, page_size: config.params.page_size })
  }
  if (config.url === '/auth/users' && config.method === 'post') {
    return response(config, {
      id: 'usr-created', username: body.username, display_name: body.display_name,
      status: body.status, platform_role: body.platform_role, auth_version: 1,
      created_at: '2026-09-10T00:00:00Z', updated_at: '2026-09-10T00:00:00Z',
    }, 201)
  }
  if (config.url === '/auth/users/user%2Fwith%2Fslash' && config.method === 'patch') {
    return response(config, {
      id: 'user/with/slash', username: 'immutable', display_name: body.display_name,
      status: body.status, platform_role: body.platform_role, auth_version: 2,
      created_at: '2026-09-10T00:00:00Z', updated_at: '2026-09-10T00:01:00Z',
    })
  }
  throw new Error(`unexpected request ${config.method} ${config.url}`)
}

assert.equal(await store.signIn({ username: ' initial ', password: ' pass with spaces ' }), true)
assert.equal(store.isAdmin, true)
assert.match(storage.getItem('current_user'), /__identity_epoch/)
const firstEpoch = store.identityEpoch
assert.deepEqual(requests.at(-1).body, { username: ' initial ', password: ' pass with spaces ' })

assert.equal(await store.refreshUser(), true)
assert.equal(store.user.username, 'authoritative')
assert.equal(store.isAdmin, false, 'cached ADMIN role must be replaced by authoritative /auth/me')
assert.notEqual(store.identityEpoch, firstEpoch)

const slowLogin = store.signIn({ username: 'slow-a', password: 'slow-password' })
assert.equal(await store.signIn({ username: 'fast-b', password: 'fast-password' }), true)
const fastEpoch = store.identityEpoch
slowLoginResolve()
assert.equal(await slowLogin, false, 'late login must not overwrite the newer identity')
assert.equal(store.user.username, 'fast-b')
assert.equal(store.identityEpoch, fastEpoch)

const pendingLogout = store.logoutFromServer()
assert.equal(await store.signIn({ username: 'new-login', password: 'new-password' }), true)
const reloginEpoch = store.identityEpoch
logoutResolve()
assert.deepEqual(await pendingLogout, { revoked: true, local_cleared: false })
assert.equal(store.user.username, 'new-login', 'late logout must not clear a concurrent login')
assert.equal(store.identityEpoch, reloginEpoch)

await runtime.authApi.getUsers({ page: 3, page_size: 40 })
await runtime.authApi.createUser({
  username: ' New.User ', display_name: ' New User ', password: '  raw password  ',
  platform_role: 'USER', status: 'ACTIVE',
})
await runtime.authApi.updateUser('user/with/slash', {
  display_name: 'Updated Name', platform_role: 'ADMIN', status: 'ACTIVE',
})
assert.deepEqual(requests.at(-3).params, { page: 3, page_size: 40 })
assert.deepEqual(requests.at(-2).body, {
  username: ' New.User ', display_name: ' New User ', password: '  raw password  ',
  platform_role: 'USER', status: 'ACTIVE',
})
assert.equal(requests.at(-1).url, '/auth/users/user%2Fwith%2Fslash')
assert.deepEqual(requests.at(-1).body, {
  display_name: 'Updated Name', platform_role: 'ADMIN', status: 'ACTIVE',
})

meRole = 'ADMIN'
assert.equal(await store.refreshUser(), true)
const passwordResult = await store.changePassword({
  current_password: ' current password ',
  new_password: ' new password with spaces ',
})
assert.deepEqual(passwordResult, { changed: true, reauthentication_required: true, local_cleared: true })
assert.deepEqual(requests.at(-1).body, {
  current_password: ' current password ',
  new_password: ' new password with spaces ',
})
assert.equal(storage.getItem('access_token'), null)
assert.equal(storage.getItem('current_user'), null)

console.log(JSON.stringify({
  status: 'passed',
  checks: {
    corrupted_json_safe_logout: true,
    me_roles_authoritative: true,
    identity_epoch_changes: true,
    late_login_rejected_with_same_token_text: true,
    late_logout_did_not_clear_relogin: true,
    password_whitespace_preserved: true,
    management_payloads_exact: true,
    user_id_path_encoded: true,
  },
  captured_request_count: requests.length,
  storage_listener_count: storageListeners.length,
}))
