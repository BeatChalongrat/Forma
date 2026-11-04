"""Real Redis admission across OS processes, local HTTP model, no external API."""
import json
import multiprocessing
import uuid
from concurrent.futures import ProcessPoolExecutor

import httpx


def call(key):
    from forma.ai.llm import _post_json, LLMError
    from forma.core.settings import get_settings
    s = get_settings()
    s.llm_shared_concurrency, s.llm_shared_rpm, s.llm_task_budget_s = 2, 5, 3
    try:
        _post_json('http://fake:8001/v1/chat/completions', {'Authorization': key},
                   {'messages': [{'content': 'Evidence Analyst'}, {'content': '{}'}]}, 2)
        return 'sent'
    except LLMError:
        return 'not_admitted'


if __name__ == '__main__':
    httpx.post('http://fake:8001/control', json={'mode': 'normal', 'requests': 0, 'max_active': 0, 'starts': []}).raise_for_status()
    key = 'probe-' + uuid.uuid4().hex
    with ProcessPoolExecutor(max_workers=8, mp_context=multiprocessing.get_context('spawn')) as pool:
        results = list(pool.map(call, [key] * 8))
    stats = httpx.get('http://fake:8001/stats').json()
    assert results.count('sent') == stats['requests'] == 5, (results, stats)
    assert stats['max_active'] <= 2, stats
    print('RESULT_JSON=' + json.dumps({'results': results, 'requests': stats['requests'], 'max_active': stats['max_active']}))
