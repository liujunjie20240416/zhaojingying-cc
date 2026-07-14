/**
 * Create a single-flight access-token refresher.
 * All concurrent callers receive the same promise, and the token is applied
 * before that promise resolves so retries cannot reuse the expired token.
 */
export function createTokenRefresher({requestRefresh, applyToken, onFailure}) {
  let inFlight = null

  return function refreshAccessToken() {
    if (inFlight) return inFlight

    inFlight = Promise.resolve()
      .then(requestRefresh)
      .then(payload => {
        const token = payload?.access
        if (!token) throw new Error('刷新响应缺少 access token')
        applyToken(token)
        return token
      })
      .catch(error => {
        onFailure(error)
        throw error
      })
      .finally(() => {
        inFlight = null
      })

    return inFlight
  }
}
