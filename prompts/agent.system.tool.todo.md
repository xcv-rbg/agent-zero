## todo

Use this tool to **track and execute tasks** created by the War Room planning system. The todo list is your structured work queue — every task the War Room identifies gets tracked here, and you work through them methodically until the job is done.

### What it does
Maintains a nested, ordered list of tasks with priorities, statuses, and subtasks. Provides methods to add, update, query, and manage tasks throughout execution. This is the bridge between **planning** (think tool) and **execution** (your tools and actions).

### Methods

| Method | Description | Args |
|---|---|---|
| `add` | Add a single task | `title` (required), `description`, `priority` (low/normal/high/critical), `subtasks` (list of {title, description?, priority?}) |
| `update` | Update task fields | `task_id` (required), `title`, `description`, `status` (pending/in_progress/done/blocked), `priority` |
| `list` | List all tasks | `status` (optional filter: pending/in_progress/done/blocked), `format` ("compact" or "full") |
| `get` | Get full task details | `task_id` (required) |
| `remove` | Delete a task — **SUPERUSER ONLY** | `task_id` (required) |
| `reorder` | Reorder tasks by priority | `task_ids` (ordered list of task IDs) |
| `add_subtask` | Add a subtask to a parent | `parent_id` (required), `title` (required), `description`, `priority` |
| `bulk_add` | Add multiple tasks at once | `tasks` (list of task objects, each with optional `subtasks`) |
| `next` | Get the highest-priority pending task | _(none)_ |
| `progress` | Get completion statistics | _(none)_ |

### Permissions

- **Read** (`list`, `get`, `next`, `progress`): Open to all agents.
- **Write** (`add`, `update`, `reorder`, `add_subtask`, `bulk_add`): Think tool, utility agent, and main agent only.
- **Delete** (`remove`): Superuser/human only. You cannot remove tasks — only mark them `done` or `blocked`.

### When to use

- After the `think` tool populates the todo list via `bulk_add`, begin working through tasks using `todo:next`.
- Mark each task `in_progress` before you start working on it, and `done` when you finish.
- **After marking a task `done`**, a War Room validation automatically runs to verify quality. If it finds issues, you'll be told to fix them.
- Use `todo:progress` to report completion status when the user asks how things are going.
- Use `todo:add` or `todo:add_subtask` if new work is discovered mid-execution.
- **You CANNOT send a final response while tasks are incomplete.** The response tool will be blocked until all tasks are done and the War Room has validated the work.

### Error recovery

If the agent crashes or encounters an error:
1. Call `todo:list` to see the current state of all tasks.
2. Find the task that was `in_progress` — that's where you left off.
3. Resume that task. If the previous approach failed, try an alternative.
4. Continue working through the remaining tasks.

### When NOT to use

- Simple one-shot questions that don't need multi-step task tracking.
- When the user explicitly says "just do it" without planning.
- Trivial lookups, greetings, or clarification questions.

### Mandatory workflow

Follow this loop after the War Room creates the todo list:

1. **Think tool** creates the todo list (`bulk_add`).
2. Call `todo:next` to get the highest-priority pending task.
3. Call `todo:update` to mark the task `in_progress`.
4. **Execute the task** using whatever tools are needed.
5. Call `todo:update` to mark the task `done`.
6. Repeat from step 2 until `todo:next` returns no pending tasks.
7. Call `todo:progress` to confirm 100% completion.
8. Deliver the final response via the `response` tool.

> **Do not skip steps.** Every task must transition through `pending → in_progress → done`. If a task cannot be completed, mark it `blocked` with a description of the blocker.

### Examples

#### Adding a task with subtasks

~~~json
{
  "thoughts": [
    "The War Room identified a refactoring task with three distinct steps. I'll add it with subtasks so I can track each step independently."
  ],
  "headline": "Adding refactoring task to todo list",
  "tool_name": "todo",
  "tool_args": {
    "method": "add",
    "title": "Refactor authentication module",
    "description": "Extract token validation into a shared utility and update all callers.",
    "priority": "high",
    "subtasks": [
      { "title": "Extract token validation helper", "priority": "high" },
      { "title": "Update login endpoint to use helper" },
      { "title": "Update middleware to use helper" }
    ]
  }
}
~~~

#### Listing tasks in compact format

~~~json
{
  "thoughts": [
    "Before responding to the user, I need to verify all tasks are complete."
  ],
  "headline": "Checking todo list status",
  "tool_name": "todo",
  "tool_args": {
    "method": "list",
    "format": "compact"
  }
}
~~~

#### Marking a task done

~~~json
{
  "thoughts": [
    "I've finished implementing the database migration. Marking task 3 as done and moving to the next task."
  ],
  "headline": "Completing task: database migration",
  "tool_name": "todo",
  "tool_args": {
    "method": "update",
    "task_id": "3",
    "status": "done"
  }
}
~~~
