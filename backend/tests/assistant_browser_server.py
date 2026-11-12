"""Disposable browser fixture. Run directly; never reads deployment env or calls providers."""

import os
import tempfile
import time
from pathlib import Path

root = (
    Path(os.environ["FORMA_BROWSER_FIXTURE_ROOT"])
    if os.environ.get("FORMA_BROWSER_FIXTURE_ROOT")
    else Path(tempfile.mkdtemp(prefix="forma-chat-browser-"))
)
root.mkdir(parents=True, exist_ok=True)
os.environ.update(
    {
        "FORMA_DATABASE_URL": "sqlite:///" + (root / "test.db").as_posix(),
        "FORMA_DATA_DIR": str(root / "data"),
        "FORMA_ENV": "dev",
        "FORMA_AUTH": "dev",
        "FORMA_LLM_PROVIDER": "mock",
        "FORMA_LLM_MOCK_ONLY": "true",
        "FORMA_JOB_BACKEND": "thread",
        "FORMA_STORAGE": "local",
        "FORMA_RETENTION_INTERVAL_H": "0",
    }
)

from forma.services import code_sandbox, workspace_assistant  # noqa: E402
from forma.services.assistant_runtime import step  # noqa: E402


def fake(ctx, session, principal, question, *args, **kwargs):
    stream = kwargs.get("stream")
    step("read_mock", {}, lambda: {"count": 12})
    if "slow" in question:
        for i in range(16):
            if stream:
                stream.delta("working " if i == 0 else ".")
            time.sleep(0.5)
    text = "**Verified result**\n\n| Item | Count |\n|---|---:|\n| Files | 12 |\n\n- Source checked\n- Ready"
    if stream:
        stream.delta(text)
    return {
        "answer": text,
        "ai": True,
        "evidence": [],
        "actions": [],
        "clarifications": [
            {
                "question": "Which period?",
                "reason": "Select the period for this report",
                "options": ["November", "October"],
            }
        ]
        if "clarify" in question
        else [],
    }


class FakeSandbox:
    def healthy(self):
        return True

    def run(self, *args, **kwargs):
        raise AssertionError("Browser fixture must not execute generated code")


code_sandbox.get_runner = lambda settings: FakeSandbox()
workspace_assistant.ask = fake
if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient
    from tests.test_workspace import uncertain_mapping

    from forma.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        rnd, mid, pointer = uncertain_mapping(client, root)

    @app.get("/api/test-fixture")
    def fixture():
        # Exists ONLY in this disposable test entrypoint, never in the production app.
        return {"round_id": rnd["id"], "mapping_id": mid, "pointer": pointer, "mock_only": True}

    print("Disposable mock database:", root, flush=True)
    uvicorn.run(app, host="127.0.0.1", port=18171)
