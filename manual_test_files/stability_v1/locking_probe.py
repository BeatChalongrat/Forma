"""PostgreSQL progress on a separate connection and stale-owner publication fencing."""
import datetime as dt
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, text

from forma.db.models import AuditLog, Customer, ImportRound, Job
from forma.core.settings import get_settings
from forma.db.session import session_scope
from forma.jobs.execution import Execution, JobStopped, current
from forma.jobs.runner import JobProgress


def main():
    assert 'postgres:5432/test' in get_settings().database_url
    with session_scope() as s:
        customer = Customer(code='LOCK-' + uuid.uuid4().hex[:8], name='Lock probe')
        s.add(customer)
        s.flush()
        rnd = ImportRound(customer_id=customer.id, status='profiled')
        s.add(rnd)
        s.flush()
        job = Job(kind='test', payload={}, round_id=rnd.id, status='running',
                  lease_token='probe', lease_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60))
        s.add(job)
        s.flush()
        jid, rid = job.id, rnd.id
    progress = JobProgress(jid)
    token = current.set(Execution(jid, 'probe', time.monotonic() + 60))
    try:
        with session_scope() as business:
            business.scalar(select(ImportRound).where(ImportRound.id == rid).with_for_update())
            with session_scope() as read:
                read.execute(text("SET LOCAL statement_timeout = '3s'"))
                read.scalar(select(Customer.id).where(Customer.id == customer.id))
            start = time.monotonic()
            progress.update(35, 'progress while business holds the round lock')
            elapsed = time.monotonic() - start
            assert elapsed < 2, elapsed
            def revoke():
                with session_scope() as s:
                    j = s.get(Job, jid)
                    j.status, j.lease_token = 'cancelled', None
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(revoke).result(3)
            business.add(AuditLog(action='forbidden-late-result', entity='test', entity_id=jid))
            business.flush()  # even an already-flushed result must still be fenced at commit
    except JobStopped:
        pass
    else:
        raise AssertionError('revoked owner committed a business result')
    finally:
        current.reset(token)
    with session_scope() as s:
        assert s.scalar(select(AuditLog.id).where(AuditLog.entity_id == jid)) is None
    print('RESULT_JSON=' + json.dumps({'progress_while_round_locked_s': elapsed,
                                      'stale_business_commit_rejected': True}), flush=True)


if __name__ == '__main__':
    main()
