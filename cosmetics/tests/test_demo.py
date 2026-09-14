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
