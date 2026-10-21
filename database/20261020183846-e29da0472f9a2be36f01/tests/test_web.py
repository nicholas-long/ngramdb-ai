import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from server import access, app, auth, config, events, files, graph, tasks, worker

NODE = '20240101000000-00000000000000000001'
OTHER = '20240101000000-00000000000000000002'

@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    node = root / 'database' / NODE
    node.mkdir(parents=True)
    (node / 'README.md').write_text('# Example\n\n<script>alert(1)</script>')
    (node / 'data.json').write_text('{"title":"Example"}')
    (node / 'tags.ngram').write_text('example\n')
    (node / 'output.json').write_text('{"ok":true}')
    (node / 'table.csv').write_text('a,b\n1,2\n')
    monkeypatch.setenv('NGRAM_RUNTIME_DIR', str(root / '.ngram-runtime'))
    state = tmp_path / 'private'
    state.mkdir()
    for module in (config, auth, tasks, worker, app, access):
        monkeypatch.setattr(module, 'STATE', state)
    for module in (config, files, graph, events):
        monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(config, 'CONFIG', state / 'config.json')
    ph = PasswordHasher()
    config.atomic(config.CONFIG, {'users': {name: {'passwordHash':ph.hash('test-passphrase'), 'role':role} for name,role in [('owner','operator'),('other','operator'),('viewer','viewer'),('admin','admin')]}, 'repositories':{'test':str(root)}, 'secureCookies':True})
    def cli(*args):
        if args == ('query',): return [NODE]
        return []
    monkeypatch.setattr(graph, 'cli', cli)
    monkeypatch.setattr(graph, 'CACHE', (0,None))
    monkeypatch.setattr(graph, 'NODE_CACHE', (0,None))
    monkeypatch.setattr(graph, 'FILE_CACHE', (0,None))
    monkeypatch.setattr(graph, 'LOOSE_CACHE', (0,None,None))
    auth.SESSIONS.clear()
    auth.ATTEMPTS.clear()
    import logging
    monkeypatch.setattr(access, 'LOGGER', logging.Logger('test.access', level=logging.INFO))
    client = TestClient(app.create_app(), base_url='https://testserver')
    yield client, root, node, state
    for handler in access.LOGGER.handlers:
        handler.close()

def sign(client, user='owner'):
    response = client.post('/api/login', json={'username':user,'password':'test-passphrase'})
    assert response.status_code == 200
    return {'x-csrf-token': response.json()['csrf']}

@pytest.mark.parametrize('url',['/api/nodes','/api/files','/api/graph','/api/activity','/api/events','/api/tasks','/api/nodes/'+NODE+'/file?path=README.md'])
def test_anonymous_denied(env,url):
    assert env[0].get(url).status_code == 401

def test_sessions_csrf_roles(env):
    c,_,_,_=env
    headers=sign(c,'viewer')
    cookie=c.cookies.get(auth.COOKIE)
    assert c.get('/api/nodes').status_code==200
    assert c.get('/api/tasks').status_code==403
    assert c.post('/api/logout',json={}).status_code==403
    assert c.post('/api/logout',json={},headers=headers).status_code==200
    assert c.get('/api/nodes').status_code==401
    sign(c)
    assert c.cookies.get(auth.COOKIE)!=cookie
    assert c.get('/api/me').json()['role']=='operator'

def test_cookie_headers_and_rate_limit(env):
    c=env[0]
    response=c.post('/api/login',json={'username':'owner','password':'test-passphrase'})
    cookie=response.headers['set-cookie']
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert "object-src 'none'" in response.headers['content-security-policy']
    for i in range(7):c.post('/api/login',json={'username':'x','password':'wrong'})
    assert c.post('/api/login',json={'username':'x','password':'wrong'}).status_code==429

def test_origin_and_body_limits(env):
    c=env[0];h=sign(c)
    assert c.post('/api/tasks',headers=dict(h,origin='https://evil.example'),json={}).status_code==403
    assert c.post('/api/tasks',content='x'*40000,headers=h).status_code==413

