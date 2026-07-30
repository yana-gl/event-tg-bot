<script setup>
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template>
  <div class="nf">
    <h2>404 — страница не найдена</h2>
    <el-button type="primary" @click="router.push({ name: 'events' })">К списку событий</el-button>
  </div>
</template>

<style scoped>
.nf { padding: 60px 32px; text-align: center; }
</style>