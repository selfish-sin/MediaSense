# 依赖锁与质量验证

基准平台：Windows x64、Python 3.13；前端使用 Node.js 24。
`backend/requirements.in` 是运行直接依赖意图，`requirements.txt` 锁定完整传递依赖。
`requirements-dev.in` 引入运行意图并约束运行锁，再增加测试、ruff、pip-tools 和 TestClient 的 httpx2。
不要用整个开发环境的 `pip freeze` 替代锁文件；GPU 扩展按需另装，不进 CPU 基准锁。

## 安装与验证（仓库根目录，PowerShell）

```powershell
py -3.13 -m venv backend/venv
backend/venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
backend/venv/Scripts/python.exe -m pip check
# 首次安装且 config.py 不存在时才复制；保留已有本机配置。
if (-not (Test-Path backend/config.py)) { Copy-Item backend/config.example.py backend/config.py }
npm --prefix frontend ci
backend/venv/Scripts/python.exe verify.py all
```

仅运行服务时安装 `backend/requirements.txt` 即可。`backend/start.bat` 优先使用项目 venv，
缺依赖时安装运行锁；安装或导入失败会停止启动，保留明确报错。
不再依赖 `bilibili-api-python`，B站链路使用自研 HTTP 实现；抖音签名显式依赖 cryptography。

## 更新锁（相同 Python/平台，仓库根目录）

```powershell
backend/venv/Scripts/python.exe -m piptools compile --allow-unsafe --no-emit-index-url --no-emit-trusted-host --strip-extras --output-file backend/requirements.txt backend/requirements.in
backend/venv/Scripts/python.exe -m piptools compile --allow-unsafe --no-emit-index-url --no-emit-trusted-host --strip-extras --output-file backend/requirements-dev.txt backend/requirements-dev.in
```

默认保留已有锁版本；有意升级时在相应命令添加 `--upgrade-package 包名`，运行锁先于开发锁更新。
`--allow-unsafe` 用于同时锁定 setuptools/pip 等被 pip-tools 默认排除的传递依赖。
锁中不写源地址、凭证或本机文件路径；不要提交包含私有源的 pip 配置。
换 Python 次版本或操作系统时先重新解析并在干净环境验证，不能把本锁宣称为跨平台锁。

## 检查入口契约

`verify.py [all|backend|frontend]`：默认 all，未知目标退出 2；任一检查失败退出 1。
backend 包含完整 Python E/F/W 检查（仅 E501 行长不阻断）和 pytest；frontend 包含 eslint、vitest、构建。
RuntimeWarning 与未回收异常使测试失败；不得靠全局忽略警告掩盖协程泄漏。
CI 使用相同锁、同一入口；本地验证通过不等于远程 CI 已执行。
