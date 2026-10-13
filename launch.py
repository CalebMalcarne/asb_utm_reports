"""Start the UI plus an independent worker; terminate both on Ctrl+C."""
import os
import subprocess
import sys
from pathlib import Path

def main():
    root=Path(__file__).resolve().parent; os.chdir(root)
    # Create settings encryption material before worker/UI race to open it.
    from storage import Store
    Store()
    worker=subprocess.Popen([sys.executable,str(root/'scheduler.py')])
    ui=None
    try:
        ui=subprocess.Popen([sys.executable,'-m','streamlit','run',str(root/'app.py'),'--server.address=127.0.0.1'])
        ui.wait()
    except KeyboardInterrupt: pass
    finally:
        for process in (ui,worker):
            if process and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill()
if __name__=='__main__': main()
