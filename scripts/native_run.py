#!/usr/bin/env python3
"""Submit/status/collect/stop a frozen task on qualified native H3 installations.

No auto-retry of submission. --execute and project authorization required for submit.
"""
import argparse,json,os,re,shlex,subprocess,sys,time
from pathlib import Path,PurePosixPath
from recast import read,save,sha,ident,verify_task,now
ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15']
TERMINAL={'finished','failed','stopped','stopped_before_start'}
def cluster(path):
 c=read(path);ids=set();physical=set()
 if type(c.get('timeout_s')) not in [int,float] or not 0<=c['timeout_s']<=604800:raise ValueError('timeout_s must be 0..604800')
 for n in c['nodes']:
  ident(n['id'])
  if n['id'] in ids or n['expected_hostname'] in physical:raise ValueError('duplicate node or physical host')
  ids.add(n['id']);physical.add(n['expected_hostname'])
  if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*',n['ssh']):raise ValueError('invalid SSH alias')
  for k in ['python','comfy_root','runs_root','gpu_lock','author_runner','legacy_backend']:
   v=n[k];p=PurePosixPath(v)
   if not p.is_absolute() or '..' in p.parts or len(p.parts)<3 or '\n' in v:raise ValueError('invalid remote '+k)
  if not isinstance(n.get('env',{}),dict) or any(not re.fullmatch('[A-Z][A-Z0-9_]*',k) or not isinstance(v,str) for k,v in n.get('env',{}).items()):raise ValueError('invalid environment')
 return c

def ssh(n,argv):
 return subprocess.check_output([*SSH,n['ssh'],shlex.join([str(x) for x in argv])],text=True,timeout=30)
def py(n,code,*args):return ssh(n,[n['python'],'-c',code,*args])
def upload(n,src,dst):
 code="import pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); f=p.open('xb'); shutil.copyfileobj(sys.stdin.buffer,f); f.close()"
 with Path(src).open('rb') as f:subprocess.run([*SSH,n['ssh'],shlex.join([n['python'],'-c',code,str(dst)])],stdin=f,check=True,timeout=300)
def download(n,src,dst):
 code="import pathlib,shutil,sys; f=pathlib.Path(sys.argv[1]).open('rb'); shutil.copyfileobj(f,sys.stdout.buffer)"
 with Path(dst).open('xb') as f:subprocess.run([*SSH,n['ssh'],shlex.join([n['python'],'-c',code,str(src)])],stdout=f,check=True,timeout=300)
def base(n,run):return str(PurePosixPath(n['runs_root'])/ident(run))
def state(n,remote):
 code="""import json,pathlib,sys
p=pathlib.Path(sys.argv[1]); q=p/'worker.json'
d=json.loads(q.read_text()) if q.exists() else {'state':'unknown_not_started'}
if d['state'] in ['ready','running']:
 stat=pathlib.Path('/proc/%s/stat'%d['wrapper_pid'])
 valid=False
 if stat.exists():
  fields=stat.read_text().split(') ',1)[1].split();valid=fields[0]!='Z' and fields[19]==d.get('wrapper_start_ticks')
 d['wrapper_alive']=valid
 if not valid:d['state']='unknown_wrapper_dead'
print(json.dumps(d))"""
 return json.loads(py(n,code,remote))
def project_guard(path):
 p=read(Path(path)/'project.json')
 if p.get('production_state') in ['stopped_by_user','paused','complete'] or p.get('generation_authorized') is not True:raise ValueError('Project is stopped/paused/unauthorized. A new explicit production request is required; packaging is not restart authorization.')
 return p

