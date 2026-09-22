// 工作台快捷键判定与文案（纯函数，便于单测）
//
// 约定：Ctrl + Enter（macOS 同时接受 ⌘ + Enter）在工作台任意位置提交任务，
// 这样在顶部输入区粘完链接即可直接开始，不必滚到底部的提交按钮。
// 判定与文案抽成纯函数，组件只负责绑定/展示（frontend spec：可测逻辑不进 .vue）。

/** 是否为「提交/开始处理」快捷键：Ctrl/Cmd + Enter */
export function isSubmitShortcut(e) {
  if (!e || !(e.ctrlKey || e.metaKey)) return false
  // 主键盘 Enter / 小键盘 Enter / keyCode 兜底（旧浏览器与合成事件）
  return e.key === 'Enter' || e.code === 'NumpadEnter' || e.keyCode === 13
}

/** 平台修饰键显示名：macOS 用 ⌘，其余用 Ctrl */
export function modifierLabel(platform = '') {
  return /mac/i.test(String(platform)) ? '⌘' : 'Ctrl'
}

/** 输入卡头部展示的快捷键提示（让用户在输入区就能发现，无需滚到提交按钮） */
export function shortcutHint(platform = '') {
  return `${modifierLabel(platform)} + Enter 快速开始`
}
