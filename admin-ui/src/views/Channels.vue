<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useToast } from 'vuestic-ui'
import { apiGet, apiPost } from '../api/client'

const { init: notify } = useToast()
const items = ref([])
const loading = ref(false)
const saving = ref(false)
const form = reactive({ username: '', initial_posts: 20 })
const INITIAL_STATUSES = {
  pending: 'Ожидает первой загрузки', loading: 'Первая загрузка идёт',
  complete: 'Первая загрузка завершена', existing: 'Ранее настроенный сбор'
}
const columns = [
  { key: 'username', label: 'Канал', width: '160px' },
  { key: 'enabled', label: 'Сбор', width: '220px' },
  { key: 'initial_posts', label: 'Первая загрузка', width: '240px' },
  { key: 'last_attempt_at', label: 'Последняя попытка', width: '180px' },
  { key: 'last_success_at', label: 'Telegram проверен', width: '180px' },
  { key: 'last_error', label: 'Ошибка', width: '220px' }
]

async function refresh() {
  loading.value = true
  try {
    items.value = (await apiGet('/api/channels')).items
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
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
    notify({ message: 'Канал добавлен. Сбор начнётся при следующей проверке', color: 'success' })
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
  } finally {
    saving.value = false
  }
}

async function update(row, fields) {
  saving.value = true
  try {
    await apiPost(`/api/channels/${row.username}`, fields)
    await refresh()
    notify({ message: 'Настройки сохранены', color: 'success' })
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
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
  <div class="page stack">
    <div>
      <h1 class="page-title">Каналы</h1>
      <p class="page-description">Источники публикаций для городской афиши.</p>
    </div>
    <va-card>
      <va-card-title>Добавить канал</va-card-title>
      <va-card-content>
        <va-form class="channel-form" @submit.prevent="add">
          <va-input v-model="form.username" label="Публичная ссылка или @имя канала" placeholder="https://t.me/channel" />
          <va-counter v-model="form.initial_posts" :min="1" :max="1000" :step="1"
            label="Постов при подключении" />
          <va-button type="submit" :loading="saving" :disabled="!form.username.trim()">Добавить канал</va-button>
        </va-form>
        <p class="form-help muted">Последние посты загружаются один раз, затем собираются все новые. Глубину загрузки можно изменить до её начала.</p>
      </va-card-content>
    </va-card>

    <va-alert color="info" outline>
      Новые настройки применятся при следующей проверке. Обычно проверка начинается через 30 минут после окончания предыдущей.
      Приостановка сохраняет события и позицию сбора.
    </va-alert>

    <va-card>
      <va-card-content>
        <div class="toolbar">
          <h2 class="section-title">Подключённые каналы</h2>
          <va-button preset="secondary" icon="refresh" :loading="loading" @click="refresh">Обновить состояние</va-button>
        </div>
        <div class="table-wrapper">
          <va-data-table :items="items" :columns="columns" :loading="loading" items-track-by="username"
            no-data-html="Каналов пока нет. Добавьте первый.">
            <template #cell(username)="{ value }">
              <a :href="`https://t.me/${value}`" target="_blank" rel="noopener">@{{ value }}</a>
            </template>
            <template #cell(enabled)="{ rowData }">
              <div class="channel-status">
                <va-chip :color="rowData.enabled ? 'success' : 'secondary'" size="small">
                  {{ rowData.enabled ? 'Включён' : 'Приостановлен' }}
                </va-chip>
                <span class="muted">{{ INITIAL_STATUSES[rowData.initial_status] }}</span>
                <va-button preset="secondary" size="small" :disabled="saving"
                  @click="update(rowData, { enabled: !rowData.enabled })">
                  {{ rowData.enabled ? 'Приостановить' : 'Возобновить' }}
                </va-button>
              </div>
            </template>
            <template #cell(initial_posts)="{ rowData }">
              <div v-if="rowData.initial_status === 'pending'" class="channel-status">
                <va-counter v-model="rowData.initial_posts" :min="1" :max="1000" :step="1" aria-label="Постов при подключении" />
                <va-button preset="plain" :disabled="saving" @click="update(rowData, { initial_posts: rowData.initial_posts })">
                  Сохранить число
                </va-button>
              </div>
              <span v-else>{{ rowData.mode === 'legacy' ? 'Сохранён прежний прогресс' : `${rowData.initial_posts} последних постов` }}</span>
            </template>
            <template #cell(last_attempt_at)="{ value }">{{ timestamp(value) }}</template>
            <template #cell(last_success_at)="{ value }">{{ timestamp(value) }}</template>
            <template #cell(last_error)="{ value }"><span class="channel-error">{{ value || '—' }}</span></template>
          </va-data-table>
        </div>
        <p class="form-help muted">Время показано в часовом поясе устройства. Доступность нового канала определяется при следующем сборе.</p>
      </va-card-content>
    </va-card>
  </div>
</template>

<style scoped>
.channel-form { display: grid; grid-template-columns: minmax(240px, 1fr) 240px auto; gap: 20px; align-items: end; }
.form-help { margin-top: 16px; line-height: 1.5; }
.section-title { font-size: 20px; font-weight: 600; }
.channel-status { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; white-space: normal; }
.channel-status .va-counter { width: 180px; }
.channel-error { white-space: normal; overflow-wrap: anywhere; }
@media (max-width: 1100px) { .channel-form { grid-template-columns: minmax(0, 1fr); } }
</style>
