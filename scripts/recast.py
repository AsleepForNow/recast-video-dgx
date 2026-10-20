#!/usr/bin/env python3
"""Local recast production tools. No network or GPU submission in this entrypoint."""
import argparse,copy,datetime,hashlib,json,math,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path
from fractions import Fraction
ROOT=Path(__file__).resolve().parents[1]
DIRS=['00_原片','01_分镜与动作','02_场景参考','03_提示词与任务','04_生成片段','05_后处理','06_成片','07_质检','09_脚本与流程']
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def read(p): return json.loads(Path(p).read_text())
def save(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 f=p.with_name(p.name+'.tmp-'+str(os.getpid()));f.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');os.replace(f,p)
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def ident(s):
 if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',s):raise ValueError('ID requires 1-64 letters, digits, hyphen, underscore')
 return s
def tool(n):
 x=os.environ.get(n.upper(),n)
 if not shutil.which(x):raise ValueError('Missing '+n+'; set '+n.upper()+' to its executable path')
 return x
def run(args,**kw):return subprocess.run([str(x) for x in args],check=True,**kw)
def probe(p):
 return json.loads(subprocess.check_output([tool('ffprobe'),'-v','error','-count_frames','-show_streams','-show_format','-of','json',str(p)],text=True))
def video(d):return next(x for x in d['streams'] if x['codec_type']=='video')
def frames(v):return int(v.get('nb_read_frames') or v.get('nb_frames'))
def source_info(p):
 d=probe(p);v=video(d)
 if Fraction(v['r_frame_rate'])!=Fraction(v['avg_frame_rate']):raise ValueError('VFR or inconsistent rate: establish a source timeline before using CFR helpers')
 return {'path':str(Path(p).resolve()),'sha256':sha(p),'frame_count':frames(v),'fps':v['avg_frame_rate'],'width':v['width'],'height':v['height'],'probe':d}
def resolve(base,p):
 p=Path(p);return p.resolve() if p.is_absolute() else (Path(base)/p).resolve()
def validate_plan(d,require_review=False):
 n=d['source']['frame_count'];cursor=0;seen=set()
 if type(n)!=int or n<=0:raise ValueError('positive source frame_count required')
 for s in d['segments']:
  ident(s['id'])
  if s['id'] in seen:raise ValueError('duplicate segment ID')
  seen.add(s['id'])
  a,b=s['start'],s['end']
  if type(a)!=int or type(b)!=int or a!=cursor or b<=a or b>n:raise ValueError('gap, overlap, invalid range at '+s['id'])
  if require_review and not s.get('action_review',{}).get('passed'):raise ValueError('unreviewed shot/action: '+s['id'])
  cursor=b
 if cursor!=n:raise ValueError('plan must cover every source frame exactly once')
 return True

def reviewed(r,label):
 if not isinstance(r,dict) or not r.get('passed') or not r.get('reviewer') or not r.get('evidence') or not r.get('at'):raise ValueError('Actual review record required: '+label)
def init(a):
 p=Path(a.project).resolve()
 if p.exists():raise ValueError('New project folder must not exist; existing projects are read in place')
 info=source_info(a.source);p.mkdir(parents=True)
 for d in DIRS:(p/d).mkdir()
 src=p/DIRS[0]/('source'+Path(a.source).suffix.lower());shutil.copyfile(a.source,src)
 info['path']=str(src.relative_to(p));save(p/'project.json',{'schema_version':1,'project_id':ident(a.id),'source':info,'roles':{},'production_state':'planning','generation_authorized':False})
 save(p/DIRS[1]/'plan.json',{'schema_version':1,'source':{k:info[k] for k in ['frame_count','fps','sha256']},'segments':[]})
 print(p)
def frame_export(a):
 p=Path(a.output);p.mkdir(parents=True,exist_ok=True)
 for n in a.frames:
  if n<0:raise ValueError('negative frame')
  run([tool('ffmpeg'),'-v','error','-n','-i',a.source,'-vf',f'select=eq(n\,{n})','-frames:v','1',p/f'frame-{n:06}.png'])
  if not (p/f'frame-{n:06}.png').exists():raise ValueError('frame outside decoded video')
def rgb(a):
 info=source_info(a.source)
 if not 0<=a.start<a.end<=info['frame_count']:raise ValueError('invalid source interval')
 rate=Fraction(info['fps']);duration=Fraction(a.end-a.start,1)/rate
 length=5+17*max(0,math.ceil((math.ceil(duration*a.fps)-5)/17))
 vf=f'trim=start_frame={a.start}:end_frame={a.end},setpts=PTS-STARTPTS,fps={a.fps},scale=640:360,pad=640:368:0:4:black,tpad=stop_mode=clone:stop_duration={float(length/a.fps)+1}'
 cmd=[tool('ffmpeg'),'-v','error','-n','-i',a.source,'-vf',vf,'-frames:v',str(length),'-an','-c:v','libx264','-crf','14',a.output]
 # Retain musical reference only if source has audio; final audio is restored from source separately.
 if any(s['codec_type']=='audio' for s in info['probe']['streams']):
  cmd=[tool('ffmpeg'),'-v','error','-n','-i',a.source,'-vf',vf,'-af',f'atrim=start={float(Fraction(a.start,1)/rate)}:end={float(Fraction(a.end,1)/rate)},asetpts=PTS-STARTPTS,apad','-frames:v',str(length),'-t',str(length/a.fps),'-c:v','libx264','-crf','14','-c:a','aac',a.output]
 run(cmd);save(str(a.output)+'.json',{'source_sha256':info['sha256'],'source_start':a.start,'source_end':a.end,'source_fps':info['fps'],'native_fps':a.fps,'native_frames':length,'soft_reference_only':True})

def build_h3(a):
 taskfile=Path(a.task).resolve();t=read(taskfile);base=taskfile.parent
 ident(t['run_id']);ident(t['segment_id']);ident(t['version']);reviewed(t.get('action_review'),'complete action');reviewed(t.get('reference_review'),'references')
 p=Path(a.output).resolve()
 if p.exists():raise ValueError('Immutable task output exists')
 source=source_info(resolve(base,t['source']))
 if not 0<=t['start']<t['end']<=source['frame_count'] or Fraction(source['fps'])!=Fraction(t['source_fps']):raise ValueError('source interval or FPS mismatch')
 sf=Fraction(t['source_fps']);nf=Fraction(t.get('native_fps','24'));length=int(t['native_frames'])
 if nf!=24 or length<5 or (length-5)%17:raise ValueError('This H3 template requires 24fps and length=5+17k')
 if length<math.ceil(Fraction(t['end']-t['start'],1)/sf*nf):raise ValueError('insufficient native frames')
 refs=t['scene_references'];roles=t['identities']
 if not refs or not roles:raise ValueError('scene references and identity portraits required')
 if len(refs)>10 or len(roles)>10:raise ValueError('template ID slots exceeded')
 if t.get('continuation'):
  c=t['continuation'];reviewed(c.get('review'),'actual prior tail')
  if not c.get('predecessor_run') or not c.get('tail_sha256') or sha(resolve(base,c['tail_path']))!=c['tail_sha256']:raise ValueError('actual prior tail is missing or changed')
  if not any(resolve(base,x['path'])==resolve(base,c['tail_path']) and x['frame']==t['start'] for x in refs):raise ValueError('continuation first reference must be actual inspected prior tail')
 g=read(ROOT/'assets/h3-api-base.json');p.mkdir(parents=True);(p/'inputs').mkdir();files=[];guide=[];image_keys=[]
 def add(src,remote,node,geometry=False):
  src=resolve(base,src)
  if not src.is_file():raise ValueError('Missing input '+str(src))
  dest=p/'inputs'/remote
  if geometry:run([tool('ffmpeg'),'-v','error','-n','-i',src,'-vf','scale=1280:720,pad=1280:736:0:8:black','-frames:v','1',dest])
  else:shutil.copyfile(src,dest)
  files.append({'path':str(dest.relative_to(p)),'remote_name':remote,'sha256':sha(dest),'source_sha256':sha(src)})
  if node:g[node]={'class_type':'LoadImage','inputs':{'image':remote}};image_keys.append([node,0])
 for i,r in enumerate(refs):
  if not t['start']<=r['frame']<t['end']:raise ValueError('reference frame outside interval')
  k=str(100+i);idx=round(Fraction(r['frame']-t['start'],1)/sf*nf)
  if idx>=length:raise ValueError('guide beyond generation')
  if idx in [x[0] for x in guide]:raise ValueError('references collapse to same native guide frame')
  add(r['path'],t['run_id']+f'-scene-{r["frame"]}.png',k,True);guide.append((idx,[k,0]))
 for i,r in enumerate(roles):add(r['path'],t['run_id']+f'-identity-{i}'+Path(r['path']).suffix,str(120+i))
 rgb_path=resolve(base,t['rgb_path']);rgb_evidence=read(str(rgb_path)+'.json')
 if rgb_evidence['source_sha256']!=source['sha256'] or rgb_evidence['source_start']!=t['start'] or rgb_evidence['source_end']!=t['end']:raise ValueError('RGB source provenance mismatch')
 save(p/'rgb-provenance.json',rgb_evidence);add(rgb_path,t['run_id']+'-rgb.mp4',None)
 v=video(probe(rgb_path))
 if Fraction(v['avg_frame_rate'])!=nf or frames(v)<length:raise ValueError('RGB reference rate/count mismatch')
 descriptions='\n'.join(f'<Subject {i+1}> is {r["name"]} from Picture{len(refs)+i+1}. Costume: {r["wardrobe"]}. Position/source performer: {r["track"]}.' for i,r in enumerate(roles))
 prompt='subject_definitions:\n'+descriptions+'\nscene_references:\n'+'\n'.join(f'Picture{i+1}: source frame {r["frame"]} at {float(Fraction(r["frame"]-t["start"],1)/sf):.4f}s.' for i,r in enumerate(refs))+'\nVideo1 is source RGB motion and framing, a soft reference only. Source faces are not target identities. Identity portrait clothing must not enter the scene.\ninterval_actions:\n'+t['action_prompt']+'\nPreserve source action timing, camera, clothing and occlusion; no new gestures, new cuts or identity changes.'
 g['35']={'class_type':'VHS_LoadVideo','inputs':{'video':t['run_id']+'-rgb.mp4','force_rate':0,'custom_width':640,'custom_height':368,'frame_load_cap':length,'skip_first_frames':0,'select_every_nth':1,'format':'None'}}
 inp={'clip':['5',1],'vae':['8',0],'audio_vae':['9',0],'prompt':prompt,'width':1280,'height':736,'length':length,'ref_image_size':'match','ref_videos.ref_video_0':['35',0]}
 if any(s['codec_type']=='audio' for s in probe(rgb_path)['streams']):inp['ref_audios.ref_audio_0']=['35',2]
 for i,key in enumerate(image_keys):inp[f'ref_images.ref_image_{i}']=key
 g['11']={'class_type':'MiniMaxH3ReferenceToVideo','inputs':inp}
 g['45']={'class_type':'ImageScale','inputs':{'image':['20',0],'upscale_method':'area','width':640,'height':368,'crop':'disabled'}}
 g['46']=copy.deepcopy(g['11']);g['46']['inputs'].update(width=1920,height=1088,prompt=prompt.replace('Video1 is source RGB motion and framing, a soft reference only. Source faces are not target identities.', 'Video1 is the actual first-pass recast output, preserve target identities and its temporal sequence; reference constraints remain soft.'))
 g['46']['inputs']['ref_videos.ref_video_0']=['45',0]
 for offset,cond,latent,guider in [(60,'11',['11',1],'12'),(70,'46',['28',0],'13')]:
  previous=[cond,0]
  for i,(frame,key) in enumerate(guide):
   k=str(offset+i);g[k]={'class_type':'MiniMaxH3AddGuide','inputs':{'positive':previous,'latent':latent,'vae':['8',0],'image':key,'frame_idx':frame}};previous=[k,0]
  g[guider]['inputs']['conditioning']=previous
 split=int(t.get('first_stage_split',2))
 if not 1<=split<=10:raise ValueError('split outside qualified template range')
 g['16']['inputs']['step']=split;g['17']['inputs']['noise_seed']=int(t['seed'])
 for k,label in [('22','first'),('33','second')]:g[k]['inputs']['filename_prefix']='recast/'+t['run_id']+'-'+label
 for k,changes in t.get('model_overrides',{}).items():
  if k not in ['1','4','5','6','8','9','25']:raise ValueError('model override node not allowed')
  g[k]['inputs'].update(changes)
 validate_graph(g)
 save(p/'H3_API.json',g);save(p/'native-spec.json',{'job':t['run_id'],'graph':g,'plan':{'output_ids':['22','33']},'evidence':True,'extra_pnginfo':{}})
 save(p/'task.json',t)
 manifest={'schema_version':1,'run_id':t['run_id'],'segment_id':t['segment_id'],'version':t['version'],'source_sha256':source['sha256'],'source_start':t['start'],'source_end':t['end'],'source_fps':str(sf),'native_fps':str(nf),'native_frames':length,'native_source_start':t['start'],'guide_native_frames':[x[0] for x in guide],'inputs':files,'graph_sha256':sha(p/'H3_API.json'),'spec_sha256':sha(p/'native-spec.json'),'task_sha256':sha(p/'task.json'),'rgb_provenance_sha256':sha(p/'rgb-provenance.json'),'reference_review':t['reference_review'],'action_review':t['action_review'],'source_motion_hard_locked':False}
 save(p/'manifest.json',manifest);print(p)
def validate_graph(g):
 for k,n in g.items():
  if not isinstance(n.get('class_type'),str) or not isinstance(n.get('inputs'),dict):raise ValueError('invalid API node '+k)
  for v in n['inputs'].values():
   if isinstance(v,list) and len(v)==2 and isinstance(v[0],str) and type(v[1])==int and v[0] not in g:raise ValueError('dangling edge '+k+' -> '+v[0])
def verify_task(p):
 p=Path(p);m=read(p/'manifest.json');ident(m['run_id']);ident(m['segment_id']);ident(m['version'])
 for f in m['inputs']:
  if Path(f['path']).is_absolute() or '..' in Path(f['path']).parts or not re.fullmatch(r'[A-Za-z0-9_.-]+',f['remote_name']):raise ValueError('invalid input path/name')
  if sha(p/f['path'])!=f['sha256']:raise ValueError('frozen input changed')
 for file,key in [('H3_API.json','graph_sha256'),('native-spec.json','spec_sha256'),('task.json','task_sha256'),('rgb-provenance.json','rgb_provenance_sha256')]:
  if sha(p/file)!=m[key]:raise ValueError('frozen graph/spec/task changed')
 reviewed(m.get('reference_review'),'references');reviewed(m.get('action_review'),'actions');validate_graph(read(p/'H3_API.json'));return m

def allocation(a):
 c=read(a.cluster);out=[];busy=set(a.busy);claimed=set();needed=2 if a.mode=='paired' else 1
 hosts=[n['id'] for n in c['nodes'] if n.get('enabled',True)]
 if len(hosts)!=len(set(hosts)):raise ValueError('duplicate node ID')
 for path in a.tasks:
  m=verify_task(path)
  if m['segment_id'] in claimed:raise ValueError('two versions of same segment in allocation')
  claimed.add(m['segment_id']);free=[x for x in hosts if x not in busy]
  if len(free)<needed:out.append({'run_id':m['run_id'],'state':'waiting_capacity'});continue
  selected=free[:needed];busy.update(selected);out.append({'run_id':m['run_id'],'segment_id':m['segment_id'],'nodes':selected,'world_size':needed,'state':'planned_not_submitted'})
 print(json.dumps({'observed_busy':a.busy,'allocations':out,'live_preflight_required':True},ensure_ascii=False,indent=2))
def map_indices(count,source_fps,native_fps,source_start=0,native_source_start=0):
 sf=Fraction(source_fps);nf=Fraction(native_fps)
 return [round(Fraction(source_start+i-native_source_start,1)/sf*nf) for i in range(count)]
def map_video(a):
 count=a.end-a.start
 if count<=0:raise ValueError('invalid interval')
 v=video(probe(a.input));nf=Fraction(v['avg_frame_rate'])
 if nf!=Fraction(a.native_fps):raise ValueError('actual native FPS mismatch')
 idx=map_indices(count,a.source_fps,a.native_fps,a.start,a.native_source_start)
 if min(idx)<0 or max(idx)>=frames(v):raise ValueError('native coverage missing; do not clone source faces or silently clamp')
 out=Path(a.output)
 if out.exists():raise ValueError('output exists')
 # Decode/encode streamed frames. Exact round-half-even nearest mapping, not FFmpeg fps filter policy.
 w,h=v['width'],v['height'];size=w*h*3
 dec=subprocess.Popen([tool('ffmpeg'),'-v','error','-i',a.input,'-frames:v',str(max(idx)+1),'-f','rawvideo','-pix_fmt','rgb24','-an','pipe:1'],stdout=subprocess.PIPE)
 enc=subprocess.Popen([tool('ffmpeg'),'-v','error','-n','-f','rawvideo','-pix_fmt','rgb24','-s',f'{w}x{h}','-r',a.source_fps,'-i','pipe:0','-an','-c:v','libx264','-crf','15','-pix_fmt','yuv420p',str(out)],stdin=subprocess.PIPE)
 try:
  j=-1;data=None
  for target in idx:
   while j<target:
    data=dec.stdout.read(size);j+=1
    if len(data)!=size:raise ValueError('short native decode')
   enc.stdin.write(data)
  enc.stdin.close()
  if enc.wait()!=0:raise ValueError('mapping encode failed')
 finally:
  dec.stdout.close()
  if dec.poll() is None:dec.terminate()
  dec.wait()
  if enc.poll() is None:enc.kill();enc.wait()
 if frames(video(probe(out)))!=count:raise ValueError('mapped frame count mismatch')
 save(str(out)+'.mapping.json',{'source_start':a.start,'source_end':a.end,'source_fps':a.source_fps,'native_fps':a.native_fps,'native_source_start':a.native_source_start,'indices':idx,'policy':'nearest round-half-even; no optical flow','input_sha256':sha(a.input),'output_sha256':sha(out)})

def qa(a):
 info=probe(a.input);v=video(info);f=frames(v);rate=Fraction(v['avg_frame_rate']);checks={'frames':f==a.frames,'fps':rate==Fraction(a.fps),'decode':False};audio=None
 result=run([tool('ffmpeg'),'-v','error','-i',a.input,'-f','null','-'],capture_output=True,text=True);checks['decode']=not result.stderr.strip()
 result=run([tool('ffmpeg'),'-v','info','-i',a.input,'-vf','blackdetect=d=0.02:pix_th=0.02','-an','-f','null','-'],capture_output=True,text=True)
 black=[l.strip() for l in result.stderr.splitlines() if 'black_start:' in l]
 if a.audio_source:
  def pcm(p):return subprocess.check_output([tool('ffmpeg'),'-v','error','-i',p,'-map','0:a:0','-f','s16le','-acodec','pcm_s16le','-ar','48000','-ac','2','pipe:1'])
  x,y=pcm(a.audio_source),pcm(a.input);audio={'method':'full decoded 48k stereo PCM equality','source_sha256':hashlib.sha256(x).hexdigest(),'output_sha256':hashlib.sha256(y).hexdigest(),'exact':x==y};checks['audio_exact']=x==y
 d={'at':now(),'artifact_sha256':sha(a.input),'checks':checks,'technical_pass':all(checks.values()),'black_candidates':black,'black_review_pending':bool(black),'audio':audio,'visual_acceptance':False,'dynamic_review':'pending'};save(a.output,d);print(json.dumps(d,indent=2,ensure_ascii=False))
def assemble(a):
 plan=read(a.plan);validate_plan(plan);base=Path(a.plan).resolve().parent
 actual_source=source_info(a.source)
 if plan['source'].get('sha256')!=actual_source['sha256'] or plan['source']['frame_count']!=actual_source['frame_count'] or Fraction(plan['source']['fps'])!=Fraction(actual_source['fps']):raise ValueError('assembly source hash/clock mismatch')
 if Path(a.output).exists():raise ValueError('output exists')
 paths=[];shape=None;codec=None
 for s in plan['segments']:
  if not s.get('selected_file'):raise ValueError('missing generated segment '+s['id'])
  p=resolve(base,s['selected_file']);v=video(probe(p))
  if frames(v)!=s['end']-s['start'] or Fraction(v['avg_frame_rate'])!=Fraction(plan['source']['fps']):raise ValueError('selected mapped interval mismatch '+s['id'])
  cur=(v['width'],v['height'],v['codec_name'],v.get('pix_fmt'))
  if shape is None:shape=cur
  if cur!=shape:raise ValueError('normalize dimensions/codec/pix_fmt before assembly')
  if not a.preview:
   q=read(resolve(base,s['review_record']))
   if q.get('artifact_sha256')!=sha(p) or not q.get('technical_pass') or not q.get('visual_acceptance') or not q.get('continuous_playback_complete') or not q.get('reviewer'):raise ValueError('segment not visually accepted '+s['id'])
  paths.append(p)
 if any("'" in str(p) or '\n' in str(p) for p in paths):raise ValueError('concat paths cannot contain apostrophe/newline')
 with tempfile.TemporaryDirectory() as tmp:
  listing=Path(tmp)/'concat.txt';listing.write_text(''.join("file '"+str(p)+"'\n" for p in paths))
  silent=Path(tmp)/'video.mp4'
  run([tool('ffmpeg'),'-v','error','-n','-f','concat','-safe','0','-i',listing,'-an','-c:v','libx264','-crf','15','-pix_fmt','yuv420p','-fps_mode','passthrough',silent])
  # Never -shortest; it may silently drop the final video frames. Keep source audio packets.
  run([tool('ffmpeg'),'-v','error','-n','-i',silent,'-i',a.source,'-map','0:v:0','-map','1:a:0','-c','copy','-movflags','+faststart',a.output])
 actual=video(probe(a.output))
 if frames(actual)!=plan['source']['frame_count'] or Fraction(actual['avg_frame_rate'])!=Fraction(plan['source']['fps']):raise ValueError('assembled clock mismatch')
 save(str(a.output)+'.assembly.json',{'at':now(),'preview':a.preview,'visual_acceptance':False,'full_dynamic_review':'pending','segments':[{'id':s['id'],'start':s['start'],'end':s['end'],'file':str(p),'sha256':sha(p)} for s,p in zip(plan['segments'],paths)],'source_sha256':sha(a.source),'output_sha256':sha(a.output),'audio':'source packet copy; run qa --audio-source to verify'})
def report(a):
 p=Path(a.project);data=read(p/'project.json');lines=['# 制作状态','',f'工程：{data["project_id"]}',f'生产状态：{data["production_state"]}','', '|任务|文件完整性|技术检查|动态验收|','|---|---|---|---|']
 for m in sorted((p/'03_提示词与任务').glob('**/manifest.json')):
  try:t=verify_task(m.parent);ok='通过'
  except Exception as e:t=read(m);ok=str(e)
  qpath=p/'07_质检'/(t['run_id']+'.json');q=read(qpath) if qpath.exists() else {}
  lines.append(f'|{t["run_id"]}|{ok}|{q.get("technical_pass","待检查")}|{q.get("visual_acceptance","待验收")}|')
 Path(a.output).write_text('\n'.join(lines)+'\n')
def main():
 p=argparse.ArgumentParser(description=__doc__);sp=p.add_subparsers(dest='command',required=True)
 s=sp.add_parser('init');s.add_argument('project');s.add_argument('--source',required=True);s.add_argument('--id',required=True);s.set_defaults(fn=init)
 s=sp.add_parser('probe');s.add_argument('input');s.set_defaults(fn=lambda a:print(json.dumps(source_info(a.input),ensure_ascii=False,indent=2)))
 s=sp.add_parser('validate-plan');s.add_argument('plan');s.add_argument('--require-review',action='store_true');s.set_defaults(fn=lambda a:print(validate_plan(read(a.plan),a.require_review)))
 s=sp.add_parser('frames');s.add_argument('source');s.add_argument('output');s.add_argument('--frames',nargs='+',type=int,required=True);s.set_defaults(fn=frame_export)
 s=sp.add_parser('rgb');s.add_argument('source');s.add_argument('output');s.add_argument('--start',type=int,required=True);s.add_argument('--end',type=int,required=True);s.add_argument('--fps',type=int,default=24);s.set_defaults(fn=rgb)
 s=sp.add_parser('build-h3');s.add_argument('task');s.add_argument('output');s.set_defaults(fn=build_h3)
 s=sp.add_parser('verify-task');s.add_argument('task');s.set_defaults(fn=lambda a:print(json.dumps(verify_task(a.task),ensure_ascii=False,indent=2)))
 s=sp.add_parser('allocate');s.add_argument('--cluster',required=True);s.add_argument('--tasks',nargs='+',required=True);s.add_argument('--busy',nargs='*',default=[]);s.add_argument('--mode',choices=['independent','paired'],default='independent');s.set_defaults(fn=allocation)
 s=sp.add_parser('map');s.add_argument('input');s.add_argument('output');s.add_argument('--start',required=True,type=int);s.add_argument('--end',required=True,type=int);s.add_argument('--source-fps',required=True);s.add_argument('--native-fps',default='24');s.add_argument('--native-source-start',required=True,type=int);s.set_defaults(fn=map_video)
 s=sp.add_parser('qa');s.add_argument('input');s.add_argument('output');s.add_argument('--frames',type=int,required=True);s.add_argument('--fps',required=True);s.add_argument('--audio-source');s.set_defaults(fn=qa)
 s=sp.add_parser('assemble');s.add_argument('plan');s.add_argument('source');s.add_argument('output');s.add_argument('--preview',action='store_true');s.set_defaults(fn=assemble)
 s=sp.add_parser('report');s.add_argument('project');s.add_argument('output');s.set_defaults(fn=report)
 a=p.parse_args();a.fn(a)
if __name__=='__main__':
 try:main()
 except (ValueError,KeyError,OSError,subprocess.SubprocessError) as e:sys.exit(str(e))
