/*
 * 功能：在每个请求头里自动添加`access token`。
 * 然后拦截请求结果，如果返回结果是身份认证失败（401），
 * 则说明`access_token`过期了，那么调用api刷新token`，
 * 如果刷新成功，则重新发送原请求。
*/

import { fetchEventSource } from '@microsoft/fetch-event-source';
import { useUserStore } from "@/stores/user.js";
import {refreshAccessToken} from "./api.js";
import CONFIG_API from "@/js/config/config.js";

const BASE_URL = CONFIG_API.HTTP_URL

/**
 * 通用的流式请求工具
 * @param {string} url 请求地址
 * @param {object} options 配置项 (method, body, onmessage, onerror等)
 */
export default async function streamApi(url, options = {}) {
    const userStore = useUserStore();
    let authRetryCount = 0;

    const startFetch = async () => {
        try {
            return await fetchEventSource(BASE_URL + url, {
                method: options.method || 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${userStore.accessToken}`,
                    ...options.headers,
                },
                body: JSON.stringify(options.body || {}),
                signal: options.signal,

                openWhenHidden: true,  // 允许后台运行，防止浏览器因隐藏页面而强制关闭它
                async onopen(response) {
                    // 1. 处理 401 Token 过期
                    if (response.status === 401) {
                        if (authRetryCount >= 1) {
                            throw new Error("登录状态已失效，请重新登录");
                        }
                        await refreshAccessToken();
                        authRetryCount += 1;
                        // The shared refresher has stored the new token before this
                        // signal reaches onerror and starts the one allowed retry.
                        throw new Error("TOKEN_REFRESHED");
                    }

                    if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) {
                        const errorData = await response.json().catch(() => ({}));
                        throw new Error(errorData.detail || `请求失败: ${response.status}`);
                    }
                },

                onmessage(msg) {
                    if (msg.data === '[DONE]') {
                        if (options.onmessage) options.onmessage('', true);
                        return
                    }
                    try {
                        const json = JSON.parse(msg.data);
                        if (options.onmessage) options.onmessage(json, false);
                    } catch (e) {
                        console.error("流解析失败:", e);
                    }
                },

                onerror(err) {
                    // Throwing stops fetch-event-source's internal retry, whose
                    // cloned headers still contain the expired token. The outer
                    // loop below starts one fresh request with the stored token.
                    if (err.message === "TOKEN_REFRESHED") {
                        throw err;
                    }

                    // 其他错误则按用户定义的 onerror 处理
                    if (options.onerror) {
                        options.onerror(err);
                    }
                    throw err; // 停止自动重试
                },

                onclose: options.onclose,
            });
        } catch (err) {
            if (err.message === "TOKEN_REFRESHED") return startFetch()
            throw err
        }
    };

    return await startFetch();
}
