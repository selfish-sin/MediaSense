import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

/**
 * ESLint flat config（2026-09-12 接入）
 * 基线：js recommended + vue essential（正确性规则，不含格式类）
 * 范围：src/（含 __tests__），格式风格由现有约定约束，不引入 prettier
 */
export default [
  { ignores: ['dist/**', 'coverage/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    files: ['**/*.{js,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.browser },
    },
    rules: {
      // 单文件组件允许单词名（App.vue 等既有文件）
      'vue/multi-word-component-names': 'off',
      // 未使用变量：忽略下划线前缀（有意的占位/忽略参数）
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
]
