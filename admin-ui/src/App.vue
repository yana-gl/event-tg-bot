<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useBreakpoint } from 'vuestic-ui'
import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const breakpoints = useBreakpoint()
const sidebarVisible = ref(!breakpoints.mdDown)
const isPublic = computed(() => Boolean(route.meta.public))
const navigation = [
  { name: 'events', label: 'События', icon: 'event' },
  { name: 'channels', label: 'Каналы', icon: 'forum' }
]

watch(() => breakpoints.mdDown, (mobile) => { sidebarVisible.value = !mobile })
watch(() => route.fullPath, () => {
  if (breakpoints.mdDown) sidebarVisible.value = false
})

function isActive(name) {
  return route.name === name || (name === 'events' && route.name === 'event-detail')
}

function logout() {
  auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <router-view v-if="isPublic" />
  <va-layout
    v-else
    :top="{ fixed: true, order: 2 }"
    :left="{ fixed: true, absolute: breakpoints.mdDown, order: 1, overlay: breakpoints.mdDown && sidebarVisible }"
    @left-overlay-click="sidebarVisible = false"
  >
    <template #top>
      <va-navbar class="app-navbar" color="backgroundSecondary">
        <template #left>
          <div class="navbar-brand">
            <va-button preset="plain" :icon="sidebarVisible ? 'menu_open' : 'menu'"
              aria-label="Переключить меню" :aria-expanded="sidebarVisible" @click="sidebarVisible = !sidebarVisible" />
            <router-link to="/events" class="app-logo">TG <span>Event</span></router-link>
          </div>
        </template>
        <template #right>
          <div class="navbar-actions">
            <span class="user-name">{{ auth.user || 'Администратор' }}</span>
            <va-avatar color="primary" size="small">{{ (auth.user || 'A').slice(0, 1).toUpperCase() }}</va-avatar>
            <va-button preset="plain" icon="logout" aria-label="Выйти" @click="logout" />
          </div>
        </template>
      </va-navbar>
    </template>

    <template #left>
      <va-sidebar v-model="sidebarVisible" width="280px" minimized-width="0" color="backgroundSecondary">
        <nav aria-label="Основная навигация" class="app-navigation">
          <va-sidebar-item v-for="item in navigation" :key="item.name" :to="{ name: item.name }"
            :active="isActive(item.name)" active-color="rgba(21, 78, 193, 0.1)"
            :text-color="isActive(item.name) ? 'primary' : 'textPrimary'" :hover-opacity="0.1">
            <va-sidebar-item-content>
              <va-icon :name="item.icon" :color="isActive(item.name) ? 'primary' : 'secondary'" size="20px" />
              <va-sidebar-item-title>{{ item.label }}</va-sidebar-item-title>
            </va-sidebar-item-content>
          </va-sidebar-item>
        </nav>
      </va-sidebar>
    </template>

    <template #content>
      <main class="app-content">
        <router-view :key="route.fullPath" />
      </main>
    </template>
  </va-layout>
</template>

<style scoped>
.app-navbar { padding: 12px 24px; }
.navbar-brand, .navbar-actions { display: flex; align-items: center; gap: 16px; }
.app-logo { color: var(--va-primary); font-size: 24px; font-weight: 700; text-decoration: none; white-space: nowrap; }
.app-logo span { color: var(--va-text-primary); font-weight: 600; }
.app-navigation { padding-top: 16px; }
.app-content { min-width: 0; padding: 24px; }
@media (max-width: 640px) {
  .app-navbar { padding: 12px 16px; }
  .user-name { display: none; }
  .navbar-brand, .navbar-actions { gap: 8px; }
  .app-content { padding: 16px; }
}
</style>
