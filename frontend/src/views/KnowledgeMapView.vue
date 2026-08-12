<script setup lang="ts">
import cytoscape, { type Core, type ElementDefinition, type EventObject } from 'cytoscape'
import { ElButton } from 'element-plus'
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
  type Ref,
} from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getKnowledgeBase } from '@/services/knowledgeBases'
import { getKnowledgeMap } from '@/services/knowledgeMap'
import type {
  KnowledgeMapEdge,
  KnowledgeMapNode,
  KnowledgeMapNodeType,
  KnowledgeMapResponse,
} from '@/types/knowledgeMap'

const route = useRoute()
const router = useRouter()
const knowledgeBaseId = String(route.params.knowledgeBaseId)
const shellKbName = inject<Ref<string>>('shellKbName', ref(''))
const graphElement = ref<HTMLElement | null>(null)
const graphData = ref<KnowledgeMapResponse>({ nodes: [], edges: [] })
const loading = ref(true)
const error = ref('')
const selectedNode = ref<KnowledgeMapNode | null>(null)
const selectedEdge = ref<KnowledgeMapEdge | null>(null)
const filters = reactive<Record<KnowledgeMapNodeType, boolean>>({
  knowledge_base: true,
  knowledge_entry: true,
  document: true,
  tag: true,
})
let graph: Core | null = null
let resizeObserver: ResizeObserver | null = null

const nodeById = computed(() => new Map(graphData.value.nodes.map((node) => [node.id, node])))
const entryCount = computed(
  () => graphData.value.nodes.filter(({ type }) => type === 'knowledge_entry').length,
)
const relatedReasons = computed(() => {
  if (selectedEdge.value?.type !== 'related') return []
  const tags = Array.isArray(selectedEdge.value.metadata.shared_tags)
    ? selectedEdge.value.metadata.shared_tags.map(String)
    : []
  const documents = Array.isArray(selectedEdge.value.metadata.shared_document_ids)
    ? selectedEdge.value.metadata.shared_document_ids.map(String)
    : []
  return [...tags.map((tag) => `共享标签：${tag}`), ...documents.map((id) => `共享文档：${id}`)]
})

function elements(): ElementDefinition[] {
  return [
    ...graphData.value.nodes.map((node) => ({
      group: 'nodes' as const,
      data: { id: node.id, label: node.label, nodeType: node.type },
    })),
    ...graphData.value.edges.map((edge) => ({
      group: 'edges' as const,
      data: { id: edge.id, source: edge.source, target: edge.target, edgeType: edge.type },
    })),
  ]
}

function selectElement(event: EventObject): void {
  const id = event.target.id()
  if (event.target.isNode()) {
    selectedNode.value = nodeById.value.get(id) ?? null
    selectedEdge.value = null
    return
  }
  selectedNode.value = null
  selectedEdge.value = graphData.value.edges.find((edge) => edge.id === id) ?? null
}

function applyFilters(): void {
  if (!graph) return
  graph.batch(() => {
    graph?.nodes().forEach((node) => {
      const type = node.data('nodeType') as KnowledgeMapNodeType
      node.style('display', filters[type] ? 'element' : 'none')
    })
    graph?.edges().forEach((edge) => {
      const visible =
        edge.source().style('display') !== 'none' && edge.target().style('display') !== 'none'
      edge.style('display', visible ? 'element' : 'none')
    })
  })
}

function initializeGraph(): void {
  if (!graphElement.value) return
  const compactLabels = window.innerWidth <= 680
  graph?.destroy()
  graph = cytoscape({
    container: graphElement.value,
    elements: elements(),
    minZoom: 0.2,
    maxZoom: 3,
    wheelSensitivity: 0.2,
    style: [
      {
        selector: 'node',
        style: {
          label: 'data(label)',
          'font-size': compactLabels ? 8 : 10,
          'text-max-width': compactLabels ? '72px' : '120px',
          'text-wrap': 'ellipsis',
          'background-color': '#7a8497',
          color: '#19233b',
          'text-valign': 'bottom',
          'text-margin-y': 7,
        },
      },
      {
        selector: 'node[nodeType = "knowledge_base"]',
        style: { 'background-color': '#2f6eff', width: 38, height: 38 },
      },
      {
        selector: 'node[nodeType = "knowledge_entry"]',
        style: { 'background-color': '#16865c', width: 28, height: 28 },
      },
      {
        selector: 'node[nodeType = "document"]',
        style: { 'background-color': '#9a6700', shape: 'round-rectangle' },
      },
      {
        selector: 'node[nodeType = "tag"]',
        style: { 'background-color': '#8b5cf6', width: 18, height: 18 },
      },
      {
        selector: 'edge',
        style: { width: 1, 'line-color': '#cbd2dc', 'curve-style': 'bezier', opacity: 0.7 },
      },
      {
        selector: 'edge[edgeType = "related"]',
        style: { 'line-style': 'dashed', 'line-color': '#8b5cf6' },
      },
      { selector: ':selected', style: { 'border-width': 3, 'border-color': '#2f6eff' } },
    ],
    layout: {
      name: 'cose',
      animate: false,
      fit: true,
      padding: 32,
      nodeDimensionsIncludeLabels: true,
    },
  })
  graph.on('tap', 'node, edge', selectElement)
  graph.on('tap', (event) => {
    if (event.target === graph) {
      selectedNode.value = null
      selectedEdge.value = null
    }
  })
  applyFilters()
  resizeObserver?.disconnect()
  resizeObserver = new ResizeObserver(() => graph?.resize())
  resizeObserver.observe(graphElement.value)
}

