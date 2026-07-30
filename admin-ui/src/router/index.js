import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '../api/client'

const routes = [
  { path: '/', redirect: '/events' },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/Login.vue'),
    meta: { public: true }
  },
  {
    path: '/events',
    name: 'events',
    component: () => import('../views/EventList.vue')
  },
  {
    path: '/events/:id',
    name: 'event-detail',
    component: () => import('../views/EventDetail.vue')
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('../views/NotFound.vue'),
    meta: { public: true }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!getToken()) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})

export default router