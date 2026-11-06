<script setup>
import { onMounted, reactive, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useEventsStore, EVENT_STATES } from '../stores/events'
import { useModal, useToast } from 'vuestic-ui'
import { apiGet } from '../api/client'

const store = useEventsStore()
const route = useRoute()
const router = useRouter()
const { init: notify } = useToast()
const { confirm } = useModal()

const CATEGORIES = [
  'music', 'cinema', 'lecture', 'exhibition', 'market', 'workshop',
  'food', 'sport', 'party', 'kids', 'other'
]
const STATUSES = [
  { value: 'draft', label: 'Черновик' },
  { value: 'published', label: 'Опубликовано' },
  { value: 'rejected', label: 'Отклонено' },
  { value: 'not_event', label: 'Не событие' }
]
const EXTRACTED_FIELDS = {
  title: 'Название', date: 'Дата', end_date: 'До', time: 'Время', end_time: 'До времени',
  place: 'Место', address: 'Адрес', price: 'Цена', organizer: 'Организатор',
  previous_date: 'Прежняя дата', reason: 'Обоснование'
}
const ACTIONS = { announcement: 'Анонс', update: 'Уточнение', reschedule: 'Перенос', cancel: 'Отмена' }

const form = reactive({})
const detailLoaded = ref(false)
const mergeTarget = ref('')

function loadDetail(d) {
  if (!d) return
  for (const k of ['title', 'date', 'end_date', 'time', 'end_time', 'place', 'address', 'price', 'status', 'event_state']) {
    form[k] = d[k] ?? ''
  }
  form.category = d.category ?? null
}

function editedFields() {
  return { ...form, category: form.category || null }
}

watchEffect(() => {
  if (store.detail) loadDetail(store.detail)
})

async function save() {
  try {
    await store.update(route.params.id, editedFields())
    notify({ message: 'Сохранено', color: 'success' })
    await store.fetchDetail(route.params.id)
  } catch (e) {
    const details = e.details ? Object.values(e.details).join('; ') : ''
    notify({ message: details ? `Не сохранено: ${details}` : e.message, color: 'danger' })
  }
}

async function hide() {
  try {
    await store.update(route.params.id, { status: 'rejected' })
    await store.fetchDetail(route.params.id)
    notify({ message: 'Событие скрыто', color: 'success' })
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
  }
}

async function resolveConflict(useForm) {
  const detail = store.detail
  try {
    await store.update(route.params.id, {
      ...(useForm ? editedFields() : {}),
      resolve_conflict: { post_id: detail.conflict_post_id, at: detail.conflict_at }
    })
    await store.fetchDetail(route.params.id)
    notify({ message: 'Спорное изменение разобрано', color: 'success' })
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
  }
}

async function merge() {
  const targetId = Number(mergeTarget.value)
  if (!Number.isInteger(targetId) || targetId < 1 || targetId === Number(route.params.id)) {
    notify({ message: 'Укажите ID другой карточки', color: 'danger' })
    return
  }
  try {
    const target = await apiGet(`/api/events/${targetId}`)
    const confirmed = await confirm({
      message: `Останется #${target.id}: ${target.title || 'Без названия'}, ${target.date || 'без даты'}, ${target.time || 'без времени'}, ` +
      `${target.place || 'место не указано'}, ${target.status}, ${EVENT_STATES[target.event_state]}. ` +
      'Поля и статус этой карточки сохранятся. Источники присоединятся; текущая карточка будет удалена. ' +
      'Различающиеся поля сохраняемой карточки будут защищены от автоматических изменений.',
      title: 'Объединить события?',
      okText: 'Объединить',
      cancelText: 'Отмена',
      noOutsideDismiss: true
    })
    if (!confirmed) return
    await store.merge(route.params.id, targetId)
    await router.replace({ name: 'event-detail', params: { id: targetId } })
    notify({ message: 'События объединены', color: 'success' })
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
  }
}

async function remove() {
  const confirmed = await confirm({
    message: 'Удалить событие безвозвратно?',
    title: 'Удаление события',
    okText: 'Удалить',
    cancelText: 'Отмена',
    noOutsideDismiss: true
  })
  if (!confirmed) return
  try {
    await store.remove(route.params.id)
    notify({ message: 'Удалено', color: 'success' })
    router.replace({ name: 'events' })
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
  }
}

async function refreshDetail() {
  try {
    await store.fetchDetail(route.params.id)
    detailLoaded.value = true
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
  }
}

onMounted(refreshDetail)
</script>

