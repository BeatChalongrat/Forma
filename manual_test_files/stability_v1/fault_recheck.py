"""Verify supervisor recovery with the production restart policy, while the 300 jobs are running."""
import json
import subprocess
import time
import httpx

from run import COMPOSE, FAKE, HERE, request


def main():
    httpx.post(FAKE + '/control', json={'mode': 'hang'}).raise_for_status()
    until = time.monotonic() + 120
    running = []
    while time.monotonic() < until:
        running = [j for j in request('GET', '/api/jobs', params={'status': 'running'}) if j['progress'] >= 35]
        if len(running) >= 2:
            break
        time.sleep(.5)
    assert running
    start = time.monotonic()
    before = {j['id']: j['attempts'] for j in running}
    killed = []
    for name in ('forma-stability-verified-worker-1', 'forma-stability-verified-worker-2'):
        out = subprocess.run(['docker', 'kill', '--signal', 'KILL', name], capture_output=True)
        killed.append({'container': name, 'exit_code': out.returncode})
    # docker kill is an explicit operator stop, which disables unless-stopped auto restart.
    # Start restores the failed host; recovery of its claimed work must need no manual job retry.
    subprocess.run([*COMPOSE, 'start', 'worker'], check=True)
    httpx.post(FAKE + '/control', json={'mode': 'faults'}).raise_for_status()
    recovered = []
    until = time.monotonic() + 240
    while time.monotonic() < until:
        states = [request('GET', f'/api/jobs/{jid}') for jid in before]
        if all(j['status'] == 'queued' or j['attempts'] > before[j['id']] or j['status'] == 'done' for j in states):
            recovered = states
            break
        time.sleep(2)
    assert recovered, states
    recovery_seconds = time.monotonic() - start
    subprocess.run([*COMPOSE, 'restart', 'redis'], check=True)
    time.sleep(25)
    supervisors = []
    for name in ('forma-stability-verified-worker-1', 'forma-stability-verified-worker-2'):
        state = json.loads(subprocess.check_output(['docker', 'inspect', '--format', '{{json .State}}', name]))
        assert state['Running'], state
        supervisors.append({'container': name, 'running': state['Running']})
    result = {'killed_while_mapping': killed, 'jobs': before, 'recovered_attempts': {j['id']: j['attempts'] for j in recovered},
              'recovery_seconds': recovery_seconds, 'recovered_states': {j['id']: j['status'] for j in recovered},
              'redis_restart_workers': supervisors, 'manual_start_after_intentional_docker_kill': True,
              'manual_start_after_redis_restart': False}
    (HERE / 'fault-recheck.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
