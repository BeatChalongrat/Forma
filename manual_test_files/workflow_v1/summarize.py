"""Read frozen evaluation artifacts; never feeds an answer key to a model."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def summarize(run_id):
    rows = []
    for folder in sorted((ROOT / 'runs' / run_id).iterdir()):
        if not folder.is_dir() or not (folder / 'state.json').exists():
            continue
        state = read(folder / 'state.json')
        if not (folder / 'before.json').exists():
            rows.append({'case': folder.name, 'failed_stage': state.get('failed_stage'), 'complete': state.get('complete')})
            continue
        before, score = read(folder / 'before.json'), read(folder / 'score-before.json')
        mapping = before.get('mapping') or {}
        logs = mapping.get('agent_log') or []
        workflow = next((e.get('data', {}).get('workflow') for e in logs if e.get('agent') == 'mapping_workflow'), None)
        qs = before.get('questions', {}).get('questions', [])
        policy = mapping.get('policy') or {}
        row = {
            'case': folder.name, 'accuracy': score['field_accuracy'],
            'correct_fields': score['correct_fields'], 'expected_fields': score['expected_fields'],
            'rows': score['rows_got'], 'expected_rows': score['rows_expected'],
            'extra_rows': score['extra_rows'], 'extra_values': score.get('extra_values', []),
            'wrong': score['wrong'], 'expected_questions': score.get('expected_questions'),
            'questions': [{k: q.get(k) for k in ['id', 'kind', 'action', 'prompt', 'question', 'source_value', 'options', 'option_labels', 'contested']} for q in qs],
            'open_questions': before.get('questions', {}).get('open_questions', []),
            'decision': state.get('decision'), 'seconds': state.get('elapsed_active_seconds'),
            'usage': {key: sum((e.get('usage') or {}).get(key, 0) or 0 for e in logs)
                      for key in ['calls', 'input_tokens', 'output_tokens', 'cost_usd']},
            'llm_failed': [note for e in logs for note in e.get('notes', []) if note.startswith('llm_failed')],
            'verification': policy.get('verification'),
            'self_check': next((e.get('data', {}).get('self_check') for e in reversed(logs)
                                if e.get('data', {}).get('self_check')), None),
            'workflow': workflow,
        }
        if (folder / 'score-after-human.json').exists():
            row['after_human'] = read(folder / 'score-after-human.json')
        rows.append(row)
    complete = [r for r in rows if 'correct_fields' in r]
    total = sum(r['expected_fields'] for r in complete)
    correct = sum(r['correct_fields'] for r in complete)
    summary = {'run': run_id, 'completed': len(complete), 'correct': correct, 'total': total,
               'weighted_accuracy': correct / total if total else None,
               'mean_case_accuracy': sum(r['accuracy'] for r in complete) / len(complete) if complete else None,
               'seconds': sum(r['seconds'] or 0 for r in complete),
               'usage': {k: sum(r['usage'][k] for r in complete) for k in ['calls', 'input_tokens', 'output_tokens', 'cost_usd']},
               'question_types': dict(Counter(q['kind'] for r in complete for q in r['questions'])),
               'cases': rows}
    (ROOT / f'{run_id}-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'cases'}, ensure_ascii=False))
    for r in complete:
        wf = r.get('workflow') or {}
        print(r['case'], round(100*r['accuracy'], 2), 'rows', r['rows'], 'questions', len(r['questions']),
              'calls', r['usage']['calls'], 'seconds', r['seconds'], 'workflow', wf.get('status'), 'failure', r['llm_failed'])


if __name__ == '__main__':
    import sys
    for run in sys.argv[1:]:
        summarize(run)
