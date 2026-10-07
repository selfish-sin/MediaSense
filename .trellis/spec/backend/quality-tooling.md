# 验证、依赖与启动契约（2026-10-07）

## 1. 范围 / 触发

修改 verify.py、Python/Node 依赖、CI、启动脚本、main.lifespan 或索引配置时读取。
安装与锁再生成的具体命令以 `docs/dependency-locks.md` 为单一来源。

## 2. 签名

- `python verify.py [all|backend|frontend]`；默认 all。
- `@asynccontextmanager async def lifespan(app: FastAPI)`，绑定 `FastAPI(lifespan=lifespan)`。
- `backend/start.bat`：优先 backend/venv/Scripts/python.exe，缺失时 PATH python。
- `codegraph.json: {"exclude": ["backend/static/", ".trellis/tasks/archive/"]}`。

## 3. 契约

- backend 门禁 = ruff（backend 与根验证/打包工具）+ pytest；frontend = eslint + vitest + build。
  ruff 显式使用 backend/pyproject.toml，不继承工作区外的配置；只排除 E501。
- 拒绝未知目标；任一检查失败最终失败。子进程缺失给可行动报错，不能打印空成功。
- RuntimeWarning / PytestUnraisableExceptionWarning 是失败；超时测试替身必须释放收到的协程。
- Windows x64/Python 3.13 运行锁完整固定；开发锁以运行锁为约束，增加 ruff/pip-tools/httpx2 等。
  不把 pip freeze 整个环境当运行锁，不写源凭证或本机绝对路径；跨平台需重新解析和验证。
- lifespan 严格保留顺序：init_db → fail_interrupted_jobs → cleanup_expired_files → yield。
  应由 Socket.IO 组合应用转发 lifespan；测试与冒烟只触碰隔离数据目录，不启动付费调用。
- CodeGraph 排除已提交构建与归档目录；仍用路径/符号核对模糊查询，不把图的“无测试”当事实。

## 4. 验证 / 错误矩阵

| 条件 | 行为 |
|---|---|
| verify 未知目标 | 退出 2，不运行检查 |
| lint/test/build 或执行工具失败 | 退出 1，包含失败检查名 |
| 所有指定检查通过 | 退出 0 |
| 启动缺依赖且安装失败 / 再次导入失败 | start.bat 停止，保留操作指引 |
| 服务启动遗留非终态任务 | failed + 重启中断原因，可重试；已完成不变 |
| 数据库初始化失败 | 启动失败，不伪装可用 |

## 5. Good / Base / Bad

- Good：干净 venv 安装开发锁 → pip check → 启动导入 → 全套 verify。
- Base：只改后端时运行 verify.py backend；前端源码改动必须构建并提交产物。
- Bad：verify.py backedn 显示通过，或忽略未等待协程告警；两者都不可接受。

## 6. 必须测试

- test_verify.py：未知目标不执行且退出 2；ruff 失败仍检查 pytest 并最终退出 1。
- test_lifespan.py：真实 socket_app + TestClient 上下文，非终态恢复/已完成保留，任务/队列/OpenAPI 可读。
- 全量验收包含默认示例配置与真实本地 HTTP 冒烟；不得调用用户运行数据的清理入口。
- CI 的配置解析、依赖检查和本地模拟可验证；未 push 不能声称远程 CI 已执行。

## 7. Wrong / Correct

```python
# Wrong：拼错 target 也不运行任何检查就报告成功。
target = sys.argv[1]
# Correct：解析目标，未知目标直接失败。
parser.add_argument('target', nargs='?', choices=('all', 'backend', 'frontend'), default='all')

# Wrong：替代 wait_for 却遗留收到的协程。
async def timeout(coro, **kwargs):
    raise TimeoutError()
# Correct：替身拥有释放责任。
async def timeout(coro, **kwargs):
    coro.close()
    raise TimeoutError()
```
