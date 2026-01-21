"""Example tests for testing-worker bundle.

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
    assert "testing-worker" in content, "bundle.md should define testing-worker"


def test_bundle_has_required_sections():
    """Test that bundle.md has required sections."""
    from pathlib import Path

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    content = bundle_path.read_text()

    # Check for required YAML frontmatter
    assert "---" in content, "bundle.md should have YAML frontmatter"
    assert "bundle:" in content, "bundle.md should define bundle section"
    assert "name: testing-worker" in content, "bundle should be named testing-worker"

    # Check for required sections
    assert "tools:" in content, "bundle.md should define tools"
    assert "tool-filesystem" in content, "bundle should include filesystem tool"
    assert "tool-bash" in content, "bundle should include bash tool"
    assert "tool-issue" in content, "bundle should include issue tool"
    assert "python-check" in content, "bundle should include python-check"


def test_bundle_has_security_config():
    """Test that filesystem tool has write restrictions to tests only."""
    from pathlib import Path
    import re

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    content = bundle_path.read_text()

    # Check for filesystem write restrictions
    assert "allowed_write_paths" in content, (
        "filesystem tool should have write restrictions"
    )
    assert "tests/**" in content, "should allow writing to tests/"

    # Extract the allowed_write_paths section and verify src/** is NOT in it
    # The pattern matches the YAML list under allowed_write_paths
    write_paths_match = re.search(
        r'allowed_write_paths:\s*\n((?:\s+-\s+"[^"]+"\s*\n?)+)',
        content
    )
    if write_paths_match:
        write_paths_section = write_paths_match.group(1)
        assert "src/**" not in write_paths_section, (
            "testing-worker should not have src/** in allowed_write_paths"
        )


def test_bundle_has_testing_tools():
    """Test that testing-specific tools are present."""
    from pathlib import Path

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    content = bundle_path.read_text()

    # Check for testing tools
    assert "tool-bash" in content, "bundle should have bash tool for running tests"
    assert "python-check" in content, "bundle should have python-check for code quality"


def test_bundle_no_web_tools():
    """Test that web tools are NOT present (not needed for testing)."""
    from pathlib import Path

    bundle_path = Path(__file__).parent.parent / "bundle.md"
    content = bundle_path.read_text()

    # Testing worker should NOT have web tools
    assert "tool-web-search" not in content, "testing-worker should not have web-search"
    assert "tool-web-fetch" not in content, "testing-worker should not have web-fetch"


def test_context_instructions_exist():
    """Test that context instructions exist."""
    from pathlib import Path

    instructions_path = Path(__file__).parent.parent / "context" / "instructions.md"
    assert instructions_path.exists(), "context/instructions.md should exist"

    content = instructions_path.read_text()
    assert len(content) > 0, "instructions should have content"
    assert "testing worker" in content.lower(), (
        "instructions should mention testing worker"
    )


def test_readme_exists():
    """Test that README exists and has basic sections."""
    from pathlib import Path

    readme_path = Path(__file__).parent.parent / "README.md"
    assert readme_path.exists(), "README.md should exist"

    content = readme_path.read_text()
    assert "# Testing Worker Bundle" in content, "README should have title"
    assert "## Overview" in content, "README should have overview"
    assert "## Installation" in content, "README should have installation"
    assert "## Usage" in content, "README should have usage section"
