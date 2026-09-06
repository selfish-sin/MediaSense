"""
MediaSense build script - PyInstaller + Inno Setup
Usage: python build_installer.py
"""
import subprocess
import shutil
import sys
import os
from pathlib import Path

PROJECT = Path(__file__).parent
DIST = PROJECT / 'dist'
BUILD_DIR = DIST / 'MediaSense'
VERSION_FILE = PROJECT / 'version.txt'
DEFAULT_VERSION = '1.0.0'


def get_version():
    """版本号唯一来源：根目录 version.txt（缺失时回退 DEFAULT_VERSION）"""
    try:
        v = VERSION_FILE.read_text(encoding='utf-8').strip()
        return v or DEFAULT_VERSION
    except OSError:
        return DEFAULT_VERSION

def run(cmd, **kw):
    print(f'> {cmd}')
    r = subprocess.run(cmd, shell=True, cwd=str(PROJECT), **kw)
    if r.returncode != 0:
        print(f'FAILED: {cmd}')
        sys.exit(1)

def clean():
    print('[1/5] Cleaning...')
    for d in [PROJECT / 'build', DIST]:
        if d.exists():
            shutil.rmtree(d)

def build_frontend():
    print('[2/5] Building frontend...')
    run('npm run build', cwd=str(PROJECT / 'frontend'))

def build_exe():
    print('[3/5] PyInstaller (MediaSense.spec)...')
    # 统一走 MediaSense.spec：collect-submodules/hidden-imports/icon/CUDA 裁剪
    # 全部沉淀在 spec，避免 CLI 参数与 spec 双份维护漂移
    run(f'"{sys.executable}" -m PyInstaller --noconfirm --clean MediaSense.spec')

def assemble():
    print('[4/5] Assembling distribution...')
    target = BUILD_DIR
    # PyInstaller output is in dist/MediaSense/ already
    # Copy backend source (for imports)
    backend_dst = target / 'backend'
    if backend_dst.exists():
        shutil.rmtree(backend_dst)
    # 注意排除 config.py：它是开发者本机配置（gitignore 同理），
    # 安装包必须只带 config.example.py，由下方复制为默认 config.py
    shutil.copytree(PROJECT / 'backend', backend_dst, ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', '.pytest-tmp', '.coverage',
        'data', 'venv', '.venv', '*.bak*', 'tests', 'config.py'
    ))
    # Ensure static dir exists (frontend build output)
    static_src = PROJECT / 'backend' / 'static'
    static_dst = backend_dst / 'static'
    if not static_dst.exists() and static_src.exists():
        shutil.copytree(static_src, static_dst)

    # Copy ffmpeg
    bin_dst = target / 'bin'
    bin_dst.mkdir(exist_ok=True)
    for f in ['ffmpeg.exe', 'ffprobe.exe']:
        src = PROJECT / 'bin' / f
        if src.exists():
            shutil.copy2(src, bin_dst / f)

    # Create models directory (user downloads models via UI on first run)
    (target / 'models').mkdir(exist_ok=True)

    # Create default config if not exists
    config_dst = backend_dst / 'config.py'
    if not config_dst.exists():
        shutil.copy2(backend_dst / 'config.example.py', config_dst)

    print(f'  Distribution ready: {target}')

def build_installer(version):
    print('[5/5] Inno Setup...')
    iss = PROJECT / 'installer.iss'
    if not iss.exists():
        print('  installer.iss not found, skipping Inno Setup')
        print(f'  You can distribute the folder: {BUILD_DIR}')
        return
    # Find ISCC
    iscc_paths = [
        r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        r'C:\Program Files\Inno Setup 6\ISCC.exe',
    ]
    iscc = next((p for p in iscc_paths if os.path.exists(p)), None)
    if not iscc:
        iscc = shutil.which('ISCC')
    if not iscc:
        print('  Inno Setup not found. Install from https://jrsoftware.org/isdl.php')
        print(f'  You can distribute the folder: {BUILD_DIR}')
        return
    run(f'"{iscc}" /DMyAppVersion={version} installer.iss')
    print(f'  Installer created: dist/MediaSense-Setup-{version}.exe')

if __name__ == '__main__':
    version = get_version()
    print(f'Building MediaSense v{version}')
    clean()
    build_frontend()
    build_exe()
    assemble()
    build_installer(version)
    print('\nDONE!')
