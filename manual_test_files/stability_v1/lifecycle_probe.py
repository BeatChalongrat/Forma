"""Real API file replacement, stop and continue while a fake provider is waiting."""
import json
import os
import time

import httpx

from run import FAKE, HERE, create, csv, request


def wait(jid, status, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = request('GET', f'/api/jobs/{jid}')
        if status == 'mapping':
            if j['status'] == 'running' and j.get('progress', 0) >= 35:
                return j
        elif j['status'] in status:
            return j
        time.sleep(.25)
    raise AssertionError((jid, j))


def run(i):
    row = create(i)
    row['job_id'] = request('POST', f"/api/rounds/{row['round_id']}/run", json={'use_llm': True})['job_id']
    wait(row['job_id'], 'mapping')
    return row


def main():
    ai = request('GET', '/api/settings/ai')
    assert ai['model'] == 'fake-stability' and 'fake:8001' in ai['base_url']
    base = int(os.environ.get('STABILITY_PROBE_START', '1001'))
    httpx.post(FAKE + '/control', json={'mode': 'hang'}).raise_for_status()
    try:
        old = run(base)
        before_replace = time.monotonic()
        successor = request('POST', f"/api/rounds/{old['round_id']}/replace",
                            data={'reason': 'stability test replacement'},
                            files=[('files', ('corrected.csv', csv(base), 'text/csv'))])
        replace_seconds = time.monotonic() - before_replace
    finally:
        httpx.post(FAKE + '/control', json={'mode': 'normal'}).raise_for_status()
    assert request('GET', f"/api/jobs/{old['job_id']}")['status'] == 'cancelled'
    new_job = wait(successor['job_id'], ('done', 'failed'))
    assert new_job['status'] == 'done', new_job
    assert request('GET', f"/api/rounds/{old['round_id']}")['status'] == 'superseded'
    assert not (request('GET', f"/api/jobs/{old['job_id']}").get('result') or {}).get('mapping_version_id')
    httpx.post(FAKE + '/control', json={'mode': 'hang'}).raise_for_status()
    try:
        stopped = run(base + 1)
        before_stop = time.monotonic()
        request('POST', f"/api/jobs/{stopped['job_id']}/cancel")
        stop_seconds = time.monotonic() - before_stop
    finally:
        httpx.post(FAKE + '/control', json={'mode': 'normal'}).raise_for_status()
    state = request('GET', f"/api/jobs/{stopped['job_id']}")
    assert state['status'] == 'cancelled'
    # The parent marks the source after the stopped child releases its business transaction.
    deadline = time.monotonic() + 10
    while request('GET', f"/api/rounds/{stopped['round_id']}")['status'] != 'failed':
        assert time.monotonic() < deadline
        time.sleep(.2)
    request('POST', f"/api/jobs/{stopped['job_id']}/retry")
    resumed = wait(stopped['job_id'], ('done', 'failed'))
    assert resumed['status'] == 'done', resumed
    assert resumed['attempts'] == state['attempts'] + 1
    result = {'replacement': {'old': old, 'new_round': successor['id'], 'new_job_status': new_job['status'], 'seconds': replace_seconds},
              'stop_resume': {'job_id': stopped['job_id'], 'status': resumed['status'], 'attempts': resumed['attempts'], 'stop_api_seconds': stop_seconds}}
    (HERE / 'lifecycle.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