def test_current_file_without_restart(env):
    c,root,node,_=env;sign(c)
    endpoint='/api/nodes/'+NODE+'/file'
    assert c.get(endpoint,params={'path':'output.json'}).json()['content']=='{"ok":true}'
    (node/'output.json').write_text('{"changed":true}')
    assert c.get(endpoint,params={'path':'output.json'}).json()['content']=='{"changed":true}'
    result=c.get('/api/nodes/'+NODE).json()
    assert result['id']==NODE and result['status']=='unknown'
    assert result['links']['self']=='/nodes/'+NODE
    assert c.get('/api/files?extension=json').json()['total']==2
    assert c.get('/api/files?q=*.csv').json()['total']==1
    assert c.get('/api/nodes?q=example&tag=example').json()['total']==1
    assert c.get('/api/nodes?limit=0').status_code==422

@pytest.mark.parametrize('path',['../README.md','%2e%2e/.env','/etc/passwd','.env','.git/config','credentials','secret.txt','auth.json','id_rsa','private.key','dir/../../data.json','dir\\output.json','a\x00b'])
def test_paths_denied(env,path):
    c,_,node,_=env;sign(c)
    assert c.get('/api/nodes/'+NODE+'/file',params={'path':path}).status_code in (400,403,404)

def test_symlinks_hidden_and_unix_permissions(env):
    c,_,node,state=env;sign(c)
    (state/'outside').write_text('private')
    (node/'escape.txt').symlink_to(state/'outside')
    (node/'escape-dir').symlink_to(state,target_is_directory=True)
    (node/'.env').write_text('PASSWORD=unsafe')
    for path in ['escape.txt','escape-dir/outside']:
        assert c.get('/api/nodes/'+NODE+'/file',params={'path':path}).status_code==404
    listing=c.get('/api/nodes/'+NODE+'/files').json()
    assert not any(f['path'] in ['escape.txt','escape-dir','.env'] for f in listing)
    private=node/'restricted.txt';private.write_text('private');private.chmod(0)
    if os.getuid()!=0:
        assert c.get('/api/nodes/'+NODE+'/file',params={'path':'restricted.txt'}).status_code==404
    private.chmod(0o600)

def test_bounded_preview_and_active_download(env):
    c,_,node,_=env;sign(c)
    (node/'big.txt').write_text('x'*(files.CAP+10))
    (node/'evil.html').write_text('<script>alert(1)</script>')
    p=c.get('/api/nodes/'+NODE+'/file',params={'path':'big.txt'}).json()
    assert p['truncated'] and len(p['content'])==files.CAP
    response=c.get('/api/nodes/'+NODE+'/file',params={'path':'evil.html','download':'true'})
    assert response.headers['content-type']=='application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment')
    assert c.get('/api/nodes/'+NODE+'/file',params={'path':'table.csv'}).json()['rows']==[['a','b'],['1','2']]

def test_public_namespace(env):
    c,_,_,state=env
    assert c.get('/public/anything').status_code==404
    publish=state/'public'/'approved';publish.mkdir(parents=True)
    (publish/'data.json').write_text('{"public":true}')
    assert c.get('/public/approved/data.json').status_code==200
    assert c.get('/public/approved/%2e%2e/config.json').status_code in (403,404)
    (publish/'link.txt').symlink_to(state/'config.json')
    assert c.get('/public/approved/link.txt').status_code==404

def test_task_idempotency_ownership_cancel_review(env):
    c,_,_,_=env;h=sign(c)
    body={'prompt':'inspect this repo','repository':'test','mode':'read-only'}
    headers=dict(h,**{'idempotency-key':'mobile-1'})
    response=c.post('/api/tasks',headers=headers,json=body)
    assert response.status_code==201
    task=response.json();id=task['id']
    assert c.post('/api/tasks',headers=headers,json=body).json()['id']==id
    assert c.post('/api/tasks',headers=headers,json=dict(body,prompt='different')).status_code==409
    sign(c,'other')
    assert c.get('/api/tasks/'+id).status_code==404
    assert c.get('/api/tasks/'+id+'/events').status_code==404
    assert c.get('/api/tasks').json()==[]
    h=sign(c)
    assert c.post('/api/tasks/'+id+'/cancel',headers=h,json={}).json()['status']=='cancelled'
    assert c.post('/api/tasks',headers=h,json=dict(body,repository='/tmp')).status_code==400
    assert c.post('/api/tasks',headers=h,json=dict(body,mode='danger-full-access')).status_code==422
    assert c.post('/api/tasks',headers=h,json=dict(body,prompt='  ')).status_code==422
    assert c.post('/api/tasks/'+id+'/review',headers=h,json={}).status_code==409
    worker.finish(task,status='needs-review')
    assert c.post('/api/tasks/'+id+'/review',headers=h,json={}).json()['reviewedBy']=='owner'

