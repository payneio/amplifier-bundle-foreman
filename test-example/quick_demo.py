#!/usr/bin/env python3
"""Quick standalone demo of the Foreman orchestrator concept.

This demo doesn't require amplifier-core or amplifier-foundation to be installed.
It shows how the foreman breaks down work requests and coordinates workers.

Run with: python test-example/quick_demo.py
"""

import random
import re
from dataclasses import dataclass, field


# =============================================================================
# Minimal Mock Infrastructure
# =============================================================================


@dataclass
class Issue:
    id: str
    title: str
    description: str
    priority: int
    issue_type: str
    status: str = "open"
    result: str | None = None
    block_reason: str | None = None
    worker_pool: str = ""


@dataclass
class IssueStore:
    issues: dict[str, Issue] = field(default_factory=dict)
    _next_id: int = 1

    def create(self, title: str, description: str, priority: int, issue_type: str) -> Issue:
        issue = Issue(
            id=f"FORE-{self._next_id}",
            title=title,
            description=description,
            priority=priority,
            issue_type=issue_type,
        )
        self._next_id += 1
        self.issues[issue.id] = issue
        return issue

    def update(self, issue_id: str, **kwargs) -> Issue | None:
        if issue_id in self.issues:
            for k, v in kwargs.items():
                setattr(self.issues[issue_id], k, v)
            return self.issues[issue_id]
        return None


# =============================================================================
# Foreman Logic (simplified)
# =============================================================================


