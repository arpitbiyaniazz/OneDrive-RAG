import pytest
from app.evals.metrics import (
    eval_answer_relevance,
    eval_citation_accuracy,
    eval_context_recall,
    eval_faithfulness,
)
from app.evals.runner import runner


def test_metric_faithfulness():
    context = ["Employees are entitled to 18 annual leave days per year.", "Carryover is limited to 5 days."]
    grounded_answer = "Employees receive 18 annual leave days per year."
    hallucinated_answer = "Employees receive 45 annual vacation days and free helicopter travel to Antarctica."

    score_good = eval_faithfulness(grounded_answer, context)
    score_bad = eval_faithfulness(hallucinated_answer, context)

    assert score_good >= 0.8
    assert score_bad <= 0.4


def test_metric_context_recall():
    expected = ["Leave Policy 2026.pdf", "Employee Handbook.docx"]
    retrieved_perfect = ["Leave Policy 2026.pdf", "Employee Handbook.docx"]
    retrieved_partial = ["Leave Policy 2026.pdf", "Unrelated.txt"]
    retrieved_none = ["Different.pdf"]

    assert eval_context_recall(retrieved_perfect, expected) == 1.0
    assert eval_context_recall(retrieved_partial, expected) == 0.5
    assert eval_context_recall(retrieved_none, expected) == 0.0


def test_metric_citation_accuracy():
    ground_truth = ["Budget 2026.xlsx"]
    citations_valid = [{"filename": "Budget 2026.xlsx", "page": 1}]
    citations_invalid = [{"filename": "Random Document.pdf", "page": 1}]

    assert eval_citation_accuracy(citations_valid, ground_truth) == 1.0
    assert eval_citation_accuracy(citations_invalid, ground_truth) == 0.0


@pytest.mark.asyncio
async def test_full_rag_evaluation_benchmark():
    """
    Executes the golden benchmark dataset through the RAG pipeline
    and verifies that benchmark quality metrics meet minimum standards.
    """
    summary = await runner.run_evaluation(prompt_version="v2")
    assert summary["total_samples"] == 5
    assert summary["mean_faithfulness"] >= 0.70, f"Mean faithfulness too low: {summary['mean_faithfulness']}"
    assert summary["mean_context_recall"] >= 0.50, f"Mean recall too low: {summary['mean_context_recall']}"
    assert summary["mean_citation_accuracy"] >= 0.50, f"Mean citation accuracy too low: {summary['mean_citation_accuracy']}"
