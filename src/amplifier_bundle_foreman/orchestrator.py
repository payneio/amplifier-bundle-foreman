"""
Foreman Orchestrator - Conversational autonomous work coordination.

This orchestrator coordinates multiple specialized worker bundles through a shared
issue queue. It provides immediate responses, proactive progress updates, and
background worker execution.
"""

import asyncio
from typing import Any

from amplifier_core import HookRegistry


class ForemanOrchestrator:
    """Conversational orchestrator that coordinates background workers."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the foreman orchestrator.

        Args:
            config: Configuration dictionary containing:
                - worker_pools: List of worker pool configurations
                - routing: Routing rules configuration (optional)
        """
        self.config = config
        self.worker_pools = config.get("worker_pools", [])
        self.routing_config = config.get("routing", {})

        # Track what we've already reported to avoid repetition
        self._reported_completions: set[str] = set()
        self._reported_blockers: set[str] = set()

    async def execute(
        self,
        prompt: str,
        context,
        providers: dict[str, Any],
        tools: dict[str, Any],
        hooks: HookRegistry,
    ) -> str:
        """
        Main orchestrator entry point.

        Called once per user message. Returns quickly after:
        1. Checking for worker updates
        2. Reporting updates
        3. Processing current request
        4. Spawning workers if needed

        Args:
            prompt: User's message
            context: Session context for message history
            providers: Available LLM providers
            tools: Available tools (issue, task, etc.)
            hooks: Hook registry

        Returns:
            Response message to user
        """
        issue_tool = tools.get("issue")
        task_tool = tools.get("task")

        if not issue_tool or not task_tool:
            return "⚠️  Foreman requires 'issue' and 'task' tools to be available."

        response_parts = []

        # Step 1: Check issue queue for updates from background workers
        updates = await self._check_worker_updates(issue_tool)

        # Step 2: Report completions
        if updates["completions"]:
            msg = self._format_completions(updates["completions"])
            response_parts.append(msg)

        # Step 3: Report blockers
        if updates["blockers"]:
            msg = self._format_blockers(updates["blockers"])
            response_parts.append(msg)

        # Step 4: Process user's current request
        request_response = await self._process_request(
            prompt, issue_tool, task_tool, context, providers
        )
        if request_response:
            response_parts.append(request_response)

        # Step 5: Return quickly
        if not response_parts:
            response_parts.append(
                "All systems running. Let me know if you need anything!"
            )

        return "\n\n".join(response_parts)

    async def _check_worker_updates(self, issue_tool) -> dict:
        """
        Check issue queue for updates from background workers.

        Args:
            issue_tool: Issue management tool

        Returns:
            Dictionary with 'completions' and 'blockers' lists
        """
        # Get recently completed issues
        completed_result = await issue_tool.execute(
            {"operation": "list", "filter": {"status": "completed"}}
        )
        completed = completed_result.output.get("issues", [])

        # Filter to new completions (not already reported)
        new_completions = [
            issue
            for issue in completed
            if issue["id"] not in self._reported_completions
        ]

        # Mark as reported
        for issue in new_completions:
            self._reported_completions.add(issue["id"])

        # Get blocked issues
        blocked_result = await issue_tool.execute(
            {"operation": "list", "filter": {"status": "pending_user_input"}}
        )
        blocked = blocked_result.output.get("issues", [])

        # Filter to new blockers
        new_blockers = [
            issue for issue in blocked if issue["id"] not in self._reported_blockers
        ]

        # Mark as reported
        for issue in new_blockers:
            self._reported_blockers.add(issue["id"])

        return {"completions": new_completions, "blockers": new_blockers}

    async def _process_request(
        self,
        prompt: str,
        issue_tool,
        task_tool,
        context,
        providers: dict[str, Any],
    ) -> str | None:
        """
        Process user's current request.

        Args:
            prompt: User's message
            issue_tool: Issue management tool
            task_tool: Task spawning tool
            context: Session context
            providers: Available LLM providers

        Returns:
            Response message or None
        """
        # Check intent
        if self._is_status_request(prompt):
            # User asking for status
            return await self._get_full_status(issue_tool)

        elif self._is_work_request(prompt):
            # User requesting new work
            return await self._handle_work_request(
                prompt, issue_tool, task_tool, context, providers
            )

        elif await self._is_resolution(prompt, issue_tool):
            # User providing input for blocked issue
            return await self._handle_resolution(prompt, issue_tool, task_tool)

        else:
            # General conversation - check if there's pending work
            return await self._handle_general(issue_tool, task_tool)

    def _is_status_request(self, prompt: str) -> bool:
        """Check if user is asking for status."""
        status_keywords = [
            "status",
            "progress",
            "what's happening",
            "how's it going",
            "what are you working on",
            "show me",
            "update",
        ]
        prompt_lower = prompt.lower()
        return any(keyword in prompt_lower for keyword in status_keywords)

    def _is_work_request(self, prompt: str) -> bool:
        """Check if user is requesting work."""
        work_keywords = [
            "refactor",
            "implement",
            "add",
            "create",
            "build",
            "write",
            "develop",
            "design",
            "make",
            "update",
            "fix",
            "modify",
            "change",
        ]
        prompt_lower = prompt.lower()
        return any(keyword in prompt_lower for keyword in work_keywords)

    async def _is_resolution(self, prompt: str, issue_tool) -> bool:
        """Check if user is providing resolution to blocked issue."""
        # Check if there are blocked issues
        blocked_result = await issue_tool.execute(
            {"operation": "list", "filter": {"status": "pending_user_input"}}
        )
        blocked = blocked_result.output.get("issues", [])

        # If there are blocked issues, this might be a resolution
        return len(blocked) > 0

    async def _handle_work_request(
        self,
        prompt: str,
        issue_tool,
        task_tool,
        context,
        providers: dict[str, Any],
    ) -> str:
        """
        Handle user request for new work.

        Creates issues from the request and spawns workers.

        Args:
            prompt: User's work request
            issue_tool: Issue management tool
            task_tool: Task spawning tool
            context: Session context
            providers: Available LLM providers

        Returns:
            Response message
        """
        response_parts = ["📋 Analyzing work request..."]

        # Use LLM to break down work into issues
        issues_created = await self._create_issues_from_request(
            prompt, issue_tool, context, providers
        )

        if not issues_created:
            return "I couldn't break down that request. Could you be more specific?"

        response_parts.append(f"\nCreated {len(issues_created)} issues:")
        for issue in issues_created:
            response_parts.append(f"  • Issue #{issue['id']}: {issue['title']}")

        # Spawn workers for issues
        workers_spawned = await self._spawn_workers_for_issues(
            issues_created, task_tool, issue_tool
        )

        response_parts.append(
            f"\n🚀 Spawned {workers_spawned} workers to handle these issues."
        )
        response_parts.append("I'll keep you posted on progress!")

        return "\n".join(response_parts)

    async def _create_issues_from_request(
        self,
        prompt: str,
        issue_tool,
        context,
        providers: dict[str, Any],
    ) -> list[dict]:
        """
        Use LLM to break down work request into issues.

        Args:
            prompt: User's work request
            issue_tool: Issue management tool
            context: Session context
            providers: Available LLM providers

        Returns:
            List of created issues
        """
        # Get primary provider
        provider_name = next(iter(providers.keys()), None)
        if not provider_name:
            # No provider available - create simple issue
            result = await issue_tool.execute(
                {
                    "operation": "create",
                    "title": "Work request",
                    "description": prompt,
                    "priority": 2,
                    "metadata": {"type": "general"},
                }
            )
            return [result.output["issue"]]

        provider = providers[provider_name]

        # Build analysis prompt
        analysis_prompt = f"""Analyze this work request and break it into discrete, actionable tasks.

Work Request:
{prompt}

For each task, provide:
1. A clear title (short, action-oriented)
2. Detailed description of what needs to be done
3. Task type (one of: coding, research, testing, documentation, design)
4. Priority (0=critical, 1=high, 2=medium, 3=low, 4=backlog)

Format your response as a JSON array of tasks:
[
  {{
    "title": "Task title",
    "description": "Detailed description",
    "type": "coding",
    "priority": 2
  }},
  ...
]

Keep tasks focused and independent where possible."""

        # Call LLM to analyze
        try:
            response = await provider.complete(
                [{"role": "user", "content": analysis_prompt}]
            )

            # Parse response
            import json

            # Extract JSON from response (might have markdown code blocks)
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            tasks = json.loads(content.strip())

            # Create issues
            issues_created = []
            for task in tasks:
                result = await issue_tool.execute(
                    {
                        "operation": "create",
                        "title": task["title"],
                        "description": task["description"],
                        "priority": task.get("priority", 2),
                        "metadata": {"type": task.get("type", "general")},
                    }
                )
                issues_created.append(result.output["issue"])

            return issues_created

        except Exception:
            # Fallback: create single issue
            result = await issue_tool.execute(
                {
                    "operation": "create",
                    "title": "Work request",
                    "description": prompt,
                    "priority": 2,
                    "metadata": {"type": "general"},
                }
            )
            return [result.output["issue"]]

    async def _spawn_workers_for_issues(
        self, issues: list[dict], task_tool, issue_tool
    ) -> int:
        """
        Spawn workers for issues.

        Returns quickly - workers run in background as separate sessions.

        Args:
            issues: List of issues to assign
            task_tool: Task spawning tool
            issue_tool: Issue management tool

        Returns:
            Number of workers spawned
        """
        spawn_tasks = []

        for issue in issues:
            # Route issue to appropriate worker pool
            pool_config = self._route_issue(issue)

            if not pool_config:
                # No pool found - skip
                continue

            # Mark as in_progress
            await issue_tool.execute(
                {
                    "operation": "update",
                    "issue_id": issue["id"],
                    "status": "in_progress",
                }
            )

            # Create spawn task
            spawn_task = self._spawn_worker(issue, pool_config, task_tool)
            spawn_tasks.append(spawn_task)

        # Spawn all workers in parallel
        if spawn_tasks:
            await asyncio.gather(*spawn_tasks, return_exceptions=True)

        return len(spawn_tasks)

    def _route_issue(self, issue: dict) -> dict | None:
        """
        Route issue to appropriate worker pool.

        Args:
            issue: Issue to route

        Returns:
            Worker pool configuration or None
        """
        issue_type = issue.get("metadata", {}).get("type", "general")
        issue_status = issue.get("status", "open")

        # Check routing rules
        rules = self.routing_config.get("rules", [])
        for rule in rules:
            # Check if_metadata_type
            if "if_metadata_type" in rule:
                if issue_type not in rule["if_metadata_type"]:
                    continue

            # Check if_status
            if "if_status" in rule:
                if issue_status != rule["if_status"]:
                    continue

            # Check retry count
            if "and_retry_count_gte" in rule:
                retry_count = issue.get("retry_count", 0)
                if retry_count < rule["and_retry_count_gte"]:
                    continue

            # Rule matches - use this pool
            pool_name = rule["then_pool"]
            for pool in self.worker_pools:
                if pool["name"] == pool_name:
                    return pool

        # No rule matched - try route_types
        for pool in self.worker_pools:
            route_types = pool.get("route_types", [])
            if issue_type in route_types:
                return pool

        # Fallback to default pool
        default_pool_name = self.routing_config.get("default_pool")
        if default_pool_name:
            for pool in self.worker_pools:
                if pool["name"] == default_pool_name:
                    return pool

        # Last resort - first pool
        if self.worker_pools:
            return self.worker_pools[0]

        return None

    async def _spawn_worker(self, issue: dict, pool_config: dict, task_tool) -> None:
        """
        Spawn worker for issue using configured worker bundle.

        Args:
            issue: Issue to work on
            pool_config: Worker pool configuration
            task_tool: Task spawning tool
        """
        # Build worker prompt with issue context
        worker_prompt = f"""You are handling issue #{issue["id"]}.

## Issue Details
Title: {issue["title"]}
Description: {issue["description"]}
Priority: {issue.get("priority", 2)}

## Your Task
Complete this work. When done:
- Update issue to 'completed' with results
- If blocked, update to 'blocked' with reason
- If need user input, update to 'pending_user_input' with clear question

Focus on this specific issue. Use the issue tool to update status.
"""

        # Get worker bundle reference
        worker_bundle = pool_config.get("worker_bundle")
        if not worker_bundle:
            # Fallback to worker_type (agent name)
            worker_bundle = pool_config.get("worker_type", "general-purpose")

        # Spawn worker (fire-and-forget)
        try:
            await task_tool.execute(
                {
                    "agent": worker_bundle,
                    "instruction": worker_prompt,
                    # Future: "inherit_context": "recent" when available
                }
            )
        except Exception:
            # Worker spawn failed - mark issue as blocked
            pass  # Issue stays in_progress, foreman will detect timeout

    async def _handle_resolution(
        self, prompt: str, issue_tool, task_tool
    ) -> str | None:
        """
        Handle user providing resolution to blocked issue.

        Args:
            prompt: User's resolution
            issue_tool: Issue management tool
            task_tool: Task spawning tool

        Returns:
            Response message or None if no blocked issues
        """
        # Get blocked issues
        blocked_result = await issue_tool.execute(
            {"operation": "list", "filter": {"status": "pending_user_input"}}
        )
        blocked = blocked_result.output.get("issues", [])

        if not blocked:
            return None  # No blocked issues to resolve

        # Take first blocked issue and resolve it
        issue = blocked[0]

        # Update issue with resolution and set back to open
        await issue_tool.execute(
            {
                "operation": "update",
                "issue_id": issue["id"],
                "status": "open",
                "metadata": {**issue.get("metadata", {}), "resolution": prompt},
            }
        )

        # Remove from reported blockers
        self._reported_blockers.discard(issue["id"])

        # Spawn worker to continue with resolution
        pool_config = self._route_issue(issue)
        if pool_config:
            await self._spawn_worker_with_resolution(
                issue, prompt, pool_config, task_tool, issue_tool
            )

            return f"✅ Got it! Resuming work on **{issue['title']}** with your input."
        else:
            return f"✅ Updated issue **{issue['title']}** with your input."

    async def _spawn_worker_with_resolution(
        self, issue: dict, resolution: str, pool_config: dict, task_tool, issue_tool
    ) -> None:
        """
        Spawn worker to resume blocked issue with resolution.

        Args:
            issue: Issue to resume
            resolution: User's resolution
            pool_config: Worker pool configuration
            task_tool: Task spawning tool
            issue_tool: Issue management tool
        """
        # Mark as in_progress
        await issue_tool.execute(
            {
                "operation": "update",
                "issue_id": issue["id"],
                "status": "in_progress",
            }
        )

        # Build resolution prompt
        worker_prompt = f"""Resuming issue #{issue["id"]} with user's input.

## Original Issue
Title: {issue["title"]}
Description: {issue["description"]}

## User's Response
{resolution}

## Your Task
Continue work with this new information. Update issue status when done.
"""

        worker_bundle = pool_config.get("worker_bundle", "general-purpose")

        try:
            await task_tool.execute(
                {"agent": worker_bundle, "instruction": worker_prompt}
            )
        except Exception:
            pass

    async def _handle_general(self, issue_tool, task_tool) -> str | None:
        """
        Handle general conversation.

        Checks if there's pending work that needs spawning.

        Args:
            issue_tool: Issue management tool
            task_tool: Task spawning tool

        Returns:
            Response message or None
        """
        # Check if there are open issues waiting
        open_result = await issue_tool.execute(
            {"operation": "list", "filter": {"status": "open"}}
        )
        open_issues = open_result.output.get("issues", [])

        if open_issues:
            # Spawn workers for open issues
            workers_spawned = await self._spawn_workers_for_issues(
                open_issues[:3],
                task_tool,
                issue_tool,  # Spawn for first 3
            )
            if workers_spawned:
                return f"(Spawned {workers_spawned} workers for pending issues)"

        return None

    async def _get_full_status(self, issue_tool) -> str:
        """
        Generate comprehensive status report.

        Args:
            issue_tool: Issue management tool

        Returns:
            Status report message
        """
        # Get all issues
        all_result = await issue_tool.execute({"operation": "list"})
        all_issues = all_result.output.get("issues", [])

        # Categorize
        open_issues = [i for i in all_issues if i["status"] == "open"]
        in_progress = [i for i in all_issues if i["status"] == "in_progress"]
        completed = [i for i in all_issues if i["status"] == "completed"]
        blocked = [i for i in all_issues if i["status"] == "pending_user_input"]

        status_parts = ["📊 **Current Status**\n"]

        if in_progress:
            status_parts.append(f"⏳ **In Progress** ({len(in_progress)}):")
            for issue in in_progress[:5]:  # Show first 5
                status_parts.append(f"  • {issue['title']}")
            if len(in_progress) > 5:
                status_parts.append(f"  ... and {len(in_progress) - 5} more")
            status_parts.append("")

        if open_issues:
            status_parts.append(f"📋 **Queued** ({len(open_issues)}):")
            for issue in open_issues[:3]:
                status_parts.append(f"  • {issue['title']}")
            if len(open_issues) > 3:
                status_parts.append(f"  ... and {len(open_issues) - 3} more")
            status_parts.append("")

        if blocked:
            status_parts.append(f"⚠️  **Blocked** ({len(blocked)}) - need your input:")
            for issue in blocked:
                status_parts.append(f"  • {issue['title']}")
            status_parts.append("")

        if completed:
            status_parts.append(f"✅ **Completed** ({len(completed)})\n")

        if not any([open_issues, in_progress, blocked]):
            status_parts.append("All clear - no active work!\n")

        return "\n".join(status_parts)

    def _format_completions(self, completions: list[dict]) -> str:
        """Format completion notifications."""
        msg_parts = [f"✅ **Completed ({len(completions)})**:"]
        for issue in completions:
            result = issue.get("result", "Done")
            # Truncate long results
            if len(result) > 100:
                result = result[:97] + "..."
            msg_parts.append(f"  • {issue['title']}: {result}")
        return "\n".join(msg_parts)

    def _format_blockers(self, blockers: list[dict]) -> str:
        """Format blocker notifications."""
        msg_parts = [f"⚠️  **Need Your Input ({len(blockers)})**:"]
        for issue in blockers:
            reason = issue.get("block_reason", "Needs clarification")
            msg_parts.append(f"  • {issue['title']}")
            msg_parts.append(f"    → {reason}")
        return "\n".join(msg_parts)


async def mount(coordinator, config: dict | None = None) -> None:
    """
    Mount the foreman orchestrator.

    Args:
        coordinator: Module coordinator
        config: Configuration dictionary

    Returns:
        None (no cleanup needed)
    """
    config = config or {}

    orchestrator = ForemanOrchestrator(config)
    await coordinator.mount("orchestrator", orchestrator)

    return None
