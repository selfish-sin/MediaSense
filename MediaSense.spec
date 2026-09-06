# -*- mode: python ; coding: utf-8 -*-
import os
import re
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['engineio.async_drivers.asgi', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'multipart']
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('engineio')
hiddenimports += collect_submodules('yt_dlp')
hiddenimports += collect_submodules('faster_whisper')
hiddenimports += collect_submodules('bilibili_api')
hiddenimports += collect_submodules('httpx')
tmp_ret = collect_all('ctranslate2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ★ CUDA 依赖裁剪：ctranslate2 会在运行时尝试动态加载 CUDA 库（cublas/cudnn 等），
#   默认安装包去掉这些大体积 DLL（数百 MB）作为 CPU-only；需要 GPU 推理时在构建命令里
#   设置 MEDIASENSE_KEEP_CUDA=1（Win: set MEDIASENSE_KEEP_CUDA=1 后执行 build_installer.py）。
_KEEP_CUDA = os.environ.get('MEDIASENSE_KEEP_CUDA', '').strip().lower() in ('1', 'true', 'yes', 'on')
if not _KEEP_CUDA:
    _CUDA_DLL = re.compile(r'(?:cublas|cublasLt|nvrtc|cudart|cudnn)[^\\/]*\.dll$', re.IGNORECASE)
    _before = len(binaries)
    binaries = [b for b in binaries if not _CUDA_DLL.search(b[0])]
    print(f'[spec] 已剔除 {_before - len(binaries)} 个 CUDA 相关 DLL（设置 MEDIASENSE_KEEP_CUDA=1 可保留）')


a = Analysis(
    ['launcher.py'],
    pathex=['backend'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MediaSense',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['bilibili_video_icon_v2_cropped.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MediaSense',
)
