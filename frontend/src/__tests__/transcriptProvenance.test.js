import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TranscriptProvenance from '../components/TranscriptProvenance.vue'

function mountWith(meta) {
  return mount(TranscriptProvenance, { props: { meta } })
}

describe('TranscriptProvenance', () => {
  it('meta 为 null（历史任务）不渲染', () => {
    const w = mountWith(null)
    expect(w.find('.prov').exists()).toBe(false)
  })

  it('未知 source 不渲染空壳', () => {
    const w = mountWith({ source: 'whatever' })
    expect(w.find('.prov').exists()).toBe(false)
  })

  it('平台字幕来源：B站 + 抽查通过，无引擎/缓存行', () => {
    const w = mountWith({ source: 'platform_subtitle', subtitle_from: 'bilibili', subtitle_check: 'passed' })
    expect(w.find('.prov').exists()).toBe(true)
    expect(w.text()).toContain('B站平台字幕')
    expect(w.text()).toContain('内容抽查通过')
    expect(w.text()).not.toContain('识别引擎')
    expect(w.text()).not.toContain('降级')
  })

  it('识别来源：引擎行 + 全新识别；本地引擎带档位', () => {
    const w = mountWith({
      source: 'transcription', engine: 'local', engine_label: '本地 Whisper',
      requested_engine: 'local', requested_engine_label: '本地 Whisper',
      cache: 'miss', degraded: false, model_size: 'medium',
    })
    expect(w.text()).toContain('语音识别')
    expect(w.text()).toContain('本地 Whisper · medium 档')
    expect(w.text()).toContain('本次为全新识别')
    expect(w.text()).not.toContain('降级')
  })

  it('降级：warn 行同时给出实际引擎与首选引擎', () => {
    const w = mountWith({
      source: 'transcription', engine: 'cloud_free', engine_label: '云端 XingChen（免费）',
      requested_engine: 'cloud', requested_engine_label: '云端 MiMo',
      cache: 'miss', degraded: true,
    })
    const warn = w.find('.prov-line--warn')
    expect(warn.exists()).toBe(true)
    expect(warn.text()).toContain('云端 XingChen（免费）')
    expect(warn.text()).toContain('云端 MiMo')
  })

  it('缓存命中与断点续传复用分别有对应文案', () => {
    const hit = mountWith({
      source: 'transcription', engine: 'cloud_free', engine_label: '云端 XingChen（免费）',
      requested_engine: 'cloud_free', requested_engine_label: '云端 XingChen（免费）',
      cache: 'hit', degraded: false,
    })
    expect(hit.text()).toContain('复用了同音频的转录缓存')

    const resume = mountWith({ source: 'transcription', resume: true })
    expect(resume.text()).toContain('复用了上次已完成的转录文件（断点续传）')
  })

  it('文本输入来源不显示引擎行', () => {
    const w = mountWith({ source: 'text_input' })
    expect(w.text()).toContain('直接文本输入')
    expect(w.text()).not.toContain('识别引擎')
  })
})
