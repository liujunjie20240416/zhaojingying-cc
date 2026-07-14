<script setup>

import {nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch} from "vue";
import api from "@/js/http/api.js";
import Character from "@/components/character/Character.vue";
import {useRoute} from "vue-router";
import {getApiErrorMessage} from "@/js/http/errors.js";


const characters=ref([])
const isLoading = ref(false)
const hasCharacters = ref(true)
const sentinelRef = useTemplateRef('sentinel-ref')
const route = useRoute()
const loadError = ref('')

function checkSentinelVisible() {  // 判断哨兵是否能被看到
  if (!sentinelRef.value) return false

  const rect = sentinelRef.value.getBoundingClientRect()
  return rect.top < window.innerHeight && rect.bottom > 0
}

async function loadMore(){
  if(isLoading.value||!hasCharacters.value) return
  isLoading.value=true
  loadError.value=''

  let newCharacters=[]
  try{
    const res = await api.get('/api/homepage/index/',{
      params:{
        items_count:characters.value.length,
        search_query:route.query.q||''
      }
    })
    const data = res.data
    if(data.result==='success'){
      newCharacters=data.characters
    }
  }catch (err){
    loadError.value=getApiErrorMessage(err, '角色列表加载失败，请稍后重试')
  }finally {
    isLoading.value=false
    if(newCharacters.length===0){
      hasCharacters.value=false
    }else{
      characters.value.push(...newCharacters)
      await nextTick()
      if(checkSentinelVisible()){
        await loadMore()
      }
    }
  }
}

let observer = null
onMounted(async ()=>{
  await loadMore()

  observer=new IntersectionObserver(entries => {
    entries.forEach(entry=>{
      if(entry.isIntersecting){
        loadMore()
      }
    })
  },{
    root:null,rootMargin:'2px',threshold:0
  })
  observer.observe(sentinelRef.value)
})
function reset() {
  characters.value = []
  isLoading.value = false
  hasCharacters.value = true
  loadMore()
}

watch(() => route.query.q, newQ => {
  reset()
})


onBeforeUnmount(()=>{
  observer?.disconnect()
})


</script>

<template>
  <div class="flex flex-col items-center mb-12">
    <div v-if="loadError" class="alert alert-error mt-4 w-[calc(100%-1.5rem)] max-w-xl text-sm">{{ loadError }}</div>
    <div class="grid w-full grid-cols-1 justify-items-center gap-6 px-3 mt-6 sm:grid-cols-[repeat(auto-fill,minmax(240px,1fr))] sm:gap-9 sm:px-9 sm:mt-12">
      <Character
          v-for="character in characters"
          :key="character.id"
          :character="character"
      />
    </div>
    <div ref="sentinel-ref" class="h-2 mt-8 "></div>
    <div v-if="isLoading" class="text-gray-500 mt-4">加载中...</div>
    <div v-else-if="!hasCharacters" class="text-gray-500 mt-4">没有更多角色了</div>
  </div>
</template>

<style scoped>

</style>
