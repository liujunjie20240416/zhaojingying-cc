export function getApiError(error, fallback = '操作失败，请稍后重试') {
  const payload = error?.response?.data
  return {
    code: payload?.error?.code || 'unknown_error',
    message: payload?.error?.message || payload?.detail || error?.message || fallback,
    retryable: payload?.error?.retryable === true,
    status: error?.response?.status || 0,
  }
}

export function getApiErrorMessage(error, fallback) {
  return getApiError(error, fallback).message
}
