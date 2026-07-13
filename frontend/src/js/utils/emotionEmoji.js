export const EMOTION_EMOJI_MAP = {
  开心: '😊',
  高兴: '😊',
  生气: '😠',
  很生气: '😡',
  委屈: '🥺',
  哭: '😭',
  难过: '😢',
  害羞: '😳',
  亲亲: '😘',
  无语: '😑',
  惊讶: '😮',
  爱你: '❤️',
  想你: '🥰',
  撒娇: '🥺',
  别扭: '🙂‍↕️',
  吃醋: '😤',
}

export const USER_EMOJI_MEANINGS = {
  '😊': '开心、高兴、心情不错',
  '😄': '很开心、兴奋',
  '😆': '开心、觉得好笑',
  '😠': '生气、不满',
  '😡': '很生气、强烈不满',
  '😭': '难过、哭、情绪很重',
  '😢': '难过、失落',
  '🥺': '委屈、撒娇、想被哄',
  '😳': '害羞、被戳中、紧张',
  '😘': '亲密、亲亲、表达喜欢',
  '🥰': '喜欢、幸福、亲密',
  '😑': '无语、冷淡、不想接话',
  '😮': '惊讶、意外',
  '❤️': '爱意、喜欢、亲密',
  '💕': '喜欢、亲密、甜蜜',
  '🙂‍↕️': '不满、别扭、有点抗拒、嘴硬或小情绪',
  '🙂‍↔️': '拒绝、不要、不认同、轻微抗拒',
  '😤': '吃醋、不满、傲娇、嘴硬',
}

const markerPattern = /【([^【】]{1,12})】|\[([^[\]]{1,12})\]/g

export function parseEmotionText(text = '') {
  const parts = []
  let lastIndex = 0

  for (const match of text.matchAll(markerPattern)) {
    const [raw, fullWidthLabel, squareLabel] = match
    const label = fullWidthLabel || squareLabel
    const emoji = EMOTION_EMOJI_MAP[label]

    if (match.index > lastIndex) {
      parts.push({
        type: 'text',
        text: text.slice(lastIndex, match.index),
      })
    }

    if (emoji) {
      parts.push({
        type: 'emoji',
        text: emoji,
        label,
      })
    } else {
      parts.push({
        type: 'text',
        text: raw,
      })
    }

    lastIndex = match.index + raw.length
  }

  if (lastIndex < text.length) {
    parts.push({
      type: 'text',
      text: text.slice(lastIndex),
    })
  }

  return parts.length ? parts : [{type: 'text', text}]
}

export function detectUserEmojiContext(text = '') {
  const context = []

  for (const [emoji, meaning] of Object.entries(USER_EMOJI_MEANINGS)) {
    if (text.includes(emoji)) {
      context.push({emoji, meaning})
    }
  }

  return context
}
