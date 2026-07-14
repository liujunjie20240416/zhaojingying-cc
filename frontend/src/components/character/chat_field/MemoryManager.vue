<script setup>
import {computed, onMounted, ref, watch} from 'vue'
import api from '@/js/http/api.js'
import {getApiErrorMessage} from '@/js/http/errors.js'

const props = defineProps({friendId: {type: Number, required: true}})
const emit = defineEmits(['close'])

const memories = ref([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const editingId = ref(null)
const draft = ref({fact: '', subject: 'user', category: 'preference'})
const activeSubject = ref('all')
const expandedEvidenceId = ref(null)
const evidenceLoadingId = ref(null)
const evidenceByMemory = ref({})

const labels = {
  identity: '身份信息', preference: '偏好习惯', experience: '重要经历', relationship: '相处习惯',
}
const categories = Object.entries(labels)
const subjectLabels = {
  user: '用户',
  girlfriend: '女友',
  relationship: '两人关系',
}
const stateLabels = {
  current: '当前有效',
  historical: '历史状态',
  superseded: '已替代',
}
const sourceLabels = {
  user: '你手动添加',
  import: '从导入聊天提炼',
  ai: '从 AI 聊天提炼',
}
const evidenceSourceLabels = {
  user_assertion: '你的手动确认',
  import_chat: '导入聊天记录',
  online_chat: '与 AI 的聊天',
}
const subjects = Object.entries(subjectLabels)
const subjectTabs = computed(() => [
  ['all', `全部 ${memories.value.length}`],
  ...Object.entries(subjectLabels).map(([subject, label]) => [
    subject,
    `${label} ${memories.value.filter(memory => memory.subject === subject).length}`,
  ]),
])
const visibleMemories = computed(() => activeSubject.value === 'all'
  ? memories.value
  : memories.value.filter(memory => memory.subject === activeSubject.value))
const grouped = computed(() => Object.keys(subjectLabels).map(subject => ({
  subject,
  label: subjectLabels[subject],
  categories: Object.keys(labels).map(category => ({
    category,
    label: labels[category],
    items: visibleMemories.value.filter(memory => memory.subject === subject && memory.category === category),
  })).filter(group => group.items.length),
})).filter(group => group.categories.length))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get('/api/friend/memory/', {params: {friend_id: props.friendId}})
    if (res.data.result === 'success') memories.value = res.data.memories
    else error.value = res.data.result || '读取记忆失败'
  } catch (err) {
    error.value = getApiErrorMessage(err, '读取记忆失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function startAdd() {
  editingId.value = 'new'
  draft.value = {fact: '', subject: 'user', category: 'preference'}
}
function startEdit(memory) {
  editingId.value = memory.id
  draft.value = {fact: memory.fact, subject: memory.subject || 'user', category: memory.category}
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
    error.value = getApiErrorMessage(err, '保存失败，请稍后重试')
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
    error.value = getApiErrorMessage(err, '操作失败，请稍后重试')
  }
}

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('zh-CN')
}

async function toggleEvidence(memory) {
  if (expandedEvidenceId.value === memory.id) {
    expandedEvidenceId.value = null
    return
  }
  expandedEvidenceId.value = memory.id
  if (evidenceByMemory.value[memory.id]) return
  evidenceLoadingId.value = memory.id
  error.value = ''
  try {
    const response = await api.get(`/api/friend/memory/${memory.id}/evidence/`)
    evidenceByMemory.value = {
      ...evidenceByMemory.value,
      [memory.id]: response.data.evidences || [],
    }
  } catch (err) {
    error.value = getApiErrorMessage(err, '记忆依据加载失败，请稍后重试')
    expandedEvidenceId.value = null
  } finally {
    evidenceLoadingId.value = null
  }
}

watch(() => props.friendId, () => {
  expandedEvidenceId.value = null
  evidenceByMemory.value = {}
  load()
})
onMounted(load)
</script>

