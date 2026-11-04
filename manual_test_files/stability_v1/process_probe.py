"""Run only in isolated test container, with API/worker schedulers stopped."""
import asyncio
import datetime as dt
import json
import os
import time

from forma.core.settings import get_settings
from forma.db.models import Job
from forma.db.session import session_scope
from forma.jobs import runner


def new():
    with session_scope() as s:
        j = Job(kind='stability_uncooperative', payload={})
        s.add(j)
        s.flush()
        return j.id


async def cancellation():
    get_settings().job_timeout_s = 60
    os.environ['FORMA_JOB_TIMEOUT_S'] = '60'
    jid = new()
    task = asyncio.create_task(runner.arq_run_job({}, jid, 0))
    until = time.monotonic() + 30
    state = {}
    while time.monotonic() < until:
        state = await asyncio.to_thread(runner.job_status, jid)
        if (state.get('message') or '').startswith('fault probe'):
            break
        await asyncio.sleep(.1)
    assert state.get('worker'), state
    pid = int(state['worker'].split(':')[-1])
    start = time.monotonic()
    await asyncio.to_thread(runner.cancel_job, jid)
    await asyncio.wait_for(task, 8)
    elapsed = time.monotonic() - start
    assert not os.path.exists(f'/proc/{pid}'), pid
    assert runner.job_status(jid)['status'] == 'cancelled'
    return {'cancelled_job': jid, 'child_exited': True, 'seconds_after_cancel': elapsed}


async def timeout():
    get_settings().job_timeout_s = 10
    os.environ['FORMA_JOB_TIMEOUT_S'] = '10'
    jid = new()
    start = time.monotonic()
    try:
        await runner.arq_run_job({}, jid, 0)
    except TimeoutError:
        pass
    else:
        raise AssertionError('hard deadline did not fire')
    state = runner.job_status(jid)
    pid = int(state['worker'].split(':')[-1])
    assert not os.path.exists(f'/proc/{pid}')
    expiry = dt.datetime.fromisoformat(state['lease_expires_at'])
    assert expiry <= dt.datetime.now(dt.UTC)
    runner.cancel_job(jid)  # terminalize the deliberately poisonous test job before schedulers restart
    return {'timeout_job': jid, 'child_exited': True, 'lease_expired': True, 'elapsed_s': time.monotonic() - start}


async def main():
    assert 'postgres:5432/test' in get_settings().database_url
    result = {'cancellation': await cancellation(), 'hard_timeout': await timeout()}
    print('RESULT_JSON=' + json.dumps(result), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
