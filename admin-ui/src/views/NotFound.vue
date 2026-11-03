<script setup>
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template>
  <div class="nf">
    <h1 class="page-title">404 — страница не найдена</h1>
    <va-button @click="router.push({ name: 'events' })">К списку событий</va-button>
  </div>
</template>

<style scoped>
.nf { padding: 60px 32px; text-align: center; }
</style>
