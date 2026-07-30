<script setup>
import { onMounted, reactive, ref, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useEventsStore } from '../stores/events'
import { ElMessage, ElMessageBox } from 'element-plus'

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

const form = reactive({})
const detailLoaded = ref(false)

function loadDetail(d) {
  if (!d) return
  for (const k of ['title', 'date', 'end_date', 'time', 'end_time', 'place', 'address', 'price', 'status']) {
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
          <el-button type="primary" :loading="store.saving" @click="save">Сохранить</el-button>
          <el-button type="danger" @click="remove">Удалить</el-button>
        </el-form>
      </el-card>

      <div class="meta">
        <el-card v-if="store.detail">
          <template #header>Исходный пост</template>
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
</style>