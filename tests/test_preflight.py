"""Pre-deployment sanity checks.

Run before `./setup/00_run_all.sh` to catch:
  - Bash syntax errors in setup scripts
  - Python compile errors
  - Malformed plists
  - YAML config issues
  - Missing required files
  - Broken inter-file references (placeholders not replaced, paths drifted)

These tests are fast (<5s) and run with no dependencies on services.
"""

from __future__ import annotations

import plistlib
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- file existence
class TestRequiredFiles:
    @pytest.mark.parametrize("relpath", [
        "setup/_lib.sh",
        "setup/00_run_all.sh",
        "setup/01_create_user.sh",
        "setup/02_fortress_acls.sh",
        "setup/03_install_ollama.sh",
        "setup/04_messages_bridge.sh",
        "setup/05_brain_stub.sh",
        "setup/06_install_hermes.sh",
        "brain-stub/server.py",
        "brain-stub/requirements.txt",
        "launchagents/com.hedgefund.brain-stub.plist",
        "launchagents/com.sarvesh.messages-bridge.plist",
        "hermes-config/config.yaml",
        "hermes-config/Modelfile.llama-finance",
        "skills/phase0-verify/SKILL.md",
        "skills/phase0-verify/scripts/phase0_verify.py",
        "bench/bench_llama_finance.py",
        "RUNBOOK.md",
    ])
    def test_file_exists(self, relpath):
        path = ROOT / relpath
        assert path.exists(), f"{relpath} missing"
        assert path.stat().st_size > 0, f"{relpath} is empty"


# ---------------------------------------------------------------- bash syntax
class TestBashSyntax:
    @pytest.mark.parametrize("script", sorted(ROOT.glob("setup/*.sh")))
    def test_bash_syntax_clean(self, script):
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"{script.name}: {result.stderr}"

    def test_setup_scripts_executable(self):
        for s in ROOT.glob("setup/*.sh"):
            # _lib.sh is sourced, doesn't need +x; the rest do.
            if s.name.startswith("_"):
                continue
            assert s.stat().st_mode & 0o111, f"{s.name} not executable"


