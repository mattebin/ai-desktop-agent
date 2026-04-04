import { invoke } from "@tauri-apps/api/core";
import type { SlashCommand } from "./slashCommands";

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
  tool?: string;
  target?: string;
  evidence_summary?: string;
  evidence_preview?: EvidenceSummary;
};

export type EvidenceSummary = {
  evidence_id?: string;
  timestamp?: string;
  source_action?: string;
  evidence_kind?: string;
  reason?: string;
  summary?: string;
  active_window_title?: string;
  active_window_class_name?: string;
  active_window_process?: string;
  target_window_title?: string;
  window_count?: number;
  monitor_count?: number;
  screen_size?: string;
  window_summary?: string;
  screen_summary?: string;
  has_screenshot?: boolean;
  has_artifact?: boolean;
  screenshot_scope?: string;
  screenshot_backend?: string;
  screenshot_path?: string;
  bundle_path?: string;
  ui_evidence_present?: boolean;
  ui_control_count?: number;
  observation_token?: string;
  is_partial?: boolean;
  recency_seconds?: number;
  backend?: string;
  selection_reason?: string;
};

export type EvidenceArtifact = {
  evidence_id?: string;
  artifact_available?: boolean;
  artifact_type?: string;
  artifact_path?: string;
  artifact_name?: string;
  availability_state?: string;
  reason?: string;
  can_preview?: boolean;
  content_path?: string;
  bundle_path?: string;
  summary?: string;
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
    run_phase?: string;
    run_focus?: RunFocus;
    active_task?: ActiveTask;
    pending_approval?: PendingApproval;
    result_status?: string;
    result_message_preview?: string;
  };
};

export type ActiveTask = {
  task_id?: string;
  session_id?: string;
  status?: string;
  goal?: string;
  last_message?: string;
  run_id?: string;
  approval_needed?: boolean;
  approval_reason?: string;
  progress?: {
    stage?: string;
    detail?: string;
    result_status?: string;
  };
};

