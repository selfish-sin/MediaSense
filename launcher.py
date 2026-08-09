"""
MediaSense Launcher - PyInstaller entry point
Starts the FastAPI server and opens the default browser.
"""
import sys
import os
import threading
import webbrowser
import time

def get_base_dir():
    """Get the base directory (exe location when frozen, script dir otherwise)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def main():
    base_dir = get_base_dir()
    backend_dir = os.path.join(base_dir, 'backend')

    # Ensure working directory is backend/ so relative imports work
    os.chdir(backend_dir)
    sys.path.insert(0, backend_dir)

    # Ensure data directories exist
    data_dir = os.path.join(backend_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, 'downloads'), exist_ok=True)
    os.makedirs(os.path.join(data_dir, 'logs'), exist_ok=True)

    # Import config to get port
    from config import SERVER_PORT, SERVER_HOST

    # ★ 导入 app 对象，让 PyInstaller 追踪全部依赖（routers/services/db）
    from main import socket_app as application

    # Open browser after server starts
    def open_browser():
        time.sleep(2.5)
        webbrowser.open(f'http://localhost:{SERVER_PORT}')

    threading.Thread(target=open_browser, daemon=True).start()

    print(f'========================================')
    print(f'  MediaSense - Starting...')
    print(f'  URL: http://localhost:{SERVER_PORT}')
    print(f'  Press Ctrl+C to stop')
    print(f'========================================')

    # Start uvicorn with the imported app object
    import uvicorn
    uvicorn.run(
        application,
        host='127.0.0.1',
        port=SERVER_PORT,
        log_level='info',
    )

if __name__ == '__main__':
    main()
