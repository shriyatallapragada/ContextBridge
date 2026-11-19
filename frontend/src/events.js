// Fired after any mutation that changes dashboard counts (tasks, notes, imports).
export const DASHBOARD_STALE = "cb:dashboard-stale";

export function notifyDashboardStale() {
  try {
    window.dispatchEvent(new Event(DASHBOARD_STALE));
  } catch {
    // Non-browser environment — nothing to refresh.
  }
}
