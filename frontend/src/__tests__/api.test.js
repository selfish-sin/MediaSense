import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

// 捕获 api.js 在 axios 实例上注册的拦截器回调
const captured = vi.hoisted(() => ({
  requestSuccess: null,
  responseSuccess: null,
  responseError: null,
}))

vi.mock('axios', () => {
  const create = () => ({
    interceptors: {
      request: {
        use: (onFulfilled) => { captured.requestSuccess = onFulfilled },
      },
      response: {
        use: (onFulfilled, onRejected) => {
          captured.responseSuccess = onFulfilled
          captured.responseError = onRejected
        },
      },
    },
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  })
  return { default: { create } }
})

// 触发 api.js 模块副作用：注册拦截器
import '../utils/api'

describe('api 拦截器', () => {
  let consoleSpy

  beforeEach(() => {
    consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    consoleSpy.mockRestore()
  })

  it('请求拦截器：透传 config 不做修改', () => {
    const config = { url: '/jobs', method: 'post', timeout: 300000 }
    expect(captured.requestSuccess(config)).toBe(config)
  })

  it('响应拦截器：普通 JSON 响应解包 data', () => {
    const res = { data: { job_id: 'j1' }, config: {} }
    expect(captured.responseSuccess(res)).toEqual({ job_id: 'j1' })
  })

  it('响应拦截器：blob 响应返回完整 response（导出需要 headers）', () => {
    const res = { data: new Blob(['x']), config: { responseType: 'blob' }, headers: { 'content-disposition': 'a' } }
    expect(captured.responseSuccess(res)).toBe(res)
  })

  it('错误响应：优先取后端 detail（字符串）', async () => {
    const err = { response: { data: { detail: '任务不存在' } }, message: 'Request failed' }
    await expect(Promise.reject(err).catch(captured.responseError)).rejects.toThrow('任务不存在')
  })

  it('错误响应：FastAPI 422 校验错误（detail 为数组）逐条拼接 msg', async () => {
    const err = {
      response: { data: { detail: [{ msg: 'url 字段必填' }, { msg: 'model 字段必填' }] } },
      message: 'Request failed',
    }
    await expect(Promise.reject(err).catch(captured.responseError)).rejects.toThrow('url 字段必填; model 字段必填')
  })

  it('错误响应：无 detail 时回退 error.message', async () => {
    const err = { message: 'Network Error' }
    await expect(Promise.reject(err).catch(captured.responseError)).rejects.toThrow('Network Error')
  })

  it('错误响应：兜底文案「请求失败」', async () => {
    const err = {}
    await expect(Promise.reject(err).catch(captured.responseError)).rejects.toThrow('请求失败')
  })

  it('错误时 console.error 留痕（不静默吞错）', async () => {
    const err = { response: { data: { detail: 'boom' } } }
    await Promise.reject(err).catch(captured.responseError).catch(() => {})
    expect(consoleSpy).toHaveBeenCalled()
  })
})
