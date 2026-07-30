<script setup>
import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { ElMessage } from 'element-plus'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const form = reactive({ user: '', password: '' })
const loading = ref(false)

async function onSubmit() {
  if (!form.user || !form.password) {
    ElMessage.warning('Введите логин и пароль')
    return
  }
  loading.value = true
  try {
    await auth.login(form.user, form.password)
    const redirect = route.query.redirect || '/events'
    router.replace(redirect)
  } catch (e) {
    ElMessage.error(e.message || 'Ошибка входа')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <template #header>Вход в админ-панель</template>
      <el-form @submit.prevent="onSubmit" label-position="top">
        <el-form-item label="Логин">
          <el-input v-model="form.user" autocomplete="username" />
        </el-form-item>
        <el-form-item label="Пароль">
          <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" style="width:100%">Войти</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  display: flex;
  justify-content: center;
  padding-top: 80px;
}
.login-card {
  width: 360px;
}
</style>