<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useToast } from 'vuestic-ui'
import { useEventsStore, STATUSES, EVENT_STATES } from '../stores/events'

const store = useEventsStore()
const router = useRouter()
const { init: notify } = useToast()
const statusColors = { published: 'success', draft: 'warning', rejected: 'danger', repeat: 'secondary', not_event: 'secondary' }
const columns = [
  { key: 'id', label: 'ID', width: '70px' },
  { key: 'title', label: 'Событие', width: '260px' },
  { key: 'when', label: 'Когда', width: '170px' },
  { key: 'place', label: 'Место', width: '180px' },
  { key: 'category', label: 'Категория' },
  { key: 'status', label: 'Статус' },
  { key: 'event_state', label: 'Состояние', width: '180px' },
  { key: 'conflict_post_id', label: 'Проверка' }
]

function when(row) {
  return [row.date, row.time].filter(Boolean).join(', ') || '—'
}

function openRow({ event, item }) {
  if (!event.target.closest('a')) router.push({ name: 'event-detail', params: { id: item.id } })
}

async function refresh(status) {
  try {
    await store.fetchList(status)
  } catch (e) {
    notify({ message: e.message, color: 'danger' })
  }
}

onMounted(() => refresh())
</script>

<template>
  <div class="page">
    <h1 class="page-title">События</h1>
    <p class="page-description">Проверяйте спорные изменения и редактируйте карточки городской афиши.</p>
    <va-card>
      <va-card-content>
        <div class="toolbar">
          <va-select :model-value="store.status" :options="STATUSES" text-by="label" value-by="value"
            label="Показать" class="status-filter" @update:model-value="refresh" />
          <va-button preset="secondary" icon="refresh" :loading="store.loading" @click="refresh()">Обновить</va-button>
        </div>
        <div class="table-wrapper">
          <va-data-table :items="store.items" :columns="columns" :loading="store.loading"
            hoverable clickable no-data-html="Нет событий в этом статусе." items-track-by="id" @row:click="openRow">
            <template #cell(title)="{ rowData }">
              <router-link :to="{ name: 'event-detail', params: { id: rowData.id } }" class="event-link">
                {{ rowData.title || 'Без названия' }}
              </router-link>
            </template>
            <template #cell(when)="{ rowData }">{{ when(rowData) }}</template>
            <template #cell(place)="{ value }">{{ value || '—' }}</template>
            <template #cell(category)="{ value }">{{ value || '—' }}</template>
            <template #cell(status)="{ value }">
              <va-chip :color="statusColors[value] || 'secondary'" size="small">
                {{ STATUSES.find(s => s.value === value)?.label || value }}
              </va-chip>
            </template>
            <template #cell(event_state)="{ value }">{{ EVENT_STATES[value] || value }}</template>
            <template #cell(conflict_post_id)="{ value }">
              <va-chip v-if="value" color="warning" size="small">Спорное изменение</va-chip>
              <span v-else class="muted">—</span>
            </template>
          </va-data-table>
        </div>
      </va-card-content>
    </va-card>
  </div>
</template>

<style scoped>
.toolbar .status-filter { width: 260px; max-width: 100%; }
.event-link { display: block; font-weight: 600; line-height: 1.5; white-space: normal; }
@media (max-width: 640px) { .toolbar .status-filter { width: 100%; } }
</style>
