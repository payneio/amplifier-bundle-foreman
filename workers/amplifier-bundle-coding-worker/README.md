# Coding Worker Bundle

Specialized worker bundle for coding and implementation tasks in the Foreman architecture.

## Overview

The Coding Worker Bundle provides a focused, security-bounded environment for implementing code changes. It's designed to be spawned by the Foreman orchestrator to handle individual coding tasks through an issue queue.

### Key Features

- **Focused Capabilities**: File operations, code execution, quality checks - no web access
- **Security Boundaries**: Restricted write access to `src/**` and `tests/**` only
- **Issue-Driven**: Receives work via issue queue, updates status when complete
- **Self-Contained**: Implements, tests, and verifies changes independently
- **Quality-Focused**: Built-in Python code quality checks and testing

## Installation

```bash
# Install from source
pip install -e .

# Or as dependency in foreman config
orchestrator:
  config:
    worker_pools:
      - name: coding-pool
        worker_bundle: git+https://github.com/your-org/amplifier-bundle-coding-worker@v1.0.0
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
      - name: coding-pool
        worker_bundle: git+https://github.com/your-org/amplifier-bundle-coding-worker@v1.0.0
        max_concurrent: 3
        route_types: [coding, implementation, bugfix, refactor]
```

The foreman will:
1. Create issues for coding tasks
2. Spawn coding workers to handle them
3. Monitor issue queue for completions/blockers
4. Report progress to user

### Worker Session Flow

```
Foreman creates issue:
  "Add email validation to User model"
     ↓
Foreman spawns coding-worker with issue context
     ↓
Worker reads existing code
     ↓
Worker implements changes
     ↓
Worker runs tests and checks
     ↓
Worker updates issue: "completed"
     ↓
Session ends, foreman reports completion
```

### Issue Status Protocol

Workers communicate through issue status updates:

**Completed**:
```python
{
  "status": "completed",
  "result": "Added email validation to User model using email-validator. Modified src/models/user.py and tests/test_user.py. All tests passing."
}
```

**Blocked**:
```python
{
  "status": "blocked",
  "block_reason": "Implementation requires 'authlib' package not in requirements.txt. Should I add it?"
}
```

**Pending User Input**:
```python
{
  "status": "pending_user_input",
  "block_reason": "Caching strategy unclear. Options: (1) In-memory LRU (2) Redis (3) Memcached. Please specify."
}
```

## Capabilities

### Tools Available

| Tool | Purpose | Configuration |
|------|---------|---------------|
| `tool-filesystem` | Read/write files | Write restricted to `src/**`, `tests/**` |
| `tool-bash` | Run commands | Tests, checks, builds |
| `tool-issue` | Issue queue | Update status, query issues |
| `python-check` | Code quality | Linting, type checking, formatting |

### What Workers CAN Do

✅ Read any file in the project
✅ Write/edit files in `src/**` and `tests/**`
✅ Run bash commands (tests, checks, etc.)
✅ Update issue status
✅ Python code quality checks

### What Workers CANNOT Do

❌ Write outside `src/` and `tests/` (configs, docs, etc.)
❌ Install packages (`pip install`)
❌ Access the web
❌ Spawn other workers
❌ Make privileged system changes

## Security Model

The coding worker operates in a security-bounded environment:

### Filesystem Boundaries

```yaml
tools:
  - module: tool-filesystem
    config:
      allowed_write_paths:
        - "src/**"
        - "tests/**"
```

This prevents workers from accidentally (or maliciously):
- Modifying configuration files
- Changing dependencies
- Altering documentation
- Editing environment files

### No Network Access

Workers have no web tools, meaning they cannot:
- Fetch external resources
- Call APIs
- Download packages
- Exfiltrate data

### No Privilege Escalation

Workers cannot:
- Spawn other workers (no task tool)
- Modify their own configuration
- Access parent session context

## Development Workflow

### Creating a Coding Task

From foreman:
```
User: "Add input validation to signup form"
     ↓
Foreman creates issue with metadata: {type: "coding"}
     ↓
Routes to coding-pool
     ↓
Spawns coding-worker with issue context
```

### Worker Implementation Process

1. **Discovery**: Read issue, understand requirements, read existing code
2. **Implementation**: Make targeted changes following existing patterns
3. **Testing**: Run tests, fix failures, verify changes
4. **Status Update**: Update issue with completion status and summary

### Quality Gates

