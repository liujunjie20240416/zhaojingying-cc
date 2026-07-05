<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Menu, X } from 'lucide-vue-next'

const router = useRouter()

// ---- 视频层数据 ----
const videos = [
  { url: '/videos/landing-1-warm.mp4', label: '暖阳' },
  { url: '/videos/landing-2-water.mp4', label: '静水' },
  { url: '/videos/landing-3-forest.mp4', label: '深林' },
  { url: '/videos/landing-4-dawn.mp4', label: '黎明' },
]

const overlayPng = 'https://soft-zoom-63098134.figma.site/_assets/v11/0b4a435b2df2747593c43d7a1c9b4578f7d8d90c.png'

// ---- 状态 ----
const activeVideo = ref(0)
const isTransitioning = ref(false)
const mobileMenuOpen = ref(false)
let cooldownTimer = null

// ---- 视频切换逻辑(1000ms 冷却,防连点)----
function switchVideo(index) {
  if (index === activeVideo.value || isTransitioning.value) return
  isTransitioning.value = true
  activeVideo.value = index
  clearTimeout(cooldownTimer)
  cooldownTimer = setTimeout(() => { isTransitioning.value = false }, 1000)
}

onBeforeUnmount(() => clearTimeout(cooldownTimer))

// ---- 第 3 段视频(深林 / index 2)时切暗色 ----
const isDark = computed(() => activeVideo.value === 2)
const heroColor = computed(() => (isDark.value ? '#182C41' : '#ffffff'))
const contentStyle = computed(() => ({ color: heroColor.value, transition: 'color 700ms ease-in-out' }))
const sansFont = { fontFamily: 'system-ui, sans-serif' }

// ---- 导航映射到你的真实页面 ----
const navLinks = [
  { label: '首页', to: { name: 'homepage-index' } },
  { label: '创作', to: { name: 'create-index' } },
  { label: '好友', to: { name: 'friend-index' } },
]

function goLogin() {
  router.push({ name: 'user-account-login-index' })
}
function goTo(target) {
  mobileMenuOpen.value = false
  router.push(target)
}
</script>

