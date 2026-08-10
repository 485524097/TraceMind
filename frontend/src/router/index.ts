import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import ConversationView from '@/views/ConversationView.vue'
import DocumentView from '@/views/DocumentView.vue'
import KnowledgeBaseView from '@/views/KnowledgeBaseView.vue'
import KnowledgeView from '@/views/KnowledgeView.vue'
import KnowledgeDetailView from '@/views/KnowledgeDetailView.vue'

export default createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/knowledge-bases', name: 'knowledge-bases', component: KnowledgeBaseView },
    {
      path: '/knowledge-bases/:knowledgeBaseId/documents',
      name: 'documents',
      component: DocumentView,
    },
    {
      path: '/knowledge-bases/:knowledgeBaseId/chat',
      name: 'conversation',
      component: ConversationView,
    },
    {
      path: '/knowledge-bases/:knowledgeBaseId/knowledge',
      name: 'knowledge',
      component: KnowledgeView,
    },
    {
      path: '/knowledge-bases/:knowledgeBaseId/knowledge/:entryId',
      name: 'knowledge-detail',
      component: KnowledgeDetailView,
    },
  ],
})
