import pytest
from app.core.telemetry.otel_setup import trace_worker, trace_onedrive_call, get_tracer
from app.core.telemetry.langfuse_client import get_prompt_template, observe_rag


def test_otel_worker_decorator():
    @trace_worker("test_indexing_task")
    def sample_worker_task(x, y):
        return x + y

    res = sample_worker_task(5, 7)
    assert res == 12


def test_otel_onedrive_call_context():
    with trace_onedrive_call("list_root", "/root"):
        result = "success"
    assert result == "success"


def test_langfuse_prompt_registry():
    prompt = get_prompt_template("rag_grounded_system", version="v2")
    assert "OneDrive" in prompt
    assert "<untrusted_reference_document>" in prompt


def test_observe_rag_decorator():
    @observe_rag(name="test_query", experiment_id="exp_01")
    def sample_rag_call(q: str):
        return f"Answer for {q}"

    ans = sample_rag_call("What is leave policy?")
    assert "leave policy" in ans
