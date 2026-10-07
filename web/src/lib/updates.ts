export type UpdateRelease = { version: string; date: string; notes: string };

export type UpdateStatus = {
  checked_at?: string;
  current?: string;
  latest?: string;
  update_available?: boolean;
  releases?: UpdateRelease[];
  error?: string | null;
  instance?: string;
};

export type UpdateStep = { key: string; label: string; status: "pending" | "running" | "done" | "failed" | "skipped" };

export type UpdateState = {
  state?: "running" | "success" | "failed" | "rolled_back";
  action?: "update" | "rollback";
  from?: string;
  to?: string;
  requested_by?: string;
  started_at?: string;
  finished_at?: string | null;
  message?: string;
  backup?: string;
  steps?: UpdateStep[];
};

export type UpdateHistoryEntry = {
  id: string;
  action: "update" | "rollback";
  from_version: string;
  to_version: string;
  finished_at: string;
  result: string;
  requested_by?: string;
  migrated?: boolean;
  rolled_back?: boolean;
  backup?: string;
};

export type UpdatesResponse = {
  current: string;
  installed: boolean;
  status: UpdateStatus;
  state: UpdateState;
  history: UpdateHistoryEntry[];
  log: string[];
  in_flight_operations: number;
  blocked_by: string[];
  rollback: UpdateHistoryEntry | null;
};
