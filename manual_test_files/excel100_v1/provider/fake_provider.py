"""Offline scripted model. Mount contains DSL contracts only, no expected rows.

Tests executor and guardrails with controlled model proposals; does not measure
whether a real model can discover these proposals from an unknown workbook.
"""
import asyncio
import json
import time
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()
ROOT = Path(__file__).resolve().parent
stats = {'requests': 0, 'active': 0, 'max_active': 0, 'roles': {}, 'contracts': {}, 'unmatched': 0,
         'cost_usd': 0, 'started': time.time()}

@app.get('/stats')
def status(): return stats

@app.post('/v1/chat/completions')
async def complete(request: Request):
    body = await request.json()
    system = str(body['messages'][0]['content'])
    user = str(body['messages'][1]['content'])
    stats['requests'] += 1; stats['active'] += 1
    stats['max_active'] = max(stats['max_active'], stats['active'])
    try:
        await asyncio.sleep(.05)
        if 'Evidence Analyst' in system:
            role = 'analyst'
            answer = {'analysis': {'facts': [], 'hypotheses': [], 'mapping_guidance': [], 'missing_information': []}}
        elif 'Semantic Reviewer' in system:
            role = 'reviewer'
            answer = {'review': {'verdict':'pass', 'checked':['Scripted model only; real engine verification remains mandatory.'], 'concerns':[], 'clarifications':[]}}
        elif 'classify each detected' in system:
            role = 'classifier'; answer = {'tables':[]}
        else:
            role = 'mapper'
            try: payload, _ = json.JSONDecoder().raw_decode(user)
            except ValueError: payload = {}
            draft = payload.get('draft_mapping', {})
            ref = draft.get('meta', {}).get('target_schema', '')
            if not ref.startswith('excel100_'):
                # Match only registered test schema references, never arbitrary paths.
                import re
                refs = re.findall(r'excel100_x\d{4}@1', user)
                ref = refs[0] if refs else ''
            contract = ROOT/'contracts'/f'{ref}.json'
            if ref.startswith('excel100_') and contract.is_file():
                answer = json.loads(contract.read_text())
                stats['contracts'][ref] = stats['contracts'].get(ref, 0) + 1
            else:
                stats['unmatched'] += 1
                answer = {'mapping':draft, 'clarifications':[], 'open_questions':[]}
        stats['roles'][role] = stats['roles'].get(role, 0) + 1
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
                'usage':{'prompt_tokens':0,'completion_tokens':0}}
    finally: stats['active'] -= 1

if __name__ == '__main__': uvicorn.run(app, host='0.0.0.0', port=8001, access_log=False)
