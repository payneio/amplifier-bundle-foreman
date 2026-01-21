# Research Worker Bundle

Specialized worker bundle for research and information gathering tasks in the Foreman architecture.

## Overview

The Research Worker Bundle provides a read-only, web-enabled environment for investigating questions and gathering information. It's designed to be spawned by the Foreman orchestrator to handle research tasks through an issue queue.

### Key Features

- **Web Research**: Full access to web search and URL fetching
- **Read-Only Access**: Can read all project files but cannot modify anything
- **Information Synthesis**: Transforms raw research into actionable insights
- **Issue-Driven**: Receives work via issue queue, updates status when complete
- **Structured Output**: Clear findings with recommendations and sources

## Installation

```bash
# Install from source
pip install -e .

# Or as dependency in foreman config
orchestrator:
  config:
    worker_pools:
      - name: research-pool
        worker_bundle: git+https://github.com/your-org/amplifier-bundle-research-worker@v1.0.0
```

## Usage

### As Part of Foreman System

This bundle is designed to be spawned by the Foreman orchestrator:

```yaml
# foreman-bundle.md
orchestrator:
  module: orchestrator-foreman
  config:
    worker_pools:
      - name: research-pool
        worker_bundle: git+https://github.com/your-org/amplifier-bundle-research-worker@v1.0.0
        max_concurrent: 2
        route_types: [research, analysis, investigation]
```

The foreman will:
1. Create issues for research tasks
2. Spawn research workers to handle them
3. Monitor issue queue for completions
4. Report findings to user

### Worker Session Flow

```
Foreman creates issue:
  "Research Python async database libraries"
     ↓
Foreman spawns research-worker with issue context
     ↓
Worker searches web, reads docs
     ↓
Worker synthesizes findings
     ↓
Worker updates issue: "completed" with structured findings
     ↓
Session ends, foreman reports completion
```

### Issue Status Protocol

Workers communicate through issue status updates:

**Completed Research**:
```python
{
  "status": "completed",
  "result": """
Research Summary: Brief overview of findings

Key Findings:
• Finding 1 with supporting detail
• Finding 2 with supporting detail
• Finding 3 with supporting detail

Recommendations:
• Specific actionable recommendation 1
• Specific actionable recommendation 2

Sources:
• https://source1.com
• https://source2.com
"""
}
```

**Pending User Input**:
```python
{
  "status": "pending_user_input",
  "block_reason": """
Research scope unclear. Please clarify:

1. Specific area to focus on
2. Intended use of research
3. Any constraints or preferences

What I've found so far: [preliminary findings if any]
"""
}
```

## Capabilities

### Tools Available

| Tool | Purpose | Configuration |
|------|---------|---------------|
| `tool-web-search` | Search the internet | Full access |
| `tool-web-fetch` | Fetch URL content | Full access |
| `tool-filesystem` | Read files | Read-only (no write access) |
| `tool-issue` | Issue queue | Update status, query issues |

### What Workers CAN Do

✅ Search the web for information
✅ Fetch documentation and articles
✅ Read any file in the project
✅ Analyze and synthesize findings
✅ Update issue status

### What Workers CANNOT Do

❌ Write or modify files
❌ Execute code or commands
❌ Install packages
❌ Spawn other workers
❌ Make system changes

## Security Model

The research worker operates with read-only access to ensure safety:

### Filesystem Boundaries

```yaml
tools:
  - module: tool-filesystem
    config:
      allowed_write_paths: []  # Empty = read-only
```

This ensures workers cannot:
- Modify source code
- Change configurations
- Create files
- Delete content

### No Code Execution

Workers have no bash tool, meaning they cannot:
- Run scripts
- Execute commands
- Install packages
- Make system changes

### Full Web Access

Workers CAN access the internet for research:
- Search engines
- Documentation sites
- API references
- Tutorials and articles

## Research Workflow

### Research Process

1. **Understand Goal**: Parse issue, clarify scope
2. **Gather Information**: Web search, fetch docs, read local files
3. **Analyze**: Compare options, identify trade-offs
4. **Synthesize**: Structure findings with recommendations
5. **Update Status**: Document findings in issue result

### Output Structure

All research findings follow a consistent structure:

```markdown
## Research Summary
[2-3 sentence overview]

## Key Findings
• Finding 1: [Detail]
• Finding 2: [Detail]
• Finding 3: [Detail]

## Recommendations
• Specific recommendation 1
• Specific recommendation 2

## Trade-offs
• Option A: pros [X, Y], cons [Z]
• Option B: pros [A, B], cons [C]

## Sources
• https://official-docs.com
• https://github.com/project/repo
• https://blog.com/article
```

## Example Use Cases

### Library Comparison Research

```
User: "Research Python rate limiting libraries"
     ↓
Foreman creates issue → spawns research-worker
     ↓
Worker searches, compares libraries
     ↓
Worker provides:
  • Top 3 options with pros/cons
  • Recommendation based on project stack
  • Source links for each option
```

### API Documentation Investigation

```
User: "Find authentication flow for third-party API"
     ↓
Foreman creates issue → spawns research-worker
     ↓
Worker fetches API docs, reads examples
     ↓
Worker provides:
  • Step-by-step auth flow
  • Required credentials and setup
  • Rate limits and constraints
  • Example implementation approach
```

### Best Practices Research

```
User: "Research async Python best practices"
     ↓
Foreman creates issue → spawns research-worker
     ↓
Worker searches recent articles, official docs
     ↓
Worker provides:
  • Key patterns (with examples)
  • Common pitfalls to avoid
  • Performance considerations
  • Source articles and docs
```

