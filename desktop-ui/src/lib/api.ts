import { invoke } from "@tauri-apps/api/core";

export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8765";

type ApiEnvelope<T> = {
  ok: boolean;
  data?: T;
  error?: string;
};

export type PendingApproval = {
  kind?: string;
  reason?: string;
  summary?: string;
  step?: string;
};

export type SessionMessage = {
  message_id?: string;
  created_at?: string;
  role?: string;
  kind?: string;
  content?: string;
  task_id?: string;
  run_id?: string;
  status?: string;
};

export type SessionSummary = {
  session_id: string;
  created_at?: string;
  updated_at?: string;
  title?: string;
  status?: string;
  summary?: string;
  current_task_id?: string;
  latest_run_id?: string;
  pending_approval?: PendingApproval;
  message_count?: number;
  latest_message?: SessionMessage;
  last_result_status?: string;
  last_result_message_preview?: string;
  authoritative_reply_available?: boolean;
};

export type SessionDetail = SessionSummary & {
  latest_user_message?: string;
  last_result_message?: string;
  authoritative_reply?: SessionMessage;
  messages?: SessionMessage[];
  operator?: {
    status?: string;
    running?: boolean;
    paused?: boolean;
    active_task?: ActiveTask;
    pending_approval?: PendingApproval;
    result_status?: string;
    result_message_preview?: string;
  };
};

export type ActiveTask = {
  task_id?: string;
  status?: string;
  goal?: string;
  last_message?: string;
  run_id?: string;
  approval_needed?: boolean;
  approval_reason?: string;
};

export type BrowserState = {
  task_name?: string;
  task_step?: string;
  task_status?: string;
  workflow_name?: string;
  workflow_step?: string;
  workflow_status?: string;
  current_title?: string;
  current_url?: string;
  expected_state?: string;
  last_action?: string;
  last_successful_action?: string;
};

export type RuntimeConfig = {
  active_model?: string;
  reasoning_effort?: string;
  reasoning_scope?: string;
  reasoning_effort_applies_to_tool_calls?: boolean;
  base_url?: string;
  settings_path?: string;
  source?: string;
};

export type DesktopRuntimeStatus = {
  backend_state?: string;
  decision?: string;
  detail?: string;
  base_url?: string;
  attached?: boolean;
  managed_by_desktop?: boolean;
  ownership_confirmed?: boolean;
  api_pid?: number;
  child_pid?: number;
};

export type StatusPayload = {
  status?: string;
  running?: boolean;
  paused?: boolean;
  current_step?: string;
  goal?: string;
  result_status?: string;
  result_message?: string;
  pending_approval?: PendingApproval;
  active_task?: ActiveTask;
  browser?: BrowserState;
  queue_counts?: Record<string, number>;
  latest_alert?: AlertItem;
  latest_run?: RunEntry;
  runtime?: RuntimeConfig;
};

export type AlertItem = {
  alert_id?: string;
  created_at?: string;
  severity?: string;
  type?: string;
  source?: string;
  title?: string;
  message?: string;
  goal?: string;
  task_id?: string;
  run_id?: string;
  session_id?: string;
  state_scope_id?: string;
};

export type QueuePayload = {
  counts?: Record<string, number>;
  active_task?: ActiveTask;
  queued_tasks?: ActiveTask[];
  recent_tasks?: ActiveTask[];
  can_start_next?: boolean;
};

export type ScheduledTask = {
  scheduled_id?: string;
  goal?: string;
  status?: string;
  recurrence?: string;
  scheduled_for?: string;
  next_run_at?: string;
  last_message?: string;
};

export type ScheduledPayload = {
  counts?: Record<string, number>;
  tasks?: ScheduledTask[];
};

export type WatchTask = {
  watch_id?: string;
  goal?: string;
  status?: string;
  condition_type?: string;
  target?: string;
  match_text?: string;
  interval_seconds?: number;
  allow_repeat?: boolean;
  last_message?: string;
};

export type WatchPayload = {
  counts?: Record<string, number>;
  tasks?: WatchTask[];
};

export type RunEntry = {
  run_id?: string;
  goal?: string;
  started_at?: string;
  ended_at?: string;
  final_status?: string;
  final_summary?: string;
  result_message?: string;
  source?: string;
  session_id?: string;
  state_scope_id?: string;
};

export type SessionListPayload = {
  items?: SessionSummary[];
  sessions?: SessionSummary[];
};

export type SessionMessagesPayload = {
  ok: boolean;
  session?: SessionSummary;
  items?: SessionMessage[];
  messages?: SessionMessage[];
};

export type SessionMutationResult = {
  ok: boolean;
  session?: SessionDetail;
  reply?: SessionMessage;
  reply_mode?: string;
  created?: boolean;
};

export type ApprovalResult = {
  ok: boolean;
  result?: {
    ok?: boolean;
    message?: string;
    status?: string;
    resumed?: boolean;
  };
  status?: StatusPayload;
  session?: SessionDetail;
};

export type StreamEvent<T = Record<string, unknown>> = {
  event: string;
  event_id?: string;
  session_id?: string;
  state_scope_id?: string;
  emitted_at?: string;
  data: T;
};

export type EnsureLocalApiResult = {
  baseUrl: string;
  started: boolean;
  managedByDesktop: boolean;
  runtimeStatus?: DesktopRuntimeStatus;
  logPath?: string;
};

function normalizeBaseUrl(baseUrl: string): string {
  return String(baseUrl || DEFAULT_API_BASE_URL).replace(/\/+$/, "");
}

