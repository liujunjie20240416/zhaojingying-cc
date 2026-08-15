<script setup>
import {onMounted, ref} from 'vue'
import api from '@/js/http/api.js'
import {getApiErrorMessage} from '@/js/http/errors.js'

const props = defineProps({friendId: {type: Number, required: true}})
const emit = defineEmits(['close'])
const diagnostics = ref(null)
const loading = ref(true)
const error = ref('')

function formatTokens(value) {
  return Number(value || 0).toLocaleString('zh-CN')
}

function formatK(value) {
  const tokens = Number(value || 0)
  return tokens ? `${(tokens / 1000).toFixed(1)}k` : '0k'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await api.get('/api/friend/context-diagnostics/', {params: {friend_id: props.friendId}})
    diagnostics.value = response.data.diagnostics
  } catch (err) {
    error.value = getApiErrorMessage(err, '读取上下文监控失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="absolute inset-0 z-20 overflow-y-auto bg-base-100 p-3 shadow-xl sm:inset-3 sm:rounded-box sm:p-5">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h3 class="text-lg font-bold">上下文监控</h3>
        <p class="text-sm opacity-65">估算值用于调试；上一轮 Provider token 为实际返回值。</p>
      </div>
      <button class="btn btn-sm btn-circle btn-ghost" @click="emit('close')">✕</button>
    </div>
    <div v-if="loading" class="flex justify-center py-10"><span class="loading loading-spinner"></span></div>
    <div v-else-if="error" class="alert alert-error text-sm">{{ error }}</div>
    <template v-else-if="diagnostics">
      <div class="stats stats-vertical mb-4 w-full border border-base-300 shadow">
        <div class="stat py-3"><div class="stat-title">当前估算输入</div><div class="stat-value text-2xl">{{ formatK(diagnostics.estimated_input_tokens) }}</div><div class="stat-desc">{{ formatTokens(diagnostics.estimated_input_tokens) }} token · 软预算 {{ formatK(diagnostics.soft_input_budget) }} · 硬预算 {{ formatK(diagnostics.hard_input_budget) }}</div></div>
        <div class="stat py-3"><div class="stat-title">上一轮实际输入</div><div class="stat-value text-2xl">{{ formatK(diagnostics.last_provider_input_tokens) }}</div><div class="stat-desc">{{ formatTokens(diagnostics.last_provider_input_tokens) }} token · Provider 返回的 usage</div></div>
      </div>
      <div class="mb-4 rounded-box bg-base-200 p-3 text-sm">状态：<span class="font-semibold">{{ diagnostics.pressure }}</span> · 当前预览 {{ diagnostics.recent_online_turns }} 轮 Online Chat</div>
      <h4 class="mb-2 font-semibold">各区块估算 Token</h4>
      <div class="space-y-2">
        <div v-for="(tokens, name) in diagnostics.components" :key="name" class="flex items-center justify-between rounded-box bg-base-200 px-3 py-2 text-sm"><span>{{ name }}</span><span class="font-mono">{{ formatTokens(tokens) }}</span></div>
      </div>
      <button class="btn btn-outline btn-sm mt-4" @click="load">刷新</button>
    </template>
  </div>
</template>
