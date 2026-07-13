<script setup>

import MenuIcon from "@/components/navbar/icons/MenuIcon.vue";
import HomepageIcon from "@/components/navbar/icons/HomepageIcon.vue";
import FriendIcon from "@/components/navbar/icons/FriendIcon.vue";
import CreateIcon from "@/components/navbar/icons/CreateIcon.vue";
import SearchIcon from "@/components/navbar/icons/SearchIcon.vue";
import {useUserStore} from "@/stores/user.js";
import UserMenu from "@/components/navbar/UserMenu.vue";
import {ref, watch} from "vue";
import {useRoute, useRouter} from "vue-router";
const user=useUserStore()
const searchQuery = ref('')
const router = useRouter()
const route = useRoute()

watch(()=>route.query.q,newQ=>{
  searchQuery.value = newQ || ''
})

function handleSearch(){
  router.push({
    name: 'homepage-index',
    query: {
      q: searchQuery.value.trim(),
    }
  })
}
</script>

<template>
<div class="drawer lg:drawer-open">
  <input id="my-drawer-4" type="checkbox" class="drawer-toggle" />
  <div class="drawer-content">
    <!-- Navbar -->
    <nav class="navbar w-full flex-wrap gap-2 px-2 app-glass-bar sm:flex-nowrap sm:px-4">
      <div class="navbar-start w-auto flex-1 sm:w-1/4 sm:flex-none">
        <label for="my-drawer-4" aria-label="open sidebar" class="btn btn-square btn-ghost">
        <!-- Sidebar toggle icon -->
        <MenuIcon/>
      </label>
      <div class="hidden px-2 font-bold text-xl min-[400px]:block" >AI Friends</div>
      </div>
      <div class="navbar-center order-3 flex w-full max-w-180 justify-center sm:order-none sm:flex-1">
        <form @submit.prevent="handleSearch" class="join flex w-full justify-center sm:w-4/5">
          <input v-model="searchQuery" class="input join-item min-w-0 flex-1 rounded-l-full" placeholder="搜索角色" />
          <button class="btn join-item rounded-r-full gap-1">
            <SearchIcon /><span class="hidden md:inline">搜索</span>
          </button>
        </form>

      </div>

      <div class="navbar-end w-auto">
        <RouterLink v-if="user.isLogin()" :to="{name:'create-index'}" active-class="btn-active" class="btn btn-ghost px-2 text-base sm:px-4"><CreateIcon /><span class="hidden md:inline">创作</span></RouterLink>
      <RouterLink  v-if="user.hasPulledUserInfo && !user.isLogin()" :to="{name:'user-account-login-index'}" active-class="btn-active" class="btn btn-ghost text-lg">登录</RouterLink>
      <UserMenu  v-else-if="user.isLogin()"/>
      </div>

    </nav>
    <!-- Page content here -->
    <slot></slot>
  </div>

  <div class="drawer-side is-drawer-close:overflow-visible">
    <label for="my-drawer-4" aria-label="close sidebar" class="drawer-overlay"></label>
    <div class="flex min-h-full flex-col items-start app-glass-side is-drawer-close:w-16 is-drawer-open:w-54">
      <!-- Sidebar content here -->
      <ul class="menu w-full grow">
        <!-- List item -->
        <li>
          <RouterLink :to="{name:'homepage-index'}" active-class="menu-focus" class="is-drawer-close:tooltip is-drawer-close:tooltip-right py-3" data-tip="首页">
            <!-- Home icon -->
            <HomepageIcon />
            <span class="is-drawer-close:hidden text-base ml-2 whitespace-nowrap">首页</span>
          </RouterLink>
        </li>
                <li>
          <RouterLink :to="{name:'friend-index'}" active-class="menu-focus" class="is-drawer-close:tooltip is-drawer-close:tooltip-right py-3" data-tip="好友">
            <!-- Home icon -->
            <FriendIcon />
            <span class="is-drawer-close:hidden text-base ml-2 whitespace-nowrap">好友</span>
          </RouterLink>
        </li>
                <li>
          <RouterLink :to="{name:'create-index'}" active-class="menu-focus" class="is-drawer-close:tooltip is-drawer-close:tooltip-right py-3" data-tip="创作">
            <!-- Home icon -->
            <CreateIcon />
            <span class="is-drawer-close:hidden text-base ml-2 whitespace-nowrap">创作</span>
          </RouterLink>
        </li>


      </ul>
    </div>
  </div>
</div>
</template>

<style scoped>
/* 顶栏/侧边栏透明,文字加白色光晕投影提升可读性 */
.app-glass-bar,
.app-glass-side {
  background: transparent;
  text-shadow: 0 1px 4px rgba(255, 255, 255, 0.95), 0 0 2px rgba(255, 255, 255, 0.8);
}
/* 图标(子组件 SVG)也加投影 */
.app-glass-bar :deep(svg),
.app-glass-side :deep(svg) {
  filter: drop-shadow(0 1px 2px rgba(255, 255, 255, 0.9));
}
/* daisyUI 按钮(创作/登录/用户菜单)自带 text-shadow,强制覆盖加白色光晕 */
.app-glass-bar :deep(.btn) {
  text-shadow: 0 1px 4px rgba(255, 255, 255, 0.95), 0 0 2px rgba(255, 255, 255, 0.8) !important;
}
</style>
