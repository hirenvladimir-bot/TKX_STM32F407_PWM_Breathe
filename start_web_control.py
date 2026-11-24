"""Start/reuse only the simple serial web interface. No dashboard or Tk window."""
from pathlib import Path
import subprocess,sys
if __name__=='__main__':
 subprocess.run([sys.executable,str(Path(__file__).with_name('start_serial_hub.py'))],check=True)
 print('Serial control: http://127.0.0.1:18765/')