<template>
  <div class="absolute inset-0 z-20 overflow-y-auto bg-base-100 p-3 shadow-xl sm:inset-3 sm:rounded-box sm:p-5">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h3 class="text-lg font-bold">长期记忆</h3>
        <p class="text-sm opacity-65">你可以维护用户、女友和两人关系的记忆。</p>
      </div>
      <button class="btn btn-sm btn-circle btn-ghost" @click="emit('close')">✕</button>
    </div>

    <div v-if="error" class="alert alert-error mb-3 py-2 text-sm">{{ error }}</div>
    <div v-if="loading" class="flex justify-center py-10"><span class="loading loading-spinner"></span></div>

    <template v-else>
      <div class="tabs tabs-box mb-4 flex-nowrap overflow-x-auto">
        <button
          v-for="[value, label] in subjectTabs"
          :key="value"
          :class="['tab', activeSubject === value ? 'tab-active' : '']"
          @click="activeSubject = value"
        >
          {{ label }}
        </button>
      </div>

      <div v-for="subjectGroup in grouped" :key="subjectGroup.subject" class="mb-5">
        <h4 class="mb-2 text-base font-bold">{{ subjectGroup.label }}</h4>
        <div v-for="group in subjectGroup.categories" :key="`${subjectGroup.subject}-${group.category}`" class="mb-3">
          <h5 class="mb-1 text-sm font-semibold opacity-70">{{ group.label }}</h5>
          <div v-for="memory in group.items" :key="memory.id" class="mb-2 rounded-box bg-base-200 p-3">
            <template v-if="editingId === memory.id">
              <textarea v-model="draft.fact" class="textarea textarea-bordered w-full" maxlength="500" />
              <select v-model="draft.subject" class="select select-bordered mt-2 w-full">
                <option v-for="[value, label] in subjects" :key="value" :value="value">{{ label }}</option>
              </select>
              <select v-model="draft.category" class="select select-bordered mt-2 w-full">
                <option v-for="[value, label] in categories" :key="value" :value="value">{{ label }}</option>
              </select>
              <div class="mt-2 flex justify-end gap-2"><button class="btn btn-sm" @click="cancelEdit">取消</button><button class="btn btn-sm btn-primary" :disabled="saving" @click="save">保存</button></div>
            </template>
            <template v-else>
              <div class="flex items-start gap-2"><p class="flex-1 whitespace-pre-wrap">{{ memory.fact }}</p><button class="btn btn-xs btn-ghost" @click="startEdit(memory)">编辑</button><button class="btn btn-xs btn-ghost text-error" @click="forget(memory)">忘掉</button></div>
              <div class="mt-2 flex flex-wrap items-center gap-1">
                <span class="badge badge-primary badge-outline badge-sm">{{ sourceLabels[memory.source] || memory.source }}</span>
                <span class="badge badge-outline badge-xs">{{ stateLabels[memory.memory_state] || '当前有效' }}</span>
                <span v-if="!memory.is_mutable" class="badge badge-outline badge-xs">不可自动改写</span>
                <span v-if="memory.evidences?.length" class="text-xs opacity-55">
                  {{ memory.evidences.length }} 条依据<span v-if="memory.evidences[0]?.chat_day"> · {{ memory.evidences[0].chat_day }}</span>
                </span>
                <span v-else class="text-xs opacity-45">暂无原始依据</span>
              </div>
              <button
                v-if="memory.evidences?.length"
                class="btn btn-ghost btn-xs mt-2 px-0"
                @click="toggleEvidence(memory)"
              >
                {{ expandedEvidenceId === memory.id ? '收起依据' : '查看依据' }}
              </button>
              <div v-if="expandedEvidenceId === memory.id" class="mt-2 rounded-box border border-base-300 bg-base-100 p-3">
                <div v-if="evidenceLoadingId === memory.id" class="flex justify-center py-3"><span class="loading loading-spinner loading-sm"></span></div>
                <div
                  v-for="evidence in evidenceByMemory[memory.id] || []"
                  v-else
                  :key="evidence.id"
                  class="mb-4 last:mb-0"
                >
                  <div class="mb-2 flex flex-wrap items-center gap-2 text-xs">
                    <span class="badge badge-neutral badge-sm">{{ evidenceSourceLabels[evidence.source_type] || evidence.source_type }}</span>
                    <span v-if="evidence.chat_day" class="opacity-60">{{ evidence.chat_day }}</span>
                  </div>
                  <blockquote v-if="evidence.excerpt" class="mb-2 border-l-2 border-primary/40 pl-3 text-sm opacity-80 whitespace-pre-wrap">{{ evidence.excerpt }}</blockquote>
                  <p v-if="!evidence.context_available" class="text-sm opacity-60">这段导入聊天未授权给当前角色关系查看。</p>
                  <div v-else-if="evidence.source_type === 'online_chat'" class="space-y-2">
                    <div v-for="item in evidence.context" :key="item.id" class="rounded-lg bg-base-200 p-2 text-sm">
                      <div class="mb-1 text-xs opacity-50">{{ formatDate(item.timestamp) }}</div>
                      <p><span class="font-semibold">你：</span>{{ item.user_message }}</p>
                      <p class="mt-1"><span class="font-semibold">AI：</span>{{ item.output }}</p>
                    </div>
                  </div>
                  <div v-else-if="evidence.source_type === 'import_chat'" class="max-h-72 space-y-1 overflow-y-auto rounded-lg bg-base-200 p-2">
                    <p v-for="item in evidence.context" :key="item.msg_index" class="text-sm">
                      <span class="mr-1 text-xs opacity-50">{{ item.timestamp }}</span>
                      <span class="font-semibold">{{ item.sender }}：</span>{{ item.content }}
                    </p>
                  </div>
                  <p v-else class="text-sm opacity-60">这是你手动添加或确认的记忆。</p>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>

      <div v-if="!grouped.length && editingId !== 'new'" class="py-6 text-center opacity-60">还没有长期记忆。</div>
      <div v-if="editingId === 'new'" class="rounded-box border border-base-300 p-3">
        <textarea v-model="draft.fact" class="textarea textarea-bordered w-full" maxlength="500" placeholder="例如：用户不喜欢吃香菜" />
        <select v-model="draft.subject" class="select select-bordered mt-2 w-full"><option v-for="[value, label] in subjects" :key="value" :value="value">{{ label }}</option></select>
        <select v-model="draft.category" class="select select-bordered mt-2 w-full"><option v-for="[value, label] in categories" :key="value" :value="value">{{ label }}</option></select>
        <div class="mt-2 flex justify-end gap-2"><button class="btn btn-sm" @click="cancelEdit">取消</button><button class="btn btn-sm btn-primary" :disabled="saving" @click="save">添加</button></div>
      </div>
      <button v-else class="btn btn-outline btn-sm mt-1" @click="startAdd">+ 添加一条记忆</button>
    </template>
  </div>
</template>
