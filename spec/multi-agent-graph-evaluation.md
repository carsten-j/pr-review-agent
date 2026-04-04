# Multi-Agent Pipeline: Graph vs Programmatic Hand-off

## Question

Can the triage→review pipeline be refactored to use Pydantic AI's graph support — a triage agent classifies the PR, then routes to a specialist reviewer agent with domain-specific instructions and tools?

## Short answer: No — for routing alone, Graph is overkill.

The current code already uses **Programmatic Hand-off**, which is exactly what Pydantic AI recommends for this kind of pipeline. `_run_triage_background()` orchestrates `run_triage()` → `run_review()` in plain Python — that *is* the idiomatic pattern.

Adding more specialist reviewers (DB, security, architecture) only changes the routing switch, not the control structure:

```python
# today
agent = security_review_agent if "security" in tags else general_review_agent

# with more specialists — still plain Python, no Graph needed
match tags:
    case tags if "security" in tags:    agent = security_review_agent
    case tags if "database" in tags:    agent = database_review_agent
    case tags if "api" in tags:         agent = api_review_agent
    case _:                             agent = general_review_agent
```

## Pydantic AI pattern summary

### Graph
Converts workflows into a formal state machine. Each step is a `BaseNode` with an async `run()` method; return type annotations define edges.

```python
class TriageNode(BaseNode):
    issue_text: str

    async def run(self) -> ReviewNode | RejectNode:
        if self.assess_priority(self.issue_text) > threshold:
            return ReviewNode(issue_text=self.issue_text)
        return RejectNode(issue_text=self.issue_text)

graph = Graph(nodes=[TriageNode, ReviewNode, RejectNode])
result = await graph.run(TriageNode(issue_text="..."))
```

Use when: state needs to survive process crashes, you need pause/resume, human-in-the-loop gates, or auto-generated workflow diagrams.

### Programmatic Hand-off (current approach)
Application code runs agents in sequence; Python decides what runs next based on the previous result.

```python
triage_result = await triage_agent.run(pr_content)
if triage_result.needs_review:
    review_result = await review_agent.run(pr_content)
```

Use when: agents handle distinct phases and routing logic is deterministic.

### Agent Delegation (agent-as-tool)
One agent calls another as a tool and resumes after it completes.

Use when: one agent needs another's output *within* its own decision process.

## When Graph would pay off in this codebase

| Need | Current state | Graph adds |
|---|---|---|
| Crash recovery | Background task dies → review lost | Snapshot after triage; resume from checkpoint |
| Human approval gate | Not implemented | Pause after triage, wait for sign-off, then review |
| Pipeline visualisation | None | Auto-generated Mermaid diagram |
| Long-running with retries | Not implemented | Resume failed node from checkpoint |

## Verdict

**Keep the current programmatic hand-off. Add specialist agents by extending the routing match statement.**

Graph solves distribution, persistence, and pause/resume. None of those are in scope. If a human-in-the-loop gate is added later (e.g. "triage says critical → require human sign-off before running full review"), that is the right moment to migrate to Graph.

## References

- https://ai.pydantic.dev/graph/
- https://ai.pydantic.dev/multi-agent-applications/
