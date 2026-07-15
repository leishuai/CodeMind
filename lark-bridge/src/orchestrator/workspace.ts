/**
 * workspace.ts — the mutable "which project is CodeMind working on" holder.
 *
 * `automind start` is a generic launcher and does NOT take a project path, so
 * the workspace root is optional and can be confirmed later: interactively at
 * startup (skippable) or from a chat message. This holder lets the CLI cwd,
 * snapshot reader, and git-diff collector all read the current root live, so a
 * runtime change takes effect without re-wiring the daemon.
 */
export interface WorkspaceState {
  /** Current project root (falls back to the home dir until confirmed). */
  root: string;
  /** Whether the user explicitly confirmed/pointed at this project root. */
  confirmed: boolean;
}

export class Workspace {
  private root: string;
  private confirmed: boolean;

  constructor(state: WorkspaceState) {
    this.root = state.root;
    this.confirmed = state.confirmed;
  }

  getRoot(): string {
    return this.root;
  }

  isConfirmed(): boolean {
    return this.confirmed;
  }

  /** Point the daemon at a project root confirmed by the user (chat/startup). */
  confirm(root: string): void {
    this.root = root;
    this.confirmed = true;
  }
}
