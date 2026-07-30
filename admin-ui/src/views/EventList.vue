<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useEventsStore, STATUSES } from '../stores/events'
import { useAuthStore } from '../stores/auth'
import { ElMessage } from 'element-plus'

const store = useEventsStore()
const auth = useAuthStore()
const router = useRouter()

function categoryLabel(value) {
  if (!value) return ''
  return value
}

function when(row) {
  const parts = []
  if (row.date) parts.push(row.date)
  if (row.time) parts.push(row.time)
  return parts.join(', ')
}

async function selectStatus(value) {
  try {
    await store.fetchList(value)
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function open(row) {
  router.push({ name: 'event-detail', params: { id: row.id } })
}

function logout() {
  auth.logout()
  router.replace({ name: 'login' })
}

onMounted(() => {
  store.fetchList().catch((e) => ElMessage.error(e.message))
})
</script>

<template>
  <div class="page">
    <header class="topbar">
      <span class="title">События</span>
      <div class="right">
        <el-tag v-if="auth.user" type="info">{{ auth.user }}</el-tag>
        <el-button text @click="logout">Выйти</el-button>
      </div>
    </header>

    <div class="filters">
      <el-select :model-value="store.status" style="width:220px" @change="selectStatus">
        <el-option v-for="s in STATUSES" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
    </div>

    <el-table v-loading="store.loading" :data="store.items" @row-click="open" highlight-current-row>
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column prop="title" label="Название" min-width="240" />
      <el-table-column label="Когда" width="180">
        <template #default="{ row }">{{ when(row) || '—' }}</template>
      </el-table-column>
      <el-table-column prop="place" label="Место" min-width="160" />
      <el-table-column label="Категория" width="120">
        <template #default="{ row }">{{ categoryLabel(row.category) || '—' }}</template>
      </el-table-column>
      <el-table-column label="Статус" width="120">
        <template #default="{ row }"><el-tag>{{ row.status }}</el-tag></template>
      </el-table-column>
    </el-table>
    <div v-if="!store.loading && !store.items.length" class="empty">Нет событий в этом статусе.</div>
  </div>
</template>

<style scoped>
.page { padding: 20px 32px; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 20px; font-weight: 600; }
.filters { margin-bottom: 16px; }
.empty { color: #909399; padding: 24px; text-align: center; }
</style>