"""CPU + synthetic media tests. No SSH, production assets or model execution."""
import argparse,contextlib,copy,fcntl,io,json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import recast as r
import native_run as n
class Production(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.p=Path(cls.tmp.name);cls.src=cls.p/'source.mp4'
  r.run([r.tool('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=size=128x72:rate=30000/1001','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-frames:v','12','-t','0.4004','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',cls.src])
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def test_plan_gaps_overlaps_and_review(self):
  d={'source':{'frame_count':12,'fps':'30000/1001'},'segments':[{'id':'S1','start':0,'end':6},{'id':'S2','start':6,'end':12}]};self.assertTrue(r.validate_plan(d))
  for a,b in [(7,12),(5,12),(6,13)]:
   x=copy.deepcopy(d);x['segments'][1].update(start=a,end=b)
   with self.assertRaises(ValueError):r.validate_plan(x)
  with self.assertRaises(ValueError):r.validate_plan(d,True)
 def test_exact_mapping_offsets(self):
  self.assertEqual(r.map_indices(6,'30000/1001','24',632,626),[5,6,6,7,8,9])
  self.assertEqual(len(r.map_indices(240,'30000/1001','24',626,626)),240)
 def test_media_pipeline_and_acceptance_gate(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);proj=p/'project';r.init(argparse.Namespace(project=proj,source=self.src,id='test-film'))
   self.assertEqual(r.read(proj/'project.json')['generation_authorized'],False)
   native=p/'rgb.mp4';r.rgb(argparse.Namespace(source=self.src,output=native,start=0,end=12,fps=24))
   outs=[]
   for start,end in [(0,6),(6,12)]:
    out=p/f'mapped{start}.mp4';r.map_video(argparse.Namespace(input=native,output=out,start=start,end=end,source_fps='30000/1001',native_fps='24',native_source_start=0));outs.append(out)
   plan=r.read(ROOT/'examples/plan.example.json');plan['source']['sha256']=r.sha(self.src)
   for seg,out in zip(plan['segments'],outs):seg['selected_file']=str(out)
   r.save(p/'plan.json',plan);review=p/'review.mp4';args=argparse.Namespace(plan=p/'plan.json',source=self.src,output=review,preview=True)
   r.assemble(args);self.assertEqual(r.frames(r.video(r.probe(review))),12)
   r.qa(argparse.Namespace(input=review,output=p/'qa.json',frames=12,fps='30000/1001',audio_source=self.src))
   report=r.read(p/'qa.json');self.assertTrue(report['technical_pass']);self.assertFalse(report['visual_acceptance'])
   args.output=p/'formal.mp4';args.preview=False
   with self.assertRaises((ValueError,FileNotFoundError)):r.assemble(args)
   self.assertFalse(args.output.exists())
   plan['segments'][0]['selected_file']=str(outs[1]);plan['segments'][0]['review_record']=str(p/'bad-review.json')
   r.save(p/'bad-review.json',{'artifact_sha256':'wrong','technical_pass':True,'visual_acceptance':True,'continuous_playback_complete':True,'reviewer':'test'})
   r.save(p/'plan.json',plan)
   with self.assertRaises(ValueError):r.assemble(args)
 def make_task(self,p):
  rgb=p/'rgb.mp4';r.rgb(argparse.Namespace(source=self.src,output=rgb,start=0,end=12,fps=24))
  r.frame_export(argparse.Namespace(source=self.src,output=p/'frames',frames=[0,6,11]))
  t=r.read(ROOT/'examples/task.example.json');t.update(source=str(self.src),end=12,native_frames=22,rgb_path=str(rgb))
  t['scene_references']=[{'frame':f,'path':str(p/'frames'/f'frame-{f:06}.png')} for f in [0,6,11]]
  t['identities'][0]['path']=str(p/'frames/frame-000000.png')
  for k in ['reference_review','action_review']:t[k]={'passed':True,'reviewer':'synthetic-test-only','at':'2026-10-20','evidence':'synthetic fixture, not real visual acceptance'}
  r.save(p/'task.json',t);target=p/'project/03_提示词与任务/S001/v001';r.build_h3(argparse.Namespace(task=p/'task.json',output=target));return target
 def test_freeze_dryrun_and_duplicate_submission(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);task=self.make_task(p);m=r.verify_task(task);self.assertEqual(m['guide_native_frames'],[0,5,9]);self.assertFalse(m['source_motion_hard_locked'])
   c=r.read(ROOT/'examples/cluster-three.example.json');r.save(p/'cluster.json',c)
   args=argparse.Namespace(task=task,cluster=p/'cluster.json',nodes=['spark-a'],execute=False,project=None)
   with patch.object(n,'ssh',side_effect=AssertionError('no network in dry run')):n.submit(args)
   c['nodes'][0]['ssh']='safe-node';r.save(p/'cluster.json',c)
   r.save(p/'project/project.json',{'project_id':'fixture','generation_authorized':True,'production_state':'producing','source':{'sha256':m['source_sha256']}})
   args.execute=True;args.project=p/'project';r.save(task/'submission.json',{'status':'submission_intent'})
   # Remove remaining placeholder config values for test validation; still no network allowed.
   c=json.loads(json.dumps(c).replace('EDIT_','fixture_'));r.save(p/'cluster.json',c)
   with patch.object(n,'ssh',side_effect=AssertionError('duplicate must not contact host')):
    with self.assertRaises(FileExistsError):n.submit(args)
   # Frozen content change invalidates the task, even though the graph still parses.
   with (task/'inputs'/m['inputs'][0]['remote_name']).open('ab') as f:f.write(b'changed')
   with self.assertRaises(ValueError):r.verify_task(task)
 def test_pair_allocation_has_no_overlap(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);r.save(p/'cluster.json',r.read(ROOT/'examples/cluster-three.example.json'))
   manifests=[{'run_id':'run-a','segment_id':'S1'},{'run_id':'run-b','segment_id':'S2'}]
   args=argparse.Namespace(cluster=p/'cluster.json',tasks=['a','b'],busy=[],mode='paired')
   output=io.StringIO()
   with patch.object(r,'verify_task',side_effect=manifests),contextlib.redirect_stdout(output):r.allocation(args)
   rows=json.loads(output.getvalue())['allocations']
   self.assertEqual(rows[0]['nodes'],['spark-a','spark-b']);self.assertEqual(rows[1]['state'],'waiting_capacity')
   args.mode='independent';output=io.StringIO()
   with patch.object(r,'verify_task',side_effect=manifests),contextlib.redirect_stdout(output):r.allocation(args)
   rows=json.loads(output.getvalue())['allocations'];self.assertFalse(set(rows[0]['nodes']) & set(rows[1]['nodes']))
 def test_stop_project_fences_submissions(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);r.save(p/'project.json',{'project_id':'test','production_state':'producing','generation_authorized':True})
   n.stop_project(argparse.Namespace(project=p,cluster='not-needed-without-jobs'))
   self.assertEqual(r.read(p/'project.json')['production_state'],'stopped_by_user');self.assertFalse(r.read(p/'project.json')['generation_authorized'])
 def test_stop_guard_and_physical_node_dedup(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);r.save(p/'project.json',{'production_state':'stopped_by_user','generation_authorized':True})
   with self.assertRaises(ValueError):n.project_guard(p)
   c=r.read(ROOT/'examples/cluster-two.example.json');c['nodes'][1]['expected_hostname']=c['nodes'][0]['expected_hostname'];r.save(p/'cluster.json',c)
   with self.assertRaises(ValueError):n.cluster(p/'cluster.json')
 def test_remote_lock_and_owned_stop_without_gpu(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'bin').mkdir();smi=p/'bin/nvidia-smi';smi.write_text('#!/bin/sh\nexit 0\n');smi.chmod(0o755)
   driver=p/'fake_runner.py';driver.write_text('import time\ntime.sleep(60)\n')
   comfy=p/'comfy';comfy.mkdir();job=p/'run';job.mkdir();r.save(job/'native-spec.json',{'graph':{},'plan':{}})
   node={'expected_hostname':socket.gethostname(),'gpu_lock':str(p/'gpu.lock'),'comfy_ports':[],'comfy_root':str(comfy),'python':sys.executable,'author_runner':str(driver),'legacy_backend':str(p),'env':{}}
   launch={'node':node,'run_id':'fixture','rank':0,'world_size':1,'master_addr':'127.0.0.1','master_port':29813,'timeout_s':10,'inputs':[],'spec_sha256':r.sha(job/'native-spec.json')};r.save(job/'launch.json',launch)
   env={**os.environ,'PATH':str(p/'bin')+os.pathsep+os.environ['PATH']}
   proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/remote_job.py'),str(job)],env=env)
   try:
    deadline=time.monotonic()+10
    while not (job/'worker.json').exists() and time.monotonic()<deadline:time.sleep(.05)
    self.assertEqual(r.read(job/'worker.json')['state'],'ready')
    with (p/'gpu.lock').open('a') as lock:
     with self.assertRaises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (job/'GO').touch()
    while r.read(job/'worker.json')['state']!='running' and time.monotonic()<deadline:time.sleep(.05)
    self.assertEqual(r.read(job/'worker.json')['state'],'running')
    (job/'STOP').touch();self.assertEqual(proc.wait(10),0);self.assertEqual(r.read(job/'worker.json')['state'],'stopped')
    with (p/'gpu.lock').open('a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   finally:
    if proc.poll() is None:proc.kill();proc.wait()
if __name__=='__main__':unittest.main()