function buildUrl(baseUrl: string, path: string, query?: Record<string, string | number | undefined>): string {
  const url = new URL(path.replace(/^\//, ""), `${normalizeBaseUrl(baseUrl)}/`);
  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (value === undefined || value === null || value === "") {
        return;
      }
      url.searchParams.set(key, String(value));
    });
  }
  return url.toString();
}

async function request<T>(baseUrl: string, path: string, init?: RequestInit, query?: Record<string, string | number | undefined>): Promise<T> {
  const url = buildUrl(baseUrl, path, query);
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Network request failed.";
    throw new Error(`Failed to fetch ${url}: ${message}`);
  }

  let payload: ApiEnvelope<T> | null = null;
  try {
    payload = (await response.json()) as ApiEnvelope<T>;
  } catch (_error) {
    if (!response.ok) {
      throw new Error(`Request to ${url} failed with status ${response.status}.`);
    }
  }

  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `Request to ${url} failed with status ${response.status}.`);
  }

  return payload.data as T;
}

export async function ensureLocalApi(): Promise<EnsureLocalApiResult> {
  let tauriError = "";
  try {
    const result = await invoke<{
      baseUrl?: string;
      started?: boolean;
      managedByDesktop?: boolean;
      runtimeStatus?: DesktopRuntimeStatus;
      logPath?: string;
    }>("ensure_local_api");
    return {
      baseUrl: normalizeBaseUrl(result.baseUrl || DEFAULT_API_BASE_URL),
      started: Boolean(result.started),
      managedByDesktop: Boolean(result.managedByDesktop),
      runtimeStatus: result.runtimeStatus,
      logPath: result.logPath,
    };
  } catch (error) {
    tauriError = error instanceof Error ? error.message : "Tauri bootstrap failed.";
  }

  try {
    await request<Record<string, unknown>>(DEFAULT_API_BASE_URL, "/health");
    return {
      baseUrl: normalizeBaseUrl(DEFAULT_API_BASE_URL),
      started: false,
      managedByDesktop: false,
    };
  } catch (error) {
    const httpError = error instanceof Error ? error.message : "HTTP fallback failed.";
    if (tauriError) {
      throw new Error(`Unable to reach the local operator. ${tauriError} ${httpError}`.trim());
    }
    throw error;
  }
}

export function openSessionEventStream(
  baseUrl: string,
  options: {
    sessionId?: string;
    stateScopeId?: string;
    lastEventId?: string;
  } = {},
): EventSource {
  return new EventSource(
    buildUrl(baseUrl, "/events/stream", {
      session_id: options.sessionId,
      state_scope_id: options.stateScopeId,
      last_event_id: options.lastEventId,
    }),
  );
}

export async function listSessions(baseUrl: string, limit = 24): Promise<SessionListPayload> {
  return request<SessionListPayload>(baseUrl, "/sessions", undefined, { limit });
}

export async function createSession(baseUrl: string, payload: { title?: string; message?: string } = {}): Promise<SessionMutationResult> {
  return request<SessionMutationResult>(baseUrl, "/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getSession(baseUrl: string, sessionId: string): Promise<{ ok: boolean; session: SessionDetail }> {
  return request<{ ok: boolean; session: SessionDetail }>(baseUrl, `/sessions/${encodeURIComponent(sessionId)}`);
}

export async function getSessionMessages(baseUrl: string, sessionId: string, limit = 40): Promise<SessionMessagesPayload> {
  return request<SessionMessagesPayload>(baseUrl, `/sessions/${encodeURIComponent(sessionId)}/messages`, undefined, { limit });
}

export async function sendSessionMessage(baseUrl: string, sessionId: string, message: string): Promise<SessionMutationResult> {
  return request<SessionMutationResult>(baseUrl, `/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export async function getStatus(baseUrl: string, sessionId = ""): Promise<StatusPayload> {
  return request<StatusPayload>(baseUrl, "/status", undefined, sessionId ? { session_id: sessionId } : undefined);
}

export async function getAlerts(baseUrl: string, sessionId = "", limit = 8): Promise<{ items?: AlertItem[] }> {
  return request<{ items?: AlertItem[] }>(baseUrl, "/alerts", undefined, {
    limit,
    session_id: sessionId || undefined,
  });
}

export async function getRecentRuns(baseUrl: string, sessionId = "", limit = 8): Promise<{ items?: RunEntry[] }> {
  return request<{ items?: RunEntry[] }>(baseUrl, "/runs/recent", undefined, {
    limit,
    session_id: sessionId || undefined,
  });
}

export async function getQueueState(baseUrl: string): Promise<QueuePayload> {
  return request<QueuePayload>(baseUrl, "/queue");
}

export async function getScheduledState(baseUrl: string): Promise<ScheduledPayload> {
  return request<ScheduledPayload>(baseUrl, "/scheduled");
}

export async function getWatchState(baseUrl: string): Promise<WatchPayload> {
  return request<WatchPayload>(baseUrl, "/watches");
}

export async function approvePending(baseUrl: string, sessionId = ""): Promise<ApprovalResult> {
  return request<ApprovalResult>(baseUrl, "/approval/approve", {
    method: "POST",
    body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
  });
}

export async function rejectPending(baseUrl: string, sessionId = ""): Promise<ApprovalResult> {
  return request<ApprovalResult>(baseUrl, "/approval/reject", {
    method: "POST",
    body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
  });
}