class ForemanDemo:
    """Simplified foreman demonstrating the orchestration concept."""

    def __init__(self):
        self.store = IssueStore()
        self.reported_completions: set[str] = set()
        self.worker_pools = {
            "coding-pool": ["coding", "implementation", "bugfix", "refactor"],
            "testing-pool": ["testing", "qa"],
            "research-pool": ["research", "analysis"],
        }

    def _route_issue(self, issue: Issue) -> str:
        """Route issue to appropriate worker pool."""
        for pool, types in self.worker_pools.items():
            if issue.issue_type in types:
                return pool
        return "coding-pool"  # default

    def _is_status_request(self, text: str) -> bool:
        """Check if input is asking about status/progress."""
        status_patterns = [
            r"\bstatus\b",
            r"\bhow'?s?\s+it\s+going\b",
            r"\bprogress\b",
            r"\bwhat'?s?\s+(the\s+)?status\b",
            r"\bhow\s+are\s+(we|things)\b",
            r"\bany\s+updates?\b",
            r"\bwhat'?s?\s+happening\b",
            r"\bupdate\s+me\b",
        ]
        lower = text.lower()
        return any(re.search(p, lower) for p in status_patterns)

    def _is_question_about_work(self, text: str) -> bool:
        """Check if input is asking about current work (not a new request)."""
        question_patterns = [
            r"\bwho\s+is\s+working\b",
            r"\bwhat\s+are\s+(the\s+)?workers?\s+doing\b",
            r"\bwhich\s+(pool|worker)\b",
            r"\bwhat\s+issues?\b",
            r"\bshow\s+(me\s+)?(the\s+)?issues?\b",
            r"\blist\s+(the\s+)?issues?\b",
            r"\bwhat'?s?\s+(left|remaining|pending)\b",
            r"\bhow\s+many\b",
        ]
        lower = text.lower()
        return any(re.search(p, lower) for p in question_patterns)

    def _is_work_request(self, text: str) -> bool:
        """Check if input looks like a new work request."""
        # Work requests typically:
        # - Start with verbs: add, create, implement, fix, refactor, build, make
        # - Contain technical terms
        # - Are longer than simple questions
        work_verbs = [
            r"^(please\s+)?(add|create|implement|fix|refactor|build|make|write|update|improve|remove|delete)\b",
            r"\b(the|a|an)\s+(module|function|class|file|code|test|feature)\b",
        ]
        lower = text.lower()
        
        # Short inputs are usually not work requests
        if len(text.split()) < 3:
            return False
            
        return any(re.search(p, lower) for p in work_verbs)

    def _break_down_work(self, request: str) -> list[dict]:
        """Simulate LLM breaking down a work request into tasks."""
        if "calculator" in request.lower() or "refactor" in request.lower():
            return [
                {"title": "Add type hints to all functions", "type": "coding", "priority": 2},
                {"title": "Improve error handling in divide()", "type": "coding", "priority": 1},
                {"title": "Replace string ops with Enum", "type": "refactor", "priority": 2},
                {"title": "Add comprehensive test coverage", "type": "testing", "priority": 3},
                {"title": "Update documentation", "type": "coding", "priority": 3},
            ]
        elif "test" in request.lower():
            return [
                {"title": "Run existing test suite", "type": "testing", "priority": 1},
                {"title": "Analyze coverage gaps", "type": "testing", "priority": 2},
            ]
        elif "log" in request.lower():
            return [
                {"title": "Add logging infrastructure", "type": "coding", "priority": 1},
                {"title": "Add debug logging to functions", "type": "coding", "priority": 2},
                {"title": "Add error logging", "type": "coding", "priority": 2},
            ]
        else:
            return [{"title": f"Implement: {request[:50]}", "type": "coding", "priority": 2}]

    def _format_status(self) -> str:
        """Generate a status report."""
        lines = []
        
        in_progress = [i for i in self.store.issues.values() if i.status == "in_progress"]
        open_issues = [i for i in self.store.issues.values() if i.status == "open"]
        completed = [i for i in self.store.issues.values() if i.status == "completed"]
        blocked = [i for i in self.store.issues.values() if i.status == "blocked"]

        lines.append("📊 **Current Status**")
        lines.append(f"   ⏳ In Progress: {len(in_progress)}")
        lines.append(f"   📋 Queued: {len(open_issues)}")
        lines.append(f"   ✅ Completed: {len(completed)}")
        if blocked:
            lines.append(f"   ⚠️  Blocked: {len(blocked)}")

        if in_progress:
            lines.append("\n**Currently working on:**")
            for issue in in_progress:
                lines.append(f"   • {issue.id}: {issue.title}")
                lines.append(f"     └─ Worker: {issue.worker_pool}")

        if blocked:
            lines.append("\n**⚠️ Blocked - need your input:**")
            for issue in blocked:
                lines.append(f"   • {issue.id}: {issue.title}")
                lines.append(f"     └─ Question: {issue.block_reason}")

        return "\n".join(lines)

    def _format_work_details(self) -> str:
        """Show detailed info about current work."""
        lines = []
        
        by_pool: dict[str, list[Issue]] = {}
        for issue in self.store.issues.values():
            if issue.status in ("in_progress", "open"):
                pool = issue.worker_pool or "unassigned"
                by_pool.setdefault(pool, []).append(issue)

        if not by_pool:
            return "No active work at the moment."

        lines.append("**Work by Pool:**\n")
        for pool, issues in sorted(by_pool.items()):
            lines.append(f"🔧 **{pool}** ({len(issues)} issues)")
            for issue in issues:
                status_icon = "⏳" if issue.status == "in_progress" else "📋"
                lines.append(f"   {status_icon} {issue.id}: {issue.title}")
        
        return "\n".join(lines)

    def handle_request(self, user_input: str) -> str:
        """Main entry point - handle any user input."""
        response_parts = []

        # 0. Simulate workers making progress (in real system, this happens in background)
        self._simulate_background_work()

        # 1. Check for completions to report (proactive updates)
        completions = [i for i in self.store.issues.values() 
                      if i.status == "completed" and i.id not in self.reported_completions]
        if completions:
            response_parts.append("✅ **Completed since we last spoke:**")
            for issue in completions:
                response_parts.append(f"   • {issue.id}: {issue.title}")
                if issue.result:
                    response_parts.append(f"     └─ {issue.result}")
                self.reported_completions.add(issue.id)
            response_parts.append("")

        # 2. Determine intent and respond appropriately
        if self._is_status_request(user_input):
            response_parts.append(self._format_status())

        elif self._is_question_about_work(user_input):
            response_parts.append(self._format_work_details())

        elif self._is_work_request(user_input):
            # New work request - break it down
            response_parts.append("📋 **Analyzing your request...**\n")
            tasks = self._break_down_work(user_input)

            response_parts.append(f"Breaking this into {len(tasks)} issues:\n")
            for task in tasks:
                issue = self.store.create(
                    title=task["title"],
                    description=f"From request: {user_input}",
                    priority=task["priority"],
                    issue_type=task["type"],
                )
                issue.status = "in_progress"
                issue.worker_pool = self._route_issue(issue)
                response_parts.append(f"   • {issue.id}: {task['title']}")
                response_parts.append(f"     └─ Assigned to: {issue.worker_pool}")

            response_parts.append(f"\n🚀 Workers are on it! Ask me 'status' anytime for updates.")
            response_parts.append("   (Type 'sim' to simulate workers making progress)")

        else:
            # Conversational - give a helpful response
            if not self.store.issues:
                response_parts.append("I don't have any work in progress yet.")
                response_parts.append("Give me a task like: 'Refactor the calculator module'")
            else:
                response_parts.append("I'm coordinating the work you gave me.")
                response_parts.append("Try asking: 'status', 'who is working on it', or give me more work to do.")

        return "\n".join(response_parts)

    def _simulate_background_work(self):
        """Simulate workers making progress in the background.
        
        In the real system, workers run asynchronously. For this demo,
        we simulate progress each time the user interacts.
        """
        for issue in list(self.store.issues.values()):
            if issue.status == "in_progress":
                roll = random.random()
                if roll > 0.7:  # 30% chance per interaction
                    issue.status = "completed"
                    issue.result = f"Done! {issue.title}"
                elif roll > 0.95:  # 5% chance of blocker
                    issue.status = "blocked"
                    issue.block_reason = "Need clarification on requirements"

    def simulate_worker_progress(self):
        """Manually trigger worker progress (for 'sim' command)."""
        made_progress = False
        for issue in list(self.store.issues.values()):
            if issue.status == "in_progress":
                roll = random.random()
                if roll > 0.4:  # Higher chance when manually triggered
                    issue.status = "completed"
                    issue.result = f"Done! {issue.title}"
                    made_progress = True
                elif roll > 0.9:
                    issue.status = "blocked"
                    issue.block_reason = "Need clarification on requirements"
                    made_progress = True
        return made_progress


# =============================================================================
# Interactive Demo
# =============================================================================


def main():
    print("=" * 60)
    print("  FOREMAN ORCHESTRATOR - Interactive Demo")
    print("=" * 60)
    print()
    print("The Foreman breaks down work and coordinates worker pools.")
    print()
    print("Try these:")
    print("  • 'Refactor the calculator module'  (work request)")
    print("  • 'status' or 'how's it going?'     (check progress)")
    print("  • 'who is working on it?'           (see details)")
    print("  • 'Add logging to all functions'    (more work)")
    print()
    print("Commands: 'sim' = simulate progress, 'quit' = exit")
    print("-" * 60)

    foreman = ForemanDemo()

    while True:
        try:
            user_input = input("\n👤 You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("\nGoodbye!")
            break

        if user_input.lower() == "sim":
            if foreman.simulate_worker_progress():
                print("\n🔧 [Workers made progress - check status!]")
            else:
                print("\n🔧 [Workers still working...]")
            continue

        response = foreman.handle_request(user_input)
        print(f"\n🤖 Foreman:\n{response}")


if __name__ == "__main__":
    main()
