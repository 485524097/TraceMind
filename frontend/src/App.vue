<script setup lang="ts">
import { provide, ref } from 'vue'
import { RouterView } from 'vue-router'
import AppShell from '@/components/AppShell.vue'

const shellKbName = ref('')
provide('shellKbName', shellKbName)
</script>

<template>
  <AppShell>
    <RouterView />
  </AppShell>
</template>