def submit(a):
 p=Path(a.task).resolve();m=verify_task(p);c=cluster(a.cluster);nodes=[next(n for n in c['nodes'] if n['id']==x) for x in a.nodes]
 if len(nodes) not in [1,2] or len(set(a.nodes))!=len(nodes):raise ValueError('choose 1 independent node or 2 paired nodes')
 if any(not n.get('enabled',True) for n in nodes):raise ValueError('disabled node')
 world=len(nodes)
 if world==2:
  if not c.get('paired_qualified'):raise ValueError('paired backend not qualified')
  if not re.fullmatch(r'[A-Za-z0-9_.-]+',c['master_addr']) or not 1024<=c['master_port']<=65535:raise ValueError('invalid paired fabric address/port')
 plan={'run_id':m['run_id'],'world_size':world,'nodes':[{'id':n['id'],'ssh':n['ssh'],'gpu_lock':n['gpu_lock'],'remote_run':base(n,m['run_id'])} for n in nodes],'status':'dry_run_no_network'}
 if not a.execute:print(json.dumps(plan,ensure_ascii=False,indent=2));return
 project=project_guard(a.project)
 if project.get('source',{}).get('sha256')!=m['source_sha256']:raise ValueError('authorized project source does not match frozen task')
 if 'EDIT_' in json.dumps(c) or any('192.0.2.' in n['ssh'] for n in nodes):raise ValueError('edit example cluster config before execution')
 if not p.is_relative_to(Path(a.project).resolve()):raise ValueError('task must belong to the authorized project')
 receipt=p/'submission.json'
 # Write intent exclusively BEFORE contacting remote. Lost SSH acknowledgment cannot cause duplicate generation.
 with receipt.open('x') as f:json.dump({**plan,'status':'submission_intent','at':now(),'cluster_sha256':sha(a.cluster),'project_id':project['project_id']},f,ensure_ascii=False,indent=2)
 save(p/'cluster-snapshot.json',c)
 started=[];staged=[]
 try:
  for rank,n in enumerate(nodes):
   remote=base(n,m['run_id'])
   py(n,"import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); p.mkdir(); (p/'inputs').mkdir()",remote)
   cfg={'run_id':m['run_id'],'node':n,'rank':rank,'world_size':world,'master_addr':c['master_addr'] if world==2 else '127.0.0.1','master_port':c['master_port'],'timeout_s':c['timeout_s'],'inputs':m['inputs'],'spec_sha256':m['spec_sha256']}
   save(p/f'launch-rank{rank}.json',cfg);upload(n,p/f'launch-rank{rank}.json',remote+'/launch.json');upload(n,ROOT/'scripts/remote_job.py',remote+'/remote_job.py');upload(n,p/'native-spec.json',remote+'/native-spec.json')
   for item in m['inputs']:upload(n,p/item['path'],remote+'/inputs/'+item['remote_name'])
   staged.append((n,remote))
  for n,remote in staged:
   cmd=['nohup',n['python'],'-u',remote+'/remote_job.py',remote]
   launch=shlex.join(cmd)+' > '+shlex.quote(remote+'/wrapper.log')+' 2>&1 < /dev/null & echo $!'
   pid=int(subprocess.check_output([*SSH,n['ssh'],launch],text=True,timeout=30).strip());started.append((n,remote,pid))
  deadline=time.monotonic()+45
  while True:
   states=[state(n,r) for n,r,_ in started]
   if all(x['state']=='ready' for x in states):break
   if any(x['state'] in TERMINAL for x in states) or time.monotonic()>deadline:raise RuntimeError('all-rank preflight did not reach ready: '+json.dumps(states))
   time.sleep(1)
  project_guard(a.project)
  for n,r,_ in started:py(n,"import pathlib,sys; (pathlib.Path(sys.argv[1])/'GO').touch()",r)
  save(receipt,{**plan,'status':'submitted','at':now(),'cluster_sha256':sha(a.cluster),'project_id':project['project_id'],'wrapper_pids':{n['id']:pid for n,r,pid in started}})
  print(receipt)
 except BaseException:
  # Stop only these wrappers through owned cancellation files, never global queue interruption or pkill.
  for n,r,_ in started:
   try:py(n,"import pathlib,sys; (pathlib.Path(sys.argv[1])/'STOP').touch()",r)
   except Exception:pass
  # Keep original intent; remote receipt/liveness must be reconciled before any future submission.
  raise

def existing(a):
 p=Path(a.task).resolve();m=verify_task(p) if a.action=='collect' else read(p/'manifest.json');ident(m['run_id']);receipt=read(p/'submission.json');c=cluster(a.cluster)
 if receipt['cluster_sha256']!=sha(a.cluster):raise ValueError('cluster config changed since submission; use exact saved configuration or reconcile before action')
 nodes=[]
 for x in receipt['nodes']:
  n=next(n for n in c['nodes'] if n['id']==x['id'])
  if n['ssh']!=x['ssh'] or base(n,m['run_id'])!=x['remote_run']:raise ValueError('receipt destination mismatch')
  nodes.append((n,x['remote_run']))
 return p,m,nodes

def status(a):
 p,m,nodes=existing(a);s=[]
 for n,r in nodes:
  x=state(n,r)
  detail=py(n,"import pathlib,json,sys; p=pathlib.Path(sys.argv[1]); es=[]\nfor l in (p/'events.jsonl').read_text(errors='replace').splitlines()[-100:] if (p/'events.jsonl').exists() else []:\n try: es.append(json.loads(l))\n except ValueError: pass\nprint(json.dumps({'recent_events':es[-8:], 'native_exit':json.loads((p/'exit.json').read_text()) if (p/'exit.json').exists() else None}))",r)
  s.append({'node':n['id'],**x,**json.loads(detail)})
 save(p/'status.json',{'at':now(),'ranks':s,'visual_acceptance':False});print(json.dumps(s,ensure_ascii=False,indent=2))
