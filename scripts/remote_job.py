#!/usr/bin/env python3
"""Owned remote child wrapper. Invoked only by native_run submit; holds one GPU lock."""
import fcntl,hashlib,json,os,signal,socket,subprocess,sys,time
from pathlib import Path
P=Path(sys.argv[1]).resolve();cfg=json.loads((P/'launch.json').read_text());n=cfg['node']
def write(state,**kw):
 ticks=Path('/proc/self/stat').read_text().split(') ',1)[1].split()[19] if Path('/proc/self/stat').exists() else None
 d={'state':state,'run_id':cfg['run_id'],'rank':cfg['rank'],'wrapper_pid':os.getpid(),'wrapper_start_ticks':ticks,'time':time.time(),**kw};q=P/'worker.tmp';q.write_text(json.dumps(d));os.replace(q,P/'worker.json')
def terminate(proc):
 if proc.poll() is not None:return
 os.killpg(proc.pid,signal.SIGTERM)
 try:proc.wait(15)
 except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
def main():
 if socket.gethostname()!=n['expected_hostname']:raise RuntimeError('wrong physical hostname')
 lock=Path(n['gpu_lock']);lock.parent.mkdir(parents=True,exist_ok=True)
 with lock.open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  # One shared physical-GPU lock must be used by generation, dual rank and GPU post-process consumers.
  apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name','--format=csv,noheader'],text=True).strip()
  if apps:raise RuntimeError('GPU has compute processes: '+apps)
  if n.get('comfy_ports'):
   import urllib.request
   for port in n['comfy_ports']:
    with urllib.request.urlopen('http://127.0.0.1:%d/queue'%port,timeout=5) as response:q=json.load(response)
    if q['queue_running'] or q['queue_pending']:raise RuntimeError('ComfyUI queue busy')
  # All ranks must acquire locks and report ready before controller issues GO.
  write('ready');deadline=time.monotonic()+120
  while not (P/'GO').exists():
   if (P/'STOP').exists():write('stopped_before_start');return
   if time.monotonic()>deadline:raise RuntimeError('GO not received; ready lease expired')
   time.sleep(.25)
  if (P/'STOP').exists():write('stopped_before_start');return
  # Verify staged content before model input becomes visible; run-specific names prevent collisions.
  inputdir=Path(n['comfy_root'])/'input';inputdir.mkdir(exist_ok=True)
  import shutil
  for item in cfg['inputs']:
   src=P/'inputs'/item['remote_name'];digest=hashlib.sha256(src.read_bytes()).hexdigest()
   if digest!=item['sha256']:raise RuntimeError('staged input checksum mismatch')
   dst=inputdir/item['remote_name']
   if dst.exists() and hashlib.sha256(dst.read_bytes()).hexdigest()!=digest:raise RuntimeError('different existing remote input')
   if not dst.exists():shutil.copyfile(src,dst)
  for file,key in [('native-spec.json','spec_sha256')]:
   if hashlib.sha256((P/file).read_bytes()).hexdigest()!=cfg[key]:raise RuntimeError('staged spec changed')
  env={**os.environ,**n.get('env',{}),'PYTHONDONTWRITEBYTECODE':'1'}
  cmd=[n['python'],'-u',n['author_runner'],'--comfy-root',n['comfy_root'],'--legacy-backend',n['legacy_backend'],'--rank',str(cfg['rank']),'--world',str(cfg['world_size']),'--master',cfg['master_addr'],'--port',str(cfg['master_port']),'--output',str(P),'--graph-file',str(P/'native-spec.json')]
  with (P/'process.log').open('ab') as log:
   proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
   start=time.monotonic()
   try:
    write('running',child_pid=proc.pid)
    while proc.poll() is None:
     if (P/'STOP').exists():terminate(proc);write('stopped',child_exit=proc.returncode);return
     if cfg['timeout_s'] and time.monotonic()-start>cfg['timeout_s']:terminate(proc);raise RuntimeError('owned runtime timeout')
     time.sleep(.5)
   except BaseException:terminate(proc);raise
   write('finished' if proc.returncode==0 else 'failed',child_exit=proc.returncode)
try:main()
except BaseException as e:
 write('failed',error=str(e));sys.exit(1)
