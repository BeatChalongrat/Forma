"""Read-only graph progress for the last evaluation workflow; never prints credentials or file data."""
import json
import psycopg
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url
from langgraph.checkpoint.postgres import PostgresSaver
from forma.core.settings import get_settings

dsn = make_url(get_settings().database_url).set(drivername='postgresql').render_as_string(hide_password=False)
with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
    row = conn.execute("SELECT thread_id, attempts FROM forma_mapping_attempts WHERE thread_id LIKE 'mapping-%%' ORDER BY expires_at DESC LIMIT 1").fetchone()
    if row:
        saved = PostgresSaver(conn).get_tuple({'configurable': {'thread_id': row['thread_id']}})
        state = saved.checkpoint['channel_values'] if saved else {}
        print(json.dumps({'attempts': row['attempts'], 'roles': [
            {k: e.get(k) for k in ['role', 'outcome', 'elapsed_ms']} for e in state.get('events', [])],
            'failure': state.get('failure'), 'review': (state.get('review') or {}).get('verdict'),
            'reviews': [e['data']['review'] for e in state.get('events', []) if e.get('data', {}).get('review')],
            'concerns': (state.get('review') or {}).get('concerns', [])}, ensure_ascii=False))
