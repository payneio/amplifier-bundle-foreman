"""
Foreman Orchestrator - LLM-driven work coordination through issues and workers.

This orchestrator runs a standard agent loop but guides the LLM to coordinate work
through issues and background workers rather than doing everything directly.
"""

import asyncio
import json
import logging
from typing import Any

from amplifier_core import HookRegistry, ToolSpec
from amplifier_core.message_models import ChatRequest, Message

logger = logging.getLogger(__name__)

# Worker agent configurations - defined inline to ensure availability during spawn
# These configurations tell the spawn system how to set up each worker type
WORKER_AGENT_CONFIGS = {
    "foreman:coding-worker": {
        "description": "Coding worker for implementation tasks",
        "bundle": "git+https://github.com/microsoft/amplifier-foundation@main",
        "instructions": """You are a coding worker. Complete the assigned issue and update its status.

When done, use the issue_manager tool with:
- operation: "update"
- params:
  - issue_id: [the issue ID from your assignment]
  - status: "completed"
  - description: [summary of what you did]

If you need clarification, update the issue with status "pending_user_input".""",
    },
    "foreman:research-worker": {
        "description": "Research worker for analysis tasks",
        "bundle": "git+https://github.com/microsoft/amplifier-foundation@main",
        "instructions": """You are a research worker. Investigate the assigned issue thoroughly.

When done, use the issue_manager tool with:
- operation: "update"
- params:
  - issue_id: [the issue ID from your assignment]
  - status: "completed"
  - description: [your findings and analysis]

If you need clarification, update the issue with status "pending_user_input".""",
    },
    "foreman:testing-worker": {
        "description": "Testing worker for QA tasks",
        "bundle": "git+https://github.com/microsoft/amplifier-foundation@main",
        "instructions": """You are a testing worker. Verify the assigned issue.

When done, use the issue_manager tool with:
- operation: "update"
- params:
  - issue_id: [the issue ID from your assignment]
  - status: "completed"
  - description: [test results and findings]

If you need clarification, update the issue with status "pending_user_input".""",
    },
}

# System prompt that makes the LLM act as a foreman
FOREMAN_SYSTEM_PROMPT = """You are a FOREMAN - a work coordinator who delegates tasks to specialized workers.

## YOUR ROLE

You DO NOT do the work yourself. Instead, you:
1. **Break down requests** into discrete issues using the `issue_manager` tool
2. **Let workers handle** the actual implementation
3. **Report progress** from workers to the user
4. **Handle blockers** when workers need clarification

## WORKFLOW

### When user requests work (build, create, implement, fix, etc.):
1. Acknowledge the request briefly
2. Use `issue_manager` with operation="create" to create issues for each task
3. Report what issues were created
4. Workers will be spawned automatically for each issue

### When user asks for status:
1. Use `issue_manager` with operation="list" to get current issues
2. Summarize: in_progress, completed, blocked

### When user provides clarification for a blocked issue:
1. Use `issue_manager` with operation="update" to add the clarification
2. Worker will resume automatically

## CRITICAL RULES

- **NEVER use bash, write_file, or other tools directly for work requests**
- **ALWAYS create issues** for work that needs to be done
- **Keep responses brief** - workers do the heavy lifting
- **Use emojis** for status: 📋 new work, ✅ completed, ⏳ in progress, ⚠️ blocked

## ISSUE TYPES

Valid issue types are: `task`, `feature`, `bug`, `epic`, `chore`
- Use `task` for implementation work
- Use `feature` for new functionality
- Use `bug` for fixes
- Use `chore` for maintenance/setup

## EXAMPLE

User: "Build me a calculator app"

You should respond:
"📋 Creating issues for calculator app...

Created 3 issues:
- Issue #1: Set up project structure (task)
- Issue #2: Implement calculator logic (feature)
- Issue #3: Create CLI interface (task)

🚀 Workers are being dispatched. I'll keep you posted on progress!"

Then CREATE those issues using the issue_manager tool with the correct issue_type.
"""


