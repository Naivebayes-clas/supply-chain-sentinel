"""
Eval Harness: runs golden dataset through the agent, scores results.

Metrics:
  - Route accuracy: did the planner pick the right path?
  - Content accuracy: LLM-as-judge scores answer correctness (0-1)
  - Hallucination check: LLM-as-judge flags fabricated data
  - Latency: time per query

Output: evals/results/eval_report_{timestamp}.md
"""

import json
import time
import re
import os
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

try:
    from observability.langfuse_config import flush_langfuse
except ImportError:
    def flush_langfuse():
        pass


# ──────────────────────────────────────────────
# LLM-as-Judge
# ──────────────────────────────────────────────
def get_judge_llm():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=os.environ.get("OPENAI_API_KEY"),
    )


_judge = None

def llm_judge(question: str, answer: str, expected_keywords: list[str]) -> dict:
    """
    Use GPT-4o-mini to score the answer.
    Returns: {"correctness": 0.0-1.0, "hallucination": bool, "reasoning": str}
    """
    global _judge
    if _judge is None:
        _judge = get_judge_llm()

    prompt = f"""You are an eval judge for a logistics data agent. Score the answer below.

Question: {question}
Answer: {answer}
Expected key facts (the answer should reference these): {expected_keywords}

Score the answer on:
1. CORRECTNESS (0.0 to 1.0): Does the answer correctly and specifically address the question?
   - 1.0 = directly answers with correct specific data
   - 0.7 = mostly correct but vague or partially complete
   - 0.4 = partially relevant but misses the key point
   - 0.1 = barely relevant or mostly wrong
   - 0.0 = completely wrong, off-topic, or an error message

2. HALLUCINATION (true/false): Does the answer mention any carrier name, city, number, or fact
   that could not possibly come from a logistics dataset with carriers [XPO, Old Dominion, FedEx, UPS, Estes]
   and cities [Mississauga, Burlington, Toronto, Montreal, Vancouver, Calgary, Ottawa, Winnipeg, Halifax]?
   Only flag if it clearly invents something. Being vague is NOT hallucination.

Respond in JSON only:
{{"correctness": 0.0, "hallucination": false, "reasoning": "one sentence"}}"""

    response = _judge.invoke(prompt)
    text = response.content.strip()

    # Parse JSON from response (handle markdown fences)
    text = re.sub(r'^```json?\n?', '', text)
    text = re.sub(r'\n?```$', '', text)

    try:
        result = json.loads(text)
        return {
            "correctness": float(result.get("correctness", 0.0)),
            "hallucination": bool(result.get("hallucination", False)),
            "reasoning": result.get("reasoning", ""),
        }
    except (json.JSONDecodeError, ValueError):
        # Fallback: try to extract numbers
        match = re.search(r'(\d+\.?\d*)', text)
        correctness = float(match.group(1)) if match else 0.0
        if correctness > 1.0:
            correctness = 1.0
        return {
            "correctness": correctness,
            "hallucination": "true" in text.lower(),
            "reasoning": text[:100],
        }


# ──────────────────────────────────────────────
# Eval infrastructure
# ──────────────────────────────────────────────
@dataclass
class EvalResult:
    id: int
    question: str
    expected_route: str
    actual_route: str
    answer: str
    route_correct: bool
    content_score: float
    hallucination_flag: bool
    judge_reasoning: str
    latency_ms: float
    category: str


def load_golden_dataset(path: str = "evals/golden_dataset.json") -> list[dict]:
    with open(path) as f:
        return json.load(f)


def run_single_eval(item: dict) -> EvalResult:
    """Run one question through the agent and score it with LLM-as-judge."""
    start = time.time()

    try:
        from agent.graph import ask, planner, AgentState

        plan_result = planner({"question": item["question"]})
        actual_route = plan_result.get("plan", "unknown")
        answer = ask(item["question"])

    except Exception as e:
        answer = f"ERROR: {str(e)}"
        actual_route = "error"

    latency_ms = (time.time() - start) * 1000

    # Score route
    route_correct = (actual_route == item["expected_route"])

    # Score content with LLM-as-judge
    try:
        judge_result = llm_judge(
            question=item["question"],
            answer=answer,
            expected_keywords=item["expected_answer_contains"],
        )
        content_score = judge_result["correctness"]
        hallucination_flag = judge_result["hallucination"]
        judge_reasoning = judge_result["reasoning"]
    except Exception as e:
        content_score = 0.0
        hallucination_flag = False
        judge_reasoning = f"Judge error: {str(e)}"

    return EvalResult(
        id=item["id"],
        question=item["question"],
        expected_route=item["expected_route"],
        actual_route=actual_route,
        answer=answer,
        route_correct=route_correct,
        content_score=content_score,
        hallucination_flag=hallucination_flag,
        judge_reasoning=judge_reasoning,
        latency_ms=latency_ms,
        category=item["category"],
    )


