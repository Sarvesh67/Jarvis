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

import pathlib
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


FIRST_ASKS = [(d["id"], d["asks"][0], d) for d in DEMO]


@pytest.mark.demo
@pytest.mark.parametrize("qid,question,spec", FIRST_ASKS, ids=[f"agent:{i}" for i, _, _ in FIRST_ASKS])
def test_demo_agent_path(gateway, qid, question, spec):
    """The live demo does not answer through `router.answer`: serve/agent.py lets the model
    pick templates from the table in the skill. The two drifted once — the skill never
    listed the distance templates, so every "near" question on the demo lost its ranking
    while the test above stayed green. One phrasing per question, through the agent."""
    from serve import agent
    done = next(ev for ev in agent.stream(question) if ev["type"] == "done")
    used = [e.get("template") for e in done["evidence"]]
    assert spec["template"] in used, (
        f"{qid}: the demo agent answered {question!r} with {used}, expected {spec['template']!r}")


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
    """The demo's instructions ARE the console skill's, read from the versioned file.

    The demo and the console agent load the same `cosmetics/skills/` directory — the
    console through a symlink from ~/.hermes. A paraphrase kept separately drifts from
    the product within a week, and then the demo stops being the product.

    The guarantees asserted here are v2's, and they are deliberately NOT "the model never
    writes a query": v2 lets it, under a guard, for counting and ranking. What must hold
    is the claim-based rule that replaced the structural one — a REGULATORY claim may
    still only come from a reviewed template.
    """
    from serve import agent
    assert agent.SKILL_DIR.exists(), f"cosmetics skill missing at {agent.SKILL_DIR}"
    for marker in ("Regulatory claims come from templates only",
                   "Registry presence is not permission to manufacture",
                   "Never mix jurisdictions",
                   "Tier 4 never answers a licence, compliance or restriction question"):
        assert marker.lower() in agent.SYSTEM.lower(), (
            f"the skill no longer carries {marker!r} — the demo's guarantees changed")


@pytest.mark.demo
def test_the_console_loads_the_same_skill_as_the_demo(gateway):
    """~/.hermes must resolve to the repo copy, or the two answer under different rules.

    This is the failure the wiring exists to prevent, and it is silent: a stale directory
    in the profile keeps working, so the console goes on answering under an older
    contract while the repo and the demo have moved on. That is exactly what had
    happened — a v2 skill sat one level down from the v1 the console was loading.
    """
    from serve import agent
    profile = (pathlib.Path.home() / ".hermes" / "profiles" / "cosmetics" / "skills")
    if not profile.exists():
        pytest.skip("no Hermes profile on this machine")

    live = profile / "jarvis-cosmetics-search"
    assert live.resolve() == agent.SKILL_DIR.resolve(), (
        f"the console loads {live.resolve()} but the demo loads "
        f"{agent.SKILL_DIR.resolve()} — re-run setup/09_demo.sh")

    # jarvis-knowledge is shared by four profiles, so it lives at the repo root. The
    # cosmetics profile carries it but must never answer a cosmetics question with it:
    # that skill's graphs are hedgefund and msme, and cosmetics is deliberately outside
    # the Cognee layer. Asserting the link keeps the copy under review; asserting the
    # absence keeps the boundary honest.
    shared = profile / "jarvis-knowledge"
    if shared.exists():
        repo_shared = agent.SKILL_DIR.parent / "jarvis-knowledge"
        assert shared.resolve() == repo_shared.resolve(), (
            f"jarvis-knowledge in the profile is {shared.resolve()}, not the repo copy "
            f"at {repo_shared} — re-run setup/09_demo.sh")
        assert "cosmetics" not in shared.joinpath("SKILL.md").read_text().lower(), (
            "jarvis-knowledge now mentions cosmetics. Its graphs are hedgefund and msme; "
            "the cosmetics graph is Neo4j and outside Cognee, so a cosmetics question "
            "must not be routed through it.")


def test_every_template_has_a_plain_stage_label():
    """The demo shows each stage while it runs. A template without a label shows its
    function name ("Running ingredient restriction india") to whoever is watching."""
    from serve import agent
    ids = [t["id"] for t in yaml.safe_load((ROOT / "pipeline" / "query_templates.yaml").read_text())]
    assert not [i for i in ids if i not in agent.STAGE]
