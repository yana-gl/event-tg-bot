<script setup>
import { onMounted, reactive, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useEventsStore, EVENT_STATES } from '../stores/events'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiGet } from '../api/client'

const store = useEventsStore()
const route = useRoute()
const router = useRouter()

const CATEGORIES = [
  'music', 'cinema', 'lecture', 'exhibition', 'market', 'workshop',
  'food', 'sport', 'party', 'kids', 'other'
]
const STATUSES = [
  { value: 'draft', label: 'draft' },
  { value: 'published', label: 'published' },
  { value: 'rejected', label: 'rejected' },
  { value: 'not_event', label: 'not_event' }
]
const EXTRACTED_FIELDS = {
  title: 'Название', date: 'Дата', end_date: 'До', time: 'Время', end_time: 'До времени',
  place: 'Место', address: 'Адрес', price: 'Цена', organizer: 'Организатор',
  previous_date: 'Прежняя дата', reason: 'Обоснование'
}
const ACTIONS = { announcement: 'Анонс', update: 'Уточнение', reschedule: 'Перенос', cancel: 'Отмена' }

const form = reactive({})
const detailLoaded = ref(false)
const mergeTarget = ref(null)

function loadDetail(d) {
  if (!d) return
  for (const k of ['title', 'date', 'end_date', 'time', 'end_time', 'place', 'address', 'price', 'status', 'event_state']) {
    form[k] = d[k] ?? (d[k] === null ? null : '')
  }
  form.category = d.category ?? ''
}

watchEffect(() => {
  if (store.detail) loadDetail(store.detail)
})

async function save() {
  try {
    await store.update(route.params.id, { ...form })
    ElMessage.success('Сохранено')
    await store.fetchDetail(route.params.id)
  } catch (e) {
    const details = e.details ? Object.values(e.details).join('; ') : ''
    ElMessage.error(details ? `Не сохранено: ${details}` : e.message)
  }
}

