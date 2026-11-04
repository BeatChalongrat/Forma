"""No model/network provider calls: production checkpoint and budget persistence smoke test."""
from uuid import uuid4
from typing import TypedDict

from langgraph.graph import START, END, StateGraph
from forma.ai.llm import Usage
from forma.ai.workflow_store import workflow_storage
from forma.core.settings import get_settings


class State(TypedDict):
    value: int


thread = 'workflow-smoke-' + uuid4().hex
config = {'configurable': {'thread_id': thread}}
settings = get_settings()
with workflow_storage(settings, thread) as (saver, ledger):
    attempt = ledger.reserve()
    ledger.record(attempt, Usage(calls=1, input_tokens=3, output_tokens=2))
    builder = StateGraph(State)
    builder.add_node('increment', lambda state: {'value': state['value'] + 1})
    builder.add_edge(START, 'increment')
    builder.add_edge('increment', END)
    graph = builder.compile(checkpointer=saver)
    assert graph.invoke({'value': 4}, config)['value'] == 5
with workflow_storage(settings, thread) as (saver, ledger):
    graph = builder.compile(checkpointer=saver)
    assert graph.get_state(config).values['value'] == 5
    assert not graph.get_state(config).next
    assert ledger.used() == 1 and ledger.usage().input_tokens == 3
    assert ledger.reserve() == 2
print('PostgreSQL checkpoint + attempt/usage ledger persist across connections: PASS; no API calls')