## Configuration Examples

### Basic Research Pool

```yaml
worker_pools:
  - name: research-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-research-worker@v1.0.0
    max_concurrent: 2
    route_types: [research, analysis, investigation]
```

### Specialized Research Pools

```yaml
worker_pools:
  # Technical research
  - name: tech-research-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-research-worker@v1.0.0
    max_concurrent: 2
    route_types: [library-research, api-research, architecture-research]
  
  # Competitive analysis
  - name: competitive-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-research-worker@v1.0.0
    max_concurrent: 1
    route_types: [competitive-analysis, market-research]
```

## Extending This Bundle

### Custom Tool Configuration

Add domain-specific search or data sources:

```yaml
# research-worker-custom.md
includes:
  - bundle: git+https://github.com/your-org/amplifier-bundle-research-worker@v1.0.0

tools:
  # Add specialized search tool
  - module: tool-academic-search
    config:
      api_key: ${SCHOLAR_API_KEY}
  
  # Add database query (read-only)
  - module: tool-database-query
    config:
      connection_string: ${DATABASE_URL}
      read_only: true
```

### Custom Instructions

Extend research guidelines for your domain:

```yaml
# research-worker-custom.md
includes:
  - bundle: amplifier-bundle-research-worker

---

# Additional Research Guidelines

For this project:
- Always check compatibility with Python 3.10+
- Prefer libraries with active maintenance (commits in last 6 months)
- Note licensing (must be MIT or Apache 2.0)
- Check for existing usage in our stack

@research-worker:context/instructions.md
```

## Comparison with Other Workers

| Worker Type | Focus | Tools | When to Use |
|-------------|-------|-------|-------------|
| **Research Worker** | Information gathering | Web, read files | Investigation, analysis |
| Coding Worker | Implementation | Files (write), bash | Code changes, bug fixes |
| Testing Worker | QA and validation | Test runners, coverage | Comprehensive testing |
| Privileged Worker | Unrestricted tasks | All tools | Config changes, installs |

## Best Practices

### For Foreman Configuration

1. **Pool sizing**: 1-2 workers usually sufficient (research is I/O bound)
2. **Type routing**: Use specific types (api-research, library-research)
3. **Timeout**: Research may take longer - configure appropriately

### For Issue Creation

1. **Clear questions**: "Research X for Y purpose" not just "Research X"
2. **Context**: Include what you already know or have tried
3. **Constraints**: Specify any requirements (language, license, etc.)
4. **Purpose**: Why you need this research (helps focus findings)

### For Workers

1. **Multiple sources**: Don't rely on single source
2. **Check dates**: Prefer recent information
3. **Verify with docs**: Official documentation beats blog posts
4. **Cite sources**: Always include URLs
5. **Be actionable**: Focus on what can be done with findings

## Troubleshooting

### Worker Can't Find Information

**Expected**: Sometimes information genuinely doesn't exist
**Worker Response**: Documents what was searched and suggests alternatives

### Research Scope Too Broad

**Expected**: Some questions need clarification
**Worker Response**: Updates issue to `pending_user_input` with clarifying questions

### Outdated Information

**Prevention**: Workers prioritize recent sources and note dates
**Fix**: If noticed, update issue with note about outdated info found

### Network Issues

**Rare**: If web fetch fails, worker should note and try alternatives
**Fix**: Check network connectivity, API keys if needed

## Testing

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# With coverage
pytest --cov
```

### Manual Testing

Test the worker with a mock issue:

```python
# Create test issue
issue = {
    "id": "test-1",
    "title": "Research Python logging libraries",
    "description": "Compare standard logging vs loguru vs structlog",
    "metadata": {"type": "research"}
}

# Spawn worker
amplifier task execute \
  agent="research-worker" \
  instruction="Handle issue #test-1: Research Python logging libraries..."
```

## Quality Indicators

### Good Research Output

✅ Answers the question clearly
✅ Compares multiple options with trade-offs
✅ Makes specific recommendations
✅ Cites authoritative sources
✅ Relates to project context
✅ Identifies next actions

### Poor Research Output

❌ Just dumps search results
❌ No comparison or synthesis
❌ Generic recommendations
❌ No source citations
❌ Ignores project context
❌ Leaves next steps unclear

## Common Patterns

### Library Selection Research
Worker searches, compares features/maintenance/compatibility, recommends based on project stack.

### API Integration Research
Worker fetches docs, extracts auth flow, notes rate limits, suggests implementation approach.

### Architecture Investigation
Worker researches patterns, compares approaches, relates to project scale, recommends architecture.

### Error Investigation
Worker searches for error message, finds solutions, relates to project setup, suggests fixes.

## Contributing

Contributions welcome! Please:

1. Add tests for new features
2. Follow bundle conventions
3. Update documentation
4. Test with foreman integration

## License

MIT

## Related Projects

- [Foreman Bundle](https://github.com/your-org/amplifier-bundle-foreman) - Orchestrator
- [Coding Worker](https://github.com/your-org/amplifier-bundle-coding-worker) - Implementation
- [Testing Worker](https://github.com/your-org/amplifier-bundle-testing-worker) - QA tasks
- [Issue Bundle](https://github.com/your-org/amplifier-bundle-issues) - Issue queue

## Support

- GitHub Issues: [Report bugs](https://github.com/your-org/amplifier-bundle-research-worker/issues)
- Documentation: See `context/` directory
- Examples: See foreman bundle examples
