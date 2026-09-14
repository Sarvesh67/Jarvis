"""Every demo question, and every paraphrase of it, still works.

The demo set (`validate/demo_questions.yaml`) is a promise about what happens live, in
front of someone, when they type their own wording rather than the example. So each
question is asserted twice over: it must reach the SAME template under every paraphrase,
and that template must still return rows.

Routing goes through the model by default, which makes this suite a network test. It is
marked `demo` and skipped when the gateway is unreachable, so the rest of the suite stays
offline. Run it before a demo:

    .venv/bin/python -m pytest tests/test_demo.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lib  # noqa: E402

DEMO = yaml.safe_load((ROOT / "validate" / "demo_questions.yaml").read_text())
CASES = [(d["id"], q, d) for d in DEMO for q in d["asks"]]


@pytest.fixture(scope="session")
def gateway():
    """Skip rather than fail when the router's model is not reachable.

    A failure here would mean "the demo is broken", and an unplugged laptop is not that.
    """
    import httpx
    try:
        httpx.get("http://127.0.0.1:4000/health/liveliness", timeout=5.0)
    except Exception as e:                                     # pragma: no cover
        pytest.skip(f"LiteLLM gateway not reachable: {e}")
    try:
        drv = lib.neo4j_driver()
        with drv.session() as s:
            s.run("RETURN 1").single()
        drv.close()
    except Exception as e:                                     # pragma: no cover
        pytest.skip(f"Neo4j not reachable: {e}")


@pytest.mark.demo
@pytest.mark.parametrize("qid,question,spec", CASES, ids=[f"{i}:{q[:40]}" for i, q, _ in CASES])
def test_demo_question(gateway, qid, question, spec):
    from pipeline import router
    res = router.answer(question, limit=50)

    assert res.get("template") == spec["template"], (
        f"{qid}: {question!r} routed to {res.get('template') or res.get('answered_by')!r}, "
        f"expected {spec['template']!r} — a paraphrase changed the answer path")

    rows = res.get("rows") or []
    assert len(rows) >= spec["expect_min"], (
        f"{qid}: {question!r} returned {len(rows)} rows, expected >= {spec['expect_min']}")

    if spec.get("expect_empty_reason"):
        assert not rows, f"{qid}: expected this question to have no answer"
        reason = res.get("empty_reason") or ""
        assert len(reason) > 80, (
            f"{qid}: an unanswerable question must explain itself, got {reason!r}")


@pytest.mark.demo
def test_keyword_routing_survives_a_dead_model(gateway):
    """The demo must not go blank because the gateway is down mid-session.

    Keyword routing is the fallback, and it is weaker than the model — it is allowed to
    fall through to graph context rather than pick a template it only half-believes in.
    What it may NOT do is route to the WRONG template, because a template is what makes a
    regulatory claim.
    """
    from pipeline import router
    wrong = []
    for qid, question, spec in CASES:
        res = router.answer(question, use_model=False, limit=50)
        got = res.get("template")
        if got is not None and got != spec["template"]:
            wrong.append(f"{qid}: {question!r} -> {got!r} (wanted {spec['template']!r})")
    assert not wrong, "keyword fallback routed to the wrong template:\n  " + "\n  ".join(wrong)


@pytest.mark.demo
@pytest.mark.parametrize("qid,question,spec",
                         [(d["id"], d["asks"][0], d) for d in DEMO],
                         ids=[d["id"] for d in DEMO])
def test_agent_reaches_the_same_evidence(gateway, qid, question, spec):
    """The demo answers through the console's agent, not through the router directly.

    The router tests above prove the TOOL still works. This proves the thing a customer
    actually sees: that the agent picks the reviewed query the question needs, and that
    its prose rests on evidence rather than on the model's own knowledge of cosmetics
    regulation — which is exactly the failure this product exists to prevent.
    """
    from serve import agent
    res = agent.answer(question)

    assert res["reply"].strip(), f"{qid}: the agent returned no answer"
    assert res["evidence"], (
        f"{qid}: the agent answered {question!r} WITHOUT running a query. Prose with no "
        f"evidence behind it is the one output this product must never produce.")

    used = [e.get("template") for e in res["evidence"]]
    assert spec["template"] in used, (
        f"{qid}: expected the agent to run {spec['template']!r}, it ran {used!r}")


@pytest.mark.demo
def test_agent_carries_the_console_skill(gateway):
    """The demo's instructions ARE the console skill's, read from the skill file.

    A paraphrase kept in the repo drifts from the product within a week, and then the
    demo stops being the product. If the skill moves, this fails loudly rather than the
    demo quietly answering under different rules.
    """
    from serve import agent
    assert agent.SKILL_DIR.exists(), f"cosmetics skill missing at {agent.SKILL_DIR}"
    for marker in ("never write database queries",
                   "Never present registry presence as a licence",
                   "Never mix jurisdictions"):
        assert marker.lower() in agent.SYSTEM.lower(), (
            f"the skill no longer carries {marker!r} — the demo's guarantees changed")
