import { defineStore } from 'pinia'
import { apiLogin, setToken, clearToken, getUser } from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: getUser() || '',
    isAuthenticated: !!getUser()
  }),
  actions: {
    async login(user, password) {
      const data = await apiLogin(user, password)
      setToken(data.token, data.user)
      this.user = data.user
      this.isAuthenticated = true
    },
    logout() {
      clearToken()
      this.user = ''
      this.isAuthenticated = false
    }
  }
})