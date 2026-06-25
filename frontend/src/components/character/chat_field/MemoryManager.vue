<script setup>
import {computed, onMounted, ref, watch} from 'vue'
import api from '@/js/http/api.js'

const props = defineProps({friendId: {type: Number, required: true}})
const emit = defineEmits(['close'])

const memories = ref([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const editingId = ref(null)
const draft = ref({fact: '', category: 'preference'})

const labels = {
  identity: '身份信息', preference: '偏好习惯', experience: '重要经历', relationship: '相处习惯',
}
const categories = Object.entries(labels)
const grouped = computed(() => Object.keys(labels).map(category => ({
  category, label: labels[category], items: memories.value.filter(memory => memory.category === category),
})).filter(group => group.items.length))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get('/api/friend/memory/', {params: {friend_id: props.friendId}})
    if (res.data.result === 'success') memories.value = res.data.memories
    else error.value = res.data.result || '读取记忆失败'
  } catch {
    error.value = '读取记忆失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function startAdd() {
  editingId.value = 'new'
  draft.value = {fact: '', category: 'preference'}
}
function startEdit(memory) {
  editingId.value = memory.id
  draft.value = {fact: memory.fact, category: memory.category}
}
function cancelEdit() { editingId.value = null }

async function save() {
  const fact = draft.value.fact.trim()
  if (!fact) return
  saving.value = true
  error.value = ''
  try {
    let res
    if (editingId.value === 'new') {
      res = await api.post('/api/friend/memory/', {...draft.value, fact, friend_id: props.friendId})
    } else {
      res = await api.put(`/api/friend/memory/${editingId.value}/`, {...draft.value, fact})
    }
    if (res.data.result !== 'success') throw new Error(res.data.result)
    await load()
    editingId.value = null
  } catch (err) {
    error.value = err.message || '保存失败，请稍后重试'
  } finally {
    saving.value = false
  }
}

async function forget(memory) {
  if (!confirm(`确定忘掉“${memory.fact}”吗？`)) return
  try {
    const res = await api.post(`/api/friend/memory/${memory.id}/forget/`)
    if (res.data.result !== 'success') throw new Error(res.data.result)
    memories.value = memories.value.filter(item => item.id !== memory.id)
  } catch (err) {
    error.value = err.message || '操作失败，请稍后重试'
  }
}

watch(() => props.friendId, load)
onMounted(load)
</script>

<template>
  <div class="absolute inset-3 z-20 overflow-y-auto rounded-box bg-base-100 p-5 shadow-xl">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h3 class="text-lg font-bold">我记得的你</h3>
        <p class="text-sm opacity-65">你可以修改、补充或忘掉这里的内容。</p>
      </div>
      <button class="btn btn-sm btn-circle btn-ghost" @click="emit('close')">✕</button>
    </div>

    <div v-if="error" class="alert alert-error mb-3 py-2 text-sm">{{ error }}</div>
    <div v-if="loading" class="flex justify-center py-10"><span class="loading loading-spinner"></span></div>

    <template v-else>
      <div v-for="group in grouped" :key="group.category" class="mb-4">
        <h4 class="mb-1 text-sm font-semibold opacity-70">{{ group.label }}</h4>
        <div v-for="memory in group.items" :key="memory.id" class="mb-2 rounded-box bg-base-200 p-3">
          <template v-if="editingId === memory.id">
            <textarea v-model="draft.fact" class="textarea textarea-bordered w-full" maxlength="500" />
            <select v-model="draft.category" class="select select-bordered mt-2 w-full">
              <option v-for="[value, label] in categories" :key="value" :value="value">{{ label }}</option>
            </select>
            <div class="mt-2 flex justify-end gap-2"><button class="btn btn-sm" @click="cancelEdit">取消</button><button class="btn btn-sm btn-primary" :disabled="saving" @click="save">保存</button></div>
          </template>
          <template v-else>
            <div class="flex items-start gap-2"><p class="flex-1 whitespace-pre-wrap">{{ memory.fact }}</p><button class="btn btn-xs btn-ghost" @click="startEdit(memory)">编辑</button><button class="btn btn-xs btn-ghost text-error" @click="forget(memory)">忘掉</button></div>
            <span v-if="memory.source === 'user'" class="badge badge-outline badge-xs mt-1">你确认过</span>
          </template>
        </div>
      </div>

      <div v-if="!grouped.length && editingId !== 'new'" class="py-6 text-center opacity-60">还没有长期记忆。</div>
      <div v-if="editingId === 'new'" class="rounded-box border border-base-300 p-3">
        <textarea v-model="draft.fact" class="textarea textarea-bordered w-full" maxlength="500" placeholder="例如：用户不喜欢吃香菜" />
        <select v-model="draft.category" class="select select-bordered mt-2 w-full"><option v-for="[value, label] in categories" :key="value" :value="value">{{ label }}</option></select>
        <div class="mt-2 flex justify-end gap-2"><button class="btn btn-sm" @click="cancelEdit">取消</button><button class="btn btn-sm btn-primary" :disabled="saving" @click="save">添加</button></div>
      </div>
      <button v-else class="btn btn-outline btn-sm mt-1" @click="startAdd">+ 添加一条记忆</button>
    </template>
  </div>
</template>