def test_structured_activity_failure_and_stale(env):
    c,root,_,_=env;sign(c)
    event={'timestamp':time.time(),'nodeId':NODE,'runId':'r1','kind':'completed','exitCode':7,'duration':2}
    runtime=root/'.ngram-runtime';runtime.mkdir()
    (runtime/'events.jsonl').write_text(json.dumps(event)+'\nnot-json\n')
    assert c.get('/api/nodes').json()['items'][0]['status']=='failed'
    assert c.get('/api/activity').json()[0]['exitCode']==7
    assert c.get('/api/activity?nodeId='+OTHER).json()==[]
    assert events.status(dict(event,kind='started',timestamp=1))=='stale'
    assert events.status(dict(event,kind='started',pid=999999999,host=__import__('socket').gethostname()))=='stale'

def test_graph_directions_and_focus(env,monkeypatch):
    c=env[0];sign(c)
    snap=graph.snapshot()
    snap['nodes'].append({'id':OTHER,'title':'Other','tags':[]})
    snap['edges']=[{'id':'edge','source':OTHER,'target':NODE,'kind':'dependency'}]
    result=c.get('/api/graph?focus='+NODE+'&depth=1').json()
    assert result['edges'][0]['source']==OTHER
    assert len(result['nodes'])==2
    assert len(c.get('/api/graph?focus='+NODE+'&depth=0').json()['nodes'])==1

def test_worker_fake_codex(env,monkeypatch,tmp_path):
    c,root,_,state=env;h=sign(c)
    subprocess.run(['git','init',str(root)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'-c','user.email=test@example.org','-c','user.name=Test','commit','--allow-empty','-m','initial'],check=True,capture_output=True)
    binary=tmp_path/'bin';binary.mkdir()
    executable=binary/'codex'
    executable.write_text('#!/usr/bin/env python3\nimport pathlib,sys\na=sys.argv\nassert a[1:3]==["exec","--json"]\nassert "--sandbox" in a and "danger-full-access" not in a\npathlib.Path(a[a.index("--output-last-message")+1]).write_text("Reviewed")\nif a[a.index("--sandbox")+1]=="workspace-write": pathlib.Path(a[a.index("-C")+1],"proposal.txt").write_text("proposed")\nprint("{\"event\":\"done\"}")\n')
    # Keep this fake program syntactically simple, valid JSON output is not parsed by worker.
    executable.write_text(executable.read_text().replace('print("{\"event\":\"done\"}")','print("done")'))
    executable.chmod(0o755)
    monkeypatch.setenv('PATH',str(binary)+os.pathsep+os.environ['PATH'])
    for mode in ['read-only','patch']:
        task=c.post('/api/tasks',headers=h,json={'repository':'test','prompt':'test','mode':mode}).json()
        worker.finish(task,status='running')
        worker.execute(task)
        result=c.get('/api/tasks/'+task['id']).json()
        assert result['status']==('needs-review' if mode=='patch' else 'completed')
        assert result['result']=='Reviewed'
        if mode=='patch':assert '+proposed' in result['diff']
    assert not (root/'proposal.txt').exists()


