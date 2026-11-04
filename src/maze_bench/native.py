import gzip,json,os,queue,shutil,signal,subprocess,threading,time
from pathlib import Path
from .common import dump
ROOT=Path(__file__).resolve().parent
PYTHON=Path(__import__("sys").executable)
def command(cfg,case,broker):
 home=case/'harness';home.mkdir(exist_ok=True);workspace=Path(cfg.get('workspace',case/'workspace'));workspace.mkdir(parents=True,exist_ok=True)
 CLAUDE=Path(cfg.get('harness_executable') or shutil.which('claude') or 'claude');AGY=Path(cfg.get('harness_executable') or shutil.which('agy') or 'agy')
 env=os.environ.copy()
 for k in list(env):
  if k.startswith(('ANTHROPIC_','CLAUDE_','AGY_LLM_GATEWAY_','OPENAI_','CODEX_')) or any(t in k.upper()for t in ('API_KEY','AUTH_TOKEN','SECRET','PASSWORD','ACCESS_KEY','PRIVATE_KEY')) or k.upper().endswith('_TOKEN') or k.lower() in ('http_proxy','https_proxy','all_proxy','no_proxy'):env.pop(k,None)
 env.update(NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost',DISABLE_AUTOUPDATER='1',AGY_CLI_DISABLE_AUTO_UPDATE='1')
 mcp={}if cfg.get('direct_observations')else{'maze':{'command':str(PYTHON),'args':[str(ROOT/('observe_server.py' if cfg.get('observation_only') else 'mcp_maze.py')),str(case)]}}
 text='';h=cfg['harness']
 private_profile=Path(cfg.get('profile_root',Path.home()/'.local/share/mirror-maze-benchmark/profiles'/cfg.get('run_id','default')/cfg['case_id'])) if cfg.get('direct_observations')else None
 if private_profile:
  private_profile.mkdir(parents=True,exist_ok=True)
  if h=='codex':
   (private_profile/'codex').mkdir(parents=True,exist_ok=True);env['CODEX_HOME']=str(private_profile/'codex')
  elif h=='claude':env['CLAUDE_CONFIG_DIR']=str(private_profile/'claude')
  else:cfg['_profile_home']=str(private_profile/'agy-home')
 if h=='claude':
  env.update(ANTHROPIC_BASE_URL=broker.base,ANTHROPIC_API_KEY='local-maze-broker',CLAUDE_CONFIG_DIR=str(private_profile/'claude')if private_profile else str(home/'claude'),CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',API_TIMEOUT_MS=str(int(cfg.get('planning_timeout',3600)*1000)),CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS=str(int(cfg.get('planning_timeout',3600)*1000)))
  args=[str(CLAUDE),'--bare','--print','--disable-slash-commands','--model',cfg['model_label'],'--output-format','stream-json','--verbose','--tools','','--strict-mcp-config','--mcp-config',json.dumps({'mcpServers':mcp}),'--setting-sources','','--no-session-persistence','--permission-mode','dontAsk','--allowedTools','mcp__maze__observe,mcp__maze__perform','--',text]
 elif h=='codex':
  env['MAZE_BROKER_TOKEN']='local-maze-broker'
  overrides={'model_provider':'maze_eval','web_search':'disabled','features.shell_tool':False,'features.unified_exec':False,'features.view_image':False,'features.multi_agent':False,'features.image_generation':False,'features.code_mode':False,'project_doc_max_bytes':0,'model_providers.maze_eval.name':'Maze evaluation broker','model_providers.maze_eval.base_url':broker.base+'/v1','model_providers.maze_eval.env_key':'MAZE_BROKER_TOKEN','model_providers.maze_eval.wire_api':'responses','model_providers.maze_eval.request_max_retries':1,'model_providers.maze_eval.stream_max_retries':0,'mcp_servers.maze.command':str(PYTHON),'mcp_servers.maze.args':[str(ROOT/('observe_server.py' if cfg.get('observation_only') else 'mcp_maze.py')),str(case)],'mcp_servers.maze.tool_timeout_sec':3600,'mcp_servers.maze.default_tools_approval_mode':'approve','mcp_servers.maze.tools.perform.output_token_limit':200000}
  args=[str(cfg.get('harness_executable') or shutil.which('codex') or 'codex'),'exec','--ignore-user-config','--skip-git-repo-check','--ephemeral','--json','--sandbox','read-only','-m',cfg['model_label']]
  for k,v in overrides.items():args+=['-c',k+'='+json.dumps(v,ensure_ascii=False)]
  if cfg.get('direct_observations'):
   args=[args[0],*args[1:]]
   clean=[];i=0
   while i<len(args):
    if args[i]=='-c' and i+1<len(args)and args[i+1].startswith('mcp_servers.'):
     i+=2;continue
    clean.append(args[i]);i+=1
   args=clean
  args+=[text]
 else:
  harness_home=Path(cfg.get('_profile_home',home/'isolated-home'));gemini=harness_home/'.gemini';cli=gemini/'antigravity-cli';cli.mkdir(parents=True,exist_ok=True)
  dump(cli/'settings.json',{'permissions':{'deny':['read_file(*)','write_file(*)','read_url(*)','execute_url(*)','command(*)','unsandboxed(*)'],'allow':['mcp(maze/observe)','mcp(maze/perform)']},'disableSlashCommands':True,'model':cfg['model_label'],'trustedWorkspaces':[str(workspace)]})
  dump(cli/'mcp_config.json',{'mcpServers':mcp});dump(gemini/'config/mcp_config.json',{'mcpServers':mcp});dump(workspace/'.agents/mcp_config.json',{'mcpServers':mcp});dump(workspace/'.agents/settings.json',{'permissions':{'allow':['mcp(maze/observe)'],'deny':['read_file(*)','write_file(*)','command(*)','read_url(*)','execute_url(*)']}})
  env.update(HOME=str(harness_home),GEMINI_HOME=str(gemini),ANTIGRAVITY_GEMINI_DIR=str(gemini),ANTIGRAVITY_APP_DATA_DIR=str(cli),AGY_LLM_GATEWAY_URL=broker.base,AGY_LLM_GATEWAY_API_KEY='local-maze-broker',AGY_LLM_GATEWAY_WIRE_PROTOCOL='openai',AGY_LLM_GATEWAY_MODELS=cfg['model_label'],AGY_CLI_HIDE_ACCOUNT_INFO='1')
  args=[str(AGY),'--print',text,'--output-format','stream-json','--disable-slash-commands','--model',cfg['model_label'],'--effort',cfg.get('effort','medium'),'--log-file',str(home/'agy.log')]
 if cfg.get('direct_observations'):
  args=[a for a in args if a!='--no-session-persistence']
  if h=='claude':
   idx=args.index('--allowedTools');args[idx+1]=''
 return args,env,workspace

class Native:
 def __init__(self,cfg,case,broker):
  self.cfg,self.case,self.broker=cfg,case,broker;args,self.env,self.cwd=command(cfg,case,broker);self.args=args;self.thread_id=None;self.process=None;self.single_agy=cfg['harness']=='agy';self.events=queue.Queue();self.stderr=(case/'stderr.log').open('a');self.raw=gzip.open(case/'native.jsonl.gz','at',encoding='utf-8');self.turns=[]
  if cfg['harness']=='claude':
   self.args=args[:-2]+['--input-format','stream-json'];
   if cfg.get('_resume_transcript'):
    self.args=[a for a in self.args if a!='--no-session-persistence']+['--resume',cfg['_resume_transcript']];self.args=[a.replace('mcp__maze__observe,mcp__maze__perform','mcp__maze__observe') for a in self.args]
  elif cfg['harness']=='agy':
   self.args=[args[0],*args[3:]]
   self.thread_id=cfg.get('_resume_conversation')
  else:self.args=[a for a in args[:-1] if a!='--ephemeral']
  dump(case/'native-invocation.json',{'command':self.args,'cwd':str(self.cwd),'harness':cfg['harness'],'effort':cfg.get('effort','native default'),'environment_variable_names':sorted(k for k in self.env if k.startswith(('ANTHROPIC','CLAUDE','AGY_','MAZE_','GEMINI'))),'started_at':time.time()})
  if cfg['harness']=='claude':self.start(self.args)
 def start(self,args):
  self.process=subprocess.Popen(args,cwd=self.cwd,env=self.env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,text=True,encoding='utf-8',bufsize=1,start_new_session=True)
  def read():
   try:
    for line in self.process.stdout:
     self.raw.write(line);self.raw.flush()
     try:self.events.put(json.loads(line))
     except:pass
   finally:self.events.put(None)
  self.reader=threading.Thread(target=read,daemon=True);self.reader.start()
 def ask(self,text,timeout=3600):
  h=self.cfg['harness'];start=time.monotonic();events=[]
  if h=='codex':
   self.events=queue.Queue();args=self.args+(['resume',self.thread_id,'-'] if self.thread_id else ['-']);self.start(args);self.process.stdin.write(text);self.process.stdin.close()
  elif h=='agy':
   self.events=queue.Queue();args=self.args+(['--conversation',self.thread_id]if self.thread_id else[])+['--print',text];self.start(args);self.process.stdin.close()
  else:
   msg={'type':'user','message':{'role':'user','content':[{'type':'text','text':text}]}} if h=='claude' else {'event':'user','message':{'content':text}}
   self.process.stdin.write(json.dumps(msg)+'\n');self.process.stdin.flush()
  final=None;error=None;candidate=None;public_groups={};legacy_header_error=None;current_user_index=None
  while time.monotonic()-start<timeout:
   try:event=self.events.get(timeout=max(.1,timeout-(time.monotonic()-start)))
   except queue.Empty:raise TimeoutError('harness_turn_timeout')
   if event is None:
    if h=='codex' and final is not None:break
    if h=='agy' and candidate is not None:
     self.thread_id=candidate.get('conversation_id',self.thread_id)
     if candidate.get('status')!='SUCCESS':
      response=candidate.get('response','').strip();fresh=bool(response)and current_user_index is not None and any(index>current_user_index and g['done']and response in g['text']and g['usage'].get('output_tokens',0)>0 for index,g in public_groups.items())
      transient=any(marker in str(candidate.get('error')).lower()for marker in ['timeout awaiting response headers','upstream_unavailable','502 bad gateway','temporarily unavailable'])
      if transient and fresh:legacy_header_error=str(candidate['error'])
      else:raise RuntimeError('agy_error: '+str(candidate.get('error'))[:800])
     final=candidate.get('response','');break
    raise RuntimeError('harness_exited: '+str(self.process.poll()))
   events.append(event)
   if h=='claude' and event.get('type')=='system' and event.get('subtype')=='init':self.thread_id=event.get('session_id')
   if h=='agy' and event.get('event')=='step_update':
    update=event.get('step_update',{})
    if update.get('step_type')=='user_input':public_groups={};current_user_index=update.get('step_index')
    if update.get('step_type')=='agent_response':
     group=public_groups.setdefault(update.get('step_index'),{'text':'','done':False,'usage':{}});group['text']+=update.get('text_delta','')
     if update.get('state')=='DONE':group['done']=True;group['usage']=update.get('usage',{})
   if h=='codex':
    if event.get('type')=='thread.started':self.thread_id=event['thread_id']
    if event.get('type')=='item.completed' and event.get('item',{}).get('type')=='agent_message':final=event['item'].get('text','')
    if event.get('type')=='turn.failed':raise RuntimeError('codex_turn_failed: '+str(event.get('error')))
    if event.get('type')=='turn.completed':break
   elif h=='claude' and event.get('type')=='result':
    if event.get('is_error'):raise RuntimeError('claude_error: '+str(event.get('result') or event.get('errors'))[:800])
    final=event.get('result','');break
   elif h=='agy' and event.get('event')=='result':
    candidate=event['result'];self.thread_id=candidate.get('conversation_id',self.thread_id)
  else:raise TimeoutError('harness_turn_timeout')
  if h in('codex','agy'):
   self.process.wait(timeout=20);self.reader.join(10)
  self.turns.append({'seconds':time.monotonic()-start,'native_event_count':len(events),'final':final,'accepted_complete_response_with_legacy_header_error':legacy_header_error})
  return final
 def close(self):
  if self.process and self.process.poll()is None:
   try:self.process.stdin.close();self.process.wait(timeout=5)
   except Exception:
    try:os.killpg(self.process.pid,signal.SIGTERM);self.process.wait(timeout=10)
    except:pass
  if hasattr(self,'reader'):self.reader.join(5)
  self.raw.close();self.stderr.close()
