"""Start/reuse the loopback serial bridge; no startup tasks or external bind."""
from pathlib import Path
import subprocess,sys,time,json,urllib.request
BASE='http://127.0.0.1:18765'
def healthy():
 try:
  with urllib.request.urlopen(BASE+'/health',timeout=.5) as r:return json.load(r).get('app')=='tkx-led-bridge'
 except (OSError,ValueError):return False
if __name__=='__main__':
 if not healthy():
  py=Path.home()/'Documents/ChatGPT/示波器电赛准备/work/dap-diagnostic-venv/Scripts/pythonw.exe'
  if not py.exists():py=Path(sys.executable)
  log=(Path(__file__).parent/'serial-hub-error.log').open('ab')
  process=subprocess.Popen([str(py),str(Path(__file__).with_name('serial_hub.py'))],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
  log.close()
  for _ in range(40):
   if healthy():break
   if process.poll() is not None:raise SystemExit('Serial hub exited; see serial-hub-error.log')
   time.sleep(.1)
  else:raise SystemExit('Serial hub unavailable; port18765 may be occupied')
 print(BASE)
