"""Compatibility entry: start the simple serial webpage, not the old dashboard."""
from pathlib import Path
import subprocess,sys
if __name__=='__main__':
 subprocess.run([sys.executable,str(Path(__file__).with_name('start_web_control.py'))],check=True)
