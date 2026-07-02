const TIME_GAP_MS = 5 * 60 * 1000

export function shouldShowTime(currentTime, previousTime) {
  if (!currentTime) return false
  if (!previousTime) return true

  const current = new Date(currentTime).getTime()
  const previous = new Date(previousTime).getTime()

  if (Number.isNaN(current) || Number.isNaN(previous)) return false

  return current - previous >= TIME_GAP_MS
}

export function formatChatTime(value) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''

  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfMessageDay = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const dayDiff = Math.round((startOfToday - startOfMessageDay) / (24 * 60 * 60 * 1000))
  const time = date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })

  if (dayDiff === 0) return `今天 ${time}`
  if (dayDiff === 1) return `昨天 ${time}`

  const monthDay = `${date.getMonth() + 1}月${date.getDate()}日`
  if (date.getFullYear() === now.getFullYear()) return `${monthDay} ${time}`

  return `${date.getFullYear()}年${monthDay} ${time}`
}
