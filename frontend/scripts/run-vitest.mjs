/**
 * vitest 启动包装（Windows 专用坑）
 *
 * 症状：从**小写盘符**（如 `cd d:\projects\...` 后 `npm test`）运行 vitest 时，
 *  每个测试文件都在 `describe()` 处崩：
 *    TypeError: Cannot read properties of undefined (reading 'config')
 *      at initSuite (@vitest/runner/dist/chunk-artifact.js)
 *  16 个套件全部 Failed Suites、Tests: no tests。
 *
 * 根因：vite 的 module runner 按**真实路径**（大写盘符 `D:\...`）解析并加载 `@vitest/*`，
 *  而 Node 侧按 cwd 的小写盘符加载同一批包 → 同一模块被当成两份实例，
 *  @vitest/runner 的 collector context 与 worker state 分裂（initSuite 拿不到 config）。
 *  已验证：vitest 4.1.10 / 5.0.3、vite 7.x / 8.x 均复现；把 cwd 换成大写盘符即恢复正常。
 *  配置层（root 规范化 / resolve.alias / server.deps.external）都拦不住，
 *  因为分裂发生在 vitest 主进程自身的模块解析上，只能从入口把 cwd 修正。
 *
 * 做法：把 frontend 根目录规范化为大写盘符后再 spawn vitest，透传全部参数与退出码。
 * 非 Windows（无盘符）时原样启动，零副作用。
 */
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const normalizedRoot = frontendRoot.replace(/^([a-zA-Z]):/, (_, drive) => `${drive.toUpperCase()}:`)
const vitestEntry = path.join(normalizedRoot, 'node_modules', 'vitest', 'vitest.mjs')

const child = spawn(process.execPath, [vitestEntry, ...process.argv.slice(2)], {
  cwd: normalizedRoot,
  stdio: 'inherit',
})
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal)
  else process.exit(code ?? 1)
})
