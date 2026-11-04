"""Read-only extraction of the pre-repair candidate for an offline sensitivity check."""
import json
import sys
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url
from langgraph.checkpoint.postgres import PostgresSaver
from forma.core.settings import get_settings

dsn = make_url(get_settings().database_url).set(drivername='postgresql').render_as_string(hide_password=False)
with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
    for checkpoint in PostgresSaver(conn).list({'configurable': {'thread_id': sys.argv[1]}}):
        state = checkpoint.checkpoint['channel_values']
        if state.get('repairs', 0) == 0 and (state.get('review') or {}).get('verdict') == 'repair':
            Path('/tmp/t12-initial-mapping.json').write_text(json.dumps(state['mapping']), encoding='utf-8')
            print('Saved pre-repair candidate; no model call and no DB writes')
            break
    else:
        raise RuntimeError('No pre-repair checkpoint found')
