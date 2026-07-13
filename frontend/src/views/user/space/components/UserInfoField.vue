<script setup>
defineProps(['userProfile'])
</script>

<template>
<div v-if="userProfile" class="flex w-full max-w-xl flex-col items-center gap-4 px-4 mt-6 sm:mt-12 sm:flex-row sm:items-start sm:gap-8">
  <div class="avatar">
    <div class="w-28 rounded-full sm:w-44">
      <img :src="userProfile.photo" alt="">
    </div>
  </div>

    <div class="flex min-w-0 flex-col justify-center text-center sm:h-44 sm:w-64 sm:text-left">
  <div class="text-xl font-bold line-clamp-1 break-all sm:text-2xl">{{userProfile.username}}</div>
  <div class="text-sm text-gray-500 mt-2">AIFriends号:{{userProfile.user_id}}</div>
  <div class="text-sm mt-4 line-clamp-4 break-all sm:h-20">{{userProfile.profile}}</div>
    </div>
</div>
</template>

<style scoped>

</style>
