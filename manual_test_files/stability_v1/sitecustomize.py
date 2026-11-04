"""Test-only fault handler; loaded ONLY by the explicit probe subprocess environment."""
import os

if os.environ.get('FORMA_STABILITY_TEST_HANDLERS') == '1':
    import signal
    import time
    from forma.jobs.runner import handler

    @handler('stability_uncooperative')
    def uncooperative(ctx, payload, progress):
        # Simulates a native parser that ignores cancellation and graceful termination.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        progress.update(10, 'fault probe: intentionally uncooperative parser')
        while True:
            time.sleep(1)
