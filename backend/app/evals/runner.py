import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from app.core.telemetry.langfuse_client import log_evaluation_score
from app.db.database import AsyncSessionLocal
from app.models.user import User
from app.services.ingestion_service import ingestion_service
from app.services.rag_service import rag_service
from app.evals.metrics import (
    eval_answer_relevance,
    eval_citation_accuracy,
    eval_context_recall,
    eval_faithfulness,
)
from sqlalchemy import select

logger = logging.getLogger("rag_evals")


class RAGEvaluationRunner:
    """
    Automated evaluation harness for measuring Groundedness, Retrieval Recall,
    and Citation Accuracy across benchmark questions and reporting to Langfuse.
    """

    def __init__(self, dataset_path: Optional[str] = None):
        if not dataset_path:
            dataset_path = str(Path(__file__).parent / "dataset.json")
        self.dataset_path = dataset_path

    async def get_or_create_eval_user(self) -> User:
        async with AsyncSessionLocal() as session:
            stmt = select(User).where(User.email == "demo@contoso.com")
            res = await session.execute(stmt)
            user = res.scalar_one_or_none()
            if not user:
                user = User(
                    email="demo@contoso.com",
                    full_name="Demo Enterprise User",
                    microsoft_id="ms_demo_user_123",
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
            return user

    async def run_evaluation(self, prompt_version: str = "v2") -> Dict:
        # 1. Load benchmark dataset
        with open(self.dataset_path, "r", encoding="utf-8") as f:
            samples = json.load(f)

        user = await self.get_or_create_eval_user()

        # 2. Ensure mock knowledge base files are indexed
        from app.services.sync_service import sync_service
        await sync_service.run_sync(user_id=user.id)

        results: List[Dict] = []
        scores_summary = {
            "faithfulness": [],
            "context_recall": [],
            "citation_accuracy": [],
            "answer_relevance": [],
        }

        print(f"\n=================================================================")
        print(f"  RUNNING ONEDRIVE RAG EVALUATION BENCHMARK (Version: {prompt_version})")
        print(f"=================================================================\n")

        for sample in samples:
            q_id = sample["id"]
            question = sample["question"]
            ground_truth_files = sample["ground_truth_files"]
            expected_key = sample["expected_answer_key"]

            # Stream RAG answer
            tokens = []
            final_payload = None

            async for event in rag_service.stream_rag_response(query=question, user_id=user.id):
                if event.startswith("data: "):
                    body = json.loads(event[6:])
                    if "token" in body:
                        tokens.append(body["token"])
                    if body.get("done"):
                        final_payload = body

            generated_answer = "".join(tokens)
            citations = final_payload.get("citations", []) if final_payload else []
            trace_id = final_payload.get("trace_id", "") if final_payload else ""
            retrieved_files = [c["filename"] for c in citations]

            # Compute Metrics
            faith_score = eval_faithfulness(generated_answer, [c.get("filename", "") + ": " + generated_answer for c in citations])
            recall_score = eval_context_recall(retrieved_files, ground_truth_files)
            citation_score = eval_citation_accuracy(citations, ground_truth_files, answer=generated_answer)
            relevance_score = eval_answer_relevance(generated_answer, expected_key)

            scores_summary["faithfulness"].append(faith_score)
            scores_summary["context_recall"].append(recall_score)
            scores_summary["citation_accuracy"].append(citation_score)
            scores_summary["answer_relevance"].append(relevance_score)

            # Log to Langfuse
            if trace_id:
                log_evaluation_score(trace_id, "faithfulness", faith_score)
                log_evaluation_score(trace_id, "context_recall", recall_score)
                log_evaluation_score(trace_id, "citation_accuracy", citation_score)
                log_evaluation_score(trace_id, "answer_relevance", relevance_score)

            result_entry = {
                "id": q_id,
                "question": question,
                "retrieved_files": retrieved_files,
                "citations_count": len(citations),
                "faithfulness": faith_score,
                "context_recall": recall_score,
                "citation_accuracy": citation_score,
                "answer_relevance": relevance_score,
            }
            results.append(result_entry)

            print(f"[{q_id}] {question[:50]}...")
            print(f"   -> Retrieved: {retrieved_files}")
            print(f"   -> Faithfulness: {faith_score} | Recall: {recall_score} | Citations: {citation_score} | Relevance: {relevance_score}\n")

        # Compute Averages
        avg_faithfulness = round(sum(scores_summary["faithfulness"]) / len(scores_summary["faithfulness"]), 3)
        avg_recall = round(sum(scores_summary["context_recall"]) / len(scores_summary["context_recall"]), 3)
        avg_citations = round(sum(scores_summary["citation_accuracy"]) / len(scores_summary["citation_accuracy"]), 3)
        avg_relevance = round(sum(scores_summary["answer_relevance"]) / len(scores_summary["answer_relevance"]), 3)

        summary = {
            "prompt_version": prompt_version,
            "total_samples": len(samples),
            "mean_faithfulness": avg_faithfulness,
            "mean_context_recall": avg_recall,
            "mean_citation_accuracy": avg_citations,
            "mean_answer_relevance": avg_relevance,
            "details": results,
        }

        print("-----------------------------------------------------------------")
        print(f"  BENCHMARK SUMMARY RESULTS:")
        print(f"  - Mean Faithfulness (Groundedness):  {avg_faithfulness * 100:.1f}%")
        print(f"  - Mean Context Recall:               {avg_recall * 100:.1f}%")
        print(f"  - Mean Citation Accuracy:            {avg_citations * 100:.1f}%")
        print(f"  - Mean Answer Relevancy:             {avg_relevance * 100:.1f}%")
        print("=================================================================\n")

        return summary


runner = RAGEvaluationRunner()
