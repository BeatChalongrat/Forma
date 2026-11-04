"""Local deterministic HTTP model: exercises real network/retry/limiter and graph, costs $0."""
import asyncio
import json
import time
from collections import Counter

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
stats = {"requests": 0, "active": 0, "max_active": 0, "faults": {}, "starts": [], "mode": "normal"}
seen = Counter()


@app.get('/stats')
def status():
    return stats


@app.post('/control')
async def control(request: Request):
    stats.update(await request.json())
    return stats


@app.post('/v1/chat/completions')
async def complete(request: Request):
    body = await request.json()
    system, user = [m['content'] for m in body['messages'][:2]]
    stats['requests'] += 1
    stats['active'] += 1
    stats['max_active'] = max(stats['max_active'], stats['active'])
    stats['starts'].append(time.time())
    ident = str(hash(system + user))
    seen[ident] += 1
    try:
        if stats['mode'] == 'hang':
            await asyncio.sleep(30)
        if stats['mode'] == 'faults' and seen[ident] == 1:
            number = stats['requests']
            if number % 11 == 0:
                stats['faults']['429'] = stats['faults'].get('429', 0) + 1
                return JSONResponse({'error': 'simulated quota'}, status_code=429, headers={'Retry-After': '1'})
            if number % 13 == 0:
                stats['faults']['503'] = stats['faults'].get('503', 0) + 1
                return JSONResponse({'error': 'simulated outage'}, status_code=503)
            if number % 31 == 0:
                stats['faults']['timeout'] = stats['faults'].get('timeout', 0) + 1
                await asyncio.sleep(5)
        await asyncio.sleep(0.3)
        if 'Evidence Analyst' in system:
            answer = {'analysis': {'facts': [], 'hypotheses': [], 'mapping_guidance': [], 'missing_information': []}}
        elif 'Semantic Reviewer' in system:
            answer = {'review': {'verdict': 'pass', 'checked': ['fake test only'], 'concerns': [], 'clarifications': []}}
        elif 'classify each detected' in system:
            answer = {'tables': []}
        else:
            try:
                payload, _ = json.JSONDecoder().raw_decode(user)
            except ValueError:
                payload = {}
            answer = {'mapping': payload.get('draft_mapping', {}), 'clarifications': [], 'open_questions': []}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}],
                'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}
    finally:
        stats['active'] -= 1


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8001, access_log=False)
