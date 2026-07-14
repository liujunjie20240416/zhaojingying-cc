import assert from 'node:assert/strict'
import test from 'node:test'

import {createTokenRefresher} from '../src/js/http/tokenRefresh.js'

test('refresh stores the new access token before callers continue', async () => {
  const events = []
  const refresh = createTokenRefresher({
    requestRefresh: async () => ({access: 'new-token'}),
    applyToken: token => events.push(`stored:${token}`),
    onFailure: () => events.push('logout'),
  })

  const token = await refresh()

  events.push(`continued:${token}`)
  assert.deepEqual(events, ['stored:new-token', 'continued:new-token'])
})

test('concurrent callers share one refresh request', async () => {
  let requestCount = 0
  let release
  const response = new Promise(resolve => { release = resolve })
  const refresh = createTokenRefresher({
    requestRefresh: async () => {
      requestCount += 1
      return response
    },
    applyToken: () => {},
    onFailure: () => {},
  })

  const first = refresh()
  const second = refresh()
  release({access: 'shared-token'})

  assert.deepEqual(await Promise.all([first, second]), ['shared-token', 'shared-token'])
  assert.equal(requestCount, 1)
})

test('a failed refresh logs out once and a later call can retry', async () => {
  let requestCount = 0
  let logoutCount = 0
  const refresh = createTokenRefresher({
    requestRefresh: async () => {
      requestCount += 1
      if (requestCount === 1) throw new Error('expired refresh cookie')
      return {access: 'recovered-token'}
    },
    applyToken: () => {},
    onFailure: () => { logoutCount += 1 },
  })

  await assert.rejects(refresh(), /expired refresh cookie/)
  assert.equal(await refresh(), 'recovered-token')
  assert.equal(requestCount, 2)
  assert.equal(logoutCount, 1)
})