def run_all_evals() -> list[EvalResult]:
    """Run the full golden dataset."""
    dataset = load_golden_dataset()
    print(f"🧪 Running {len(dataset)} evals (LLM-as-judge scoring)...")

    results = []
    for i, item in enumerate(dataset):
        print(f"   [{i+1}/{len(dataset)}] Q{item['id']}: {item['question'][:50]}...", end=" ")
        result = run_single_eval(item)
        status = "✅" if (result.route_correct and result.content_score >= 0.5) else "❌"
        print(f"{status} (route={'✓' if result.route_correct else '✗'}, score={result.content_score:.2f}, {result.latency_ms:.0f}ms)")
        results.append(result)

    return results


def generate_report(results: list[EvalResult]) -> str:
    """Generate a markdown eval report."""
    total = len(results)
    route_correct = sum(1 for r in results if r.route_correct)
    avg_content = sum(r.content_score for r in results) / total
    hallucinations = sum(1 for r in results if r.hallucination_flag)
    avg_latency = sum(r.latency_ms for r in results) / total
    pass_count = sum(1 for r in results if r.route_correct and r.content_score >= 0.5)

    categories = {}
    for r in results:
        if r.category not in categories:
            categories[r.category] = []
        categories[r.category].append(r)

    lines = []
    lines.append("# Eval Report — Supply Chain Sentinel Agent")
    lines.append(f"\n**Generated:** {datetime.now().isoformat()}")
    lines.append(f"**Dataset:** {total} golden Q&A pairs")
    lines.append(f"**Scoring:** LLM-as-judge (GPT-4o-mini)\n")

    lines.append("## Summary")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Route accuracy | {route_correct}/{total} ({route_correct/total:.0%}) |")
    lines.append(f"| Avg correctness score | {avg_content:.2f} / 1.0 |")
    lines.append(f"| Hallucination flags | {hallucinations}/{total} ({hallucinations/total:.0%}) |")
    lines.append(f"| Avg latency | {avg_latency:.0f} ms |")
    lines.append(f"| Pass rate (route ✓ + score ≥0.5) | {pass_count}/{total} ({pass_count/total:.0%}) |")

    lines.append("\n## Per-Category Breakdown")
    lines.append("| Category | N | Route Acc | Avg Score | Halluc |")
    lines.append("|----------|---|-----------|-----------|--------|")
    for cat, cat_results in sorted(categories.items()):
        n = len(cat_results)
        ra = sum(1 for r in cat_results if r.route_correct) / n
        ac = sum(r.content_score for r in cat_results) / n
        h = sum(1 for r in cat_results if r.hallucination_flag)
        lines.append(f"| {cat} | {n} | {ra:.0%} | {ac:.2f} | {h} |")

    lines.append("\n## Failed Queries (score < 0.5)")
    failed = [r for r in results if not (r.route_correct and r.content_score >= 0.5)]
    if failed:
        lines.append("| ID | Question | Route | Score | Reasoning |")
        lines.append("|----|----------|-------|-------|-----------|")
        for r in failed:
            route_mark = "✓" if r.route_correct else f"✗ ({r.actual_route})"
            lines.append(f"| {r.id} | {r.question[:40]}... | {route_mark} | {r.content_score:.2f} | {r.judge_reasoning[:60]} |")
    else:
        lines.append("\n✅ All queries passed.")

    lines.append("\n## Full Results")
    lines.append("| ID | Q | Route | Score | Halluc | Latency |")
    lines.append("|----|---|-------|-------|--------|---------|")
    for r in results:
        route_mark = "✓" if r.route_correct else f"✗"
        halluc = "⚠️" if r.hallucination_flag else ""
        lines.append(f"| {r.id} | {r.question[:45]} | {route_mark} | {r.content_score:.2f} | {halluc} | {r.latency_ms:.0f}ms |")

    return "\n".join(lines)


if __name__ == "__main__":
    results = run_all_evals()
    report = generate_report(results)

    results_dir = Path("evals/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = results_dir / f"eval_report_{timestamp}.md"
    report_path.write_text(report)
    (results_dir / "eval_report_latest.md").write_text(report)

    flush_langfuse()

    print(f"\n📊 Report written to {report_path}")
    print(f"   Also copied to {results_dir / 'eval_report_latest.md'}")
    print(f"\n   Summary: {sum(1 for r in results if r.route_correct)}/{len(results)} route correct, "
          f"avg score {sum(r.content_score for r in results)/len(results):.2f}, "
          f"{sum(1 for r in results if r.hallucination_flag)} hallucinations, "
          f"pass rate {sum(1 for r in results if r.route_correct and r.content_score >= 0.5)}/{len(results)}")   