# ---------------------------------------------------------------- python compile
class TestPythonCompile:
    @pytest.mark.parametrize("py", [
        "brain-stub/server.py",
        "skills/phase0-verify/scripts/phase0_verify.py",
        "bench/bench_llama_finance.py",
    ])
    def test_compiles(self, py):
        result = subprocess.run(
            ["python3", "-m", "py_compile", str(ROOT / py)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------- plist
class TestLaunchAgents:
    @pytest.mark.parametrize("plist", sorted(ROOT.glob("launchagents/*.plist")))
    def test_plist_parses(self, plist):
        with plist.open("rb") as f:
            data = plistlib.load(f)
        assert "Label" in data
        assert "ProgramArguments" in data
        assert isinstance(data["ProgramArguments"], list)

    def test_brain_stub_plist_targets_router(self):
        with (ROOT / "launchagents/com.hedgefund.brain-stub.plist").open("rb") as f:
            data = plistlib.load(f)
        # Should run server.py from the brain-stub venv
        args = data["ProgramArguments"]
        assert any("server.py" in a for a in args)
        assert any(".venv" in a for a in args)
        assert data.get("UserName") == "hedgefund"

    def test_messages_bridge_has_placeholders(self):
        # The messages-bridge plist is a TEMPLATE — placeholders are filled by setup/04.
        body = (ROOT / "launchagents/com.sarvesh.messages-bridge.plist").read_text()
        assert "__PROXY_BIN__" in body
        assert "__MAC_MSG_BIN__" in body
        assert "__HOME__" in body


# ---------------------------------------------------------------- yaml
class TestHermesConfig:
    @pytest.fixture
    def cfg(self):
        with (ROOT / "hermes-config/config.yaml").open() as f:
            return yaml.safe_load(f)

    def test_yaml_parses(self, cfg):
        assert isinstance(cfg, dict)

    def test_brain_points_at_router(self, cfg):
        assert cfg["model"]["base_url"] == "http://127.0.0.1:8765/v1"

    def test_brain_id_is_auto(self, cfg):
        # "auto" tells the router to pick a tier per call.
        assert cfg["model"]["id"] == "auto"

    def test_messages_mcp_points_at_bridge(self, cfg):
        # mcp-proxy exposes SSE on /sse, not /. Hitting the bare host returns 404
        # and every tool call silently fails — the /sse suffix is mandatory.
        msgs = cfg["mcp_servers"]["messages"]
        assert msgs["url"] == "http://127.0.0.1:5000/sse"

    def test_command_allowlist_no_dangerous(self, cfg):
        bad = ["sudo", "rm -rf", "osascript", "networksetup", "csrutil"]
        for entry in cfg["command_allowlist"]:
            for danger in bad:
                assert danger not in entry, f"dangerous: {entry}"


# ---------------------------------------------------------------- modelfile
class TestModelfile:
    def test_has_placeholder(self):
        body = (ROOT / "hermes-config/Modelfile.llama-finance").read_text()
        # __GGUF_PATH__ replaced at install time
        assert "__GGUF_PATH__" in body
        assert "FROM " in body

    def test_has_llama3_template(self):
        body = (ROOT / "hermes-config/Modelfile.llama-finance").read_text()
        assert "<|begin_of_text|>" in body
        assert "<|start_header_id|>" in body
        assert "<|eot_id|>" in body

    def test_temperature_low_for_extraction(self):
        body = (ROOT / "hermes-config/Modelfile.llama-finance").read_text()
        m = re.search(r"PARAMETER temperature ([\d.]+)", body)
        assert m, "temperature must be set"
        assert float(m.group(1)) <= 0.3, "temperature too high for structured extraction"


# ---------------------------------------------------------------- skill
class TestPhase0VerifySkill:
    def test_skill_md_has_frontmatter(self):
        body = (ROOT / "skills/phase0-verify/SKILL.md").read_text()
        assert body.startswith("---\n")
        # frontmatter requires name + description per Hermes docs
        assert re.search(r"^name:\s*phase0-verify", body, re.MULTILINE)
        assert re.search(r"^description:", body, re.MULTILINE)

    def test_frontmatter_within_2k(self):
        body = (ROOT / "skills/phase0-verify/SKILL.md").read_text()
        # Hermes has a 2KB truncation bug for the closing ---
        end = body.find("---", 4)  # skip opening ---
        assert end > 0 and end < 2000, "frontmatter must close within first 2KB"

    def test_skill_md_under_5k_tokens(self):
        # Rough: ~4 chars per token. 5K tokens ≈ 20K chars. Hermes recommends terse.
        body = (ROOT / "skills/phase0-verify/SKILL.md").read_text()
        assert len(body) < 20_000


# ---------------------------------------------------------------- cross-file
class TestCrossFileConsistency:
    """Things that need to agree across files (drift catches bugs early)."""

    def test_router_port_consistent(self):
        cfg = yaml.safe_load((ROOT / "hermes-config/config.yaml").read_text())
        with (ROOT / "launchagents/com.hedgefund.brain-stub.plist").open("rb") as f:
            plist = plistlib.load(f)
        # Hermes config points at 8765
        assert "8765" in cfg["model"]["base_url"]
        # And the server binds 8765 (via the python script)
        server_src = (ROOT / "brain-stub/server.py").read_text()
        assert 'port=8765' in server_src or '"127.0.0.1", port=8765' in server_src

    def test_messages_port_consistent(self):
        cfg = yaml.safe_load((ROOT / "hermes-config/config.yaml").read_text())
        plist_body = (ROOT / "launchagents/com.sarvesh.messages-bridge.plist").read_text()
        assert "5000" in cfg["mcp_servers"]["messages"]["url"]
        assert "<string>5000</string>" in plist_body

    def test_bridge_url_default(self):
        # Router default points at the CC bridge on 3456.
        src = (ROOT / "brain-stub/server.py").read_text()
        assert "127.0.0.1:3456" in src