def test_real_scheduler_failure_events_and_dependency_pruning(tmp_path):
    import shutil
    actual = Path(__file__).resolve().parents[3]
    root = tmp_path / 'ngram'
    root.mkdir()
    cli_id = '20240704164832-ae846f0d186e581bb7aa'
    source = actual / 'database' / cli_id
    target = root / 'database' / cli_id
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('*.log','*.gz','__pycache__'))
    (root / 'ngram').symlink_to(target / 'ngram-cli')
    runtime = tmp_path / 'runtime'
    env = dict(os.environ, NGRAM_RUNTIME_DIR=str(runtime))
    def cli(*args):
        return subprocess.run([str(root/'ngram'),*args], cwd=root,env=env,capture_output=True,text=True)
    created = cli('create','--title','Integration fixture','--tag','cron-daily')
    assert created.returncode == 0
    node_id=created.stdout.strip().splitlines()[-1]
    node=root/'database'/node_id
    run=node/'run';run.write_text('#!/bin/bash\nexit 7\n');run.chmod(0o755)
    assert cli('run-single',node_id).returncode==1
    recorded=[json.loads(line) for line in (runtime/'events.jsonl').read_text().splitlines()]
    assert [e['kind'] for e in recorded]==['started','completed']
    assert recorded[-1]['exitCode']==7
    hashes=node/'.ngram.dependencies.hashes'
    assert hashes.is_file() and hashes.stat().st_size>0
    assert hashes.read_text()==cli('dependstats',node_id).stdout
    recorded_before=(runtime/'events.jsonl').read_bytes()
    hash_time=hashes.stat().st_mtime_ns
    assert cli('run-single',node_id).returncode==2
    assert hashes.stat().st_mtime_ns==hash_time
    assert (runtime/'events.jsonl').read_bytes()==recorded_before
    # Expiring the daily hash permits another failed attempt, then skips again.
    expired=time.time()-86400-120
    os.utime(hashes,(expired,expired))
    cli('run','--single')
    attempts=[json.loads(line) for line in (runtime/'events.jsonl').read_text().splitlines()]
    failures=[e for e in attempts if e.get('nodeId')==node_id and e['kind']=='completed']
    assert len(failures)==2 and all(e['exitCode']==7 for e in failures)
    assert hashes.stat().st_mtime>expired
    assert cli('run-single',node_id).returncode==2
    run.write_text('#!/bin/bash\necho stable\n')
    assert cli('run-single',node_id).returncode==0
    assert cli('run-single',node_id).returncode==2
    for name in ['node_modules','.venv','__pycache__','.pytest_cache']:
        ignored=node/name/'nested'
        ignored.mkdir(parents=True)
        f=ignored/'script';f.write_text('#!/bin/bash\necho '+cli_id+'\n');f.chmod(0o755)
    assert 'node_modules' not in cli('checksums',node_id).stdout
    assert 'node_modules' not in cli('find-executable',str(node),'-type','f').stdout
    assert cli_id not in cli('query','id',node_id,'looserefs').stdout
    run.write_text('#!/bin/bash\n# dependency: ' + cli_id + '\necho stable\n')
    assert cli_id in cli('refs',node_id).stdout
    # The service launcher and scheduler remain separate processes.
    service = cli('create','--title','Service integration fixture','--tag','service')
    service_id = service.stdout.strip().splitlines()[-1]
    service_script = root/'database'/service_id/'run-service'
    marker = tmp_path/'service-started'
    service_script.write_text('#!/bin/bash\nprintf started > "' + str(marker) + '"\n')
    service_script.chmod(0o755)
    proc = subprocess.Popen([str(root/'ngram'),'start'],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        deadline=time.monotonic()+10
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.05)
        assert marker.read_text()=='started'
        assert proc.poll() is None
    finally:
        import signal
        os.killpg(proc.pid,signal.SIGTERM)
        proc.wait(5)

@pytest.mark.parametrize('reason',['cancelled','timeout'])
def test_worker_stops_process_group(env,monkeypatch,tmp_path,reason):
    c,root,node,state=env;h=sign(c)
    binary=tmp_path/'bin';binary.mkdir()
    executable=binary/'codex'
    executable.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH',str(binary)+os.pathsep+os.environ['PATH'])
    config_data=config.settings();config_data['taskTimeout']=0
    if reason=='timeout':config.atomic(config.CONFIG,config_data)
    task=c.post('/api/tasks',headers=h,json={'repository':'test','prompt':'test','mode':'read-only'}).json()
    worker.finish(task,status='running',cancelRequested=reason=='cancelled')
    worker.execute(task)
    result=c.get('/api/tasks/'+task['id']).json()
    assert result['status']==('cancelled' if reason=='cancelled' else 'failed')
    assert result['exitCode']!=0


