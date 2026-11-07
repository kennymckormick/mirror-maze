"""Native Cursor CLI observations and turns, without a model API broker."""
import gzip,hashlib,json,os,queue,shutil,signal,subprocess,threading,time
from pathlib import Path
from .common import dump,sha
from .credentials import cursor_api_key
from .cursor_protocol import completed_cursor_reply
from .cursor_auth_safety import apply_cursor_memory_auth,keychain_safe_cursor_command
from .transport import Selection,frame_metadata


class CursorObservation:
    def __init__(self,cfg,case):
        self.cfg,self.case=cfg,Path(case);self.transport=self;self.log=self.case/'wire.json'
        self.calls=json.loads(self.log.read_text())if self.log.exists()else[]
        self.selection=None;self.last_delivery=None;self.round=None
        self.profile=Path(cfg['profile_root']);self.workspace=self.profile/'workspace'
        self.workspace.mkdir(parents=True,exist_ok=True)

    def set_feed(self,feed):
        self.round=feed['round']
        self.selection=Selection([dict(f,file=f.get('file',Path(f['path']).name),round=f.get('round',self.round-1),index=f.get('index',i))for i,f in enumerate(feed['frames'])])

    def begin_turn(self):
        folder=self.workspace/'observations'/f'turn-{len(self.calls)+1:06d}';folder.mkdir(parents=True,exist_ok=True)
        attachments=[]
        for i,frame in enumerate(self.selection.kept):
            target=folder/f'round-{self.round:04d}-image-{i+1:04d}.png'
            try:os.link(frame['path'],target)
            except OSError:target.write_bytes(Path(frame['path']).read_bytes())
            attachments.append({'path':str(target),'source_file':frame['file'],'sha256':sha(target)})
        record={'index':len(self.calls)+1,'kind':'native_cursor_image_attachment','observation_round':self.round,
                'main_inference':True,'images':[frame_metadata(f)for f in self.selection.kept],
                'attachments':attachments,'trim_events':self.selection.drops,'source_alias':self.cfg['source_alias'],
                'native_client_finished':False,'started_at':time.time(),'no_custom_model_api_bridge':True}
        self.calls.append(record);dump(self.log,self.calls)
        manifest=self.case/'native-inputs'/f'turn-{record["index"]:06d}.json'
        dump(manifest,record);record['native_input_manifest']=str(manifest.relative_to(self.case));dump(self.log,self.calls)
        return record

    def finish_turn(self,record,session,proof,result):
        for item,image in zip(record['attachments'],record['images']):assert sha(item['path'])==image['sha256']
        record.update(native_client_finished=True,native_session_id=session,native_output_proof=proof,
                      native_result_sha256=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest(),
                      native_usage=result.get('usage',{}),seconds=time.time()-record['started_at'])
        dump(self.log,self.calls)
        self.last_delivery={'round':self.round,'request_index':record['index'],'images':record['images'],
                            'trim_events':record['trim_events'],'original_frames':len(self.selection.original),
                            'evidence_type':'native_cursor_cli_image_attachments','native_output_proof':proof}

    def close(self):pass


