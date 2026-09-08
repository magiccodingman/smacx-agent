#!/usr/bin/env python3
"""Run only against an explicitly selected disposable native worker."""
import argparse,json,socket,time,threading
from pathlib import Path
from smacx_controller import bridge_request_to
p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);p.add_argument('--token-file',required=True)
a=p.parse_args();token=Path(a.token_file).read_text().strip()
def ping():
    return bridge_request_to('127.0.0.1',a.port,token,'ping',timeout=4)
assert ping()['ok']
results=[]
for partial in (b'',b'{"op":'):
    with socket.create_connection(('127.0.0.1',a.port),timeout=4) as idle:
        if partial:idle.sendall(partial)
        time.sleep(.2)
        started=time.monotonic()
        assert ping()['ok']  # Idle socket stays open throughout this request.
        elapsed=time.monotonic()-started
        assert elapsed<3.5,elapsed
        results.append(round(elapsed,3))
# Repeated partial bytes cannot extend a frame forever.
with socket.create_connection(('127.0.0.1',a.port),timeout=4) as trickle:
    stop=threading.Event()
    def feed():
        while not stop.is_set():
            try:trickle.sendall(b' ')
            except OSError:break
            stop.wait(.2)
    sender=threading.Thread(target=feed);sender.start()
    try:
        time.sleep(.2);started=time.monotonic();assert ping()['ok']
        assert time.monotonic()-started<3.5
    finally:stop.set();sender.join()
# Ordinary fragmented frames remain accepted within the receive budget.
with socket.create_connection(('127.0.0.1',a.port),timeout=4) as client:
    payload=json.dumps({'op':'ping','token':token}).encode()+b'\n'
    client.sendall(payload[:5]);time.sleep(.1);client.sendall(payload[5:])
    with client.makefile('rb') as response:assert json.loads(response.readline())['ok']
print(json.dumps({'pass':True,'idle_and_partial_recovery_seconds':results,'fragmented_ping':True}))
