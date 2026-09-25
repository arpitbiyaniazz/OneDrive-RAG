import re
from typing import Dict, List, Optional


def eval_faithfulness(answer: str, retrieved_contexts: List[str]) -> float:
    """
    Measures the degree to which claims in the generated answer are grounded in the retrieved context.
    Returns a score between 0.0 and 1.0.
    """
    if not answer or not retrieved_contexts:
        return 0.0

    combined_context = " ".join(retrieved_contexts).lower()

    # Split answer into sentence statements
    sentences = [s.strip() for s in re.split(r"[.!?\n]", answer) if len(s.strip()) > 10]
    if not sentences:
        return 1.0

    grounded_count = 0
    for sentence in sentences:
        words = [w.lower() for w in re.findall(r"\b[A-Za-z0-9_$-]+\b", sentence) if len(w) > 3]
        if not words:
            grounded_count += 1
            continue

        # Check what percentage of key terms appear in the retrieved context
        matches = sum(1 for w in words if w in combined_context)
        if matches / len(words) >= 0.5:
            grounded_count += 1

    return round(grounded_count / len(sentences), 3)


def eval_context_recall(retrieved_files: List[str], ground_truth_files: List[str]) -> float:
    """
    Measures whether the retrieval engine returned the expected documents.
    Returns recall score between 0.0 and 1.0.
    """
    if not ground_truth_files:
        return 1.0
    if not retrieved_files:
        return 0.0

    retrieved_lower = [f.lower() for f in retrieved_files]
    hits = 0
    for expected in ground_truth_files:
        expected_lower = expected.lower()
        if any(expected_lower in r or r in expected_lower for r in retrieved_lower):
            hits += 1

    return round(hits / len(ground_truth_files), 3)


def eval_citation_accuracy(
    citations: List[Dict],
    ground_truth_files: List[str],
    answer: Optional[str] = None,
) -> float:
    """
    Measures whether cited documents in the answer correspond to verified ground truth sources.
    If answer contains in-text citations like [1], evaluates the precision of active citations;
    otherwise evaluates overall citation hit-rate.
    Returns accuracy score between 0.0 and 1.0.
    """
    if not ground_truth_files:
        return 1.0
    if not citations:
        return 0.0

    # If answer contains bracketed citations [1], [2]
    cited_indices = set()
    if answer:
        matches = re.findall(r"\[(\d+)\]", answer)
        cited_indices = {int(m) for m in matches}

    active_citations = [
        c for c in citations if not cited_indices or c.get("source_id") in cited_indices
    ]
    if not active_citations:
        active_citations = citations

    hits = 0
    for expected in ground_truth_files:
        if any(expected.lower() in c.get("filename", "").lower() for c in active_citations):
            hits += 1

    return round(hits / len(ground_truth_files), 3)


def eval_answer_relevance(answer: str, expected_key: str) -> float:
    """
    Checks if the key ground truth fact or numeric answer is present in the generated response.
    Returns 1.0 if present, else 0.0 (or partial credit if multiple terms).
    """
    if not answer or not expected_key:
        return 0.0

    key_terms = [k.strip().lower() for k in expected_key.split(",") if k.strip()]
    if not key_terms:
        return 1.0

    answer_lower = answer.lower()
    matches = sum(1 for term in key_terms if term in answer_lower)
    return round(matches / len(key_terms), 3)
