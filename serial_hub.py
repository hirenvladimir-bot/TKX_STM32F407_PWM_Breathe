"""Local serial owner shared by the control panel and project dashboard."""
import argparse,json,queue,re,threading,time
from collections import deque
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
import serial
from pathlib import Path
from serial.tools import list_ports

IDENTITY='tkx-led-bridge'
ALLOWED={'http://127.0.0.1:18764','http://localhost:18764','http://127.0.0.1:18765','http://localhost:18765'}
class Hub:
 def __init__(self,port):
  self.lock=threading.RLock();self.port=port;self.connected=False;self.error='';self.stop=threading.Event();self.reconnect=threading.Event()
  self.queue=queue.Queue(64);self.events=deque(maxlen=1024);self.history=deque(maxlen=1100);self.event_id=0
  self.state=None;self.wave=None;self.state_at=None;self.wave_at=None;self.parse_errors=0;self.generation=0;self.stream_id=str(time.time_ns())
  self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def connect(self,port):
  if not re.fullmatch(r'COM[1-9][0-9]*',port):raise ValueError('Invalid COM port')
  with self.lock:
   if port!=self.port:
    self.port=port;self.generation+=1;self.stream_id=str(time.time_ns());self.connected=False;self.state=None;self.wave=None;self.history.clear();self.events.clear();self.state_at=self.wave_at=None;self.reconnect.set()
 def command(self,command):
  if not re.fullmatch(r'(GET|DEFAULT|[MBTP] [0-9]{1,5})',command):raise ValueError('Invalid command')
  with self.lock:
   if not self.connected or self.reconnect.is_set():raise ConnectionError('Serial port disconnected')
   try:self.queue.put_nowait(command)
   except queue.Full:raise ConnectionError('Command queue full')
 def add_line(self,line,generation=None):
  with self.lock:
   if generation is not None and generation!=self.generation:return
   self.event_id+=1;self.events.append({'id':self.event_id,'line':line})
   try:
    if line.startswith(('STATE ','WAVE ')):
     d={k:(v if k=='name' else int(v)) for k,v in (token.split('=',1) for token in line.split()[1:])}
     if line.startswith('STATE '):
      for k in ['mode','brightness','period','pause','duty','uptime']:assert k in d
      assert 0<=d['mode']<=3 and 0<=d['brightness']<=1000 and 0<=d['duty']<=1000
      self.state=d;self.state_at=time.monotonic()
     else:
      for k in ['t','duty','ccr','eff','target','period','mode','hz','pause']:assert k in d
      assert all(0<=d[k]<=1000 for k in ['duty','ccr','eff','target']) and 0<=d['mode']<=3 and d['hz']==1000
      if self.wave and d['t']<self.wave['t']:self.history.clear();self.stream_id=str(time.time_ns())
      d['event_id']=self.event_id;self.wave=d;self.wave_at=time.monotonic();self.history.append(d)
      while self.history and d['t']-self.history[0]['t']>40000:self.history.popleft()
   except (ValueError,KeyError,AssertionError):self.parse_errors+=1
 def snapshot(self,after=None):
  with self.lock:
   now=time.monotonic();sa=None if self.state_at is None else max(0,now-self.state_at);wa=None if self.wave_at is None else max(0,now-self.wave_at)
   age=min(x for x in [sa,wa] if x is not None) if sa is not None or wa is not None else None
   out={'app':IDENTITY,'project':'天空星 PWM 灯效','source':'serial','port':self.port,'baud':115200,
    'connected':self.connected,'error':self.error,'stream_id':self.stream_id,'last_update_age_s':age,'state_age_s':sa,'wave_age_s':wa,
    'state':self.state,'wave':self.wave,'latest_event_id':self.event_id,'parse_errors':self.parse_errors,
    'events':[x for x in self.events if after is None or x['id']>after]}
   if after is None:out['history']=list(self.history)
   return out
 def live(self,after):
  with self.lock:
   reset=after>self.event_id or (bool(self.events) and after<self.events[0]['id']-1)
   out=self.snapshot(0 if reset else after)
   out['history_reset']=bool(reset)
   out['history_delta']=[x for x in self.history if reset or x.get('event_id',0)>after]
   return out
 def run(self):
  while not self.stop.is_set():
   try:
    with self.lock:port=self.port;generation=self.generation;self.reconnect.clear()
    with serial.Serial(port,115200,timeout=.05,write_timeout=.25) as connection:
     with self.lock:
      if generation!=self.generation:continue
      self.connected=True;self.error=''
     connection.write(b'GET\n');buffer=bytearray()
     while not self.stop.is_set() and not self.reconnect.is_set():
      for _ in range(4):
       try:cmd=self.queue.get_nowait()
       except queue.Empty:break
       data=(cmd+'\n').encode('ascii')
       if connection.write(data)!=len(data):raise OSError('Partial serial write')
       self.add_line('HOST > '+cmd,generation)
      buffer.extend(connection.read(min(max(1,connection.in_waiting),4096)))
      if len(buffer)>16384:buffer.clear();self.parse_errors+=1
      while b'\n' in buffer:
       raw,_,buffer=buffer.partition(b'\n')
       try:line=raw.decode('ascii').strip()
       except UnicodeDecodeError:self.parse_errors+=1;continue
       if line:self.add_line(line,generation)
   except (OSError,serial.SerialException) as exc:
    with self.lock:self.error=f'{type(exc).__name__}: {exc}'
   finally:
    with self.lock:self.connected=False
    while True:
     try:self.queue.get_nowait()
     except queue.Empty:break
   self.stop.wait(.5)

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def respond(self,obj,code=200):
  body=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode('utf-8');self.send_response(code)
  origin=self.headers.get('Origin','http://127.0.0.1:18764')
  if origin in ALLOWED:self.send_header('Access-Control-Allow-Origin',origin)
  self.send_header('Vary','Origin');self.send_header('Cache-Control','no-store');self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)))
  self.send_header('Access-Control-Allow-Headers','Content-Type');self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS');self.end_headers();self.wfile.write(body)
 def do_OPTIONS(self):self.respond({'ok':True})
 def do_GET(self):
  parsed=urlparse(self.path)
  if parsed.path in ('/','/index.html','/web_control.html'):
   page=Path(__file__).with_name('web_control.html')
   if not page.is_file():return self.respond({'error':'Control page is not installed'},404)
   body=page.read_bytes();self.send_response(200)
   self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)))
   self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
  if parsed.path=='/health':return self.respond({'app':IDENTITY,'version':1})
  if parsed.path=='/api/ports':return self.respond({'ports':[{'device':p.device,'description':p.description} for p in list_ports.comports()]})
  if parsed.path=='/api/snapshot':return self.respond(self.server.hub.snapshot())
  if parsed.path=='/api/live':
   try:after=int(parse_qs(parsed.query).get('after',['0'])[0])
   except ValueError:return self.respond({'error':'Invalid cursor'},400)
   return self.respond(self.server.hub.live(after))
  if parsed.path=='/api/events':
   try:after=int(parse_qs(parsed.query).get('after',['0'])[0])
   except ValueError:return self.respond({'error':'Invalid cursor'},400)
   return self.respond(self.server.hub.snapshot(after))
  return self.respond({'error':'Not found'},404)
 def do_POST(self):
  if self.headers.get('Origin') and self.headers['Origin'] not in ALLOWED:return self.respond({'error':'Origin denied'},403)
  try:
   n=int(self.headers.get('Content-Length','0'))
   if not 0<n<=1024:raise ValueError('Invalid request length')
   obj=json.loads(self.rfile.read(n))
   if self.path=='/api/connect':self.server.hub.connect(str(obj['port']))
   elif self.path=='/api/command':self.server.hub.command(str(obj['command']))
   else:return self.respond({'error':'Not found'},404)
   self.respond({'ok':True})
  except (ValueError,KeyError,TypeError) as exc:self.respond({'error':str(exc)},400)
  except ConnectionError as exc:self.respond({'error':str(exc)},503)

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--port',default='COM16');args=ap.parse_args()
 hub=Hub(args.port)
 server=ThreadingHTTPServer(('127.0.0.1',18765),Handler);server.hub=hub
 try:server.serve_forever()
 finally:hub.stop.set();hub.thread.join(2);server.server_close()
