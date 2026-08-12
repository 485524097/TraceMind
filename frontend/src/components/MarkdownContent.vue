<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

const props = defineProps<{ content: string }>()
const markdown = new MarkdownIt({
  html: false,
  linkify: false,
  typographer: false,
})

// Knowledge snapshots must never trigger remote image loads.
markdown.disable('image')

const rendered = computed(() => markdown.render(props.content))
</script>

<template>
  <!-- markdown-it escapes raw HTML and rejects unsafe URL schemes with this configuration. -->
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="markdown-content" v-html="rendered"></div>
</template>
