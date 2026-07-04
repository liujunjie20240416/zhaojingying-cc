<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  videoUrl: { type: String, default: '' },
  imageUrl: { type: String, default: '' },   // 传图片则用图片(Ken Burns),否则用视频
  overlay: { type: Number, default: 0.35 },
  parallax: { type: Boolean, default: true },
})

const canvasEl = ref(null)
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
    <!-- 视差外层(平移),内层做 Ken Burns / 视频铺满 -->
    <div
      class="absolute inset-0 dyn-parallax"
      :style="{ transform: `scale(1.06) translate(${mx * -16}px, ${my * -16}px)` }"
    >
      <img
        v-if="imageUrl"
        :src="imageUrl"
        alt=""
        class="absolute inset-0 w-full h-full object-cover ken-burns"
      />
      <video
        v-else
        :src="videoUrl"
        autoplay muted loop playsinline
        class="absolute inset-0 w-full h-full object-cover"
      ></video>
    </div>

    <!-- 粒子层(视差较弱)-->
    <canvas
      ref="canvasEl"
      class="absolute inset-0 w-full h-full pointer-events-none dyn-parallax"
      :style="{ transform: `scale(1.05) translate(${mx * -9}px, ${my * -9}px)` }"
    ></canvas>

    <!-- 暗化遮罩 -->
    <div class="absolute inset-0" :style="{ background: `rgba(0,0,0,${overlay})` }"></div>
  </div>
</template>

<style scoped>
.dyn-parallax {
  transition: transform 0.25s ease-out;
  will-change: transform;
}
/* Ken Burns:图片极慢放大 + 轻微平移,静图也有流动感 */
.ken-burns {
  animation: ken-burns 24s ease-in-out infinite alternate;
  transform-origin: center;
  will-change: transform;
}
@keyframes ken-burns {
  0%   { transform: scale(1) translate(0, 0); }
  100% { transform: scale(1.12) translate(-1.5%, -1%); }
}
</style>
