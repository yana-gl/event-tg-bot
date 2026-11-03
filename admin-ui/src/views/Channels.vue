<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiGet, apiPost } from '../api/client'

const router = useRouter()
const items = ref([])
const loading = ref(false)
const saving = ref(false)
const form = reactive({ username: '', initial_posts: 20 })
const INITIAL_STATUSES = {
  pending: 'Ожидает первой загрузки', loading: 'Первая загрузка идёт',
  complete: 'Первая загрузка завершена', existing: 'Ранее настроенный сбор'
}

async function refresh() {
  loading.value = true
  try {
    items.value = (await apiGet('/api/channels')).items
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function add() {
  saving.value = true
  try {
    await apiPost('/api/channels', { ...form })
    form.username = ''
    await refresh()
    ElMessage.success('Канал добавлен. Сбор начнётся в следующем цикле watcher')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function update(row, fields) {
  saving.value = true
  try {
    await apiPost(`/api/channels/${row.username}`, fields)
    await refresh()
    ElMessage.success('Настройки сохранены')
  } catch (e) {
    ElMessage.error(e.message)
    await refresh()
  } finally {
    saving.value = false
  }
}

function timestamp(value) {
  return value ? new Date(value).toLocaleString('ru-RU') : '—'
}

onMounted(refresh)
</script>

<template>
  <div class="page">
    <el-page-header @back="router.push({ name: 'events' })" content="Каналы" />
    <p>Работающий watcher подхватит настройки на следующем цикле. По умолчанию проверка — через 30 минут после окончания предыдущего цикла; интервал задаётся при запуске.</p>
    <el-card>
      <template #header>Добавить канал</template>
      <el-form label-position="top" @submit.prevent="add">
        <el-form-item label="Публичная ссылка или @имя канала">
          <el-input v-model="form.username" placeholder="https://t.me/channel" />
        </el-form-item>
        <el-form-item label="Сколько последних постов взять один раз при подключении">
          <el-input-number v-model="form.initial_posts" :min="1" :max="1000" :precision="0" />
        </el-form-item>
        <el-button type="primary" :loading="saving" @click="add">Добавить</el-button>
      </el-form>
    </el-card>
    <p>Затем собираются все новые посты. Приостановка сохраняет события и позицию сбора. Число постов можно изменить только до начала первой загрузки.</p>
    <el-button :loading="loading" @click="refresh">Обновить состояние</el-button>
    <p class="table-help">Прокрутите таблицу вправо, чтобы увидеть время проверок и ошибки.</p>
    <el-table :data="items" v-loading="loading">
      <el-table-column label="Канал" min-width="160">
        <template #default="{ row }"><a :href="`https://t.me/${row.username}`" target="_blank" rel="noopener">@{{ row.username }}</a></template>
      </el-table-column>
      <el-table-column label="Сбор" min-width="220">
        <template #default="{ row }">
          <div>{{ row.enabled ? 'Включён' : 'Приостановлен' }}</div>
          <div>{{ INITIAL_STATUSES[row.initial_status] }}</div>
          <el-button :disabled="saving" @click="update(row, { enabled: !row.enabled })">{{ row.enabled ? 'Приостановить' : 'Возобновить' }}</el-button>
        </template>
      </el-table-column>
      <el-table-column label="Первая загрузка" min-width="200">
        <template #default="{ row }">
          <template v-if="row.initial_status === 'pending'">
            <el-input-number v-model="row.initial_posts" :min="1" :max="1000" :precision="0" />
            <el-button text :disabled="saving" @click="update(row, { initial_posts: row.initial_posts })">Сохранить число</el-button>
          </template>
          <span v-else>{{ row.mode === 'legacy' ? 'Сохранён прежний прогресс' : `${row.initial_posts} последних постов` }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Последняя попытка" min-width="180">
        <template #default="{ row }">{{ timestamp(row.last_attempt_at) }}</template>
      </el-table-column>
      <el-table-column label="Проверка Telegram успешна" min-width="180">
        <template #default="{ row }">{{ timestamp(row.last_success_at) }}</template>
      </el-table-column>
      <el-table-column prop="last_error" label="Ошибка" min-width="220" />
    </el-table>
    <p v-if="!loading && !items.length">Каналов пока нет. Добавьте первый.</p>
    <p>Время проверок показано в часовом поясе устройства. Добавление не проверяет существование канала: доступность определится при следующем сборе.</p>
  </div>
</template>

<style scoped>
.page { padding: 20px 32px; }
.el-card { max-width: 640px; margin-top: 20px; }
.table-help { display: none; }
@media (max-width: 600px) {
  .page { padding: 16px 12px; }
  .table-help { display: block; }
}
</style>
