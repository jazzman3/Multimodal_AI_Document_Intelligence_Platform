"""Live localhost HTTP benchmark; no cloud or remote calls."""
import concurrent.futures
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timezone
from pathlib import Path

import httpx
from tests.test_core import pdf,INVOICE

ROOT=Path(__file__).resolve().parent

def percentile(items,p):
    arr=sorted(items);index=(len(arr)-1)*p/100
    lo=int(index);hi=min(lo+1,len(arr)-1)
    return arr[lo]+(arr[hi]-arr[lo])*(index-lo)

def run():
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    with tempfile.TemporaryDirectory() as tmp:
        env={**os.environ,'DATABASE_PATH':str(Path(tmp)/'bench.db'),'ANALYST_KEY':'benchmark-analyst','REVIEWER_KEY':'benchmark-reviewer','ADMIN_KEY':'benchmark-admin','TENANT_ID':'benchmark'}
        server=subprocess.Popen([sys.executable,'-m','uvicorn','docintel.api:app','--host','127.0.0.1','--port',str(port),'--log-level','error'],cwd=ROOT.parent,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        base=f'http://127.0.0.1:{port}'
        try:
            ready=False
            for _ in range(100):
                if server.poll() is not None:break
                try:
                    if httpx.get(base+'/healthz',timeout=1).status_code==200:ready=True;break
                except httpx.HTTPError:pass
                time.sleep(.1)
            if not ready:raise RuntimeError('Server did not start')
            headers={'X-API-Key':'benchmark-analyst','X-Tenant-ID':'benchmark'}
            payload=pdf(INVOICE)
            operations=[('POST',i) for i in range(30)]+[('GET',i) for i in range(90)]
            def request(op):
                method,_=op
                with httpx.Client(timeout=15) as client:
                    start=time.perf_counter()
                    if method=='POST':
                        response=client.post(base+'/api/v1/documents',headers=headers,files={'file':('invoice.pdf',payload,'application/pdf')},data={'kind':'invoice'})
                    else:
                        response=client.get(base+'/api/v1/documents',headers=headers)
                    duration=(time.perf_counter()-start)*1000
                    return method, response.status_code, duration
            start=time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                rows=list(pool.map(request,operations))
            duration=time.perf_counter()-start
            result={'created_at':datetime.now(timezone.utc).isoformat(),'environment':'local uvicorn HTTP; loopback; SQLite; deterministic PDF extraction; 4 client threads',
                    'requests':len(rows),'posts':sum(r[0]=='POST' for r in rows),'gets':sum(r[0]=='GET' for r in rows),
                    'successes':sum(r[1]==200 for r in rows),'errors':sum(r[1]!=200 for r in rows),
                    'seconds':round(duration,4),'throughput_rps':round(len(rows)/duration,2),
                    'latency_ms':{f'p{p}':round(percentile([r[2] for r in rows],p),3) for p in [50,95,99]},
                    'by_operation':{m:{'count':len([r for r in rows if r[0]==m]),'p95_ms':round(percentile([r[2] for r in rows if r[0]==m],95),3)} for m in ['POST','GET']}}
            (ROOT/'http_results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            return result
        finally:
            server.terminate()
            try:server.communicate(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.communicate()

if __name__=='__main__':print(json.dumps(run(),indent=2))
