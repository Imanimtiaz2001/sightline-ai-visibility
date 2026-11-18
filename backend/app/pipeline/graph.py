"""The research DAG.

                    ┌──────────────────┐
         START ───► │ discovery_planner│  LLM: choose keyword-research tool calls
                    └────────┬─────────┘
                    ┌────────▼──────────┐
                    │discovery_retriever│  execute calls (retry, cache, budget)
                    └────────┬──────────┘
                    ┌────────▼───────────┐
                    │discovery_normalizer│  payloads -> KeywordCandidate[]
                    └────────┬───────────┘
               candidates?   │   none
            ┌────────────────┴──────────────┐
            │                      ┌────────▼─────┐
            │                      │ seed_fallback│  profile topics, volume unknown
            │                      └────────┬─────┘
            ├───────────────────────────────┘
   ┌────────▼───────────┐
   │measurement_planner │  LLM: pick queries, phrase buyer prompts, tool calls
   └────────┬───────────┘
            │ Send() × N  (parallel, one branch per query)        targets? none ─┐
   ┌────────▼────────────┐                                                      │
   │measurement_retriever│  SERP + AI-answer calls for one query                │
   └────────┬────────────┘                                                      │
   ┌────────▼─────────────┐                                                     │
   │measurement_normalizer│  fan-in: payloads -> QueryMeasurement[]             │
   └────────┬─────────────┘                                                     │
   ┌────────▼───┐                                                               │
   │   scorer   │  deterministic opportunity score                              │
   └────────┬───┘                                                               │
measurable? │ none ────────────────────────────────────────────────────────────┤
   ┌────────▼───┐                                                               │
   │ gap_analyst│  LLM: which gaps are worth closing (rule fallback)            │
   └────────┬───┘                                                               │
 worth it?  │ none ─────────────────────────────────────────────────────────────┤
   ┌────────▼───┐                                                               │
   │ recommender│  LLM: content to publish (rule fallback)                      │
   └────────┬───┘                                                               │
   ┌────────▼───┐ ◄─────────────────────────────────────────────────────────────┘
   │  reporter  │  assemble JSON + markdown; always runs
   └────────┬───┘
           END
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.pipeline.nodes.analysis import gap_analyst
from app.pipeline.nodes.base import instrumented
from app.pipeline.nodes.normalization import discovery_normalizer, measurement_normalizer, seed_fallback
from app.pipeline.nodes.planning import discovery_planner, measurement_planner
from app.pipeline.nodes.recommendation import recommender, selected_gaps
from app.pipeline.nodes.report import reporter
from app.pipeline.nodes.retrieval import discovery_retriever, measurement_retriever
from app.pipeline.nodes.scoring import scorer
from app.pipeline.state import PipelineDeps, PipelineState

# Node name -> (function, reads, writes). The read/write sets are the stage contracts.
NODES: dict[str, tuple[Any, tuple[str, ...], tuple[str, ...]]] = {
    "discovery_planner": (discovery_planner, ("brand",), ("discovery_plan", "discovery_plan_source")),
    "discovery_retriever": (discovery_retriever, ("discovery_plan",), ("discovery_results",)),
    "discovery_normalizer": (discovery_normalizer, ("brand", "discovery_results"), ("candidates",)),
    "seed_fallback": (seed_fallback, ("brand",), ("candidates",)),
    "measurement_planner": (measurement_planner, ("brand", "candidates"), ("measurement_targets",)),
    "measurement_retriever": (measurement_retriever, ("target",), ("measurement_results",)),
    "measurement_normalizer": (
        measurement_normalizer,
        ("brand", "measurement_targets", "measurement_results"),
        ("measurements",),
    ),
    "scorer": (scorer, ("measurements",), ("scored",)),
    "gap_analyst": (gap_analyst, ("brand", "scored"), ("analysis", "analysis_source")),
    "recommender": (recommender, ("brand", "scored", "analysis"), ("recommendations", "recommendations_source")),
    "reporter": (reporter, ("brand", "scored", "analysis", "recommendations", "degradations"), ("report",)),
}


def route_after_discovery(state: PipelineState) -> str:
    return "measurement_planner" if state.get("candidates") else "seed_fallback"


def route_measurements(state: PipelineState) -> list[Send] | str:
    targets = state.get("measurement_targets") or []
    if not targets:
        return "reporter"
    return [Send("measurement_retriever", {"brand": state["brand"], "target": t}) for t in targets]


def route_after_scoring(state: PipelineState) -> str:
    measurable = any(s.breakdown.visibility is not None for s in state.get("scored") or [])
    return "gap_analyst" if measurable else "reporter"


def route_after_analysis(state: PipelineState, max_recommendations: int) -> str:
    return "recommender" if selected_gaps(state, max_recommendations) else "reporter"


def build_graph(deps: PipelineDeps) -> Any:
    graph: StateGraph[PipelineState] = StateGraph(PipelineState)
    for name, (fn, reads, writes) in NODES.items():
        item_key = (lambda s: s["target"].candidate.keyword) if name == "measurement_retriever" else None
        graph.add_node(name, instrumented(name, fn, deps, reads=reads, writes=writes, item_key=item_key))

    graph.add_edge(START, "discovery_planner")
    graph.add_edge("discovery_planner", "discovery_retriever")
    graph.add_edge("discovery_retriever", "discovery_normalizer")
    graph.add_conditional_edges("discovery_normalizer", route_after_discovery, ["measurement_planner", "seed_fallback"])
    graph.add_edge("seed_fallback", "measurement_planner")
    graph.add_conditional_edges("measurement_planner", route_measurements, ["measurement_retriever", "reporter"])
    graph.add_edge("measurement_retriever", "measurement_normalizer")
    graph.add_edge("measurement_normalizer", "scorer")
    graph.add_conditional_edges("scorer", route_after_scoring, ["gap_analyst", "reporter"])
    graph.add_conditional_edges(
        "gap_analyst",
        lambda s: route_after_analysis(s, deps.settings.max_recommendations),
        ["recommender", "reporter"],
    )
    graph.add_edge("recommender", "reporter")
    graph.add_edge("reporter", END)
    return graph.compile()


def run_pipeline(deps: PipelineDeps, initial: PipelineState) -> PipelineState:
    app = build_graph(deps)
    # Recursion limit is generous: the graph is acyclic; the limit only guards against bugs.
    config = {"recursion_limit": 50, "max_concurrency": deps.settings.dataforseo_max_concurrency}
    return cast(PipelineState, app.invoke(initial, config=config))