<template>
  <section class="landing relative w-full h-screen overflow-hidden bg-black">
    <!-- ===== 背景视频层 (z-0) ===== -->
    <div class="absolute inset-0 z-0">
      <video
        v-for="(v, i) in videos"
        :key="v.url"
        :src="v.url"
        class="absolute inset-0 w-full h-full object-cover transition-opacity duration-1000 ease-in-out"
        :class="i === activeVideo ? 'opacity-100' : 'opacity-0'"
        autoplay muted loop playsinline
      ></video>
    </div>

    <!-- ===== 透明 PNG 覆盖层 (z-1) ===== -->
    <img
      :src="overlayPng"
      alt=""
      class="pointer-events-none absolute inset-0 w-full h-full object-cover z-[1] train-bob"
    />

    <!-- ===== 内容层 (z-2) ===== -->
    <div class="relative z-[2] flex flex-col h-full">

      <!-- ---- 导航 ---- -->
      <nav class="flex items-center justify-between px-5 sm:px-8 py-5">
        <RouterLink to="/" class="text-white italic text-xl sm:text-2xl">赵晶莹</RouterLink>

        <!-- 桌面导航胶囊 -->
        <div class="hidden md:flex items-center gap-1 liquid-glass rounded-full pl-6 pr-1.5 py-1.5">
          <RouterLink
            v-for="link in navLinks"
            :key="link.label"
            :to="link.to"
            class="text-white/90 hover:text-white text-sm px-3 py-1.5 transition-colors"
            :style="sansFont"
          >{{ link.label }}</RouterLink>
          <button
            class="ml-2 bg-white text-black text-sm font-medium rounded-full px-5 py-2 hover:bg-white/90 transition-colors"
            :style="sansFont"
            @click="goLogin"
          >开始体验</button>
        </div>

        <!-- 移动端汉堡按钮 -->
        <button
          class="md:hidden liquid-glass rounded-full w-11 h-11 flex items-center justify-center relative"
          @click="mobileMenuOpen = !mobileMenuOpen"
          aria-label="menu"
        >
          <Menu
            class="w-5 h-5 text-white absolute transition-all duration-300"
            :class="mobileMenuOpen ? 'rotate-90 scale-75 opacity-0' : 'rotate-0 scale-100 opacity-100'"
          />
          <X
            class="w-5 h-5 text-white absolute transition-all duration-300"
            :class="mobileMenuOpen ? 'rotate-0 scale-100 opacity-100' : '-rotate-90 scale-75 opacity-0'"
          />
        </button>
      </nav>

      <!-- ---- 移动端全屏菜单 ---- -->
      <div
        v-if="mobileMenuOpen"
        class="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex flex-col items-center justify-center gap-8"
        @click.self="mobileMenuOpen = false"
      >
        <button
          class="absolute top-6 right-6 liquid-glass rounded-full w-11 h-11 flex items-center justify-center"
          @click="mobileMenuOpen = false"
          aria-label="close"
        >
          <X class="w-5 h-5 text-white" />
        </button>
        <button
          v-for="(link, idx) in navLinks"
          :key="link.label"
          class="mobile-link text-white text-3xl"
          :style="{ transitionDelay: (100 + idx * 50) + 'ms' }"
          @click="goTo(link.to)"
        >{{ link.label }}</button>
        <button
          class="mobile-link bg-white text-black text-lg font-medium rounded-full px-8 py-3 mt-4"
          :style="[sansFont, { transitionDelay: (100 + navLinks.length * 50) + 'ms' }]"
          @click="goTo({ name: 'user-account-login-index' })"
        >开始体验</button>
      </div>

      <!-- ---- Hero 主内容 ---- -->
      <div class="flex-1 flex flex-col items-center justify-center text-center px-5">
        <!-- Badge -->
        <div
          class="liquid-glass rounded-full px-4 py-2 mb-7 text-xs sm:text-sm"
          :style="[contentStyle, sansFont]"
        >已有 10,000+ 段对话在这里延续</div>

        <!-- 标题 -->
        <h1
          class="text-4xl sm:text-5xl md:text-7xl lg:text-[5.5rem] leading-[1.1] max-w-4xl"
          :style="contentStyle"
        >让珍贵的对话<br />从未真正结束</h1>

        <!-- 副标题 -->
        <p
          class="mt-6 max-w-xl text-sm sm:text-base leading-relaxed opacity-90"
          :style="[contentStyle, sansFont]"
        >导入真实的聊天记录,AI 会学习 TA 的语气、记忆与习惯,在往后的日子里,继续用熟悉的方式与你对话。</p>

        <!-- 抢先体验 -->
        <div
          class="liquid-glass rounded-full flex items-center justify-center p-1.5 mt-9"
        >
          <button
            class="bg-white text-black text-sm font-medium rounded-full px-8 py-2.5 whitespace-nowrap hover:bg-white/90 transition-colors"
            :style="sansFont"
            @click="goLogin"
          >抢先体验</button>
        </div>

        <!-- 视频切换器 -->
        <div class="flex items-center gap-5 sm:gap-8 mt-10">
          <button
            v-for="(v, i) in videos"
            :key="v.label"
            class="text-xs sm:text-sm pb-1 transition-all duration-300"
            :style="[
              sansFont,
              i === activeVideo
                ? { color: heroColor, opacity: 1, borderBottom: '1px solid ' + heroColor }
                : { color: heroColor, opacity: 0.5, borderBottom: '1px solid transparent' }
            ]"
            @mouseenter="i !== activeVideo && ($event.currentTarget.style.opacity = 0.8)"
            @mouseleave="i !== activeVideo && ($event.currentTarget.style.opacity = 0.5)"
            @click="switchVideo(i)"
          >{{ v.label }}</button>
        </div>
      </div>

      <!-- ---- 底部数据(始终白色)---- -->
      <div
        class="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 px-5 pb-6 text-white/70 text-xs sm:text-sm"
        :style="sansFont"
      >
        <span>真实聊天导入</span>
        <span class="hidden sm:inline text-white/30">|</span>
        <span>长期记忆沉淀</span>
        <span class="hidden sm:inline text-white/30">|</span>
        <span>延续熟悉语气</span>
        <span class="hidden sm:inline text-white/30">|</span>
        <span>回应当下情绪</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.landing {
  font-family: 'Instrument Serif', serif;
}

/* ===== 液态玻璃 ===== */
.liquid-glass {
  background: rgba(255, 255, 255, 0.01);
  background-blend-mode: luminosity;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  border: none;
  box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.1);
  position: relative;
  overflow: hidden;
}
.liquid-glass::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.4px;
  background: linear-gradient(180deg,
    rgba(255, 255, 255, 0.45) 0%, rgba(255, 255, 255, 0.15) 20%,
    rgba(255, 255, 255, 0) 40%, rgba(255, 255, 255, 0) 60%,
    rgba(255, 255, 255, 0.15) 80%, rgba(255, 255, 255, 0.45) 100%);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
}

/* ===== PNG 覆盖层 train-bob 浮动 ===== */
.train-bob {
  animation: train-bob 3s ease-in-out infinite;
}
@keyframes train-bob {
  0%   { transform: translateY(0) scale(1.03); }
  50%  { transform: translateY(-6px) scale(1.03); }
  100% { transform: translateY(0) scale(1.03); }
}

/* ===== 移动端菜单错峰进场 ===== */
.mobile-link {
  opacity: 0;
  transform: translateY(1rem);
  animation: mobile-in 500ms cubic-bezier(0.4, 0, 0.2, 1) forwards;
}
@keyframes mobile-in {
  to { opacity: 1; transform: translateY(0); }
}
</style>