async function hide() {
  try {
    await store.update(route.params.id, { status: 'rejected' })
    await store.fetchDetail(route.params.id)
    ElMessage.success('Событие скрыто')
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function resolveConflict(useForm) {
  const detail = store.detail
  try {
    await store.update(route.params.id, {
      ...(useForm ? form : {}),
      resolve_conflict: { post_id: detail.conflict_post_id, at: detail.conflict_at }
    })
    await store.fetchDetail(route.params.id)
    ElMessage.success('Спорное изменение разобрано')
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function merge() {
  const targetId = mergeTarget.value
  if (!Number.isInteger(targetId) || targetId === Number(route.params.id)) {
    ElMessage.error('Укажите ID другой карточки')
    return
  }
  try {
    const target = await apiGet(`/api/events/${targetId}`)
    await ElMessageBox.confirm(
      `Останется #${target.id}: ${target.title || 'Без названия'}, ${target.date || 'без даты'}, ${target.time || 'без времени'}, ` +
      `${target.place || 'место не указано'}, ${target.status}, ${EVENT_STATES[target.event_state]}. ` +
      'Поля и статус этой карточки сохранятся. Источники присоединятся; текущая карточка будет удалена. ' +
      'Различающиеся поля сохраняемой карточки будут защищены от автоматических изменений.',
      'Объединить события?',
      { type: 'warning', confirmButtonText: 'Объединить', cancelButtonText: 'Отмена' }
    )
    await store.merge(route.params.id, targetId)
    await router.replace({ name: 'event-detail', params: { id: targetId } })
    await store.fetchDetail(targetId)
    ElMessage.success('События объединены')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message)
  }
}

async function remove() {
  try {
    await ElMessageBox.confirm('Удалить событие безвозвратно?', 'Подтвердите', {
      type: 'warning',
      confirmButtonText: 'Удалить',
      cancelButtonText: 'Отмена'
    })
  } catch (e) {
    return
  }
  try {
    await store.remove(route.params.id)
    ElMessage.success('Удалено')
    router.replace({ name: 'events' })
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(async () => {
  try {
    await store.fetchDetail(route.params.id)
    detailLoaded.value = true
  } catch (e) {
    ElMessage.error(e.message)
  }
})
</script>

<template>
  <div class="page" v-loading="store.detailLoading && !detailLoaded">
    <el-page-header @back="router.push({ name: 'events' })" content="Событие" style="margin-bottom:16px" />

    <div v-if="store.detail" class="grid">
      <el-card class="form-card">
        <template #header>Редактирование</template>
        <el-form label-position="top">
          <el-form-item label="Название">
            <el-input v-model="form.title" />
          </el-form-item>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="Дата">
                <el-input v-model="form.date" placeholder="YYYY-MM-DD" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Дата окончания">
                <el-input v-model="form.end_date" placeholder="YYYY-MM-DD" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="Время">
                <el-input v-model="form.time" placeholder="HH:MM" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Время окончания">
                <el-input v-model="form.end_time" placeholder="HH:MM" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="Место">
            <el-input v-model="form.place" />
          </el-form-item>
          <el-form-item label="Адрес">
            <el-input v-model="form.address" />
          </el-form-item>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="Категория">
                <el-select v-model="form.category" clearable style="width:100%">
                  <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Цена">
                <el-input v-model="form.price" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="Статус">
            <el-select v-model="form.status" style="width:100%">
              <el-option v-for="s in STATUSES" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="Состояние мероприятия">
            <el-select v-model="form.event_state" style="width:100%">
              <el-option v-for="(label, value) in EVENT_STATES" :key="value" :label="label" :value="value" />
            </el-select>
          </el-form-item>
          <el-button type="primary" :loading="store.saving" @click="save">Сохранить</el-button>
          <el-button :loading="store.saving" @click="hide">Скрыть</el-button>
          <el-button type="danger" @click="remove">Удалить</el-button>
          <template v-if="store.detail.conflict_post_id">
            <el-divider />
            <p>Сверьте спорное сообщение с карточкой. Обычное сохранение не закрывает конфликт.</p>
            <el-button :loading="store.saving" @click="resolveConflict(true)">Сохранить и закрыть конфликт</el-button>
            <el-button :loading="store.saving" @click="resolveConflict(false)">Оставить карточку без изменений</el-button>
          </template>
          <el-divider />
          <el-form-item label="Объединить с карточкой (ID сохраняемой)">
            <el-input-number v-model="mergeTarget" :min="1" :precision="0" />
          </el-form-item>
          <el-button @click="merge">Объединить…</el-button>
        </el-form>
      </el-card>

      <div class="meta">
        <el-card v-if="store.detail">
          <template #header>Исходный пост</template>
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
          <div class="kv"><b>message_id:</b> {{ store.detail.message_id }}</div>
          <div class="kv"><b>Опубликовано:</b> {{ store.detail.published_at }}</div>
          <div class="kv"><b>URL:</b>
            <a :href="store.detail.url" target="_blank" rel="noopener">{{ store.detail.url }}</a>
          </div>
          <div class="kv"><b>Уверенность LLM:</b> {{ store.detail.confidence }}</div>
          <div class="kv"><b>Создано:</b> {{ store.detail.created_at }}</div>
          <el-divider />
          <div class="kv"><b>Reason LLM:</b></div>
          <pre class="text-block">{{ store.detail.reason }}</pre>
          <el-divider />
          <div class="kv"><b>Текст поста:</b></div>
          <pre class="text-block">{{ store.detail.text }}</pre>
          <el-divider />
          <b>Связанные посты и извлечённые сведения</b>
          <el-collapse>
            <el-collapse-item v-for="source in store.detail.sources" :key="source.raw_post_id"
              :title="`${source.source} #${source.message_id}`" :name="source.raw_post_id">
              <a :href="source.url" target="_blank" rel="noopener">Открыть пост</a>
              <div>{{ source.edited_at || source.published_at }}</div>
              <pre class="text-block">{{ source.text }}</pre>
              <template v-if="source.extracted_event">
                <div><b>Тип сообщения:</b> {{ ACTIONS[source.extracted_event.action || 'announcement'] }}</div>
                <template v-for="(label, key) in EXTRACTED_FIELDS" :key="key">
                  <div v-if="source.extracted_event[key]" class="kv"><b>{{ label }}:</b> {{ source.extracted_event[key] }}</div>
                </template>
              </template>
              <div v-else>Исходное извлечение неизвестно</div>
            </el-collapse-item>
          </el-collapse>
          <template v-if="store.detail.links && store.detail.links.length">
            <el-divider />
            <div class="kv"><b>Ссылки из поста:</b></div>
            <ul>
              <li v-for="l in store.detail.links" :key="l">
                <a :href="l" target="_blank" rel="noopener">{{ l }}</a>
              </li>
            </ul>
          </template>
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 20px 32px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.kv { margin-bottom: 8px; line-height: 1.5; }
.text-block { white-space: pre-wrap; word-break: break-word; background: #f5f7fa; padding: 12px; border-radius: 6px; font-family: ui-monospace, monospace; font-size: 13px; }
@media (max-width: 600px) { .page { padding: 16px 12px; } }
</style>