@pytest.mark.parametrize('legacy_uids',[False,True])
def test_worker_starts_as_website_user(env,monkeypatch,legacy_uids):
    monkeypatch.setenv('NGRAM_CODEX_WORKER_ENABLED','1')
    if legacy_uids:
        data=config.settings()
        data.update(webUid=os.getuid(),workerUid=os.getuid())
        config.atomic(config.CONFIG,data)
    monkeypatch.setattr(worker.signal,'signal',lambda *args:None)
    class IdleWorker(Exception):pass
    def idle(seconds):raise IdleWorker()
    monkeypatch.setattr(worker.time,'sleep',idle)
    with pytest.raises(IdleWorker):
        worker.main()
    assert (env[3]/'worker.lock').exists()


def test_worker_remains_opt_in(env,monkeypatch,capsys):
    monkeypatch.delenv('NGRAM_CODEX_WORKER_ENABLED',raising=False)
    worker.main()
    assert 'worker disabled' in capsys.readouterr().out
    assert not (env[3]/'worker.lock').exists()


def test_sse_delivery_and_revocation(env,monkeypatch):
    import asyncio
    c,root,node,state=env
    session={'user':'owner','role':'operator'}
    event={'timestamp':time.time()+1,'nodeId':NODE,'runId':'live','kind':'completed','exitCode':0}
    monkeypatch.setattr(app,'activity',lambda **kw:[event])
    monkeypatch.setattr(auth,'identity',lambda request:session)
    endpoint=next(route.endpoint for route in app.create_app().routes if route.path=='/api/events')
    class Request:
        async def is_disconnected(self):return False
    async def check():
        response=await endpoint(Request(),session)
        frame=await anext(response.body_iterator)
        assert 'event: activity' in frame and NODE in frame
        await response.body_iterator.aclose()
        def revoked(request):
            from fastapi import HTTPException
            raise HTTPException(401)
        monkeypatch.setattr(auth,'identity',revoked)
        response=await endpoint(Request(),session)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)
    asyncio.run(check())


@pytest.mark.parametrize('existing_edit,exit_code',[(False,0),(True,0),(False,7)])
def test_worker_imports_new_cli_created_nodes(env,monkeypatch,tmp_path,existing_edit,exit_code):
    import shutil
    c,root,existing,state=env;headers=sign(c)
    actual=Path(__file__).resolve().parents[3]
    cli_id='20240704164832-ae846f0d186e581bb7aa'
    shutil.copytree(actual/'database'/cli_id,root/'database'/cli_id,ignore=shutil.ignore_patterns('*.log','*.gz','__pycache__'))
    (root/'ngram').symlink_to('database/'+cli_id+'/ngram-cli')
    subprocess.run(['git','init',str(root)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'-c','user.email=test@example.org','-c','user.name=Test','commit','-m','initial'],check=True,capture_output=True)
    binary=tmp_path/'bin';binary.mkdir()
    executable=binary/'codex'
    executable.write_text('''#!/usr/bin/env python3
import os,pathlib,shutil,subprocess,sys
a=sys.argv
work=pathlib.Path(a[a.index('-C')+1])
assert shutil.which('ngram')==str(work/'ngram')
result=subprocess.run(['ngram','create','--title','Task-created collector'],cwd=work,capture_output=True,text=True,check=True)
node_id=result.stdout.strip().splitlines()[-1]
run=work/'database'/node_id/'run'
run.write_text('#!/bin/bash\\necho collector\\n')
run.chmod(0o755)
(run.parent/'.gitignore').write_text('generated.jsonl\\n')
(run.parent/'generated.jsonl').write_text('runtime data')
if os.environ.get('TEST_EXISTING_EDIT')=='1':
    (work/'database'/'20240101000000-00000000000000000001'/'README.md').write_text('Changed existing node')
pathlib.Path(a[a.index('--output-last-message')+1]).write_text('Created '+node_id)
print('done')
sys.exit(int(os.environ.get('TEST_EXIT_CODE','0')))
''')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH',str(binary)+os.pathsep+os.environ['PATH'])
    monkeypatch.setenv('TEST_EXISTING_EDIT','1' if existing_edit else '0')
    monkeypatch.setenv('TEST_EXIT_CODE',str(exit_code))
    task=c.post('/api/tasks',headers=headers,json={'repository':'test','prompt':'create a collector','mode':'patch'}).json()
    worker.finish(task,status='running')
    original=(existing/'README.md').read_text()
    worker.execute(task)
    result=c.get('/api/tasks/'+task['id']).json()
    if exit_code:
        assert result['status']=='failed'
        assert result['importedNodeIds']==[]
        assert len(list((root/'database').iterdir()))==2
        return
    assert result['status']=='completed',result
    assert len(result['importedNodeIds'])==1
    node_id=result['importedNodeIds'][0]
    assert (root/'database'/node_id/'run').read_text()=='#!/bin/bash\necho collector\n'
    assert os.access(root/'database'/node_id/'run',os.X_OK)
    assert not (root/'database'/node_id/'generated.jsonl').exists()
    assert (existing/'README.md').read_text()==('Changed existing node' if existing_edit else original)
    assert result['updatedNodeIds']==([NODE] if existing_edit else [])
    assert set(worker.publish_nodes(result,root,state/'worktrees'/task['id']))==set(result['publishedNodeIds'])


