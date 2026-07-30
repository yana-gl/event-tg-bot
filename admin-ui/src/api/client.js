const TOKEN_KEY = 'tg-event-admin-token'
const USER_KEY = 'tg-event-admin-user'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  if (user) localStorage.setItem(USER_KEY, user)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function getUser() {
  return localStorage.getItem(USER_KEY)
}

export class ApiError extends Error {
  constructor(message, status, details) {
    super(message)
    this.status = status
    this.details = details
  }
}

async function handle(response) {
  if (response.status === 204) return null
  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch (e) {
      data = { error: text }
    }
  }
  if (!response.ok) {
    const message = (data && (data.error || data.message)) || `HTTP ${response.status}`
    if (response.status === 401) {
      clearToken()
    }
    throw new ApiError(message, response.status, data && data.details)
  }
  return data
}

export async function apiLogin(user, password) {
  const response = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, password })
  })
  return handle(response)
}

function authHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function apiGet(path) {
  const response = await fetch(path, { headers: { ...authHeaders() } })
  return handle(response)
}

export async function apiPost(path, body, json = true) {
  const headers = { ...authHeaders() }
  if (json) headers['Content-Type'] = 'application/json'
  const response = await fetch(path, {
    method: 'POST',
    headers,
    body: json ? JSON.stringify(body || {}) : body
  })
  return handle(response)
}