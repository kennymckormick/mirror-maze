"""Native logged-in AGY ACP image turns, without a custom model API bridge."""
import base64,gzip,hashlib,json,os,queue,re,shutil,signal,subprocess,threading,time
from pathlib import Path
from .agy_protocol import completed_agy_reply,is_finish_tool,prompt_tool_violations,ForbiddenAgyToolError,EmptyAgyReplyError,committed_denied_tool_ids,ensure_fresh_denied_tools
from .common import dump,sha
from .transport import Selection,frame_metadata


def _json(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def native_user_home():
    if os.name=='posix':
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    return Path.home()


def task_policy_directory(home,profile):
    """Select a task namespace inside AGY's documented CLI settings root."""
    name='mirror-maze-'+hashlib.sha256(str(Path(profile).resolve()).encode()).hexdigest()[:24]
    root=Path(home)/'.gemini/antigravity-cli';directory=root/name
    if directory.is_symlink():raise RuntimeError('Task AGY settings namespace is a symlink')
    directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
    owner=directory/'mirror-maze-owner.json';identity={'profile':str(Path(profile).resolve()),'task':'mirror-maze'}
    if owner.exists():
        if json.loads(owner.read_text())!=identity:raise RuntimeError('Task AGY settings namespace has another owner')
    elif any(directory.iterdir()):raise RuntimeError('Refusing to modify an unowned AGY settings namespace')
    else:dump(owner,identity);owner.chmod(0o600)
    return name,directory


def _redact(text):
    text=re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+',r'\1[redacted]',text)
    return re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)[\"\x27]?\s*[:=]\s*[\"\x27]?)[^\s\"\x27,}]+',r'\1[redacted]',text)


class AgyObservation:
    def __init__(self,cfg,case):
        self.cfg,self.case=cfg,Path(case);self.transport=self;self.log=self.case/'wire.json'
        self.calls=json.loads(self.log.read_text())if self.log.exists()else[]
        self.profile=Path(cfg['profile_root']);self.workspace=self.profile/'workspace';self.workspace.mkdir(parents=True,exist_ok=True)
        self.selection=None;self.last_delivery=None;self.round=None

    def set_feed(self,feed):
        self.round=feed['round']
        self.selection=Selection([dict(f,file=f.get('file',Path(f['path']).name),round=f.get('round',self.round-1),index=f.get('index',i))for i,f in enumerate(feed['frames'])])

    def begin_turn(self):
        record={'index':len(self.calls)+1,'kind':'native_agy_acp_image_attachment','observation_round':self.round,
                'main_inference':True,'images':[frame_metadata(f)for f in self.selection.kept],
                'trim_events':list(self.selection.drops),'source_alias':self.cfg['source_alias'],
                'native_client_finished':False,'started_at':time.time(),'no_custom_model_api_bridge':True}
        self.calls.append(record);manifest=self.case/'native-inputs'/f'turn-{record["index"]:06d}.json'
        record['native_input_manifest']=str(manifest.relative_to(self.case));dump(manifest,record);dump(self.log,self.calls)
        return record

    def finish_turn(self,record,session,proof,result):
        for frame,image in zip(self.selection.kept,record['images']):
            if sha(frame['path'])!=image['sha256']:raise RuntimeError('Native AGY input image changed while planning')
        record.update(native_client_finished=True,native_session_id=session,native_output_proof=proof,
                      native_result_sha256=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest(),
                      native_usage=result.get('result',{}).get('usage',{}),seconds=time.time()-record['started_at'])
        dump(self.log,self.calls)
        self.last_delivery={'round':self.round,'request_index':record['index'],'images':record['images'],
                            'trim_events':record['trim_events'],'original_frames':len(self.selection.original),
                            'evidence_type':'native_agy_acp_image_attachments','native_output_proof':proof}

    def close(self):pass


class AgyNative:
    def __init__(self,cfg,case,broker):
        self.cfg,self.case,self.broker=cfg,Path(case),broker;self.thread_id=cfg.get('_resume_agy_native');self.turns=[]
        self.process=None;self.reader=None;self.error_reader=None;self.events=queue.Queue();self.lock=threading.RLock();self.group=None;self.active_prompt_offset=None
        self.json_event_count=0;self.next_id=1;source=self.case/'native.jsonl.gz'
        try:
            if source.exists():
                with gzip.open(source,'rt')as stream:
                    for line in stream:
                        try:event=json.loads(line)
                        except ValueError:continue
                        self.json_event_count+=1
                        if isinstance(event.get('id'),int):self.next_id=max(self.next_id,event['id']+1)
        except EOFError:raise RuntimeError('Truncated native AGY archive requires reconciliation before resume')
        self.raw=gzip.open(source,'at',encoding='utf-8');self.stderr=(self.case/'stderr.log').open('a')
        self.env=os.environ.copy()
        self.env['HOME']=str(native_user_home())
        for name in list(self.env):
            upper=name.upper()
            provider_prefix=upper.startswith(('OPENAI_','ANTHROPIC_','GEMINI_','GOOGLE_GEMINI_','AZURE_OPENAI_'))
            if upper.startswith('AGY_LLM_GATEWAY')or provider_prefix or upper in('AGY_ADC_AUTH','GOOGLE_API_KEY','GOOGLE_GENAI_USE_VERTEXAI','CLOUD_ML_REGION','GOOGLE_CLOUD_BASE_URL')or name.lower()in('http_proxy','https_proxy','all_proxy','no_proxy'):
                self.env.pop(name,None)
        proxy=cfg.get('network_proxy','http://10.251.233.73:5536')
        no_proxy='127.0.0.1,localhost,::1,.byted.org,.bytedance.net'
        self.env.update(HTTP_PROXY=proxy,HTTPS_PROXY=proxy,ALL_PROXY=proxy,NO_PROXY=no_proxy,
                        http_proxy=proxy,https_proxy=proxy,all_proxy=proxy,no_proxy=no_proxy,
                        AGY_CLI_DISABLE_AUTO_UPDATE='1',AGY_CLI_HIDE_ACCOUNT_INFO='1')
        binary=cfg.get('harness_executable')or shutil.which('agy')
        if not binary:self.close();raise RuntimeError('Native AGY executable is missing')
        # ACP bypass mode ignores effective per-tool denials. Keep the outer
        # worker fully automated, but route model permissions through our client.
        policy={'permissions':{'deny':['read_file(*)','write_file(*)','command(*)','read_url(*)','execute_url(*)','mcp(*)'],'allow':[],'ask':[]},'toolPermission':'request-review'}
        cli_dir_name,policy_dir=task_policy_directory(self.env['HOME'],broker.profile)
        dump(policy_dir/'settings.json',policy);(policy_dir/'settings.json').chmod(0o600)
        args=[str(binary),'acp','--stdio','--disable-telemetry','--include-default-customizations=false','--cli_dir_name',cli_dir_name]
        dump(self.case/'native-invocation.json',{'command':args,'cwd':str(broker.workspace),'harness':'agy','native_provider':True,
                                              'native_model':cfg.get('native_model','gemini-3.8-flash-medium'),'network_proxy':proxy,
                                              'credential_source':'existing_native_AGY_login','credential_in_command':False,'started_at':time.time(),
                                              'permission_policy':policy,'settings_file':str(policy_dir/'settings.json'),'human_approval_requests':False})
        try:
            self.process=subprocess.Popen(args,cwd=broker.workspace,env=self.env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                          text=True,encoding='utf-8',bufsize=1,start_new_session=True)
            def read_stdout():
                try:
                    for line in self.process.stdout:
                        try:event=json.loads(line)
                        except ValueError:continue
                        index=self._record(event);self.events.put((index,event))
                finally:self.events.put(None)
            def read_stderr():
                for line in self.process.stderr:self.stderr.write(_redact(line));self.stderr.flush()
            self.reader=threading.Thread(target=read_stdout,daemon=True);self.error_reader=threading.Thread(target=read_stderr,daemon=True);self.reader.start();self.error_reader.start()
            setup_deadline=time.monotonic()+cfg.get('native_setup_timeout',180)
            init=self._control('initialize',{'protocolVersion':1,'clientCapabilities':{'fs':{'readTextFile':False,'writeTextFile':False},'terminal':False},'clientInfo':{'name':'mirror-maze-benchmark','version':'0.4.1'}},setup_deadline)
            if cfg.get('observation_modality')!='text'and not init.get('agentCapabilities',{}).get('promptCapabilities',{}).get('image'):raise RuntimeError('Native AGY does not declare image support')
            # Installed AGY1.5.1 NewSessionMeta declares these bools directly
            # under _meta. They do not belong under _meta.antigravity.
            params={'cwd':str(broker.workspace),'mcpServers':[],
                    '_meta':{'disableBuiltInTools':True,'disable_built_in_tools':True}}
            if self.thread_id:
                params['sessionId']=self.thread_id
                capabilities=init.get('agentCapabilities',{}).get('sessionCapabilities',{})
                method='session/resume'if'resume'in capabilities else'session/load'
                self._control(method,params,setup_deadline)
                token=cfg.get('native_repair_cancel_token')
                if token:
                    if not re.fullmatch(r'[a-zA-Z0-9_-]+',token):raise RuntimeError('Invalid native repair token')
                    receipt=self.case/('native-pending-cancel-'+token+'.json')
                    if not receipt.exists():
                        # Official ACP cancellation is a notification, not a new
                        # inference or an RPC with a response to await.
                        notification={'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':self.thread_id}}
                        self._record({'type':'native_agy_control_notification','notification':notification,'repair_token':token})
                        self._send(notification)
                        dump(receipt,{'session_id':self.thread_id,'token':token,'at':time.time(),'notification_sent':True,'new_model_call':False,'budget_changed':False})
            else:
                session=self._control('session/new',params,setup_deadline);self.thread_id=session.get('sessionId')
                if not self.thread_id:raise RuntimeError('Native AGY did not return a conversation ID')
            dump(self.case/'native-session.json',{'thread_id':self.thread_id,'harness':'agy','native_provider':True})
            self._control('session/set_mode',{'sessionId':self.thread_id,'modeId':'default'},setup_deadline)
            model=cfg.get('native_model',cfg.get('model_label','gemini-3.8-flash-medium'));effort=cfg.get('effort','medium')
            suffix='-'+effort;base=model[:-len(suffix)]if model.endswith(suffix)else model
            self._control('session/set_config_option',{'sessionId':self.thread_id,'configId':'model','value':base},setup_deadline)
            selected=self._control('session/set_config_option',{'sessionId':self.thread_id,'configId':'thought_level','value':effort},setup_deadline)
            options={o['id']:o.get('currentValue')for o in selected.get('configOptions',[])}
            if options.get('model')!=base or options.get('thought_level')!=effort:raise RuntimeError('Native AGY selected an unexpected model or thought level')
            dump(self.case/'native-model-selection.json',{'native_model':model,'selected':options,'native_provider':True,'gateway_used':False})
        except Exception:self.close();raise

    def _record(self,event):
        with self.lock:
            index=self.json_event_count;self.raw.write(_json(event)+'\n');self.raw.flush();self.json_event_count+=1
            if self.group is not None:self.group.append(event)
            return index

    def _allocate(self):
        ident=self.next_id;self.next_id+=1;return ident

    def _send(self,payload):
        self.process.stdin.write(_json(payload)+'\n');self.process.stdin.flush()

    def _deny_server_request(self,event):
        if event['method']=='session/request_permission':
            params=event.get('params',{});call=params.get('toolCall',{});finish=is_finish_tool(call)
            desired='allow_once'if finish else'reject_once'
            choice=next((o for o in params.get('options',[])if o.get('kind')==desired),None)
            outcome={'outcome':'selected','optionId':choice.get('optionId',choice.get('id'))}if choice else{'outcome':'cancelled'}
            reply={'jsonrpc':'2.0','id':event['id'],'result':{'outcome':outcome}}
        else:reply={'jsonrpc':'2.0','id':event['id'],'error':{'code':-32601,'message':'Files, shell, web and MCP are unavailable in this observation-only benchmark'}}
        params=event.get('params',{});call=params.get('toolCall',{})
        self._record({'type':'native_agy_denied_tool_request','method':event['method'],'request_id':event['id'],
                      'tool_call_id':call.get('toolCallId'),'session_id':params.get('sessionId'),
                      'response':reply});self._send(reply)

    def _wait(self,ident,deadline):
        while time.monotonic()<deadline:
            try:item=self.events.get(timeout=max(.1,deadline-time.monotonic()))
            except queue.Empty:raise TimeoutError('native_agy_acp_turn_timeout')
            if item is None:raise RuntimeError('Native AGY ended before completing its RPC response')
            index,event=item
            if 'id'in event and'method'in event:self._deny_server_request(event);continue
            current=self.group is not None and self.active_prompt_offset is not None and index>=self.active_prompt_offset
            if current and event.get('method')=='session/update':
                update=event.get('params',{}).get('update',{})
                # Pending attempts must reach the automatic permission rejection.
                # A genuinely completed forbidden operation remains a hard failure.
                if update.get('sessionUpdate')in('tool_call','tool_call_update')and update.get('status')=='completed':
                    with self.lock:events=list(self.group)[1:]
                    digest=hashlib.sha256(json.dumps(event,sort_keys=True).encode()).hexdigest()
                    violations=[v for v in prompt_tool_violations(events,self.thread_id)if v.get('event_sha256')==digest]
                    if violations:raise ForbiddenAgyToolError(violations)
            if event.get('id')==ident and('result'in event or'error'in event):return event
        raise TimeoutError('native_agy_acp_turn_timeout')

    def _control(self,method,params,deadline):
        ident=self._allocate();self._record({'type':'native_agy_control_request','id':ident,'method':method,'params':params})
        self._send({'jsonrpc':'2.0','id':ident,'method':method,'params':params});response=self._wait(ident,deadline)
        if response.get('error'):raise RuntimeError('Native AGY setup RPC failed: '+_redact(_json(response['error']))[:500])
        return response.get('result',{})

    def ask(self,text,timeout=3600):
        start=time.monotonic();deadline=start+timeout;limit=self.cfg.get('max_body_bytes',self.cfg.get('request_byte_budget',120000000))
        denied_empty_recoveries=0;empty_denied_ids=set()
        while time.monotonic()<deadline:
            ident=self._allocate();images=[{'type':'image','mimeType':'image/png','data':base64.b64encode(Path(f['path']).read_bytes()).decode()}for f in self.broker.selection.kept]
            payload={'jsonrpc':'2.0','id':ident,'method':'session/prompt','params':{'sessionId':self.thread_id,'prompt':[{'type':'text','text':text},*images]}}
            encoded=_json(payload).encode()
            if len(encoded)>limit:
                if self.broker.selection.drop_oldest_round('native_agy_request_byte_budget'):continue
                raise RuntimeError('Native AGY request exceeds budget with its latest view pinned')
            record=self.broker.begin_turn();record.update(native_request_id=ident,native_request_bytes=len(encoded),native_model=self.cfg.get('native_model','gemini-3.8-flash-medium'))
            with self.lock:
                offset=self.json_event_count;self.group=[];self.active_prompt_offset=offset
                self._record({'type':'native_agy_rpc_request','id':ident,'method':'session/prompt','params':{'sessionId':self.thread_id},
                              'observation_round':self.broker.round,'wire_index':record['index'],'images':record['images'],
                              'prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),'request_sha256':hashlib.sha256(encoded).hexdigest(),'native_json_event_offset':offset})
            dump(self.broker.log,self.broker.calls);self._send(payload)
            try:
                response=self._wait(ident,deadline)
                if response.get('error'):
                    message=_redact(_json(response['error']));record['transport_error']=message[:1000];dump(self.broker.log,self.broker.calls)
                    if any(term in message.lower()for term in('request body too large','request entity too large','payload too large','http 413','status 413'))and self.broker.selection.drop_oldest_round('native_agy_body_limit'):
                        continue
                    raise RuntimeError('Native AGY prompt RPC failed: '+message[:500])
                with self.lock:events=list(self.group)
                try:answer,proof,result=completed_agy_reply(events,ident)
                except EmptyAgyReplyError as exc:
                    denied=exc.proof.get('native_verified_denied_tool_attempts',[])
                    ids={d['tool_call_id']for d in denied}
                    record.update(transport_error=str(exc),empty_public_reply=True,planning_charge=0,collision_charge=0,verified_denied_tool_attempts=denied)
                    dump(self.broker.log,self.broker.calls)
                    if not self.cfg.get('recover_verified_denied_empty')or not ids or denied_empty_recoveries>=3:raise
                    if ids.intersection(empty_denied_ids):raise RuntimeError('stale_denied_tool_replay: repeated empty denied intent; operator repair required')
                    empty_denied_ids.update(ids);denied_empty_recoveries+=1
                    with self.lock:self.group=None;self.active_prompt_offset=None
                    notification={'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':self.thread_id}}
                    self._record({'type':'native_agy_control_notification','notification':notification,'reason':'verified_denied_empty_infrastructure','request_id':ident,'observation_round':self.broker.round,'planning_charge':0,'collision_charge':0})
                    self._send(notification)
                    self._control('session/set_mode',{'sessionId':self.thread_id,'modeId':'default'},deadline)
                    continue
                progress=json.loads((self.case/'progress.json').read_text())if(self.case/'progress.json').exists()else{}
                ensure_fresh_denied_tools(proof,committed_denied_tool_ids(progress,self.broker.calls))
                self.broker.finish_turn(record,self.thread_id,proof,result)
                self.turns.append({'seconds':time.monotonic()-start,'native_output_proof':proof});return answer
            finally:
                with self.lock:self.group=None;self.active_prompt_offset=None
        raise TimeoutError('native_agy_acp_turn_timeout')

    def close(self):
        if self.process and self.process.poll()is None:
            try:os.killpg(self.process.pid,signal.SIGTERM);self.process.wait(timeout=10)
            except(ProcessLookupError,subprocess.TimeoutExpired):
                try:self.process.kill();self.process.wait(timeout=5)
                except ProcessLookupError:pass
        if self.reader:self.reader.join(3)
        if self.error_reader:self.error_reader.join(3)
        if hasattr(self,'raw'):self.raw.close()
        if hasattr(self,'stderr'):self.stderr.close()