function fitGraph(): void {
  graph?.fit(undefined, 32)
}

async function openSelected(): Promise<void> {
  if (!selectedNode.value?.entity_id) return
  if (selectedNode.value.type === 'knowledge_entry') {
    await router.push(
      `/knowledge-bases/${knowledgeBaseId}/knowledge/${selectedNode.value.entity_id}`,
    )
  } else if (selectedNode.value.type === 'document') {
    const relativePath = String(selectedNode.value.metadata.relative_path ?? '')
    await router.push({
      path: `/knowledge-bases/${knowledgeBaseId}/documents`,
      query: { query: relativePath, focusDocument: selectedNode.value.entity_id },
    })
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [knowledgeBase, response] = await Promise.all([
      getKnowledgeBase(knowledgeBaseId),
      getKnowledgeMap(knowledgeBaseId),
    ])
    shellKbName.value = knowledgeBase.name
    graphData.value = response
    loading.value = false
    await nextTick()
    initializeGraph()
  } catch {
    error.value = '知识图谱加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(filters, applyFilters, { deep: true })
onMounted(load)
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  graph?.destroy()
})
</script>

<template>
  <main class="knowledge-map-page">
    <header class="knowledge-map-header">
      <div>
        <h1>知识图谱</h1>
        <p>根据知识、文档和标签实时派生的关系。</p>
      </div>
      <ElButton class="secondary-button" @click="fitGraph">适应画布</ElButton>
    </header>

    <div v-if="error" class="conv-error" role="alert">{{ error }}</div>
    <div v-else-if="loading" class="muted-text">正在加载图谱…</div>
    <div v-else class="knowledge-map-layout">
      <section class="knowledge-map-workspace" aria-label="知识关系图">
        <div class="knowledge-map-filters" aria-label="节点类型筛选">
          <label v-for="(_, type) in filters" :key="type">
            <input v-model="filters[type]" type="checkbox" />
            {{
              { knowledge_base: '知识库', knowledge_entry: '知识', document: '文档', tag: '标签' }[
                type
              ]
            }}
          </label>
        </div>
        <p v-if="entryCount === 0" class="knowledge-map-empty">
          请先把会话回答保存为知识，<br />再查看关系图。
        </p>
        <div ref="graphElement" class="knowledge-map-canvas" data-testid="knowledge-map-canvas" />
      </section>

      <aside class="knowledge-map-inspector" aria-label="所选图谱项目">
        <template v-if="selectedNode">
          <span class="eyebrow">{{
            { knowledge_base: '知识库', knowledge_entry: '知识', document: '文档', tag: '标签' }[
              selectedNode.type
            ]
          }}</span>
          <h2>{{ selectedNode.label }}</h2>
          <p v-if="selectedNode.type === 'tag'" class="muted-text">
            关联 {{ selectedNode.metadata.entry_count }} 条知识
          </p>
          <button
            v-if="['knowledge_entry', 'document'].includes(selectedNode.type)"
            class="text-action map-open-action"
            type="button"
            @click="openSelected"
          >
            打开{{ selectedNode.type === 'document' ? '文档' : '知识' }} →
          </button>
        </template>
        <template v-else-if="selectedEdge">
          <span class="eyebrow">{{
            selectedEdge.type === 'related' ? '相关关系' : '包含或引用关系'
          }}</span>
          <h2>
            {{ nodeById.get(selectedEdge.source)?.label }} →
            {{ nodeById.get(selectedEdge.target)?.label }}
          </h2>
          <ul v-if="relatedReasons.length" class="knowledge-map-reasons">
            <li v-for="reason in relatedReasons" :key="reason">{{ reason }}</li>
          </ul>
        </template>
        <p v-else class="muted-text">选择节点或关系查看详情。</p>
      </aside>
    </div>
  </main>
</template>
