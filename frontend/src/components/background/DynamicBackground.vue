<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  videoUrl: { type: String, required: true },
  overlay: { type: Number, default: 0.35 },   // 暗化程度,保证前景可读
  parallax: { type: Boolean, default: true }, // 鼠标视差开关
})

const canvasEl = ref(null)
// 鼠标视差偏移(-1 ~ 1)
const mx = ref(0)
const my = ref(0)

let ctx = null
let particles = []
let animId = null

// ---- 鼠标视差 ----
function onMouseMove(e) {
  if (!props.parallax) return
  mx.value = (e.clientX / window.innerWidth - 0.5) * 2
  my.value = (e.clientY / window.innerHeight - 0.5) * 2
}

// ---- 粒子 ----
function resize() {
  const c = canvasEl.value
  if (!c) return
  c.width = window.innerWidth
  c.height = window.innerHeight
  createParticles()
}
function createParticles() {
  const c = canvasEl.value
  particles = []
  const count = Math.floor((c.width * c.height) / 12000)
  for (let i = 0; i < count; i++) {
    particles.push({
      x: Math.random() * c.width,
      y: Math.random() * c.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      size: Math.random() * 1.5 + 0.5,
      opacity: Math.random() * 0.6 + 0.2,
    })
  }
}
function animate() {
  const c = canvasEl.value
  if (!c || !ctx) return
  ctx.clearRect(0, 0, c.width, c.height)
  for (const p of particles) {
    p.x += p.vx
    p.y += p.vy
    if (p.x < 0) p.x = c.width
    if (p.x > c.width) p.x = 0
    if (p.y < 0) p.y = c.height
    if (p.y > c.height) p.y = 0
    ctx.beginPath()
    ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2)
    ctx.fillStyle = `rgba(255,255,255,${p.opacity})`
    ctx.fill()
  }
  animId = requestAnimationFrame(animate)
}

onMounted(() => {
  ctx = canvasEl.value.getContext('2d')
  resize()
  animate()
  window.addEventListener('resize', resize)
  window.addEventListener('mousemove', onMouseMove)
})
onBeforeUnmount(() => {
  cancelAnimationFrame(animId)
  window.removeEventListener('resize', resize)
  window.removeEventListener('mousemove', onMouseMove)
})
</script>

<template>
  <div class="dyn-bg fixed inset-0 overflow-hidden bg-black -z-10">
    <!-- 视频层(视差最强,营造纵深)-->
    <video
      :src="videoUrl"
      autoplay muted loop playsinline
      class="absolute inset-0 w-full h-full object-cover dyn-parallax"
      :style="{ transform: `scale(1.1) translate(${mx * -18}px, ${my * -18}px)` }"
    ></video>

    <!-- 粒子层(视差较弱,层次感)-->
    <canvas
      ref="canvasEl"
      class="absolute inset-0 w-full h-full pointer-events-none dyn-parallax"
      :style="{ transform: `scale(1.05) translate(${mx * -9}px, ${my * -9}px)` }"
    ></canvas>

    <!-- 暗化遮罩,保证前景文字/卡片清晰 -->
    <div class="absolute inset-0" :style="{ background: `rgba(0,0,0,${overlay})` }"></div>
  </div>
</template>

<style scoped>
.dyn-parallax {
  transition: transform 0.25s ease-out;
  will-change: transform;
}
</style>
