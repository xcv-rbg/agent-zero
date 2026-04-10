import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";

const STATUS_CYCLE = ["pending", "in_progress", "done"];

const STATUS_ICONS = {
  pending: "hourglass_empty",
  in_progress: "sync",
  done: "check_circle",
  blocked: "block",
};

const STATUS_CLASSES = {
  pending: "todo-status-pending",
  in_progress: "todo-status-in-progress",
  done: "todo-status-done",
  blocked: "todo-status-blocked",
};

const STATUS_LABELS = {
  pending: "Pending",
  in_progress: "In Progress",
  done: "Done",
  blocked: "Blocked",
};

const model = {
  /** @type {Array<Object>} */
  items: [],
  progress: {},
  loading: false,
  editingId: null,
  editTitle: "",
  editDescription: "",
  /** @type {Array<string|number>} */
  expandedIds: [],
  contextId: "",
  newTaskTitle: "",
  /** @type {string|null} ID of task whose status menu is open */
  statusMenuId: null,

  init() {
    // Data driven by poll or manual refresh; store provides a stable target
  },

  setContext(id) {
    if (this.contextId !== id) {
      this.contextId = id || "";
      this.items = [];
      this.progress = {};
      this.editingId = null;
      this.expandedIds = [];
    }
  },

  // Fetch todo list from API
  async refresh() {
    if (!this.contextId) return;
    this.loading = true;
    try {
      const res = await callJsonApi("/api/todo_list", { context: this.contextId });
      if (res?.ok) {
        const prevCount = this.items.length;
        this.items = res.tasks || [];
        this.progress = res.progress || {};
        this._autoOpenIfNeeded(prevCount);
      }
    } catch (e) {
      console.error("todo-store.refresh failed", e);
    } finally {
      this.loading = false;
    }
  },

  // Called from poll cycle
  applyTodoData(data) {
    if (!data) return;
    const prevCount = this.items.length;
    this.items = data.tasks || [];
    this.progress = data.progress || {};
    this._autoOpenIfNeeded(prevCount);
  },

  // Auto-open the sidebar todo section when tasks first appear
  _autoOpenIfNeeded(prevCount) {
    if (this.items.length > 0 && prevCount === 0) {
      try {
        const sidebar = window.Alpine?.store('sidebar');
        if (sidebar && !sidebar.isSectionOpen('todo')) {
          sidebar.sectionStates.todo = true;
          sidebar.persistSectionStates();
        }
      } catch (e) {
        // Non-critical — sidebar access failure should not break todo
      }
    }
  },

  // --- CRUD ---

  async addTask(title, parentId = null) {
    if (!this.contextId || !title?.trim()) return;
    try {
      const payload = { context: this.contextId, title: title.trim() };
      if (parentId) payload.parent_id = parentId;
      const res = await callJsonApi("/api/todo_add", payload);
      if (res?.ok) {
        await this.refresh();
        this.newTaskTitle = "";
      }
    } catch (e) {
      console.error("todo-store.addTask failed", e);
    }
  },

  async updateTask(taskId, fields) {
    if (!this.contextId || !taskId) return;
    try {
      const res = await callJsonApi("/api/todo_update", {
        context: this.contextId,
        task_id: taskId,
        ...fields,
      });
      if (res?.ok) {
        await this.refresh();
      }
    } catch (e) {
      console.error("todo-store.updateTask failed", e);
    }
  },

  async removeTask(taskId) {
    if (!this.contextId || !taskId) return;
    try {
      const res = await callJsonApi("/api/todo_remove", {
        context: this.contextId,
        task_id: taskId,
      });
      if (res?.ok) {
        await this.refresh();
      }
    } catch (e) {
      console.error("todo-store.removeTask failed", e);
    }
  },

  async reorderTasks(taskIds) {
    if (!this.contextId || !taskIds?.length) return;
    try {
      const res = await callJsonApi("/api/todo_reorder", {
        context: this.contextId,
        task_ids: taskIds,
      });
      if (res?.ok) {
        await this.refresh();
      }
    } catch (e) {
      console.error("todo-store.reorderTasks failed", e);
    }
  },

  async toggleStatus(taskId, currentStatus) {
    const idx = STATUS_CYCLE.indexOf(currentStatus);
    const next = STATUS_CYCLE[(idx + 1) % STATUS_CYCLE.length];
    await this.updateTask(taskId, { status: next });
  },

  // --- Status menu ---

  toggleStatusMenu(taskId) {
    this.statusMenuId = this.statusMenuId === taskId ? null : taskId;
  },

  closeStatusMenu() {
    this.statusMenuId = null;
  },

  async setStatus(taskId, newStatus) {
    this.statusMenuId = null;
    await this.updateTask(taskId, { status: newStatus });
  },

  statusLabel(status) {
    return STATUS_LABELS[status] || STATUS_LABELS.pending;
  },

  allStatuses() {
    return ["pending", "in_progress", "done", "blocked"];
  },

  // --- Reorder helpers ---

  async moveUp(taskId) {
    const ids = this.items.map((t) => t.id);
    const idx = ids.indexOf(taskId);
    if (idx <= 0) return;
    [ids[idx - 1], ids[idx]] = [ids[idx], ids[idx - 1]];
    await this.reorderTasks(ids);
  },

  async moveDown(taskId) {
    const ids = this.items.map((t) => t.id);
    const idx = ids.indexOf(taskId);
    if (idx < 0 || idx >= ids.length - 1) return;
    [ids[idx], ids[idx + 1]] = [ids[idx + 1], ids[idx]];
    await this.reorderTasks(ids);
  },

  // --- Inline editing ---

  startEdit(taskId) {
    const task = this._findTask(taskId);
    if (!task) return;
    this.editingId = taskId;
    this.editTitle = task.title || "";
    this.editDescription = task.description || "";
  },

  cancelEdit() {
    this.editingId = null;
    this.editTitle = "";
    this.editDescription = "";
  },

  async saveEdit(taskId) {
    if (!taskId || !this.editTitle.trim()) return;
    await this.updateTask(taskId, {
      title: this.editTitle.trim(),
      description: this.editDescription.trim(),
    });
    this.editingId = null;
    this.editTitle = "";
    this.editDescription = "";
  },

  // --- Expand / collapse subtasks ---

  toggleExpand(taskId) {
    const idx = this.expandedIds.indexOf(taskId);
    if (idx === -1) {
      this.expandedIds.push(taskId);
    } else {
      this.expandedIds.splice(idx, 1);
    }
  },

  isExpanded(taskId) {
    return this.expandedIds.includes(taskId);
  },

  // --- Display helpers ---

  statusIcon(status) {
    return STATUS_ICONS[status] || STATUS_ICONS.pending;
  },

  statusClass(status) {
    return STATUS_CLASSES[status] || STATUS_CLASSES.pending;
  },

  progressPercent() {
    const total = this.progress?.total || 0;
    if (total === 0) return 0;
    return Math.round(((this.progress.done || 0) / total) * 100);
  },

  topLevelTasks() {
    return this.items.filter((t) => !t.parent_id);
  },

  // --- Internal ---

  _findTask(taskId) {
    for (const t of this.items) {
      if (t.id === taskId) return t;
      for (const st of t.subtasks || []) {
        if (st.id === taskId) return st;
      }
    }
    return null;
  },
};

export const store = createStore("todo", model);
