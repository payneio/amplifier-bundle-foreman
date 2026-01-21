"""Example tests for research-worker bundle.

This bundle is primarily configuration-based (bundle.md), so tests
focus on verifying the bundle can be loaded and configured correctly.
"""



def test_bundle_exists():
    """Test that bundle.md exists and is readable."""
    from pathlib import Path

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    assert bundle_path.exists(), "bundle.md should exist"
    assert bundle_path.is_file(), "bundle.md should be a file"

    # Verify it's readable
    content = bundle_path.read_text()
    assert len(content) > 0, "bundle.md should have content"
    assert "research-worker" in content, "bundle.md should define research-worker"


def test_bundle_has_required_sections():
    """Test that bundle.md has required sections."""
    from pathlib import Path

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    content = bundle_path.read_text()

    # Check for required YAML frontmatter
    assert "---" in content, "bundle.md should have YAML frontmatter"
    assert "bundle:" in content, "bundle.md should define bundle section"
    assert "name: research-worker" in content, "bundle should be named research-worker"

    # Check for required sections
    assert "tools:" in content, "bundle.md should define tools"
    assert "tool-web-search" in content, "bundle should include web-search tool"
    assert "tool-web-fetch" in content, "bundle should include web-fetch tool"
    assert "tool-issue" in content, "bundle should include issue tool"


def test_bundle_has_security_config():
    """Test that filesystem tool is read-only."""
    from pathlib import Path

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    content = bundle_path.read_text()

    # Check for filesystem read-only configuration
    assert "tool-filesystem" in content, "bundle should include filesystem tool"
    assert "allowed_write_paths: []" in content, (
        "filesystem should be read-only (empty write list)"
    )


def test_bundle_has_web_tools():
    """Test that web research tools are present."""
    from pathlib import Path

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    content = bundle_path.read_text()

    # Check for web tools
    assert "tool-web-search" in content, "bundle should have web-search tool"
    assert "tool-web-fetch" in content, "bundle should have web-fetch tool"


def test_bundle_no_bash_tool():
    """Test that bash tool is NOT present (read-only worker)."""
    from pathlib import Path

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    content = bundle_path.read_text()

    # Research worker should NOT have bash tool (no code execution)
    assert "tool-bash" not in content, "research-worker should not have bash tool"


def test_context_instructions_exist():
    """Test that context instructions exist."""
    from pathlib import Path

    instructions_path = Path(__file__).parent.parent / "context" / "instructions.md"
    assert instructions_path.exists(), "context/instructions.md should exist"

    content = instructions_path.read_text()
    assert len(content) > 0, "instructions should have content"
    assert "research worker" in content.lower(), (
        "instructions should mention research worker"
    )


def test_readme_exists():
    """Test that README exists and has basic sections."""
    from pathlib import Path

    readme_path = Path(__file__).parent.parent / "README.md"
    assert readme_path.exists(), "README.md should exist"

    content = readme_path.read_text()
    assert "# Research Worker Bundle" in content, "README should have title"
    assert "## Overview" in content, "README should have overview"
    assert "## Installation" in content, "README should have installation"
    assert "## Usage" in content, "README should have usage section"
