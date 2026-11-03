<script setup>
import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useToast } from 'vuestic-ui'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const { init: notify } = useToast()

const form = reactive({ user: '', password: '' })
const loading = ref(false)

async function onSubmit() {
  if (!form.user || !form.password) {
    notify({ message: 'Введите логин и пароль', color: 'warning' })
    return
  }
  loading.value = true
  try {
    await auth.login(form.user, form.password)
    const redirect = route.query.redirect || '/events'
    router.replace(redirect)
  } catch (e) {
    notify({ message: e.message || 'Ошибка входа', color: 'danger' })
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <va-card class="login-card">
      <va-card-content>
      <div class="login-brand"><span>TG</span></div>
      <p class="eyebrow">АДМИНИСТРАТИВНАЯ ПАНЕЛЬ</p>
      <h1>С возвращением</h1>
      <p class="login-subtitle">Войдите, чтобы управлять событиями и каналами.</p>
      <va-form @submit.prevent="onSubmit">
        <va-input v-model="form.user" label="Логин" autocomplete="username" class="login-field" />
        <va-input v-model="form.password" label="Пароль" type="password" autocomplete="current-password" class="login-field" />
        <va-button type="submit" :loading="loading" :disabled="!form.user || !form.password" class="login-button">Войти</va-button>
      </va-form>
      </va-card-content>
    </va-card>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--va-background-primary);
}
.login-card {
  width: min(100%, 420px);
  padding: 24px;
}
.login-brand {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 8px;
  background: var(--va-primary);
  color: var(--va-text-inverted);
  font-weight: 800;
  margin-bottom: 28px;
}
.login-card .eyebrow { color: var(--va-primary); font-size: 10px; font-weight: 700; letter-spacing: .1em; margin: 0 0 8px; }
.login-card h1 { margin: 0; font-size: 28px; letter-spacing: -.04em; }
.login-subtitle { color: var(--va-secondary); font-size: 14px; line-height: 1.5; margin: 10px 0 28px; }
.login-field { margin-bottom: 16px; }
.login-button { width: 100%; margin-top: 8px; }
@media (max-width: 480px) {
  .login-card { padding: 28px 22px; }
}
</style>