def test_node_import_validates_and_never_overwrites(tmp_path):
    import runpy
    importer=Path(__file__).resolve().parents[3]/'database/20240704164832-ae846f0d186e581bb7aa/subcommands/import-node'
    publish=runpy.run_path(str(importer))['import_node']
    source=tmp_path/'source'/OTHER;source.mkdir(parents=True)
    (source/'README.md').write_text('# Imported')
    (source/'data.json').write_text('{"title":"Imported"}')
    run=source/'run';run.write_text('#!/bin/bash\necho ok\n');run.chmod(0o755)
    (source/'stdout.log').write_text('runtime log')
    (source/'__pycache__').mkdir();(source/'__pycache__'/'cached.pyc').write_text('cache')
    root=tmp_path/'live';(root/'database').mkdir(parents=True)
    (source/'escape').symlink_to(tmp_path/'outside')
    with pytest.raises(OSError):publish(source,root)
    assert not (root/'database'/OTHER).exists()
    (source/'escape').unlink()
    (source/'.env').write_text('private')
    with pytest.raises(ValueError,match='Private'):publish(source,root)
    (source/'.env').unlink()
    assert publish(source,root)==OTHER
    destination=root/'database'/OTHER
    assert not (destination/'stdout.log').exists()
    assert not (destination/'__pycache__').exists()
    assert not list((root/'database').glob('.ngram-import-*'))
    assert publish(source,root)==OTHER
    (source/'README.md').write_text('Conflicting source')
    with pytest.raises(ValueError,match='conflicts'):publish(source,root)
    assert (destination/'README.md').read_text()=='# Imported'
    (destination/'cves.jsonl').write_text('generated data')
    (destination/'stdout.log').write_text('live log')
    assert publish(source,root,overwrite=True)==OTHER
    assert (destination/'README.md').read_text()=='Conflicting source'
    assert (destination/'cves.jsonl').read_text()=='generated data'
    assert (destination/'stdout.log').read_text()=='live log'


def test_task_snapshot_includes_uncommitted_nodes_without_changing_live_index(env):
    _,root,node,state=env
    subprocess.run(['git','init',str(root)],check=True,capture_output=True)
    tracked=root/'tracked.txt';tracked.write_text('committed')
    subprocess.run(['git','-C',str(root),'add','tracked.txt'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'-c','user.email=test@example.org','-c','user.name=Test','commit','-m','initial'],check=True,capture_output=True)
    tracked.write_text('staged')
    subprocess.run(['git','-C',str(root),'add','tracked.txt'],check=True,capture_output=True)
    tracked.write_text('unstaged')
    (node/'.gitignore').write_text('generated.jsonl\n')
    (node/'generated.jsonl').write_text('large generated data stays live')
    (root/'.gitignore').write_text('.env\n')
    (root/'.env').write_text('private configuration stays live')
    index=(root/'.git/index').read_bytes()
    head=worker.git(root,'rev-parse','HEAD')
    work=state/'worktrees'/'snapshot-test';work.parent.mkdir()
    tree=worker.snapshot_worktree(root,work)
    assert (work/'database'/NODE/'README.md').read_text()==(node/'README.md').read_text()
    assert (work/'tracked.txt').read_text()=='unstaged'
    assert not (work/'database'/NODE/'generated.jsonl').exists()
    assert not (work/'.env').exists()
    assert (root/'.git/index').read_bytes()==index
    assert worker.git(root,'rev-parse','HEAD')==head
    assert worker.git(work,'diff','--name-only',tree)==''
    (work/'database'/NODE/'README.md').write_text('task fix')
    assert worker.git(work,'diff','--name-only',tree).strip()=='database/'+NODE+'/README.md'