def stop(a):
 p,m,nodes=existing(a)
 for n,r in nodes:py(n,"import pathlib,sys; p=pathlib.Path(sys.argv[1]); assert p.is_dir(); (p/'STOP').touch()",r)
 print('Owned stop flags sent. Run status to verify every rank terminal; no completion inferred from request.')
def stop_project(a):
 project=Path(a.project).resolve();data=read(project/'project.json')
 data.update(production_state='stopped_by_user',generation_authorized=False,stop_requested_at=now())
 save(project/'project.json',data);results=[]
 for receipt in sorted((project/'03_提示词与任务').glob('**/submission.json')):
  task=receipt.parent
  try:
   snapshot=task/'cluster-snapshot.json'
   arg=argparse.Namespace(task=task,cluster=snapshot if snapshot.exists() else a.cluster,action='stop')
   stop(arg);results.append({'task':str(task),'state':'stop_requested_verify_status'})
  except Exception as exc:results.append({'task':str(task),'state':'stop_failed_requires_reconcile','error':str(exc)})
 save(project/'09_脚本与流程'/'stop-receipt.json',{'at':now(),'tasks':results,'monitor_shutdown':'stop any separately configured project automation/consumer and verify; not controlled by this CLI'})
 print(json.dumps(results,ensure_ascii=False,indent=2))
def collect(a):
 p,m,nodes=existing(a)
 for n,r in nodes:
  x=state(n,r)
  if x['state']!='finished' or x.get('child_exit')!=0:raise ValueError('rank not successful: '+n['id']+' '+json.dumps(x))
 n,r=nodes[0];dest=Path(a.output).resolve();dest.mkdir(parents=True,exist_ok=False)
 history=json.loads(py(n,"import pathlib,sys; print((pathlib.Path(sys.argv[1])/'history.json').read_text())",r));save(dest/'history.json',history);files=[]
 for node,value in history['outputs'].items():
  for item in value.get('gifs',[]):
   if not item['filename'].endswith('.mp4'):continue
   rel=PurePosixPath(item.get('subfolder',''))/item['filename']
   if rel.is_absolute() or '..' in rel.parts:raise ValueError('output path escapes Comfy output')
   remote=str(PurePosixPath(n['comfy_root'])/'output'/rel);target=dest/(ident(str(node))+'-'+Path(item['filename']).name)
   remote_hash=py(n,"import hashlib,pathlib,sys; h=hashlib.sha256(); f=pathlib.Path(sys.argv[1]).open('rb')\nfor c in iter(lambda:f.read(1048576),b''):h.update(c)\nprint(h.hexdigest())",remote).strip();download(n,remote,target)
   if sha(target)!=remote_hash:raise ValueError('download checksum mismatch')
   files.append({'node':node,'file':str(target),'sha256':remote_hash})
 if not files:raise ValueError('history contains no MP4 outputs')
 for n,r in nodes:
  e=dest/('rank-'+n['id']);e.mkdir()
  # rsync uses argument protection; do not replace this with unquoted remote globs.
  subprocess.run(['rsync','-a','--protect-args','--exclude','inputs/','-e',shlex.join(SSH),n['ssh']+':'+r+'/',str(e)+'/'],check=True)
 save(dest/'collection.json',{'run_id':m['run_id'],'files':files,'generated':True,'technical_pass':False,'visual_acceptance':False});print(dest)
def main():
 p=argparse.ArgumentParser(description=__doc__);sp=p.add_subparsers(dest='action',required=True)
 for action in ['submit','status','collect','stop']:
  s=sp.add_parser(action);s.add_argument('task');s.add_argument('--cluster',required=True)
  if action=='submit':s.add_argument('--nodes',nargs='+',required=True);s.add_argument('--execute',action='store_true');s.add_argument('--project')
  if action=='collect':s.add_argument('--output',required=True)
 s=sp.add_parser('stop-project');s.add_argument('--project',required=True);s.add_argument('--cluster',required=True)
 a=p.parse_args()
 if a.action=='submit' and a.execute and not a.project:p.error('--execute requires --project')
 {'submit':submit,'status':status,'collect':collect,'stop':stop,'stop-project':stop_project}[a.action](a)
if __name__=='__main__':
 try:main()
 except (ValueError,KeyError,OSError,RuntimeError,subprocess.SubprocessError) as e:sys.exit(str(e))