Workers run checks before marking complete:
- All tests passing
- No linting errors
- Type hints valid
- Code formatted consistently

## Configuration Examples

### Basic Coding Pool

```yaml
worker_pools:
  - name: coding-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-coding-worker@v1.0.0
    max_concurrent: 3
    route_types: [coding, implementation, bugfix]
```

### Multiple Coding Pools

```yaml
worker_pools:
  # Frontend coding
  - name: frontend-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-coding-worker@v1.0.0
    max_concurrent: 2
    route_types: [frontend, ui, javascript]
  
  # Backend coding
  - name: backend-pool
    worker_bundle: git+https://github.com/your-org/amplifier-bundle-coding-worker@v1.0.0
    max_concurrent: 3
    route_types: [backend, api, database]
```

### With Escalation

```yaml
routing:
  rules:
    # Normal coding tasks
    - if_metadata_type: [coding]
      then_pool: coding-pool
    
    # Escalate blocked coding tasks to privileged pool
    - if_status: blocked
      and_retry_count_gte: 2
      then_pool: privileged-pool
```

## Extending This Bundle

### Custom Tool Configuration

Add domain-specific tools for your project:

```yaml
# coding-worker-custom.md
includes:
  - bundle: git+https://github.com/your-org/amplifier-bundle-coding-worker@v1.0.0

tools:
  # Add database tool for migrations
  - module: tool-database
    config:
      connection_string: ${DATABASE_URL}
  
  # Add project-specific linter
  - module: tool-custom-lint
```

### Custom Instructions

Override or extend worker instructions:

```yaml
# coding-worker-custom.md
includes:
  - bundle: amplifier-bundle-coding-worker

---

# Additional Instructions

For this project:
- Always use dataclasses for models
- Follow naming convention: use_snake_case
- Import order: stdlib → third-party → local

@coding-worker:context/instructions.md
```

## Comparison with Other Workers

| Worker Type | Focus | Tools | When to Use |
|-------------|-------|-------|-------------|
| **Coding Worker** | Implementation | Files, bash, checks | Code changes, bug fixes |
| Research Worker | Information gathering | Web, search | Investigation, analysis |
| Testing Worker | QA and validation | Test runners, coverage | Comprehensive testing |
| Privileged Worker | Unrestricted tasks | All tools | Blocked tasks, configs |

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
    "title": "Add function to calculate sum",
    "description": "Create src/utils/math.py with sum_numbers() function",
    "metadata": {"type": "coding"}
}

# Spawn worker
amplifier task execute \
  agent="coding-worker" \
  instruction="Handle issue #test-1: Add function to calculate sum..."
```

## Troubleshooting

### Worker Not Completing Issues

**Check**: Is the worker updating issue status before ending?
**Fix**: Ensure issue tool is available and worker has issue ID

### Worker Blocked on Permissions

**Check**: Is the file path within `src/**` or `tests/**`?
**Fix**: If need access to other paths, escalate to privileged worker

### Tests Failing in Worker

**Check**: Are tests run with correct working directory?
**Fix**: Use `pytest tests/` not relative paths

### Worker Can't Install Packages

**Expected**: Workers cannot install packages (security boundary)
**Fix**: Add packages to requirements.txt at project level

## Best Practices

### For Foreman Configuration

1. **Pool sizing**: Set `max_concurrent` based on your workload
2. **Type routing**: Use specific types for better routing
3. **Escalation**: Configure privileged pool for blocked tasks

### For Issue Creation

1. **Clear titles**: "Add email validation" not "Update user stuff"
2. **Detailed descriptions**: Include acceptance criteria
3. **Type metadata**: Set accurate type for routing
4. **Priority**: Use priority field for scheduling

### For Workers

1. **Read first**: Understand existing code before changing
2. **Test thoroughly**: Run all relevant tests
3. **Clear status**: Be specific in completion messages
4. **Ask early**: Don't stay blocked - update status promptly

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
- [Research Worker](https://github.com/your-org/amplifier-bundle-research-worker) - Web research
- [Testing Worker](https://github.com/your-org/amplifier-bundle-testing-worker) - QA tasks
- [Issue Bundle](https://github.com/your-org/amplifier-bundle-issues) - Issue queue

## Support

- GitHub Issues: [Report bugs](https://github.com/your-org/amplifier-bundle-coding-worker/issues)
- Documentation: See `context/` directory
- Examples: See foreman bundle examples