def test_node_list_and_files_do_not_build_graph_or_read_datasets(env,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    c=env[0];sign(c)
    allowed={'README.md','data.json','tags.ngram'}
    original_read=files.read
    calls=[]
    def read_metadata(node_id,path,*args,**kwargs):
        assert path in allowed, 'Node discovery must not read data files'
        return original_read(node_id,path,*args,**kwargs)
    def query_only(*args):
        calls.append(args)
        assert args==('query',), 'Node/file discovery must not query graph edges'
        return [NODE]
    def no_graph(*args,**kwargs):
        raise AssertionError('This route must not build the graph')
    monkeypatch.setattr(files,'read',read_metadata)
    monkeypatch.setattr(graph,'cli',query_only)
    monkeypatch.setattr(graph,'snapshot',no_graph)
    # Simulate a slow graph refresh holding its lock: metadata still responds.
    with ThreadPoolExecutor(max_workers=1) as pool:
        graph.LOCK.acquire()
        try:
            response=pool.submit(c.get,'/api/nodes').result(timeout=2)
        finally:
            graph.LOCK.release()
    assert response.status_code==200
    assert response.json()['items'][0]['id']==NODE
    assert calls==[('query',)]
    response=c.get('/api/files?extension=json')
    assert response.status_code==200 and response.json()['total']==2
    assert calls==[('query',)]


def test_graph_loose_references_are_opt_in(env,monkeypatch):
    c=env[0];sign(c)
    calls=[]
    def cli(*args):
        calls.append(args)
        if args==('query',):return [NODE,OTHER]
        if args==('docrefs',NODE):return [OTHER]
        return []
    monkeypatch.setattr(graph,'cli',cli)
    # Default graph requests must not need the optional loose-reference lock.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        graph.LOOSE_LOCK.acquire()
        try:
            response=pool.submit(c.get,'/api/graph').result(timeout=2)
        finally:
            graph.LOOSE_LOCK.release()
    assert response.status_code==200
    assert response.json()['edges']==[]
    assert not any(call[0] in ('docrefs','looserefs') for call in calls)
    included=c.get('/api/graph?includeLoose=true').json()
    assert included['edges']==[{'id':NODE+':'+OTHER,'source':NODE,'target':OTHER,'kind':'loose'}]
    assert c.get('/api/graph').json()['edges']==[]


def test_document_references_never_open_large_data_files(tmp_path,monkeypatch,capsys):
    import runpy
    root=tmp_path/'ngram';source=root/'database'/NODE;source.mkdir(parents=True)
    (root/'database'/OTHER).mkdir()
    (source/'README.md').write_text('# Source\n[Target](../'+OTHER+')\n')
    # A sparse multi-GB dataset is cheap to create and must never be opened.
    dataset=source/'cves.jsonl'
    with dataset.open('wb') as f:
        f.write(OTHER.encode())
        f.truncate(2*1024*1024*1024)
    script=Path(__file__).resolve().parents[3]/'database/20240704164832-ae846f0d186e581bb7aa/subcommands/docrefs'
    module=runpy.run_path(str(script))
    monkeypatch.setenv('ngram_root_dir',str(root))
    monkeypatch.setattr(__import__('sys'),'argv',['docrefs',NODE])
    original_open=os.open
    def docs_only(path,*args,**kwargs):
        assert Path(path).suffix.lower()=='.md', 'Documentation lookup opened a dataset'
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(os,'open',docs_only)
    assert module['main']()==0
    assert capsys.readouterr().out.strip()==OTHER


def test_timestamped_access_log(env):
    c, _, _, state = env
    c.get('/api/health?secret=omit')
    c.post('/api/login', headers={'origin': 'https://evil.example'}, json={})
    rows = [json.loads(line) for line in (state / 'access.jsonl').read_text().splitlines()]
    assert [row['status'] for row in rows] == [200, 403]
    assert rows[0]['request'] == 'GET /api/health HTTP/1.1'
    assert rows[0]['timestamp'].endswith('+00:00')
    assert rows[0]['ip'] == 'testclient'
    handler = access.LOGGER.handlers[0]
    handler.doRollover()
    c.get('/api/health')
    assert (state / 'access.jsonl').stat().st_mode & 0o777 == 0o600
    assert len(list(state.glob('access.jsonl*'))) == 2


def test_gzip_preview_bounded_and_download_original(env):
    import gzip
    c, _, node, _ = env
    sign(c)
    payload = b'{"ok":true}\n' * files.CAP
    compressed = gzip.compress(payload)
    (node / 'output.jsonl.gz').write_bytes(compressed)
    endpoint = '/api/nodes/' + NODE + '/file'
    response = c.get(endpoint, params={'path': 'output.jsonl.gz'})
    assert response.status_code == 200
    preview = response.json()
    assert preview['kind'] == 'json' and preview['truncated']
    assert preview['content'].encode() == payload[:files.CAP]
    assert c.get(endpoint, params={'path': 'output.jsonl.gz', 'download': True}).content == compressed
    (node / 'small.csv.gz').write_bytes(gzip.compress(b'a,b\n1,2\n'))
    preview = c.get(endpoint, params={'path': 'small.csv.gz'}).json()
    assert preview['rows'] == [['a', 'b'], ['1', '2']]
    assert not preview['truncated']
    (node / 'broken.txt.gz').write_bytes(b'invalid gzip')
    assert c.get(endpoint, params={'path': 'broken.txt.gz'}).status_code == 422
    (node / 'private.gz').symlink_to(node / 'output.jsonl.gz')
    assert c.get(endpoint, params={'path': 'private.gz'}).status_code == 404


def test_rerun_auth_hash_removal_and_no_execution(env):
    c, _, node, state = env
    endpoint = '/api/nodes/' + NODE + '/rerun'
    hashes = node / '.ngram.dependencies.hashes'
    hashes.write_text('previous hashes')
    (node / 'output.json').write_text('preserve output')
    assert c.post(endpoint, json={}).status_code == 401
    h = sign(c, 'viewer')
    assert c.post(endpoint, json={}, headers=h).status_code == 403
    assert hashes.exists()
    h = sign(c)
    assert c.post(endpoint, json={}).status_code == 403
    assert c.post(endpoint, json={}, headers=dict(h, origin='https://evil.example')).status_code == 403
    assert hashes.exists()
    assert c.post(endpoint, json={}, headers=h).status_code == 200
    assert not hashes.exists()
    assert (node / 'output.json').read_text() == 'preserve output'
    assert c.post(endpoint, json={}, headers=h).status_code == 200
    assert c.post('/api/nodes/' + OTHER + '/rerun', json={}, headers=h).status_code == 404
    assert 'node_rerun_requested' in (state / 'audit.jsonl').read_text()


def test_exact_tag_filter_and_console_files(env):
    c, _, node, _ = env
    sign(c)
    assert c.get('/api/nodes', params={'tag':'example'}).json()['total'] == 1
    assert c.get('/api/nodes', params={'tag':'exam'}).json()['total'] == 0
    (node / 'stderr.log').write_text('collector failed\n')
    (node / 'stdout.log').write_text('starting\n')
    (node / 'run').write_text('#!/bin/sh\n')
    detail = c.get('/api/nodes/' + NODE).json()
    assert {'run', 'stdout.log', 'stderr.log'} <= {f['path'] for f in detail['files']}
    assert c.get('/api/nodes/' + NODE + '/file', params={'path':'stderr.log'}).json()['content'] == 'collector failed\n'
