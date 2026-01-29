# App Layer Setup for Foreman Bundle

The foreman orchestrator requires the app layer to register the `session.spawn` capability before worker spawning will function. This document explains the required setup.

## Why App Layer Registration?

The Amplifier architecture separates **mechanism** (provided by foundation) from **policy** (provided by app layer):

- **Foundation** provides `PreparedBundle.spawn()` - the mechanism for creating child sessions
- **App layer** registers `session.spawn` capability - the policy for how agents are resolved
- **Orchestrator** consumes `session.spawn` - uses the registered capability

This separation allows different apps to customize spawning behavior without changing foundation code.

## Required Capability

The foreman orchestrator calls:

```python
spawn = self._coordinator.get_capability("session.spawn")
result = await spawn(
    agent_name=worker_bundle,      # Bundle path/URI
    instruction=worker_prompt,      # Task instruction
    parent_session=parent_session,  # For lineage/inheritance
    agent_configs={},               # Not used for bundle spawning
)
```

## Example App Setup

### Minimal Setup

```python
from amplifier_foundation import load_bundle

async def run_foreman():
    # Load and prepare the foreman bundle
    bundle = await load_bundle("./bundle.md")
    prepared = await bundle.prepare()
    session = await prepared.create_session()
    
    # Register session.spawn capability
    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session,
        agent_configs: dict | None = None,
        **kwargs,
    ) -> dict:
        """Spawn a worker session from a bundle."""
        # Load the worker bundle
        worker_bundle = await load_bundle(agent_name)
        
        # Use PreparedBundle.spawn() to create and run the session
        return await prepared.spawn(
            child_bundle=worker_bundle,
            instruction=instruction,
            parent_session=parent_session,
            compose=True,  # Inherit providers from parent
        )
    
    session.coordinator.register_capability("session.spawn", spawn_capability)
    
    # Run the foreman session
    async with session:
        response = await session.execute("Build me a calculator app")
        print(response)

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_foreman())
```

### With Provider Preferences

```python
async def spawn_capability(
    agent_name: str,
    instruction: str,
    parent_session,
    agent_configs: dict | None = None,
    provider_preferences: list | None = None,
    **kwargs,
) -> dict:
    """Spawn with optional provider preferences."""
    worker_bundle = await load_bundle(agent_name)
    
    return await prepared.spawn(
        child_bundle=worker_bundle,
        instruction=instruction,
        parent_session=parent_session,
        compose=True,
        provider_preferences=provider_preferences,  # Pass through
    )

session.coordinator.register_capability("session.spawn", spawn_capability)
```

### With Custom Bundle Resolution

```python
# Define custom bundle resolution logic
BUNDLE_ALIASES = {
    "coding-worker": "git+https://github.com/org/foreman@main#subdirectory=workers/coding-worker",
    "research-worker": "git+https://github.com/org/foreman@main#subdirectory=workers/research-worker",
}

async def spawn_capability(
    agent_name: str,
    instruction: str,
    parent_session,
    **kwargs,
) -> dict:
    """Spawn with bundle alias resolution."""
    # Resolve alias to full path
    bundle_path = BUNDLE_ALIASES.get(agent_name, agent_name)
    worker_bundle = await load_bundle(bundle_path)
    
    return await prepared.spawn(
        child_bundle=worker_bundle,
        instruction=instruction,
        parent_session=parent_session,
    )

session.coordinator.register_capability("session.spawn", spawn_capability)
```

## Integration with amplifier-app-cli

If you're using `amplifier-app-cli`, the spawn capability should be registered during session setup. Check the CLI's session initialization code for where to add the registration.

The CLI may already register a `session.spawn` capability for the task tool - in that case, the foreman should work automatically.

## Troubleshooting

### "Required capability 'session.spawn' not registered"

This error means the app layer hasn't registered the spawn capability. Ensure:

1. Your app calls `session.coordinator.register_capability("session.spawn", spawn_fn)`
2. This registration happens **before** the foreman orchestrator runs
3. The spawn function signature matches what the foreman expects

### Workers Not Starting

If spawn is registered but workers don't start:

1. Check the spawn function is actually being called (add logging)
2. Verify `load_bundle()` can resolve the worker bundle paths
3. Ensure `prepared.spawn()` has access to required providers

### Worker Errors Not Visible

Worker errors are tracked in `orchestrator._spawn_errors` and reported to the user on the next turn. Check:

1. The foreman's response mentions spawn errors
2. Look at the logs for `"Worker execution failed"` messages

## Architecture Reference

```
┌─────────────────────────────────────────────────────────────────┐
│                      YOUR APP (Policy)                          │
│                                                                 │
│  async def spawn_capability(...):                               │
│      worker_bundle = await load_bundle(agent_name)              │
│      return await prepared.spawn(child_bundle=worker_bundle...) │
│                                                                 │
│  session.coordinator.register_capability("session.spawn", ...)  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ registers
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FOREMAN ORCHESTRATOR                         │
│                                                                 │
│  spawn = coordinator.get_capability("session.spawn")            │
│  result = await spawn(agent_name=worker_bundle, ...)            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ calls
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                 AMPLIFIER FOUNDATION (Mechanism)                │
│                                                                 │
│  PreparedBundle.spawn():                                        │
│    - Composes bundles                                           │
│    - Creates mount plan                                         │
│    - Creates AmplifierSession with parent_id                    │
│    - Mounts module resolver                                     │
│    - Initializes and executes session                           │
│    - Returns result                                             │
└─────────────────────────────────────────────────────────────────┘
```

## See Also

- `.working/the-fix.md` - Detailed analysis of the spawn architecture
- `tests/test_spawn_patterns.py` - Test patterns for session spawning
- `amplifier_foundation/bundle.py:1111-1289` - PreparedBundle.spawn() implementation
