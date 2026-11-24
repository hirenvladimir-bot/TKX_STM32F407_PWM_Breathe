"""Tkinter control panel: firmware STATE is authoritative, ACK is not state."""
from __future__ import annotations
import argparse
import csv
from collections import deque
from tkinter import filedialog
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

MODES = ('呼吸', '双心跳', '萤火虫', '常亮')


def parse_state(line):
    if not line.startswith('STATE '):
        return None
    pairs = dict(token.split('=', 1) for token in line.split()[1:] if '=' in token)
    values = {key: int(pairs[key]) for key in
              ('mode', 'brightness', 'period', 'pause', 'duty', 'uptime', 'rxerr', 'dropped', 'cmds')}
    if not (0 <= values['mode'] <= 3 and 0 <= values['brightness'] <= 1000
            and 500 <= values['period'] <= 10000 and values['pause'] in (0, 1)):
        raise ValueError('STATE 参数超出协议范围')
    values['name'] = pairs.get('name', '?')
    return values


def parse_wave(line):
    pairs=dict(token.split('=',1) for token in line.split()[1:] if '=' in token)
    v={k:int(pairs[k]) for k in ('t','duty','ccr','eff','target','period','mode','hz','pause')}
    if not (0<=v['t']<=0xffffffff and all(0<=v[k]<=1000 for k in ('duty','ccr','eff','target')) and 500<=v['period']<=10000 and 0<=v['mode']<=3 and 1<=v['hz']<=1000000 and v['pause'] in (0,1)):
        raise ValueError('WAVE 参数越界')
    return v


class SerialWorker:
    """Only this thread opens, reads, writes, and closes the port."""
    def __init__(self, port, events):
        self.port = port
        self.events = events
        self.commands = queue.Queue(maxsize=64)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='lamp-serial', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def send(self, command):
        try:
            self.commands.put_nowait(command)
            return True
        except queue.Full:
            self.events.put(('error', '发送队列已满，命令未发送'))
            return False

    def run(self):
        try:
            import serial
            with serial.Serial(self.port, 115200, bytesize=8, parity='N', stopbits=1,
                               timeout=.05, write_timeout=.25) as connection:
                self.events.put(('connected', self.port))
                self.commands.put('GET')
                buffer = bytearray()
                while not self.stop_event.is_set():
                    for _ in range(8):
                        if self.stop_event.is_set():
                            break
                        try:
                            command = self.commands.get_nowait()
                        except queue.Empty:
                            break
                        payload = (command + '\n').encode('ascii')
                        if connection.write(payload) != len(payload):
                            raise OSError('串口未完整写入命令')
                        self.events.put(('sent', command))
                        # Explicitly ask for authoritative state after each edit.
                        if command != 'GET':
                            if connection.write(b'GET\n') != 4:
                                raise OSError('串口未完整写入 GET')
                    data = connection.read(min(max(connection.in_waiting, 1), 4096))
                    buffer.extend(data)
                    if len(buffer) > 16384:
                        buffer.clear()
                        self.events.put(('error', '接收行过长，已丢弃异常数据'))
                    while b'\n' in buffer:
                        line, _, buffer = buffer.partition(b'\n')
                        try:
                            text = line.decode('ascii').strip()
                        except UnicodeDecodeError:
                            self.events.put(('error', '收到非 ASCII 数据，请检查波特率和固件'))
                            continue
                        if text:
                            self.events.put(('line', text))
        except Exception as exc:
            self.events.put(('error', f'{type(exc).__name__}: {exc}'))
        finally:
            self.events.put(('disconnected', self.port))


