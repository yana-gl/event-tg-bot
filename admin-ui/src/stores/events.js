import { defineStore } from 'pinia'
import { apiGet, apiPost } from '../api/client'

export const STATUSES = [
  { value: 'draft', label: 'Черновики' },
  { value: 'published', label: 'Опубликовано' },
  { value: 'rejected', label: 'Отклонено' },
  { value: 'repeat', label: 'Повторы' },
  { value: 'not_event', label: 'Не событие' }
]

export const useEventsStore = defineStore('events', {
  state: () => ({
    status: 'draft',
    items: [],
    loading: false,
    detail: null,
    detailLoading: false,
    saving: false
  }),
  actions: {
    async fetchList(status) {
      if (status) this.status = status
      this.loading = true
      try {
        const data = await apiGet(`/api/events?status=${encodeURIComponent(this.status)}`)
        this.items = data.items || []
      } finally {
        this.loading = false
      }
    },
    async fetchDetail(id) {
      this.detailLoading = true
      try {
        this.detail = await apiGet(`/api/events/${id}`)
        return this.detail
      } finally {
        this.detailLoading = false
      }
    },
    async update(id, fields) {
      this.saving = true
      try {
        await apiPost(`/api/events/${id}`, fields)
      } finally {
        this.saving = false
      }
    },
    async remove(id) {
      return apiPost(`/api/events/${id}/delete`, {})
    }
  }
})