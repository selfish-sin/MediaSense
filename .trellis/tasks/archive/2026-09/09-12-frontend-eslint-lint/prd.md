# 前端 ESLint 接入 - PRD

## 需求

前端目前只有 vitest + vite build，没有静态检查（后端已有 ruff 最小配置）。接入 ESLint 补齐质量门禁的 lint 环节，并保证存量代码通过（不制造"一跑就红"的新门禁）。

## 验收标准

- [ ] `frontend/package.json` 新增 devDependencies（eslint 等）与 `npm run lint` script
- [ ] `frontend/eslint.config.js`（flat config）：js recommended + vue 正确性规则集 + 浏览器 globals
- [ ] `npm run lint` 全绿（0 error / 0 warning）
- [ ] `verify.py` frontend 环节纳入 lint（作为门禁）
- [ ] vitest 61 + vite build 通过（无回归）

## 不做项

- 不引入 prettier / 格式化重排（与现有代码风格冲突面大，lint 先行）
- 不动后端（ruff 已覆盖）
- 不做 vue/strongly-recommended 及以上的格式类规则（会淹没真实问题）

## 风险与回滚

- 风险等级：M0（仅新增配置与依赖，不触碰运行时代码；存量修复若涉及代码仅限 lint 报出的真实问题）
- 回滚：删除 `frontend/eslint.config.js` + `package.json` 的 script/依赖 + `verify.py` 一行改动即可