class Panel:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.events = queue.Queue()
        self.worker = None
        self.connected = False
        self.closing = False
        self.state = None
        self.last_state_time = None
        self.pending = {}  # field -> (desired value, queued at)
        self.debounce = {}
        self.slider_sent_at = {}
        self.slider_last_value = {}
        self.syncing = False
        self.connection_error = ''
        root.title('天空星 LED 控制与实时波形')
        root.geometry('1180x760')
        root.minsize(980,640)
        # Windows zoomed respects the taskbar/work area, unlike fullscreen.
        root.after_idle(lambda: root.state('zoomed'))
        self.panes=ttk.Panedwindow(root,orient='horizontal')
        self.panes.pack(fill='both',expand=True)
        self.control_frame=ttk.Frame(self.panes,width=400)
        self.wave_frame=ttk.Frame(self.panes,padding=8)
        self.panes.add(self.control_frame,weight=0)
        self.panes.add(self.wave_frame,weight=1)
        self.wave_tab=self.wave_frame  # Compatibility with earlier inspectors.
        control_canvas=tk.Canvas(self.control_frame,width=380,highlightthickness=0)
        scrollbar=ttk.Scrollbar(self.control_frame,orient='vertical',command=control_canvas.yview)
        control_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y')
        control_canvas.pack(side='left',fill='both',expand=True)
        control_tab=ttk.Frame(control_canvas)
        control_window=control_canvas.create_window((0,0),window=control_tab,anchor='nw')
        control_tab.bind('<Configure>',lambda event:control_canvas.configure(scrollregion=control_canvas.bbox('all')))
        control_canvas.bind('<Configure>',lambda event:control_canvas.itemconfigure(control_window,width=event.width))
        self._initial_sash_set=False
        def initialize_sash():
            if self._initial_sash_set or self.closing:
                return
            if self.panes.winfo_ismapped() and self.panes.winfo_width()>=980:
                self.panes.sashpos(0,400)
                self._initial_sash_set=True
            else:
                root.after(100,initialize_sash)
        root.after(100,initialize_sash)
        self.wave_history=deque()
        self.last_wave_time=None
        self.wave_frozen=None
        self.wave_span=tk.StringVar(value='30')
        tools=ttk.Frame(self.wave_tab);tools.pack(fill='x')
        ttk.Label(tools,text='历史窗口 / 秒').pack(side='left')
        ttk.Combobox(tools,textvariable=self.wave_span,values=('10','30'),state='readonly',width=5).pack(side='left')
        ttk.Button(tools,text='冻结 / 恢复',command=self.freeze_wave).pack(side='left',padx=8)
        ttk.Button(tools,text='清空历史',command=self.clear_wave).pack(side='left')
        ttk.Button(tools,text='导出 CSV',command=self.export_wave).pack(side='right')
        self.wave_canvas=tk.Canvas(self.wave_tab,bg='#0b1520',highlightthickness=0)
        self.wave_canvas.pack(fill='both',expand=True,pady=8)
        root.after(50,self.draw_wave)
        outer = ttk.Frame(control_tab, padding=14)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='LED 灯效控制', font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='串口直连 · 参数以 STATE 回读为准',wraplength=340).pack(anchor='w', pady=(2,8))
        connection = ttk.Frame(outer)
        connection.pack(fill='x')
        ttk.Label(connection, text='串口').pack(side='left')
        self.port = tk.StringVar(value=args.port)
        self.ports = ttk.Combobox(connection, textvariable=self.port, width=19)
        self.ports.pack(side='left', padx=6)
        connection_actions=ttk.Frame(outer)
        connection_actions.pack(fill='x',pady=(6,0))
        self.refresh_button = ttk.Button(connection_actions, text='刷新端口', command=self.refresh_ports)
        self.refresh_button.pack(side='left')
        self.connect_button = ttk.Button(connection_actions, text='连接', command=self.toggle_connection)
        self.connect_button.pack(side='left', padx=8)
        ttk.Label(connection_actions, text='115200 · 8N1').pack(side='right')
        self.connection_text = tk.StringVar(value='未连接')
        ttk.Label(outer, textvariable=self.connection_text, wraplength=340).pack(anchor='w', pady=(8,10))
        effect = ttk.LabelFrame(outer, text='选择灯效', padding=8)
        effect.pack(fill='x')
        self.controls = []
        for index, name in enumerate(MODES):
            button = ttk.Button(effect, text=name, command=lambda i=index: self.command(f'M {i}', 'mode', i))
            button.grid(row=index//2, column=index%2, sticky='ew', padx=3,pady=4)
            effect.columnconfigure(index%2, weight=1)
            self.controls.append(button)
        knobs = ttk.LabelFrame(outer, text='调节参数', padding=10)
        knobs.pack(fill='x', pady=10)
        self.brightness = tk.DoubleVar(value=100)
        self.period = tk.DoubleVar(value=3)
        self.brightness_text = tk.StringVar(value='亮度上限：100.0%（待连接）')
        self.period_text = tk.StringVar(value='周期：3.00 秒（越小越快）')
        ttk.Label(knobs, textvariable=self.brightness_text).pack(anchor='w')
        self.brightness_scale = ttk.Scale(knobs, from_=0, to=100, variable=self.brightness,
                                        command=lambda _: self.slider('brightness'))
        self.brightness_scale.pack(fill='x', pady=(3,10))
        ttk.Label(knobs, textvariable=self.period_text).pack(anchor='w')
        self.period_scale = ttk.Scale(knobs, from_=.5, to=10, variable=self.period,
                                    command=lambda _: self.slider('period'))
        self.period_scale.pack(fill='x', pady=3)
        self.controls += [self.brightness_scale, self.period_scale]
        self.brightness_scale.bind('<ButtonRelease-1>',lambda event:self.flush_slider('brightness'))
        self.period_scale.bind('<ButtonRelease-1>',lambda event:self.flush_slider('period'))
        actions = ttk.Frame(outer)
        actions.pack(fill='x')
        self.pause_button = ttk.Button(actions, text='暂停', command=self.toggle_pause)
        self.pause_button.pack(side='left')
        self.controls.append(self.pause_button)
        for text, callback in [('恢复默认', self.defaults), ('读取状态', lambda: self.command('GET'))]:
            button = ttk.Button(actions, text=text, command=callback)
            button.pack(side='left', padx=8)
            self.controls.append(button)
        actual = ttk.LabelFrame(outer, text='实际状态（开发板回读）', padding=8)
        actual.pack(fill='x', pady=10)
        self.actual_text = tk.StringVar(value='等待 STATE 回读，尚未确认任何参数。')
        self.pending_text = tk.StringVar(value='无待确认命令')
        ttk.Label(actual, textvariable=self.actual_text, wraplength=340,
                  font=('Microsoft YaHei UI', 11)).pack(anchor='w')
        ttk.Label(actual, textvariable=self.pending_text, wraplength=340).pack(anchor='w', pady=(7,0))
        ttk.Label(outer, text='通信日志').pack(anchor='w')
        self.log = ScrolledText(outer, height=4, state='disabled', font=('Consolas', 9))
        self.log.pack(fill='both', expand=False, pady=(4,0))
        self.set_enabled(False)
        self.refresh_ports()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(50, self.poll)
        if args.connect:
            root.after(100, self.toggle_connection)

    def log_line(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', time.strftime('%H:%M:%S ') + text + '\n')
        if int(self.log.index('end-1c').split('.')[0]) > 500:
            self.log.delete('1.0', '100.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def refresh_ports(self):
        try:
            from serial.tools import list_ports
            values = [item.device for item in list_ports.comports()]
            self.ports.configure(values=values)
            if not values:
                self.log_line('未发现串口；可以手动输入端口号。')
        except Exception as exc:
            self.log_line(f'端口刷新失败：{exc}')

    def set_enabled(self, enabled):
        for control in self.controls:
            control.configure(state='normal' if enabled else 'disabled')
        if enabled and self.state is None:
            self.pause_button.configure(state='disabled')
        self.ports.configure(state='disabled' if enabled else 'normal')
        self.refresh_button.configure(state='disabled' if enabled else 'normal')

    def cancel_edits(self):
        for after_id in self.debounce.values():
            self.root.after_cancel(after_id)
        self.debounce.clear()

    def toggle_connection(self):
        if self.worker and self.worker.thread.is_alive():
            self.disconnect()
            return
        port = self.port.get().strip()
        if not port:
            self.connection_text.set('请输入串口号。')
            return
        self.state = None
        self.last_state_time = None
        self.actual_text.set('等待新连接的 STATE 回读，尚未确认任何参数。')
        self.pending.clear()
        self.slider_last_value.clear()
        self.connection_error = ''
        self.worker = SerialWorker(port, self.events)
        self.connection_text.set(f'正在连接 {port}…')
        self.connect_button.configure(state='disabled')
        self.ports.configure(state='disabled')
        self.refresh_button.configure(state='disabled')
        self.worker.start()

    def disconnect(self):
        self.cancel_edits()
        self.pending.clear()
        self.connected = False
        self.set_enabled(False)
        self.connect_button.configure(state='disabled')
        self.connection_text.set('正在停止接收并释放串口…')
        if self.worker:
            self.worker.stop()

    def command(self, text, key=None, value=None):
        if not self.connected or not self.worker:
            return
        if self.worker.send(text) and key is not None:
            self.pending[key] = (value, time.monotonic())
            self.update_pending()

    def slider(self,key):
        if self.syncing or not self.connected: return
        self.update_slider_labels()
        remaining=.045-(time.monotonic()-self.slider_sent_at.get(key,0))
        if remaining<=0:
            self.flush_slider(key)
        elif key not in self.debounce:
            self.debounce[key]=self.root.after(max(1,round(remaining*1000)),lambda:self.send_slider(key))

    def flush_slider(self,key):
        if key in self.debounce:
            self.root.after_cancel(self.debounce.pop(key))
        self.send_slider(key)

    def send_slider(self,key):
        self.debounce.pop(key,None)
        if not self.connected:return
        value=max(0,min(1000,round(self.brightness.get()*10))) if key=='brightness' else max(500,min(10000,round(self.period.get()*1000)))
        if self.slider_last_value.get(key)==value and (self.pending.get(key,(None,0))[0]==value or (self.state is not None and self.state.get(key)==value)):return
        self.slider_last_value[key]=value
        self.slider_sent_at[key]=time.monotonic()
        self.command(f'{"B" if key=="brightness" else "T"} {value}',key,value)

    def toggle_pause(self):
        if self.state is not None:
            desired = 1 - self.pending.get('pause',(self.state['pause'],0))[0]
            self.command(f'P {desired}','pause',desired)

    def defaults(self):
        self.cancel_edits()
        # DEFAULT acknowledgement does not establish default parameter values.
        self.pending.clear()
        self.command('DEFAULT','default',None)

    def update_slider_labels(self):
        self.brightness_text.set(f'亮度上限设定：{self.brightness.get():.1f}%')
        self.period_text.set(f'周期设定：{self.period.get():.2f} 秒（越小越快）')

    def update_pending(self):
        names = {'mode':'模式','brightness':'亮度','period':'周期','pause':'暂停','default':'恢复默认'}
        def desired_text(key,value):
            if key == 'mode': return MODES[value]
            if key == 'brightness': return f'{value/10:.1f}%'
            if key == 'period': return f'{value/1000:.2f} 秒'
            if key == 'pause': return '暂停' if value else '继续'
            return '等待回读' if value == 'ACK' else '等待回执'
        if self.pending:
            self.pending_text.set('待 STATE 确认：' + '；'.join(
                f'{names[key]} {desired_text(key,value)}'
                + ('（超过 2 秒未确认）' if time.monotonic()-when>2 else '')
                for key,(value,when) in self.pending.items()))
        else:
            self.pending_text.set('无待确认命令')

    def apply_state(self, state):
        self.state = state
        self.last_state_time = time.monotonic()
        for key,(wanted,_) in list(self.pending.items()):
            # DEFAULT is confirmed only after OK DEFAULT then a subsequent STATE.
            if key == 'default':
                if wanted == 'ACK':
                    self.pending.pop(key,None)
            elif state.get(key) == wanted:
                self.pending.pop(key,None)
        self.syncing = True
        try:
            if 'brightness' not in self.pending and 'brightness' not in self.debounce:
                self.brightness.set(state['brightness']/10)
            if 'period' not in self.pending and 'period' not in self.debounce:
                self.period.set(state['period']/1000)
        finally:
            self.syncing = False
        self.update_slider_labels()
        self.pause_button.configure(state='normal',text='继续' if state['pause'] else '暂停')
        self.actual_text.set(
            f'模式：{MODES[state["mode"]]}   亮度上限：{state["brightness"]/10:.1f}%   '
            f'周期：{state["period"]/1000:.2f} 秒   {"已暂停" if state["pause"] else "运行中"}\n'
            f'PWM duty：{state["duty"]}   运行：{state["uptime"]/1000:.1f} 秒\n'
            f'接收错误：{state["rxerr"]}   丢弃：{state["dropped"]}   命令数：{state["cmds"]}')
        self.update_pending()

    def poll(self):
        for _ in range(200):
            try:
                kind, text = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'connected':
                if self.closing:
                    self.worker.stop()
                    continue
                self.connected = True
                self.last_state_time = time.monotonic()
                self.connect_button.configure(state='normal',text='停止 / 断开')
                self.set_enabled(True)
                self.log_line(f'已连接 {text}，等待开发板状态')
            elif kind == 'sent':
                self.log_line('发送 > ' + text)
            elif kind == 'error':
                self.connection_error = text
                self.log_line('错误 ! ' + text)
                self.connection_text.set('通信错误：' + text)
            elif kind == 'disconnected':
                self.connected = False
                self.cancel_edits()
                self.pending.clear()
                self.set_enabled(False)
                self.connect_button.configure(state='normal',text='连接')
                self.connection_text.set('已断开；串口已释放。' + (f' {self.connection_error}' if self.connection_error else ''))
                self.log_line(f'已释放串口 {text}')
                self.update_pending()
            elif kind == 'line':
                if text.startswith('WAVE '):
                    try:
                        wave=parse_wave(text)
                        if self.wave_history and wave['t']<self.wave_history[-1]['t']:
                            self.wave_history.clear()
                        if not self.wave_history or wave['t']!=self.wave_history[-1]['t']:
                            self.wave_history.append(wave)
                            while self.wave_history and wave['t']-self.wave_history[0]['t']>40000:
                                self.wave_history.popleft()
                            self.last_wave_time=time.monotonic()
                    except (KeyError,ValueError) as exc:
                        self.log_line(f'无效 WAVE：{exc}')
                elif text.startswith('STATE '):
                    try:
                        state = parse_state(text)
                        if self.connected:
                            self.apply_state(state)
                    except (KeyError, ValueError) as exc:
                        self.log_line(f'无效 STATE：{exc} | {text}')
                else:
                    self.log_line('收到 < ' + text)
                    if text == 'OK DEFAULT' and 'default' in self.pending:
                        self.pending['default'] = ('ACK',time.monotonic())
                    elif text.startswith('ERR '):
                        self.pending_text.set('开发板拒绝命令：' + text + '；实际参数请看回读。')
        if self.connected:
            age = time.monotonic() - self.last_state_time if self.last_state_time else 1e9
            if age > 2:
                self.connection_text.set(f'通信超时：{age:.1f} 秒未收到有效 STATE；下方为最后回读值。')
            elif self.state is None:
                self.connection_text.set('串口已连接，等待首次 STATE 回读…')
            else:
                self.connection_text.set(f'已连接 {self.port.get()} · 状态正常 · 最近回读 {age:.1f} 秒前'
                                         + (f'；通信告警：{self.connection_error}' if self.connection_error else ''))
        if self.pending:
            self.update_pending()
        if self.closing and (not self.worker or not self.worker.thread.is_alive()):
            self.root.destroy()
            return
        self.root.after(50,self.poll)

    def freeze_wave(self):
        self.wave_frozen=list(self.wave_history) if self.wave_frozen is None else None

    def clear_wave(self):
        self.wave_history.clear()
        self.wave_frozen=None
        self.last_wave_time=None

    def export_wave(self):
        rows=list(self.wave_frozen if self.wave_frozen is not None else self.wave_history)
        if not rows: return
        path=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if path:
            try:
                with open(path,'w',newline='',encoding='utf-8-sig') as f:
                    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            except OSError as exc: self.log_line(f'CSV 导出失败：{exc}')

    def draw_wave(self):
        c=self.wave_canvas;c.delete('all')
        w,h=c.winfo_width(),c.winfo_height()
        rows=list(self.wave_frozen if self.wave_frozen is not None else self.wave_history)
        if w>200 and h>220:
            left,right,top,bottom=48,w-20,60,int(h*.60)
            stale=not self.connected or self.last_wave_time is None or time.monotonic()-self.last_wave_time>2
            label='等待真实 WAVE 数据' if not rows else ('已冻结' if self.wave_frozen is not None else ('数据过期，保留最后回读' if stale else '实时遥测'))
            c.create_text(left,15,text=label,fill='#e4edf5',anchor='w')
            c.create_text(left,37,text='目标：蓝 / 渐变上限：黄 / 占空比：绿（串口回读）',fill='#bacddd',anchor='w')
            for v in (0,25,50,75,100):
                y=bottom-(bottom-top)*v/100
                c.create_line(left,y,right,y,fill='#293b4a');c.create_text(left-6,y,text=f'{v}%',fill='#bacddd',anchor='e')
            if rows:
                end=rows[-1]['t'];span=float(self.wave_span.get())*1000
                for i in range(6):
                    x=left+(right-left)*i/5
                    c.create_text(x,bottom+15,text=f'{-span/1000*(1-i/5):.0f}s',fill='#bacddd')
                for field,color in [('target','#5bc8ff'),('eff','#ffce72'),('duty','#64edb4')]:
                    points=[];last=None
                    for row in rows:
                        if row['t']<end-span: continue
                        if last is not None and row['t']-last>300:
                            if len(points)>=4:c.create_line(*points,fill=color,width=2)
                            points=[]
                        points.extend((left+(row['t']-end+span)/span*(right-left),bottom-row[field]/1000*(bottom-top)))
                        last=row['t']
                    if len(points)>=4:c.create_line(*points,fill=color,width=2)
                row=rows[-1];yhigh=bottom+85;ylow=h-45
                c.create_text(left,bottom+45,text=f'目标 {row["target"]/10:.1f}% / 实际上限 {row["eff"]/10:.1f}% / 占空比 {row["duty"]/10:.1f}%',fill='#e4edf5',anchor='w')
                c.create_text(left,bottom+65,text='PWM：按 CCR / Hz 回读重建 5 周期，非示波器实测',fill='#c5a2ff',anchor='w')
                pts=[];fraction=row['ccr']/1000
                for i in range(5):
                    x0=left+(right-left)*i/5;x1=left+(right-left)*(i+fraction)/5;x2=left+(right-left)*(i+1)/5
                    if fraction==0:pts.extend((x0,ylow,x2,ylow))
                    elif fraction==1:pts.extend((x0,yhigh,x2,yhigh))
                    else:pts.extend((x0,ylow,x0,yhigh,x1,yhigh,x1,ylow,x2,ylow))
                c.create_line(*pts,fill='#c5a2ff',width=2)
                c.create_text(left,h-18,text=f'{row["hz"]} Hz / CCR {row["ccr"]}/1000 / 横轴总长 {5000/row["hz"]:g} ms',fill='#bacddd',anchor='w')
        self.root.after(50,self.draw_wave)

    def close(self):
        self.closing = True
        self.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',default='COM16')
    parser.add_argument('--connect',action='store_true',help='启动后自动连接串口')
    parser.add_argument('--tab',choices=('waveform','control'),default='control')
    args = parser.parse_args()
    root = tk.Tk()
    Panel(root,args)
    root.mainloop()


if __name__ == '__main__':
    main()