<template>
  <div class="page">
    <va-button preset="plain" icon="arrow_back" class="back-button" :to="{ name: 'events' }">К списку событий</va-button>
    <h1 class="page-title">Карточка события</h1>

    <div v-if="store.detailLoading && !detailLoaded" class="loading-state">
      <va-progress-circle indeterminate aria-label="Загрузка события" />
    </div>
    <div v-else-if="detailLoaded && store.detail" class="detail-grid">
      <va-card>
        <va-card-title>Редактирование · #{{ store.detail.id }}</va-card-title>
        <va-card-content>
          <va-form class="stack" @submit.prevent="save">
            <va-input v-model="form.title" label="Название" />
            <div class="form-grid">
              <va-input v-model="form.date" label="Дата" placeholder="YYYY-MM-DD" />
              <va-input v-model="form.end_date" label="Дата окончания" placeholder="YYYY-MM-DD" />
              <va-input v-model="form.time" label="Время" placeholder="HH:MM" />
              <va-input v-model="form.end_time" label="Время окончания" placeholder="HH:MM" />
            </div>
            <va-input v-model="form.place" label="Место" />
            <va-input v-model="form.address" label="Адрес" />
            <div class="form-grid">
              <va-select v-model="form.category" :options="CATEGORIES" label="Категория" clearable />
              <va-input v-model="form.price" label="Цена" />
            </div>
            <va-select v-model="form.status" :options="STATUSES" text-by="label" value-by="value" label="Статус публикации" />
            <va-select v-model="form.event_state"
              :options="Object.entries(EVENT_STATES).map(([value, label]) => ({ value, label }))"
              text-by="label" value-by="value" label="Состояние мероприятия" />
            <div class="actions">
              <va-button type="submit" :loading="store.saving">Сохранить</va-button>
              <va-button preset="secondary" :disabled="store.saving" @click="hide">Скрыть</va-button>
              <va-button preset="secondary" color="danger" :disabled="store.saving" @click="remove">Удалить</va-button>
            </div>
          </va-form>

          <template v-if="store.detail.conflict_post_id">
            <va-divider />
            <va-alert color="warning" outline>
              Сверьте спорное сообщение с карточкой. Обычное сохранение не закрывает конфликт.
              <a v-if="store.detail.conflict_url" :href="store.detail.conflict_url" target="_blank" rel="noopener">Открыть сообщение</a>
            </va-alert>
            <div class="actions conflict-actions">
              <va-button :loading="store.saving" @click="resolveConflict(true)">Сохранить и закрыть конфликт</va-button>
              <va-button preset="secondary" :disabled="store.saving" @click="resolveConflict(false)">Оставить карточку без изменений</va-button>
            </div>
          </template>

          <va-divider />
          <va-form class="stack" @submit.prevent="merge">
            <va-input v-model="mergeTarget" type="number" label="Объединить с карточкой (ID сохраняемой)" placeholder="Например, 123" />
            <div><va-button preset="secondary" :disabled="store.saving" @click="merge">Объединить…</va-button></div>
          </va-form>
        </va-card-content>
      </va-card>

      <va-card>
        <va-card-title>Исходный пост</va-card-title>
        <va-card-content>
          <div class="kv"><b>Состояние:</b> {{ EVENT_STATES[store.detail.event_state] || store.detail.event_state }}</div>
          <div v-if="store.detail.last_change_url" class="kv">
            <b>Последнее изменение:</b> {{ store.detail.last_change_at }}
            <a :href="store.detail.last_change_url" target="_blank" rel="noopener">Сообщение</a>
          </div>
          <div v-if="store.detail.conflict_url" class="kv">
            <b>Спорное изменение:</b>
            <a :href="store.detail.conflict_url" target="_blank" rel="noopener">Сообщение</a>
          </div>
          <div class="kv"><b>Канал:</b> {{ store.detail.source }}</div>
          <div class="kv"><b>ID сообщения:</b> {{ store.detail.message_id }}</div>
          <div class="kv"><b>Опубликовано:</b> {{ store.detail.published_at }}</div>
          <div class="kv"><b>URL:</b>
            <a :href="store.detail.url" target="_blank" rel="noopener">{{ store.detail.url }}</a>
          </div>
          <div class="kv"><b>Уверенность LLM:</b> {{ store.detail.confidence }}</div>
          <div class="kv"><b>Создано:</b> {{ store.detail.created_at }}</div>
          <va-divider />
          <b>Обоснование LLM</b>
          <pre class="text-block">{{ store.detail.reason }}</pre>
          <b>Текст поста</b>
          <pre class="text-block">{{ store.detail.text }}</pre>
          <va-divider />
          <h2 class="sources-title">Связанные посты и извлечённые сведения</h2>
          <va-collapse v-for="source in store.detail.sources" :key="source.raw_post_id"
            :header="`${source.source} #${source.message_id}`">
            <template #body>
              <div class="source-content">
                <a :href="source.url" target="_blank" rel="noopener">Открыть пост</a>
                <div class="muted">{{ source.edited_at || source.published_at }}</div>
                <pre class="text-block">{{ source.text }}</pre>
                <template v-if="source.extracted_event">
                  <div><b>Тип сообщения:</b> {{ ACTIONS[source.extracted_event.action || 'announcement'] }}</div>
                  <template v-for="(label, key) in EXTRACTED_FIELDS" :key="key">
                    <div v-if="source.extracted_event[key]" class="kv"><b>{{ label }}:</b> {{ source.extracted_event[key] }}</div>
                  </template>
                </template>
                <div v-else>Исходное извлечение неизвестно</div>
              </div>
            </template>
          </va-collapse>
          <template v-if="store.detail.links && store.detail.links.length">
            <va-divider />
            <b>Ссылки из поста</b>
            <ul class="source-links">
              <li v-for="link in store.detail.links" :key="link">
                <a :href="link" target="_blank" rel="noopener">{{ link }}</a>
              </li>
            </ul>
          </template>
        </va-card-content>
      </va-card>
    </div>
    <va-alert v-else color="danger" outline>
      Не удалось загрузить событие.
      <va-button preset="plain" @click="refreshDetail">Повторить</va-button>
    </va-alert>
  </div>
</template>

<style scoped>
.back-button { margin-bottom: 16px; }
.detail-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 24px; align-items: start; }
.conflict-actions { margin-top: 16px; }
.sources-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; }
.source-content { padding: 16px 0; }
.source-links { padding-left: 20px; list-style: disc; overflow-wrap: anywhere; }
@media (max-width: 1100px) { .detail-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