class ForemanOrchestrator:
    """Orchestrator that guides the LLM to coordinate work through issues and workers."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the foreman orchestrator."""
        self.config = config
        self.worker_pools = config.get("worker_pools", [])
        self.routing_config = config.get("routing", {})
        self.max_iterations = config.get("max_iterations", 20)

        # Track spawned workers to avoid duplicates
        self._spawned_issues: set[str] = set()

        # Store coordinator for worker spawning
        self._coordinator: Any = None

    async def execute(
        self,
        prompt: str,
        context: Any,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: HookRegistry,
        coordinator: Any = None,
    ) -> str:
        """
        Execute the foreman agent loop.

        This runs a standard LLM conversation loop but:
        1. Injects foreman system prompt to guide behavior
        2. Monitors issue_manager calls to auto-spawn workers
        3. Reports progress from background workers
        """
        self._coordinator = coordinator

        # Get primary provider
        provider_name = next(iter(providers.keys()), None)
        if not provider_name:
            return "No LLM provider available."

        provider = providers[provider_name]

        # Check for worker updates before processing
        issue_tool = tools.get("issue_manager")
        progress_report = ""
        if issue_tool:
            progress_report = await self._check_worker_progress(issue_tool)

        # Build messages with foreman system prompt
        messages = await self._build_messages(prompt, context, progress_report)

        # Get tool specs
        tool_specs = self._get_tool_specs(tools)

        # Run the agent loop
        final_response = ""
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            # Call LLM - provider.complete() requires ChatRequest
            try:
                # Convert dict messages to Message objects
                message_objects = [Message(**m) for m in messages]
                request = ChatRequest(messages=message_objects, tools=tool_specs)
                response = await provider.complete(request)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                return f"Error communicating with LLM: {e}"

            # Extract text content from response content blocks
            if response.content:
                text_parts = []
                for block in response.content:
                    if hasattr(block, "text"):
                        text_parts.append(block.text)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                if text_parts:
                    final_response = "\n".join(text_parts)

            # Handle tool calls
            if response.tool_calls:
                # Build assistant message content from response
                assistant_content: list[Any] = []
                if response.content and isinstance(response.content, list):
                    for block in response.content:
                        if hasattr(block, "model_dump"):
                            assistant_content.append(block.model_dump())
                        else:
                            assistant_content.append(block)
                elif final_response:
                    assistant_content.append({"type": "text", "text": final_response})

                # Add assistant message with tool_calls as SEPARATE field (not in content!)
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_content if assistant_content else "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "tool": tc.name,
                            "arguments": tc.arguments,
                        }
                        for tc in response.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Execute tools and collect results
                tool_results = await self._execute_tools(
                    response.tool_calls, tools, issue_tool, hooks
                )

                # Add tool results as role="tool" messages (not user messages!)
                for result in tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result["tool_call_id"],
                            "content": result["content"],
                        }
                    )

                # Continue loop for more LLM processing
                continue

            # No tool calls - we're done
            break

        # Store conversation in context
        await self._update_context(context, prompt, final_response)

        return final_response

    async def _build_messages(
        self, prompt: str, context: Any, progress_report: str
    ) -> list[dict[str, Any]]:
        """Build message list with foreman system prompt and history."""
        messages: list[dict[str, Any]] = []

        # Add foreman system prompt
        system_content = FOREMAN_SYSTEM_PROMPT
        if progress_report:
            system_content += f"\n\n## CURRENT WORKER STATUS\n{progress_report}"

        messages.append({"role": "system", "content": system_content})

        # Add conversation history from context (get_messages is async)
        history = []
        if hasattr(context, "get_messages"):
            history = await context.get_messages()
        for msg in history[-10:]:  # Last 10 messages for context
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg.get("content", "")})

        # Add current prompt
        messages.append({"role": "user", "content": prompt})

        return messages

    def _get_tool_specs(self, tools: dict[str, Any]) -> list[ToolSpec]:
        """Get tool specifications for LLM."""
        specs = []
        for name, tool in tools.items():
            # Use Tool protocol: name, description, input_schema properties
            description = getattr(tool, "description", "")
            input_schema = getattr(tool, "input_schema", {})
            specs.append(
                ToolSpec(
                    name=name,
                    description=description,
                    parameters=input_schema,
                )
            )
        return specs

    async def _execute_tools(
        self,
        tool_calls: list[Any],
        tools: dict[str, Any],
        issue_tool: Any,
        hooks: Any,
    ) -> list[dict[str, Any]]:
        """Execute tool calls and return results, emitting hook events."""
        results = []

        for tc in tool_calls:
            tool = tools.get(tc.name)
            if not tool:
                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": f"Tool '{tc.name}' not found",
                    }
                )
                continue

            # Emit tool:pre event
            if hooks:
                await hooks.emit(
                    "tool:pre",
                    {"tool_name": tc.name, "arguments": tc.arguments},
                )

            try:
                # Execute the tool
                result = await tool.execute(tc.arguments)
                output = result.output if hasattr(result, "output") else str(result)

                # If this was an issue creation, spawn a worker
                if tc.name == "issue_manager" and tc.arguments.get("operation") == "create":
                    if isinstance(output, dict):
                        await self._maybe_spawn_worker(output, issue_tool)

                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": json.dumps(output) if isinstance(output, dict) else str(output),
                    }
                )

                # Emit tool:post event on success
                if hooks:
                    await hooks.emit(
                        "tool:post",
                        {"tool_name": tc.name, "result": output},
                    )

            except Exception as e:
                logger.error(f"Tool {tc.name} failed: {e}")
                results.append(
                    {
                        "tool_call_id": tc.id,
                        "content": f"Error: {e}",
                    }
                )
                # Emit tool:post event on error too (for logging/tracking)
                if hooks:
                    await hooks.emit(
                        "tool:post",
                        {"tool_name": tc.name, "error": str(e)},
                    )

        return results

    async def _maybe_spawn_worker(self, issue_result: dict[str, Any], issue_tool: Any) -> None:
        """Spawn a worker for a newly created issue."""
        issue = issue_result.get("issue", {})
        issue_id = issue.get("id")

        if not issue_id or issue_id in self._spawned_issues:
            return

        self._spawned_issues.add(issue_id)

        # Route to appropriate worker pool
        pool_config = self._route_issue(issue)
        if not pool_config:
            logger.warning(f"No worker pool for issue {issue_id}")
            return

        # Get worker agent name
        worker_agent = pool_config.get("worker_agent")
        if not worker_agent:
            logger.warning(f"No worker_agent configured for pool {pool_config.get('name')}")
            return

        # Get spawn capability
        spawn = self._coordinator.get_capability("session.spawn") if self._coordinator else None
        if not spawn:
            logger.warning("session.spawn capability not available")
            return

        # Build worker prompt
        worker_prompt = f"""You are a worker assigned to complete this issue:

## Issue #{issue_id}: {issue.get("title", "Untitled")}

{issue.get("description", "No description")}

## Your Task
1. Complete the work described above
2. When done, use the issue_manager tool to update the issue:
   - operation: "update"
   - issue_id: "{issue_id}"
   - status: "completed"
   - Include a summary of what you did in the comment

If you need clarification, update the issue with status "pending_user_input".
"""

        # Mark issue as in_progress
        try:
            await issue_tool.execute(
                {
                    "operation": "update",
                    "params": {
                        "issue_id": issue_id,
                        "status": "in_progress",
                    },
                }
            )
        except Exception as e:
            logger.error(f"Failed to update issue status: {e}")

        # Spawn worker (fire and forget)
        try:
            asyncio.create_task(
                spawn(
                    agent_name=worker_agent,
                    instruction=worker_prompt,
                    parent_session=self._coordinator.session,
                    agent_configs=WORKER_AGENT_CONFIGS,
                )
            )
            logger.info(f"Spawned worker {worker_agent} for issue {issue_id}")
        except Exception as e:
            logger.error(f"Failed to spawn worker: {e}")

    def _route_issue(self, issue: dict[str, Any]) -> dict[str, Any] | None:
        """Route issue to appropriate worker pool based on metadata."""
        issue_type = issue.get("metadata", {}).get("type", "general")

        # Check routing rules
        rules = self.routing_config.get("rules", [])
        for rule in rules:
            if "if_metadata_type" in rule:
                if issue_type in rule["if_metadata_type"]:
                    pool_name = rule.get("then_pool")
                    return self._get_pool_by_name(pool_name)

        # Fall back to default pool
        default_pool = self.routing_config.get("default_pool")
        if default_pool:
            return self._get_pool_by_name(default_pool)

        # Fall back to first pool
        return self.worker_pools[0] if self.worker_pools else None

    def _get_pool_by_name(self, name: str) -> dict[str, Any] | None:
        """Get worker pool config by name."""
        for pool in self.worker_pools:
            if pool.get("name") == name:
                return pool
        return None

    async def _check_worker_progress(self, issue_tool: Any) -> str:
        """Check for completed or blocked issues from workers."""
        parts = []

        try:
            # Check completed issues
            completed_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "completed"},
                }
            )
            completed = completed_result.output.get("issues", [])
            if completed:
                parts.append(f"✅ {len(completed)} issue(s) completed by workers")

            # Check blocked issues
            blocked_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "pending_user_input"},
                }
            )
            blocked = blocked_result.output.get("issues", [])
            if blocked:
                parts.append(f"⚠️ {len(blocked)} issue(s) need user input:")
                for issue in blocked[:3]:  # Show first 3
                    parts.append(f"  - #{issue['id']}: {issue['title']}")

            # Check in-progress issues
            in_progress_result = await issue_tool.execute(
                {
                    "operation": "list",
                    "params": {"status": "in_progress"},
                }
            )
            in_progress = in_progress_result.output.get("issues", [])
            if in_progress:
                parts.append(f"⏳ {len(in_progress)} issue(s) in progress")

        except Exception as e:
            logger.error(f"Failed to check worker progress: {e}")

        return "\n".join(parts)

    async def _update_context(self, context: Any, prompt: str, response: str) -> None:
        """Store the conversation turn in context."""
        try:
            if hasattr(context, "add_message"):
                await context.add_message({"role": "user", "content": prompt})
                await context.add_message({"role": "assistant", "content": response})
        except Exception as e:
            logger.error(f"Failed to update context: {e}")


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the foreman orchestrator."""
    print("FOREMAN DEBUG: Starting orchestrator-foreman mount")
    config = config or {}
    print(f"FOREMAN DEBUG: Config: {config}")
    orchestrator = ForemanOrchestrator(config)
    print("FOREMAN DEBUG: Created ForemanOrchestrator instance")
    await coordinator.mount("orchestrator", orchestrator)
    print("FOREMAN DEBUG: Successfully mounted orchestrator")
    return None
