<script setup>
import {ref, watch} from "vue";

const props= defineProps(['profile'])
const myProfile=ref(props.profile)

watch(()=>props.profile,newVal=>{
  myProfile.value=newVal
})

defineExpose({
  myProfile,
    }
   )
</script>

<template>
<field class="field">
  <label class="label text-base">简介</label>
  <textarea v-model="myProfile" rows="6" class="textarea w-full"></textarea>
</field>
</template>

<style scoped>

</style>