export type RunFocus = {
  phase?: string;
  reason?: string;
  locked?: boolean;
  task_id?: string;
  session_id?: string;
  run_id?: string;
  detail?: string;
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

export type DesktopState = {
  active_window_title?: string;
  active_window_process?: string;
  last_action?: string;
  last_target_window?: string;
  screenshot_path?: string;
  evidence_id?: string;
  evidence_summary?: string;
  evidence_bundle_path?: string;
  checkpoint_pending?: boolean;
  checkpoint_tool?: string;
  checkpoint_reason?: string;
  checkpoint_evidence_id?: string;
  selected_evidence?: EvidenceSummary;
  checkpoint_evidence?: EvidenceSummary;
  selected_target_proposals?: DesktopTargetProposalContext;
  checkpoint_target_proposals?: DesktopTargetProposalContext;
};

export type DesktopTargetProposal = {
  target_id?: string;
  target_kind?: string;
  window_title?: string;
  window_process?: string;
  source_evidence_id?: string;
  confidence?: string;
  confidence_score?: number;
  reason?: string;
  summary?: string;
  approval_required?: boolean;
  suggested_next_actions?: string[];
  point?: { x?: number; y?: number };
  region?: { x?: number; y?: number; width?: number; height?: number };
  coordinate_mode?: string;
  mapping_reason?: string;
};

export type DesktopTargetProposalContext = {
  purpose?: string;
  state?: string;
  reason?: string;
  summary?: string;
  confidence?: string;
  confidence_score?: number;
  proposal_count?: number;
  scene_class?: string;
  workflow_state?: string;
  readiness_state?: string;
  target_window_title?: string;
  pending_tool?: string;
  checkpoint_pending?: boolean;
  target_match_score?: number;
  proposer_names?: string[];
  proposals?: DesktopTargetProposal[];
};

export type RuntimeConfig = {
  active_model?: string;
  reasoning_effort?: string;
  reasoning_scope?: string;
  reasoning_effort_applies_to_tool_calls?: boolean;
  base_url?: string;
  settings_path?: string;
  settings_sources?: string[];
  source?: string;
  settings_version?: string;
  settings_loaded_at?: string;
  settings_reload_count?: number;
  settings_hot_reload?: {
    enabled?: boolean;
    scope?: string;
    notes?: string[];
  };
  tool_policy?: {
    summary?: string;
    read_only_tools?: string[];
    conditional_approval_tools?: string[];
    explicit_approval_tools?: string[];
    shell_hazard_tools?: string[];
    file_mutation_tools?: string[];
    notes?: string[];
  };
  email?: EmailStatusPayload;
};

export type ToolPolicySummary = {
  tool?: string;
  area?: string;
  risk_level?: string;
  approval_mode?: string;
  mutation_target?: string;
  summary?: string;
  planner_note?: string;
  shell_hazard?: string;
};

export type ToolSummary = {
  name?: string;
  description?: string;
  parameters?: Record<string, unknown>;
  policy?: ToolPolicySummary;
};

export type ExtensionCommandSummary = {
  type?: string;
  name?: string;
  description?: string;
  aliases?: string[];
  argumentHint?: string;
  category?: string;
  source?: string;
  promptText?: string;
  action?: string;
  extensionSlug?: string;
  relativePath?: string;
};

export type ExtensionSummary = {
  slug?: string;
  title?: string;
  description?: string;
  path?: string;
  relativePath?: string;
  source?: string;
  commandCount?: number;
  commands?: ExtensionCommandSummary[];
};

export type SkillSummary = {
  slug?: string;
  title?: string;
  description?: string;
  purpose?: string;
  whenToUse?: string[];
  path?: string;
  relativePath?: string;
  commandName?: string;
  aliases?: string[];
  promptText?: string;
  argumentHint?: string;
  tags?: string[];
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
  run_phase?: string;
  run_focus?: RunFocus;
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
  infrastructure?: Record<string, unknown>;
  desktop?: DesktopState;
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
  backendLogPath?: string;
};

export type DesktopEvidencePayload = {
  recent?: Array<Record<string, unknown>>;
  recent_summaries?: EvidenceSummary[];
  status?: {
    root?: string;
    count?: number;
    latest?: Record<string, unknown>;
    latest_summary?: EvidenceSummary;
    available?: boolean;
    reason?: string;
  };
};

export type DesktopEvidenceArtifactPayload = {
  artifact?: EvidenceArtifact;
};

export type SlashCommandCatalogPayload = {
  items?: SlashCommand[];
};

export type SkillCatalogPayload = {
  items?: SkillSummary[];
};

export type ToolCatalogPayload = {
  items?: ToolSummary[];
};

export type ExtensionCatalogPayload = {
  items?: ExtensionSummary[];
};

export type EmailStatusPayload = {
  provider?: string;
  enabled?: boolean;
  configured?: boolean;
  authenticated?: boolean;
  token_present?: boolean;
  token_valid?: boolean;
  dependency_available?: boolean;
  dependency_error?: string;
  client_secrets_path?: string;
  token_path?: string;
  profile_email?: string;
  watch_enabled?: boolean;
  watch_query?: string;
  poll_seconds?: number;
  scopes?: string[];
  restricted_scope_notice?: string;
  draft_counts?: Record<string, number>;
  last_checked_at?: string;
};

export type EmailThreadMessage = {
  message_id?: string;
  thread_id?: string;
  subject?: string;
  from?: string;
  from_address?: string;
  to?: string;
  cc?: string;
  reply_to?: string;
  date?: string;
  snippet?: string;
  body_text?: string;
  unread?: boolean;
  sent_by_self?: boolean;
};

export type EmailThreadSummary = {
  thread_id?: string;
  history_id?: string;
  message_count?: number;
  snippet?: string;
  subject?: string;
  last_from?: string;
  last_from_address?: string;
  last_date?: string;
  last_message_id?: string;
  unread?: boolean;
  messages?: EmailThreadMessage[];
};

export type EmailThreadsPayload = {
  ok?: boolean;
  provider?: string;
  profile_email?: string;
  query?: string;
  label_ids?: string[];
  items?: EmailThreadSummary[];
  thread?: EmailThreadSummary;
  error?: string;
};

export type EmailDraftSummary = {
  draft_id?: string;
  draft_type?: string;
  status?: string;
  provider?: string;
  thread_id?: string;
  message_id?: string;
  to?: string[];
  cc?: string[];
  subject?: string;
  summary?: string;
  confidence?: string;
  needs_context?: boolean;
  questions?: string[];
  updated_at?: string;
  created_at?: string;
};

export type EmailDraftsPayload = {
  ok?: boolean;
  items?: EmailDraftSummary[];
  summary?: EmailDraftSummary;
  draft?: Record<string, unknown>;
  message?: string;
  error?: string;
  paused?: boolean;
};

export type CommandExecutionResult = {
  kind?: string;
  title?: string;
  detail?: string;
  tone?: string;
  clear_draft?: boolean;
  prompt_text?: string;
  success_message?: string;
  action?: string;
  args?: string;
  result?: {
    ok?: boolean;
    message?: string;
    status?: string;
    resumed?: boolean;
  };
  status?: StatusPayload;
  session?: SessionDetail;
};

export type CommandExecutionPayload = {
  execution?: CommandExecutionResult;
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

function normalizeInvokeError(error: unknown): string {
  if (typeof error === "string" && error.trim()) {
    return error.trim();
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  return "Tauri bootstrap failed.";
}

async function waitForLocalOperator(baseUrl: string, timeoutMs = 20000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      await request<Record<string, unknown>>(baseUrl, "/health");
      return;
    } catch (error) {
      lastError = error instanceof Error ? error.message : "The local operator is not reachable yet.";
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    }
  }
  throw new Error(lastError || `Failed to fetch ${buildUrl(baseUrl, "/health")}`);
}

export async function ensureLocalApi(): Promise<EnsureLocalApiResult> {
  let tauriError = "";
  let bootResult: EnsureLocalApiResult | null = null;
  try {
    const result = await invoke<{
      baseUrl?: string;
      started?: boolean;
      managedByDesktop?: boolean;
      runtimeStatus?: DesktopRuntimeStatus;
      logPath?: string;
      backendLogPath?: string;
    }>("ensure_local_api");
    bootResult = {
      baseUrl: normalizeBaseUrl(result.baseUrl || DEFAULT_API_BASE_URL),
      started: Boolean(result.started),
      managedByDesktop: Boolean(result.managedByDesktop),
      runtimeStatus: result.runtimeStatus,
      logPath: result.logPath,
      backendLogPath: result.backendLogPath,
    };
    await waitForLocalOperator(bootResult.baseUrl);
    return bootResult;
  } catch (error) {
    tauriError = normalizeInvokeError(error);
    if (bootResult?.baseUrl) {
      const logNote = [bootResult.logPath ? `Runtime log: ${bootResult.logPath}.` : "", bootResult.backendLogPath ? `Backend log: ${bootResult.backendLogPath}.` : ""]
        .filter(Boolean)
        .join(" ");
      throw new Error(`Local operator startup failed for ${bootResult.baseUrl}. ${tauriError}${logNote ? ` ${logNote}` : ""}`.trim());
    }
  }

  try {
    await waitForLocalOperator(DEFAULT_API_BASE_URL, 6000);
    return {
      baseUrl: normalizeBaseUrl(DEFAULT_API_BASE_URL),
      started: false,
      managedByDesktop: false,
    };
  } catch (error) {
    const httpError = error instanceof Error ? error.message : "HTTP fallback failed.";
    if (tauriError) {
      throw new Error(`Unable to reach the local operator. Desktop bootstrap: ${tauriError} HTTP fallback: ${httpError}`.trim());
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

export async function getSlashCommands(baseUrl: string): Promise<SlashCommandCatalogPayload> {
  return request<SlashCommandCatalogPayload>(baseUrl, "/commands");
}

export async function getSkillCatalog(baseUrl: string): Promise<SkillCatalogPayload> {
  return request<SkillCatalogPayload>(baseUrl, "/skills");
}

export async function getToolCatalog(baseUrl: string): Promise<ToolCatalogPayload> {
  return request<ToolCatalogPayload>(baseUrl, "/tools");
}

export async function getExtensionCatalog(baseUrl: string): Promise<ExtensionCatalogPayload> {
  return request<ExtensionCatalogPayload>(baseUrl, "/extensions");
}

export async function getEmailStatus(baseUrl: string): Promise<EmailStatusPayload> {
  return request<EmailStatusPayload>(baseUrl, "/email/status");
}

export async function connectGmail(baseUrl: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(baseUrl, "/email/connect", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function listEmailThreads(
  baseUrl: string,
  options: { limit?: number; query?: string; labelIds?: string[] } = {},
): Promise<EmailThreadsPayload> {
  return request<EmailThreadsPayload>(baseUrl, "/email/threads", undefined, {
    limit: options.limit,
    query: options.query,
    label_ids: options.labelIds?.join(",") || undefined,
  });
}

export async function readEmailThread(baseUrl: string, threadId: string, maxMessages = 8): Promise<EmailThreadsPayload> {
  return request<EmailThreadsPayload>(baseUrl, `/email/threads/${encodeURIComponent(threadId)}`, undefined, { limit: maxMessages });
}

export async function listEmailDrafts(baseUrl: string, status = "", limit = 12): Promise<EmailDraftsPayload> {
  return request<EmailDraftsPayload>(baseUrl, "/email/drafts", undefined, {
    status: status || undefined,
    limit,
  });
}

export async function prepareEmailReplyDraft(
  baseUrl: string,
  payload: { thread_id: string; guidance?: string; user_context?: string },
): Promise<EmailDraftsPayload> {
  return request<EmailDraftsPayload>(baseUrl, "/email/drafts/reply", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function prepareEmailForwardDraft(
  baseUrl: string,
  payload: { thread_id: string; to: string[]; note?: string },
): Promise<EmailDraftsPayload> {
  return request<EmailDraftsPayload>(baseUrl, "/email/drafts/forward", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function sendEmailDraft(baseUrl: string, draftId: string, approvalStatus = "approved"): Promise<EmailDraftsPayload> {
  return request<EmailDraftsPayload>(baseUrl, "/email/drafts/send", {
    method: "POST",
    body: JSON.stringify({ draft_id: draftId, approval_status: approvalStatus }),
  });
}

export async function rejectEmailDraft(baseUrl: string, draftId: string, reason = "Rejected by operator."): Promise<EmailDraftsPayload> {
  return request<EmailDraftsPayload>(baseUrl, "/email/drafts/reject", {
    method: "POST",
    body: JSON.stringify({ draft_id: draftId, reason }),
  });
}

export async function executeSlashCommand(baseUrl: string, input: string, sessionId = ""): Promise<CommandExecutionPayload> {
  return request<CommandExecutionPayload>(baseUrl, "/commands/execute", {
    method: "POST",
    body: JSON.stringify(sessionId ? { input, session_id: sessionId } : { input }),
  });
}

export async function getAlerts(baseUrl: string, sessionId = "", limit = 8): Promise<{ items?: AlertItem[] }> {
  return request<{ items?: AlertItem[] }>(baseUrl, "/alerts", undefined, {
    limit,
    session_id: sessionId || undefined,
  });
}

export async function getDesktopEvidence(baseUrl: string, limit = 8): Promise<DesktopEvidencePayload> {
  return request<DesktopEvidencePayload>(baseUrl, "/desktop/evidence", undefined, { limit });
}

export async function getDesktopEvidenceArtifact(baseUrl: string, evidenceId: string): Promise<DesktopEvidenceArtifactPayload> {
  return request<DesktopEvidenceArtifactPayload>(baseUrl, `/desktop/evidence/${encodeURIComponent(evidenceId)}/artifact`);
}

export function getDesktopEvidenceArtifactContentUrl(baseUrl: string, evidenceId: string): string {
  return buildUrl(baseUrl, `/desktop/evidence/${encodeURIComponent(evidenceId)}/artifact/content`);
}

export function isDesktopEvidenceArtifactImage(artifact: EvidenceArtifact | null | undefined): boolean {
  if (!artifact?.artifact_available) {
    return false;
  }
  const artifactType = String(artifact.artifact_type || "").trim().toLowerCase();
  if (artifactType.startsWith("image/")) {
    return true;
  }
  const fallbackName = String(artifact.artifact_name || artifact.artifact_path || "").trim().toLowerCase();
  return [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"].some((extension) => fallbackName.endsWith(extension));
}

export function resolveDesktopEvidenceArtifactPreviewUrl(baseUrl: string, artifact: EvidenceArtifact | null | undefined): string {
  const contentPath = String(artifact?.content_path || "").trim();
  if (contentPath) {
    return buildUrl(baseUrl, contentPath);
  }
  const evidenceId = String(artifact?.evidence_id || "").trim();
  if (!evidenceId) {
    return "";
  }
  return getDesktopEvidenceArtifactContentUrl(baseUrl, evidenceId);
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
