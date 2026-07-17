import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import KnowledgeBaseView from '@/views/KnowledgeBaseView.vue'

export default createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/knowledge-bases', name: 'knowledge-bases', component: KnowledgeBaseView },
  ],
})
