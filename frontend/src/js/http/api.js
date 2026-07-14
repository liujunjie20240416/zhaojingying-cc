/*
 * 功能：在每个请求头里自动添加`access token`。
 * 然后拦截请求结果，如果返回结果是身份认证失败（401），
 * 则说明`access_token`过期了，
 * 那么先用`cookie`中的`refresh_token`刷新`access_token`。
 * 如果刷新失败则说明`refreh_token`也过期了，
 * 则调用`user.logout()`在浏览器内存中删除登录状态；
 * 如果刷新成功，则重新发送原请求。
*/

import axios from "axios"
import {useUserStore} from "@/stores/user.js";
import CONFIG_API from "@/js/config/config.js";
import {getApiError} from "@/js/http/errors.js";
import {createTokenRefresher} from "@/js/http/tokenRefresh.js";

const BASE_URL = CONFIG_API.HTTP_URL

const api = axios.create({
    baseURL: BASE_URL,
    withCredentials: true,
})

api.interceptors.request.use(config => {
    const user = useUserStore()
    if (user.accessToken) {
        config.headers.Authorization = `Bearer ${user.accessToken}`
    }
    return config
})

let tokenRefresher = null

function getTokenRefresher() {
    if (!tokenRefresher) {
        const user = useUserStore()
        tokenRefresher = createTokenRefresher({
            requestRefresh: async () => {
                const response = await axios.post(
                    `${BASE_URL}/api/user/account/refresh_token/`,
                    {},
                    {withCredentials: true, timeout: 5000},
                )
                return response.data
            },
            applyToken: token => user.setAccessToken(token),
            onFailure: () => user.logout(),
        })
    }
    return tokenRefresher
}

export function refreshAccessToken() {
    return getTokenRefresher()()
}

api.interceptors.response.use(
    response => response,
    async error => {
        const originalRequest = error?.config
        if (!originalRequest) {
            return Promise.reject(error)
        }

        const hasAccessToken = Boolean(originalRequest.headers?.Authorization)
        const isRefreshRequest = originalRequest.url?.includes('/api/user/account/refresh_token/')
        if (error.response?.status === 401 && hasAccessToken && !isRefreshRequest && !originalRequest._retry) {
            originalRequest._retry = true

            try {
                const token = await refreshAccessToken()
                originalRequest.headers.Authorization = `Bearer ${token}`
                return api(originalRequest)
            } catch (refreshError) {
                return Promise.reject(refreshError)
            }
        }

        error.apiError = getApiError(error)
        return Promise.reject(error)
    }
)

export default api