class CursorNative:
    def __init__(self,cfg,case,broker):
        self.cfg,self.case,self.broker=cfg,Path(case),broker;self.turns=[];self.thread_id=cfg.get('_resume_cursor')
        self.process=None;self.reader=None;self.error_reader=None;self.events=queue.Queue()
        self.credential=cursor_api_key();self.env=os.environ.copy()
        for name in list(self.env):
            if any(x in name.upper()for x in('API_KEY','AUTH_TOKEN','SECRET','PASSWORD','ACCESS_KEY','PRIVATE_KEY'))or name.upper().endswith('_TOKEN')or name.startswith(('OPENAI_','ANTHROPIC_','AGY_','GEMINI_','CODEX_'))or name.lower()in('http_proxy','https_proxy','all_proxy','no_proxy'):self.env.pop(name,None)
        profile=broker.profile;config=profile/'config';home=profile/'home';data=profile/'data'
        for path in(config,home,data):path.mkdir(parents=True,exist_ok=True)
        settings={'version':1,'editor':{'vimMode':False},'permissions':{'allow':[],'deny':['Shell(*)','Write(**)','Read(**)','WebFetch(*)','Mcp(*:*)']},
                  'network':{'useHttp1ForAgent':True},'sandbox':{'readBoundary':'workspace'},'notifications':False,'hints':False}
        dump(config/'cli-config.json',settings)
        proxy=cfg.get('network_proxy','http://127.0.0.1:7890')
        self.env.update(CURSOR_API_KEY=self.credential,CURSOR_API_ENDPOINT='https://api2.cursor.sh',CURSOR_CONFIG_DIR=str(config),CURSOR_DATA_DIR=str(data),
                        HTTP_PROXY=proxy,HTTPS_PROXY=proxy,ALL_PROXY=proxy,NO_PROXY='127.0.0.1,localhost',NODE_USE_ENV_PROXY='1',DISABLE_AUTOUPDATER='1')
        apply_cursor_memory_auth(self.env)
        binary=cfg.get('harness_executable')or shutil.which('agent')
        if not binary:raise RuntimeError('Native Cursor CLI is missing')
        native_args=['--print','--trust','--mode','ask','--disable-indexing','--output-format','stream-json','--model',cfg['native_model'],'--workspace',str(broker.workspace)]
        if cfg.get('execution_mode')=='full_access':native_args+=['--force','--sandbox','disabled']
        self.args=keychain_safe_cursor_command(binary,native_args)
        self.json_event_count=0
        try:
            if(self.case/'native.jsonl.gz').exists():
                with gzip.open(self.case/'native.jsonl.gz','rt')as f:
                    for line in f:
                        try:json.loads(line);self.json_event_count+=1
                        except ValueError:pass
        except EOFError:raise RuntimeError('Truncated native Cursor archive requires reconciliation before resume')
        self.raw=gzip.open(self.case/'native.jsonl.gz','at',encoding='utf-8');self.stderr=(self.case/'stderr.log').open('a')

    def ask(self,text,timeout=3600):
        record=self.broker.begin_turn();args=list(self.args)
        if self.env.get('AGENT_CLI_CREDENTIAL_STORE')!='memory':raise RuntimeError('Cursor authentication guard requires memory-only credentials')
        if self.thread_id:args+=['--resume',self.thread_id]
        for attachment in record['attachments']:args+=['--image',attachment['path']]
        args+=[text];start=time.monotonic();self.events=queue.Queue();events=[];offset=self.json_event_count
        dump(self.case/'native-invocation.json',{'command':args,'cwd':str(self.broker.workspace),'harness':'cursor','model':self.cfg['native_model'],
                                              'network_proxy':self.cfg.get('network_proxy','http://127.0.0.1:7890'),'credential_source':'CURSOR_API_KEY','credential_in_command':False,'started_at':time.time()})
        self.process=subprocess.Popen(args,cwd=self.broker.workspace,env=self.env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      text=True,encoding='utf-8',bufsize=1,start_new_session=True)
        def read_stdout():
            try:
                for line in self.process.stdout:
                    line=line.replace(self.credential,'[redacted]');self.raw.write(line);self.raw.flush()
                    try:event=json.loads(line)
                    except ValueError:continue
                    self.json_event_count+=1;self.events.put(event)
            finally:self.events.put(None)
        def read_stderr():
            for line in self.process.stderr:self.stderr.write(line.replace(self.credential,'[redacted]'));self.stderr.flush()
        self.reader=threading.Thread(target=read_stdout,daemon=True);self.error_reader=threading.Thread(target=read_stderr,daemon=True);self.reader.start();self.error_reader.start()
        terminal=None
        while time.monotonic()-start<timeout:
            try:event=self.events.get(timeout=max(.1,timeout-(time.monotonic()-start)))
            except queue.Empty:raise TimeoutError('cursor_native_turn_timeout')
            if event is None:break
            events.append(event)
            if event.get('type')=='system'and event.get('subtype')=='init':
                self.thread_id=event.get('session_id',self.thread_id)
                selected=event.get('model');expected=self.cfg.get('expected_cursor_model')
                if expected and selected!=expected:raise RuntimeError('Cursor selected an unexpected model: '+str(selected))
                record['native_selected_model']=selected
                dump(self.case/'native-session.json',{'thread_id':self.thread_id,'harness':'cursor'})
            if event.get('type')=='result':terminal=event;break
        if terminal is None:raise RuntimeError('Native Cursor ended without a complete turn result')
        answer,proof,result=completed_cursor_reply(events);proof['native_json_event_offset']=offset
        self.broker.finish_turn(record,self.thread_id,proof,result)
        self.turns.append({'seconds':time.monotonic()-start,'native_output_proof':proof})
        try:self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        self.reader.join(5);self.error_reader.join(5)
        return answer

    def close(self):
        if self.process and self.process.poll()is None:
            try:os.killpg(self.process.pid,signal.SIGTERM);self.process.wait(timeout=10)
            except(ProcessLookupError,subprocess.TimeoutExpired):
                try:self.process.kill();self.process.wait(timeout=5)
                except ProcessLookupError:pass
        if self.reader:self.reader.join(3)
        if self.error_reader:self.error_reader.join(3)
        self.raw.close();self.stderr.close()
