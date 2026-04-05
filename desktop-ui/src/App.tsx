import React, { createContext, startTransition, useContext, useDeferredValue, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import clsx from "clsx";
import {
  Activity,
  AlertTriangle,
  BellRing,
  Bot,
  CalendarClock,
  CheckCircle2,
  CircleDot,
  Clock3,
  Command,
  Eye,
  GitBranchPlus,
  GitCommitHorizontal,
  History,
  ImageIcon,
  LoaderCircle,
  Mail,
  Menu,
  MessagesSquare,
  MonitorSmartphone,
  MoonStar,
  PanelRightClose,
  PanelRightOpen,
  PauseCircle,
  Play,
  Puzzle,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  SquarePen,
  SunMedium,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import {
  AlertItem,
  approvePending,
  BrowserState,
  connectGmail,
  createScheduledAutomation,
  createSession,
  createWatchAutomation,
  DesktopTargetProposal,
  DesktopTargetProposalContext,
  DesktopRuntimeStatus,
  EmailDraftSummary,
  EmailDraftsPayload,
  EmailStatusPayload,
  EmailThreadSummary,
  EmailThreadsPayload,
  EvidenceArtifact,
  EvidenceSummary,
  ensureLocalApi,
  ExtensionSummary,
  executeSlashCommand,
  getExtensionCatalog,
  getAlerts,
  getDesktopEvidenceArtifact,
  getEmailDraft,
  getSkillCatalog,
  getSlashCommands,
  getEmailStatus,
  getRunDetail,
  getToolCatalog,
  isDesktopEvidenceArtifactImage,
  getDesktopEvidence,
  getQueueState,
  getRecentRuns,
  getScheduledState,
  getSession,
  getSessionMessages,
  getStatus,
  getWatchState,
  listEmailDrafts,
  listEmailThreads,
  listSessions,
  openSessionEventStream,
  prepareEmailForwardDraft,
  prepareEmailReplyDraft,
  readEmailThread,
  rejectPending,
  rejectEmailDraft,
  resolveDesktopEvidenceArtifactPreviewUrl,
  RunDetail,
  resumeTask,
  RunFocus,
  RunEntry,
  sendEmailDraft,
  sendSessionMessage,
  SessionDetail,
  SessionMessage,
  SessionSummary,
  SkillSummary,
  StatusPayload,
  StreamEvent,
  stopTask,
  ToolSummary,
  retryTask,
  updateScheduledAutomation,
  updateWatchAutomation,
  type PendingApproval,
  type QueuePayload,
  type ScheduledPayload,
  type ScheduledTask,
  type WatchPayload,
  type WatchTask,
} from "./lib/api";
import {
  type LocalSlashCommand,
  type SlashCommand,
  type SlashCommandSuggestion,
  SLASH_COMMANDS,
  applySlashCommandSuggestion,
  getSlashCommandSuggestions,
  parseSlashCommandInput,
} from "./lib/slashCommands";
import { buildSlashCommandPlan } from "./lib/commandRouter";

type ActivityTone = "neutral" | "info" | "success" | "warning" | "error";

type ActivityEntry = {
  id: string;
  label: string;
  detail: string;
  tone: ActivityTone;
  timestamp: string;
};

type ControlSnapshot = {
  queue: QueuePayload | null;
  scheduled: ScheduledPayload | null;
  watches: WatchPayload | null;
  recentRuns: RunEntry[];
  desktopEvidence: EvidenceSummary[];
  tools: ToolSummary[];
  extensions: ExtensionSummary[];
};

type WorkspaceSurface = "chat" | "automations" | "gmail" | "workflows" | "runs";

type AutomationComposerMode = "scheduled" | "watch";

type ThemeMode = "light" | "dark";

type ArtifactViewerState = {
  open: boolean;
  loading: boolean;
  requestedEvidenceId: string;
  sourceLabel: string;
  heading: string;
  artifact: EvidenceArtifact | null;
  previewUrl: string;
  imageStatus: "idle" | "loading" | "ready" | "error";
  error: string;
};

const QUICK_PROMPTS = [
  "Inspect this project and explain the main architecture.",
  "Compare the main loop and agent files and summarize the differences.",
  "Suggest exact read-only commands to inspect the operator state.",
];

const THEME_STORAGE_KEY = "ai-operator:theme";
const DRAFTS_STORAGE_KEY = "ai-operator:drafts";
const NEW_SESSION_DRAFT_KEY = "__new__";
const TRANSCRIPT_BOTTOM_THRESHOLD = 120;
const COMPOSER_MAX_HEIGHT = 180;
const RECENT_SESSION_LIMIT = 6;

const STREAM_EVENTS = [
  "stream.hello",
  "stream.reset",
  "stream.heartbeat",
  "session.frame",
  "operator.frame",
  "session.sync",
  "operator.sync",
  "session.updated",
  "session.message",
  "task.queued",
  "task.started",
  "task.progress",
  "task.paused",
  "task.resumed",
  "task.completed",
  "task.failed",
  "task.blocked",
  "approval.needed",
  "approval.cleared",
  "approval.approved",
  "approval.rejected",
  "browser.workflow",
  "runtime.updated",
  "infrastructure.updated",
  "alert",
];

function plainTextPreview(value: string | undefined, limit = 180): string {
  const text = String(value || "")
    .replace(/```[\s\S]*?```/g, " [code block] ")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^#{1,6}\s*/gm, "")
    .replace(/^>\s?/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  return trimText(text, limit);
}

function formatToolCatalogDetail(tools: ToolSummary[] = []): string {
  if (!tools.length) {
    return "No tools are registered right now.";
  }
  return tools
    .map((tool) => {
      const name = String(tool.name || "tool").trim();
      const policy = tool.policy || {};
      const risk = String(policy.risk_level || "unknown").trim();
      const approval = String(policy.approval_mode || "unknown").trim();
      const summary = String(policy.summary || tool.description || "Registered tool").trim();
      return `${name} [${risk}/${approval}] - ${summary}`;
    })
    .join("\n");
}

function formatEmailStatusDetail(payload?: EmailStatusPayload | null): string {
  if (!payload) {
    return "Email status is not available right now.";
  }
  const draftCounts = payload.draft_counts || {};
  const draftSummary =
    Object.entries(draftCounts)
      .filter(([, count]) => Number(count || 0) > 0)
      .map(([status, count]) => `${status}: ${count}`)
      .join(", ") || "none";
  const state = !payload.enabled
    ? "disabled"
    : !payload.configured
      ? "needs setup"
      : !payload.authenticated
        ? "needs sign-in"
        : "connected";
  const lines = [
    `Provider: ${String(payload.provider || "gmail").trim() || "gmail"}`,
    `State: ${state}`,
    `Account: ${String(payload.profile_email || "Not connected").trim() || "Not connected"}`,
    `Watch: ${payload.watch_enabled ? "enabled" : "disabled"}`,
    `Drafts: ${draftSummary}`,
  ];
  if (typeof payload.poll_seconds === "number") {
    lines.push(`Poll interval: ${payload.poll_seconds}s`);
  }
  if (payload.last_checked_at) {
    lines.push(`Checked: ${payload.last_checked_at}`);
  }
  return lines.join("\n");
}

function formatEmailThreadsDetail(payload?: EmailThreadsPayload | null): string {
  if (payload?.error) {
    return String(payload.error).trim();
  }
  const items = payload?.items || [];
  if (!items.length) {
    return "No inbox threads matched the current Gmail query.";
  }
  return items
    .map((thread) => {
      const subject = String(thread.subject || "No subject").trim();
      const from = String(thread.last_from || thread.last_from_address || "Unknown sender").trim();
      const date = String(thread.last_date || "").trim();
      const unread = thread.unread ? "unread" : "read";
      return `${subject} - ${from}${date ? ` (${date})` : ""} [${unread}]`;
    })
    .join("\n");
}

function formatEmailDraftsDetail(payload?: EmailDraftsPayload | null): string {
  if (payload?.error) {
    return String(payload.error).trim();
  }
  const items = payload?.items || [];
  if (!items.length) {
    return "No Gmail drafts are stored locally right now.";
  }
  return items
    .map((draft) => {
      const subject = String(draft.subject || "No subject").trim();
      const status = String(draft.status || "unknown").trim();
      const recipients = Array.isArray(draft.to) && draft.to.length ? draft.to.join(", ") : "No recipient";
      return `${subject} [${status}] - ${recipients}`;
    })
    .join("\n");
}

function getInitialDrafts(): Record<string, string> {
  try {
    const raw = window.localStorage.getItem(DRAFTS_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") {
      return {};
    }
    return Object.entries(parsed).reduce<Record<string, string>>((accumulator, [key, value]) => {
      if (typeof value === "string" && value.trim()) {
        accumulator[key] = value;
      }
      return accumulator;
    }, {});
  } catch (_error) {
    return {};
  }
}

function getDraftKey(sessionId: string): string {
  return sessionId || NEW_SESSION_DRAFT_KEY;
}

function isTranscriptNearBottom(element: HTMLDivElement | null): boolean {
  if (!element) {
    return true;
  }
  return element.scrollHeight - element.scrollTop - element.clientHeight <= TRANSCRIPT_BOTTOM_THRESHOLD;
}

function sessionPreviewText(session: SessionSummary): string {
  if (session.pending_approval?.kind) {
    return plainTextPreview(`Approval needed. ${approvalSummary(session.pending_approval)}`, 180);
  }
  return (
    plainTextPreview(
      session.last_result_message_preview || session.latest_message?.content || session.summary || "Ready for a new request.",
      180,
    ) || "Ready for a new request."
  );
}

function formatSessionStamp(value?: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function partitionSessionsForSidebar(
  sessions: SessionSummary[],
  selectedSessionId: string,
  limit = RECENT_SESSION_LIMIT,
): { recent: SessionSummary[]; history: SessionSummary[] } {
  const ordered = sessions.slice();
  if (!ordered.length) {
    return { recent: [], history: [] };
  }
  const selectedIndex = ordered.findIndex((session) => session.session_id === selectedSessionId);
  const recent = ordered.slice(0, limit);

  if (selectedIndex >= limit) {
    const selected = ordered[selectedIndex];
    const promoted = [selected, ...recent.slice(0, Math.max(limit - 1, 0))];
    const promotedIds = new Set(promoted.map((session) => session.session_id));
    return {
      recent: promoted,
      history: ordered.filter((session) => !promotedIds.has(session.session_id)),
    };
  }

  const recentIds = new Set(recent.map((session) => session.session_id));
  return {
    recent,
    history: ordered.filter((session) => !recentIds.has(session.session_id)),
  };
}

function desktopRuntimeLabel(runtimeStatus?: DesktopRuntimeStatus | null): string {
  const state = String(runtimeStatus?.backend_state || "").toLowerCase();
  if (state === "app_managed") {
    return "Desktop-managed API";
  }
  if (state === "detached") {
    return "Detached backend";
  }
  if (state === "externally_managed") {
    return "External backend";
  }
  if (state === "unhealthy") {
    return "Backend unhealthy";
  }
  if (state === "missing") {
    return "Backend starting";
  }
  return "";
}

async function copyTextToClipboard(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch (_error) {
    return false;
  }
}

function CodeBlock({
  className,
  children,
  ...rest
}: {
  className?: string;
  children?: React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const resetTimerRef = useRef<number | null>(null);
  const codeText = String(children || "").replace(/\n$/, "");
  const language = (className || "").replace(/^language-/, "").trim();

  useEffect(() => {
    return () => {
      if (resetTimerRef.current) {
        window.clearTimeout(resetTimerRef.current);
      }
    };
  }, []);

  async function handleCopy() {
    if (!codeText) {
      return;
    }
    const ok = await copyTextToClipboard(codeText);
    if (!ok) {
      return;
    }
    setCopied(true);
    if (resetTimerRef.current) {
      window.clearTimeout(resetTimerRef.current);
    }
    resetTimerRef.current = window.setTimeout(() => {
      setCopied(false);
      resetTimerRef.current = null;
    }, 1600);
  }

  return (
    <div className="code-block-shell">
      <div className="code-block-toolbar">
        <span className="code-block-language">{language ? language.toUpperCase() : "CODE"}</span>
        <button className="code-copy-button" onClick={() => void handleCopy()} type="button">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="code-block">
        <code className={className} {...rest}>
          {children}
        </code>
      </pre>
    </div>
  );
}

const markdownComponents: Components = {
  h1: ({ children }) => <h1>{children}</h1>,
  h2: ({ children }) => <h2>{children}</h2>,
  h3: ({ children }) => <h3>{children}</h3>,
  p: ({ children }) => <p>{children}</p>,
  ul: ({ children }) => <ul>{children}</ul>,
  ol: ({ children }) => <ol>{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  blockquote: ({ children }) => <blockquote>{children}</blockquote>,
  table: ({ children }) => (
    <div className="markdown-table">
      <table>{children}</table>
    </div>
  ),
  code(props) {
    const { inline, className, children, ...rest } = props as {
      inline?: boolean;
      className?: string;
      children?: React.ReactNode;
    };
    if (inline) {
      return (
        <code className="inline-code" {...rest}>
          {children}
        </code>
      );
    }
    return <CodeBlock className={className} {...rest}>{children}</CodeBlock>;
  },
  a: ({ href, children, ...props }) => (
    <a className="markdown-link" href={href} target="_blank" rel="noreferrer" {...props}>
      {children}
    </a>
  ),
};

function normalizeMessages(messages: SessionMessage[]): SessionMessage[] {
  const seen = new Set<string>();
  const ordered: SessionMessage[] = [];
  messages.forEach((message) => {
    const key = messageStableKey(message);
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    ordered.push(message);
  });
  return ordered;
}

function replaceMessagesPreservingIdentity(current: SessionMessage[], nextMessages: SessionMessage[]): SessionMessage[] {
  const normalized = normalizeMessages(nextMessages);
  if (!current.length) {
    logMessageMutation("replace", { reason: "initial", count: normalized.length });
    return normalized;
  }
  const currentByKey = new Map(current.map((message) => [messageStableKey(message), message] as const));
  const next = normalized.map((message) => {
    const existing = currentByKey.get(messageStableKey(message));
    return existing && messagesEquivalent(existing, message) ? existing : message;
  });
  if (current.length === next.length && current.every((message, index) => message === next[index])) {
    return current;
  }
  const reused = next.filter((message) => currentByKey.get(messageStableKey(message)) === message).length;
  logMessageMutation("replace", {
    reason: "authoritative_sync",
    currentCount: current.length,
    nextCount: next.length,
    reused,
  });
  return next;
}

function mergeMessages(current: SessionMessage[], incoming: SessionMessage[]): SessionMessage[] {
  const normalizedIncoming = normalizeMessages(incoming);
  if (!normalizedIncoming.length) {
    return current;
  }
  if (!current.length) {
    logMessageMutation("merge", { reason: "seed", count: normalizedIncoming.length });
    return normalizedIncoming;
  }
  const indexByKey = new Map(current.map((message, index) => [messageStableKey(message), index] as const));
  const next = current.slice();
  let added = 0;
  let updated = 0;
  for (const message of normalizedIncoming) {
    const key = messageStableKey(message);
    const existingIndex = indexByKey.get(key);
    if (existingIndex === undefined) {
      indexByKey.set(key, next.length);
      next.push(message);
      added += 1;
      continue;
    }
    const existing = next[existingIndex];
    if (!messagesEquivalent(existing, message)) {
      next[existingIndex] = message;
      updated += 1;
    }
  }
  if (!added && !updated) {
    return current;
  }
  logMessageMutation("merge", {
    reason: "incremental_frame",
    currentCount: current.length,
    nextCount: next.length,
    added,
    updated,
  });
  return next;
}

function reconcileSessionList(current: SessionSummary[], nextSessions: SessionSummary[]): SessionSummary[] {
  if (!current.length) {
    return nextSessions;
  }
  const currentById = new Map(current.map((session) => [session.session_id, session] as const));
  let changed = current.length !== nextSessions.length;
  const next = nextSessions.map((session) => {
    const existing = currentById.get(session.session_id);
    if (!existing) {
      changed = true;
      return session;
    }
    const merged = { ...existing, ...session };
    if (fingerprintValue(existing) === fingerprintValue(merged)) {
      return existing;
    }
    changed = true;
    return merged;
  });
  if (!changed && current.every((session, index) => session === next[index])) {
    return current;
  }
  return next;
}

function upsertSession(sessions: SessionSummary[], session: SessionSummary): SessionSummary[] {
  const index = sessions.findIndex((item) => item.session_id === session.session_id);
  if (index < 0) {
    const next = [...sessions, session];
    next.sort((left, right) => (right.updated_at || "").localeCompare(left.updated_at || ""));
    return next;
  }
  const existing = sessions[index];
  const merged = { ...existing, ...session };
  if (fingerprintValue(existing) === fingerprintValue(merged)) {
    return sessions;
  }
  const next = sessions.slice();
  next[index] = merged;
  next.sort((left, right) => (right.updated_at || "").localeCompare(left.updated_at || ""));
  return next;
}

function shouldRefreshControlDataFromFrame(frame?: StreamFramePayload | null): boolean {
  if (!frame) {
    return false;
  }
  const changed = new Set(
    Array.isArray(frame.changed)
      ? frame.changed
          .map((item) => String(item || "").trim().toLowerCase())
          .filter(Boolean)
      : [],
  );
  if (!changed.size) {
    return Boolean(frame.alerts?.length);
  }
  if (changed.has("runtime") || changed.has("infrastructure")) {
    return true;
  }
  for (const key of ["task", "task_progress", "pending_approval", "desktop", "alerts"]) {
    if (changed.has(key)) {
      return true;
    }
  }
  return false;
}

function isCriticalSnapshotUpdate(snapshot?: StatusPayload | null): boolean {
  if (!snapshot) {
    return false;
  }
  const phase = String(snapshot.run_phase || "").trim().toLowerCase();
  const status = String(snapshot.status || "").trim().toLowerCase();
  if (phase === "awaiting_approval" || phase === "post_execution") {
    return true;
  }
  if (["paused", "completed", "blocked", "failed", "incomplete"].includes(status)) {
    return true;
  }
  return Boolean(snapshot.pending_approval?.kind);
}

function formatTime(value?: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDateTime(value?: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function trimText(value: string | undefined, limit = 180): string {
  const text = String(value || "").trim();
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
}

function getInitialTheme(): ThemeMode {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "light" || stored === "dark") {
    return stored;
  }
  if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

function sessionMatchesQuery(session: SessionSummary, query: string): boolean {
  const text = query.trim().toLowerCase();
  if (!text) {
    return true;
  }
  const haystack = [
    session.title,
    session.summary,
    session.status,
    session.pending_approval?.kind,
    session.pending_approval?.reason,
    session.latest_message?.content,
    session.last_result_message_preview,
  ]
    .join(" ")
    .toLowerCase();
  return text.split(/\s+/).every((term) => haystack.includes(term));
}

function statusTone(status?: string): ActivityTone {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "completed") {
    return "success";
  }
  if (normalized === "paused" || normalized === "needs_attention") {
    return "warning";
  }
  if (normalized === "failed" || normalized === "blocked" || normalized === "stopped" || normalized === "incomplete") {
    return "error";
  }
  if (normalized === "running" || normalized === "queued") {
    return "info";
  }
  return "neutral";
}

function approvalSummary(pending?: PendingApproval | null): string {
  if (!pending?.kind) {
    return "";
  }
  return pending.reason || pending.summary || pending.step || pending.kind;
}

function proposalTone(state?: string): ActivityTone {
  const normalized = String(state || "").toLowerCase();
  if (normalized === "ready") {
    return "success";
  }
  if (normalized === "recovery_first" || normalized === "approval_context") {
    return "warning";
  }
  if (normalized === "blocked" || normalized === "no_safe_target") {
    return "error";
  }
  return "neutral";
}

function proposalLabel(proposal?: DesktopTargetProposal | null): string {
  if (!proposal) {
    return "Target";
  }
  return (
    proposal.window_title ||
    proposal.target_kind ||
    proposal.summary ||
    "Target"
  );
}

type IconName =
  | "brand"
  | "menu"
  | "refresh"
  | "theme-light"
  | "theme-dark"
  | "new"
  | "chat"
  | "approval"
  | "task"
  | "alert"
  | "activity"
  | "evidence"
  | "details"
  | "history"
  | "tools"
  | "gmail"
  | "desktop"
  | "automations"
  | "workflows"
  | "extensions"
  | "run"
  | "stop"
  | "handoff"
  | "commit"
  | "search";

const ICONS = {
  brand: Bot,
  menu: Menu,
  refresh: RefreshCw,
  "theme-light": SunMedium,
  "theme-dark": MoonStar,
  new: SquarePen,
  chat: MessagesSquare,
  approval: ShieldCheck,
  task: Workflow,
  alert: BellRing,
  activity: Activity,
  evidence: ImageIcon,
  details: PanelRightOpen,
  history: History,
  tools: Command,
  gmail: Mail,
  desktop: MonitorSmartphone,
  automations: CalendarClock,
  workflows: Sparkles,
  extensions: Puzzle,
  run: Play,
  stop: Square,
  handoff: GitBranchPlus,
  commit: GitCommitHorizontal,
  search: Search,
} satisfies Record<IconName, LucideIcon>;

function UiIcon({ name, className }: { name: IconName; className?: string }) {
  const Icon = ICONS[name];
  return <Icon aria-hidden className={clsx("ui-icon", className)} strokeWidth={1.85} />;
}

function StatusGlyph({ status, className }: { status?: string; className?: string }) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "running" || normalized === "queued") {
    return <LoaderCircle aria-hidden className={clsx("status-glyph", "is-running", className)} strokeWidth={1.9} />;
  }
  if (normalized === "paused" || normalized === "needs_attention") {
    return <PauseCircle aria-hidden className={clsx("status-glyph", className)} strokeWidth={1.9} />;
  }
  if (normalized === "completed") {
    return <CheckCircle2 aria-hidden className={clsx("status-glyph", className)} strokeWidth={1.9} />;
  }
  if (normalized === "failed" || normalized === "blocked" || normalized === "stopped" || normalized === "incomplete") {
    return <AlertTriangle aria-hidden className={clsx("status-glyph", className)} strokeWidth={1.9} />;
  }
  if (normalized === "idle") {
    return <Clock3 aria-hidden className={clsx("status-glyph", className)} strokeWidth={1.9} />;
  }
  return <CircleDot aria-hidden className={clsx("status-glyph", className)} strokeWidth={1.9} />;
}

function SectionTitle({
  icon,
  children,
}: {
  icon: IconName;
  children: React.ReactNode;
}) {
  return (
    <span className="section-title">
      <UiIcon name={icon} />
      <span>{children}</span>
    </span>
  );
}

const CompactMenuContext = createContext<(() => void) | null>(null);

function OverlayPortal({ children }: { children: React.ReactNode }) {
  const [container, setContainer] = useState<HTMLElement | null>(null);

  useEffect(() => {
    setContainer(document.body);
  }, []);

  return container ? createPortal(children, container) : null;
}

function CompactMenu({
  label,
  icon,
  children,
}: {
  label: string;
  icon: IconName;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ top: 0, left: 0, minWidth: 198 });

  function closeMenu() {
    setOpen(false);
  }

  function updatePosition() {
    const trigger = triggerRef.current;
    if (!trigger) {
      return;
    }
    const rect = trigger.getBoundingClientRect();
    const minWidth = Math.max(Math.round(rect.width), 198);
    const maxLeft = Math.max(12, window.innerWidth - minWidth - 12);
    const left = Math.max(12, Math.min(Math.round(rect.right - minWidth), maxLeft));
    const top = Math.round(rect.bottom + 8);
    setPosition({ top, left, minWidth });
  }

  useLayoutEffect(() => {
    if (!open) {
      return;
    }
    updatePosition();
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function handlePointerDown(event: MouseEvent) {
      const target = event.target as Node | null;
      if (target && (triggerRef.current?.contains(target) || popoverRef.current?.contains(target))) {
        return;
      }
      closeMenu();
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        closeMenu();
      }
    }
    function handleWindowChange() {
      closeMenu();
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", handleWindowChange);
    window.addEventListener("scroll", handleWindowChange, true);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", handleWindowChange);
      window.removeEventListener("scroll", handleWindowChange, true);
    };
  }, [open]);

  return (
    <CompactMenuContext.Provider value={closeMenu}>
      <div className={clsx("compact-menu", open && "is-open")}>
        <button
          aria-expanded={open}
          className="ghost-button compact-menu-trigger"
          onClick={() => setOpen((current) => !current)}
          ref={triggerRef}
          type="button"
        >
          <UiIcon name={icon} />
          <span>{label}</span>
        </button>
        {open ? (
          <OverlayPortal>
            <div className="compact-menu-popover-layer">
              <div
                className="compact-menu-popover"
                ref={popoverRef}
                style={{ top: `${position.top}px`, left: `${position.left}px`, minWidth: `${position.minWidth}px` }}
              >
                {children}
              </div>
            </div>
          </OverlayPortal>
        ) : null}
      </div>
    </CompactMenuContext.Provider>
  );
}

function CompactMenuButton({
  icon,
  children,
  onClick,
}: {
  icon: IconName;
  children: React.ReactNode;
  onClick: () => void;
}) {
  const closeMenu = useContext(CompactMenuContext);
  return (
    <button
      className="compact-menu-item"
      type="button"
      onClick={() => {
        onClick();
        closeMenu?.();
      }}
    >
      <UiIcon name={icon} />
      <span>{children}</span>
    </button>
  );
}

function hasEvidencePreview(preview?: EvidenceSummary | null): boolean {
  if (!preview) {
    return false;
  }
  return Boolean(
    preview.evidence_id ||
      preview.summary ||
      preview.active_window_title ||
      preview.target_window_title ||
      preview.has_screenshot ||
      preview.ui_evidence_present,
  );
}

function formatEvidenceRecency(seconds?: number): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
    return "";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s ago`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m ago`;
  }
  if (seconds < 86400) {
    return `${Math.round(seconds / 3600)}h ago`;
  }
  return `${Math.round(seconds / 86400)}d ago`;
}

function evidenceMetaItems(preview?: EvidenceSummary | null): string[] {
  if (!preview) {
    return [];
  }
  const items = [
    preview.active_window_title ? `Window: ${preview.active_window_title}` : "",
    preview.target_window_title ? `Target: ${preview.target_window_title}` : "",
    preview.active_window_process ? `Process: ${preview.active_window_process}` : "",
    preview.screen_summary || preview.window_summary || "",
    formatEvidenceRecency(preview.recency_seconds),
  ].filter(Boolean);
  return items.slice(0, 3);
}

function evidenceSummaryText(preview?: EvidenceSummary | null): string {
  if (!preview) {
    return "";
  }
  return (
    preview.summary ||
    preview.active_window_title ||
    preview.target_window_title ||
    preview.window_summary ||
    preview.screen_summary ||
    "Desktop evidence captured."
  );
}

function evidenceBadges(preview?: EvidenceSummary | null): string[] {
  if (!preview) {
    return [];
  }
  const badges: string[] = [];
  if (preview.is_partial) {
    badges.push("Partial");
  }
  if (preview.has_screenshot) {
    badges.push(preview.has_artifact ? "Screenshot retained" : "Screenshot unavailable");
  }
  if (preview.ui_evidence_present) {
    badges.push(preview.ui_control_count ? `UI evidence (${preview.ui_control_count})` : "UI evidence");
  }
  return badges;
}

function evidenceNote(preview?: EvidenceSummary | null): string {
  if (!preview) {
    return "";
  }
  if (preview.has_screenshot && !preview.has_artifact) {
    return "A screenshot was collected earlier, but the retained artifact is no longer available.";
  }
  if (preview.is_partial) {
    return "This evidence bundle is partial, so some desktop context may still be missing.";
  }
  return "";
}

function artifactStateMessage(artifact?: EvidenceArtifact | null): string {
  if (!artifact) {
    return "";
  }
  const state = String(artifact.availability_state || "").toLowerCase();
  if (state === "available") {
    return "Retained screenshot available.";
  }
  if (state === "missing") {
    return "The retained screenshot is no longer available in local storage.";
  }
  if (state === "pruned") {
    return "This retained screenshot was pruned from the local evidence store.";
  }
  if (state === "not_found") {
    return "This evidence reference is no longer available in the local evidence store.";
  }
  return "This evidence bundle does not currently include a retained artifact.";
}

function EvidencePreviewCard({
  title,
  preview,
  emptyText,
  onViewArtifact,
  artifactLoading,
}: {
  title: string;
  preview?: EvidenceSummary | null;
  emptyText?: string;
  onViewArtifact?: (preview: EvidenceSummary) => void;
  artifactLoading?: boolean;
}) {
  if (!hasEvidencePreview(preview)) {
    if (!emptyText) {
      return null;
    }
    return (
      <div className="evidence-preview evidence-preview-empty">
        <div className="evidence-preview-header">
          <span className="evidence-preview-title">{title}</span>
        </div>
        <p className="evidence-preview-summary">{emptyText}</p>
      </div>
    );
  }

  const metaItems = evidenceMetaItems(preview);
  const badges = evidenceBadges(preview);
  const note = evidenceNote(preview);

  return (
    <div className={clsx("evidence-preview", preview?.is_partial && "is-partial")}>
      <div className="evidence-preview-header">
        <span className="evidence-preview-title">{title}</span>
        {preview?.reason && preview.reason !== "collected" ? <span className="muted-label">{plainTextPreview(preview.reason, 28)}</span> : null}
      </div>
      <p className="evidence-preview-summary">{plainTextPreview(evidenceSummaryText(preview), 180)}</p>
      {metaItems.length ? (
        <div className="evidence-preview-meta">
          {metaItems.map((item) => (
            <span key={item} className="evidence-chip">
              {plainTextPreview(item, 48)}
            </span>
          ))}
        </div>
      ) : null}
      {badges.length ? (
        <div className="evidence-preview-meta">
          {badges.map((item) => (
            <span key={item} className="evidence-chip evidence-chip-soft">
              {item}
            </span>
          ))}
        </div>
      ) : null}
      <div className="evidence-preview-footer">
        {preview?.evidence_id ? <span className="evidence-reference">Ref {preview.evidence_id}</span> : null}
        <div className="evidence-preview-actions">
          {preview?.selection_reason ? <span className="muted-label">{plainTextPreview(preview.selection_reason, 26)}</span> : null}
          {onViewArtifact && preview?.evidence_id && (preview.has_screenshot || preview.has_artifact) ? (
            <button className="ghost-button evidence-action-button" onClick={() => onViewArtifact(preview)} type="button" disabled={artifactLoading}>
              {artifactLoading ? "Loading..." : "View artifact"}
            </button>
          ) : null}
        </div>
      </div>
      {note ? <p className="evidence-preview-note">{note}</p> : null}
    </div>
  );
}

function timelineFromEvent(event: StreamEvent<Record<string, unknown>>): ActivityEntry | null {
  const now = event.emitted_at || new Date().toISOString();
  const data = event.data || {};
  switch (event.event) {
    case "stream.hello":
    case "stream.heartbeat":
    case "session.sync":
    case "operator.sync":
    case "session.updated":
      return null;
    case "task.started":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Task started",
        detail: plainTextPreview(String(data.current_step || (data.task as { goal?: string })?.goal || "Operator work started."), 220),
        tone: "info",
        timestamp: now,
      };
    case "task.progress":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Working",
        detail: plainTextPreview(String(data.current_step || (data.task as { last_message?: string })?.last_message || "Operator is making progress."), 220),
        tone: "info",
        timestamp: now,
      };
    case "task.completed":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Task completed",
        detail: plainTextPreview(String(data.result_message || data.current_step || "The task completed successfully."), 220),
        tone: "success",
        timestamp: now,
      };
    case "task.paused":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Paused for approval",
        detail: plainTextPreview(String(data.current_step || (data.task as { approval_reason?: string })?.approval_reason || "Waiting for approval."), 220),
        tone: "warning",
        timestamp: now,
      };
    case "task.failed":
    case "task.blocked":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: event.event === "task.failed" ? "Task failed" : "Task blocked",
        detail: plainTextPreview(String(data.result_message || data.current_step || "The task needs attention."), 220),
        tone: "error",
        timestamp: now,
      };
    case "approval.needed":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Approval needed",
        detail: plainTextPreview(
          String(
            (data.pending_approval as PendingApproval | undefined)?.reason ||
              (data.pending_approval as PendingApproval | undefined)?.summary ||
              "The operator is waiting for your decision.",
          ),
          220,
        ),
        tone: "warning",
        timestamp: now,
      };
    case "approval.approved":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Approved",
        detail: plainTextPreview(String((data.message as SessionMessage | undefined)?.content || "You approved the pending action."), 220),
        tone: "success",
        timestamp: now,
      };
    case "approval.rejected":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Rejected",
        detail: plainTextPreview(String((data.message as SessionMessage | undefined)?.content || "You rejected the pending action."), 220),
        tone: "warning",
        timestamp: now,
      };
    case "browser.workflow":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Browser updated",
        detail: plainTextPreview(
          String(
            (data.browser as BrowserState | undefined)?.workflow_step ||
              (data.browser as BrowserState | undefined)?.task_step ||
              (data.browser as BrowserState | undefined)?.current_title ||
              "Browser workflow state changed.",
          ),
          220,
        ),
        tone: "info",
        timestamp: now,
      };
    case "runtime.updated":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Runtime updated",
        detail: plainTextPreview(
          String(
            (data.runtime as { active_model?: string; settings_version?: string } | undefined)?.active_model ||
              (data.runtime as { settings_version?: string } | undefined)?.settings_version ||
              "Runtime configuration changed.",
          ),
          220,
        ),
        tone: "info",
        timestamp: now,
      };
    case "infrastructure.updated":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Infrastructure updated",
        detail: plainTextPreview("Backend infrastructure state changed.", 220),
        tone: "info",
        timestamp: now,
      };
    case "alert":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: String((data.alert as AlertItem | undefined)?.title || "Alert"),
        detail: plainTextPreview(String((data.alert as AlertItem | undefined)?.message || "A new operator alert is available."), 220),
        tone: statusTone((data.alert as AlertItem | undefined)?.severity),
        timestamp: now,
      };
    case "session.message":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "New reply",
        detail: plainTextPreview(String((data.message as SessionMessage | undefined)?.content || "The conversation has a new message."), 220),
        tone: "neutral",
        timestamp: now,
      };
    case "stream.reset":
      return {
        id: event.event_id || `${event.event}:${now}`,
        label: "Live stream reset",
        detail: plainTextPreview(String((data.reason as string) || "The UI resynced the live stream."), 220),
        tone: "warning",
        timestamp: now,
      };
    default:
      return null;
  }
}

function messageDisplayKind(message: SessionMessage): "user" | "assistant" | "activity" | "error" {
  if ((message.role || "").toLowerCase() === "user") {
    return "user";
  }
  const kind = (message.kind || "").toLowerCase();
  if (kind === "error") {
    return "error";
  }
  if (kind === "status" || kind === "approval_needed" || kind === "approval") {
    return "activity";
  }
  return "assistant";
}

function MessageContent({ message }: { message: SessionMessage }) {
  return (
    <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
      {message.content || ""}
    </ReactMarkdown>
  );
}

function MessageBubble({ message }: { message: SessionMessage }) {
  if (isRenderDebugEnabled()) {
    console.debug("[ai-operator][message-render]", {
      key: messageStableKey(message),
      kind: message.kind || "message",
      status: message.status || "",
      messageId: message.message_id || "",
    });
  }
  const displayKind = messageDisplayKind(message);
  const normalizedKind = (message.kind || "").toLowerCase();
  const badge =
    normalizedKind === "final"
      ? "Final reply"
      : normalizedKind === "approval_needed"
        ? "Approval needed"
        : normalizedKind === "status"
          ? "Activity"
          : "";

  return (
    <article className={clsx("message", `message-${displayKind}`, normalizedKind && `message-kind-${normalizedKind}`)}>
      <div className="message-meta">
        <span className="message-role">
          {displayKind === "user" ? "You" : displayKind === "activity" ? "Operator activity" : "Operator"}
        </span>
        {badge ? <span className="message-badge">{badge}</span> : null}
        {message.status ? <span className={clsx("status-pill", `tone-${statusTone(message.status)}`)}>{message.status}</span> : null}
        {message.created_at ? <span className="message-time">{formatTime(message.created_at)}</span> : null}
      </div>
      <div className="message-body">
        <MessageContent message={message} />
      </div>
    </article>
  );
}

type TranscriptItem =
  | { type: "message"; key: string; message: SessionMessage }
  | { type: "activity-group"; key: string; messages: SessionMessage[] };

function activityClusterTitle(message: SessionMessage): string {
  const kind = String(message.kind || "").toLowerCase();
  if (kind === "approval_needed") {
    return "Approval checkpoint";
  }
  if (kind === "approval") {
    return "Approval update";
  }
  if (kind === "status") {
    return String(message.status || "Operator activity").trim() || "Operator activity";
  }
  return "Operator activity";
}

function activityClusterTone(message: SessionMessage): ActivityTone {
  const kind = String(message.kind || "").toLowerCase();
  if (kind === "approval_needed") {
    return "warning";
  }
  return statusTone(message.status);
}

function buildTranscriptItems(messages: SessionMessage[]): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  let activityBuffer: SessionMessage[] = [];

  function flushActivityBuffer() {
    if (!activityBuffer.length) {
      return;
    }
    const first = activityBuffer[0];
    const last = activityBuffer[activityBuffer.length - 1];
    items.push({
      type: "activity-group",
      key: `activity:${messageStableKey(first)}:${messageStableKey(last)}:${activityBuffer.length}`,
      messages: activityBuffer,
    });
    activityBuffer = [];
  }

  messages.forEach((message) => {
    if (messageDisplayKind(message) === "activity") {
      activityBuffer.push(message);
      return;
    }
    flushActivityBuffer();
    items.push({
      type: "message",
      key: messageStableKey(message),
      message,
    });
  });

  flushActivityBuffer();
  return items;
}

function ActivityCluster({ messages }: { messages: SessionMessage[] }) {
  const [expanded, setExpanded] = useState(messages.length <= 3);
  const hiddenCount = Math.max(0, messages.length - 3);
  const visibleMessages = expanded ? messages : messages.slice(-3);
  const latest = messages[messages.length - 1];

  return (
    <section className="activity-cluster">
      <div className="activity-cluster-header">
        <div className="activity-cluster-title">
          <StatusGlyph className={clsx(`tone-${activityClusterTone(latest)}`)} status={latest.status || latest.kind} />
          <span>{messages.length > 1 ? `${messages.length} operator updates` : activityClusterTitle(latest)}</span>
        </div>
        <div className="activity-cluster-meta">
          {hiddenCount > 0 ? (
            <button className="ghost-button activity-cluster-toggle" onClick={() => setExpanded((current) => !current)} type="button">
              {expanded ? "Collapse" : `Show ${hiddenCount} earlier`}
            </button>
          ) : null}
          <span className="mini-list-time">{formatTime(latest.created_at)}</span>
        </div>
      </div>
      <div className="activity-cluster-list">
        {visibleMessages.map((message) => (
          <article
            key={`${messageStableKey(message)}:${message.created_at || ""}`}
            className={clsx("activity-cluster-item", `tone-${activityClusterTone(message)}`)}
          >
            <div className="activity-cluster-item-header">
              <span className="activity-cluster-item-title">{activityClusterTitle(message)}</span>
              {message.created_at ? <span className="mini-list-time">{formatTime(message.created_at)}</span> : null}
            </div>
            <p className="activity-cluster-item-detail">{plainTextPreview(message.content || message.status || "Operator activity", 240)}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

const MemoMessageBubble = React.memo(
  MessageBubble,
  (previous, next) =>
    previous.message.message_id === next.message.message_id &&
    previous.message.created_at === next.message.created_at &&
    previous.message.kind === next.message.kind &&
    previous.message.status === next.message.status &&
    previous.message.content === next.message.content,
);

type StreamFramePayload = {
  session?: SessionSummary | SessionDetail;
  snapshot?: StatusPayload;
  alerts?: AlertItem[];
  changed?: string[];
  critical?: boolean;
};

type PendingStreamFrame = {
  session?: SessionSummary | SessionDetail;
  snapshot?: StatusPayload;
  alerts?: AlertItem[];
  messages: SessionMessage[];
  activity: ActivityEntry[];
  shouldRefreshControls: boolean;
  critical: boolean;
};

function fingerprintValue(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch (_error) {
    return "";
  }
}

function isRenderDebugEnabled(): boolean {
  if (!import.meta.env.DEV || typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem("ai-operator:debug-renders") === "1";
  } catch (_error) {
    return false;
  }
}

function messageStableKey(message: SessionMessage): string {
  const messageId = String(message.message_id || "").trim();
  if (messageId) {
    return `id:${messageId}`;
  }
  const role = String(message.role || "assistant").trim();
  const kind = String(message.kind || "message").trim();
  const createdAt = String(message.created_at || "").trim();
  const taskId = String(message.task_id || "").trim();
  const runId = String(message.run_id || "").trim();
  if (createdAt || taskId || runId) {
    return `meta:${role}:${kind}:${createdAt}:${taskId}:${runId}`;
  }
  return `body:${role}:${kind}:${String(message.content || "").trim()}`;
}

function messagesEquivalent(left: SessionMessage, right: SessionMessage): boolean {
  return (
    left.message_id === right.message_id &&
    left.created_at === right.created_at &&
    left.role === right.role &&
    left.kind === right.kind &&
    left.content === right.content &&
    left.task_id === right.task_id &&
    left.run_id === right.run_id &&
    left.status === right.status
  );
}

function logMessageMutation(mode: "replace" | "merge", detail: Record<string, unknown>) {
  if (!isRenderDebugEnabled()) {
    return;
  }
  console.debug(`[ai-operator][messages:${mode}]`, detail);
}

function mergeSessionDetail(current: SessionDetail | null, session: SessionSummary | SessionDetail): SessionDetail | null {
  if (!session?.session_id) {
    return current;
  }
  if (!current) {
    return session as SessionDetail;
  }
  if (current.session_id !== session.session_id) {
    return current;
  }
  const merged = { ...current, ...session };
  return fingerprintValue(merged) === fingerprintValue(current) ? current : merged;
}

function mergeStatusPayload(current: StatusPayload | null, update: StatusPayload | null | undefined): StatusPayload | null {
  if (!update) {
    return current;
  }
  if (!current) {
    return update;
  }
  const merged: StatusPayload = {
    ...current,
    ...update,
    runtime: update.runtime ? { ...(current.runtime || {}), ...update.runtime } : current.runtime,
    infrastructure: update.infrastructure
      ? { ...(current.infrastructure || {}), ...update.infrastructure }
      : current.infrastructure,
  };
  return fingerprintValue(merged) === fingerprintValue(current) ? current : merged;
}

function backendServiceStatus(service: unknown): string {
  const value = (service || {}) as {
    active?: string;
    reason?: string;
    message?: string;
    provider?: string;
    enabled?: boolean;
    configured?: boolean;
    authenticated?: boolean;
  };
  if (value.provider === "gmail") {
    if (!value.enabled) {
      return "disabled";
    }
    if (!value.configured) {
      return "needs setup";
    }
    if (!value.authenticated) {
      return "needs sign-in";
    }
    return "connected";
  }
  return String(value.active || value.reason || value.message || "unknown").trim() || "unknown";
}

function backendServiceTone(service: unknown): ActivityTone {
  const value = (service || {}) as {
    available?: boolean;
    active?: string;
    reason?: string;
    provider?: string;
    enabled?: boolean;
    configured?: boolean;
    authenticated?: boolean;
  };
  if (value.provider === "gmail") {
    if (!value.enabled || !value.configured || !value.authenticated) {
      return "warning";
    }
    return "success";
  }
  const active = String(value.active || "").trim().toLowerCase();
  const reason = String(value.reason || "").trim().toLowerCase();
  if (value.available === false || active === "unavailable" || active === "disabled") {
    return "warning";
  }
  if (reason.includes("error") || reason.includes("failed")) {
    return "error";
  }
  return "info";
}

function backendServiceDetail(service: unknown): string {
  const value = (service || {}) as {
    message?: string;
    reason?: string;
    provider?: string;
    profile_email?: string;
    watch_enabled?: boolean;
    draft_counts?: Record<string, number>;
  };
  if (value.provider === "gmail") {
    const prepared = Number(value.draft_counts?.prepared || 0);
    const sent = Number(value.draft_counts?.sent || 0);
    const account = String(value.profile_email || "No connected account").trim() || "No connected account";
    return plainTextPreview(
      `${account} | Watch ${value.watch_enabled ? "enabled" : "disabled"} | Drafts ${prepared} prepared, ${sent} sent`,
      140,
    );
  }
  return plainTextPreview(String(value.message || value.reason || "Backend service state is available."), 140);
}

function extensionCommandPreview(extension: ExtensionSummary): string {
  return (extension.commands || [])
    .map((command) => `/${String(command.name || "").trim()}`)
    .filter(Boolean)
    .slice(0, 6)
    .join(", ");
}

function SkeletonTranscript() {
  return (
    <div className="transcript transcript-loading">
      {[0, 1, 2].map((item) => (
        <div key={item} className="skeleton-message">
          <div className="skeleton skeleton-meta" />
          <div className="skeleton skeleton-line skeleton-line-wide" />
          <div className="skeleton skeleton-line" />
          <div className="skeleton skeleton-line skeleton-line-short" />
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [themeMode, setThemeMode] = useState<ThemeMode>(getInitialTheme);
  const [draftsBySession, setDraftsBySession] = useState<Record<string, string>>(getInitialDrafts);
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [apiManagedByDesktop, setApiManagedByDesktop] = useState(false);
  const [desktopRuntimeStatus, setDesktopRuntimeStatus] = useState<DesktopRuntimeStatus | null>(null);
  const [bootState, setBootState] = useState<"booting" | "ready" | "error">("booting");
  const [bootMessage, setBootMessage] = useState("Connecting to the local operator...");
  const [bootstrapTick, setBootstrapTick] = useState(0);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [sessionDetail, setSessionDetail] = useState<SessionDetail | null>(null);
  const [messages, setMessages] = useState<SessionMessage[]>([]);
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [controlData, setControlData] = useState<ControlSnapshot>({
    queue: null,
    scheduled: null,
    watches: null,
    recentRuns: [],
    desktopEvidence: [],
    tools: [],
    extensions: [],
  });
  const [activeSurface, setActiveSurface] = useState<WorkspaceSurface>("chat");
  const [automationComposerMode, setAutomationComposerMode] = useState<AutomationComposerMode>("scheduled");
  const [automationBusy, setAutomationBusy] = useState("");
  const [automationGoal, setAutomationGoal] = useState("");
  const [automationRunAt, setAutomationRunAt] = useState(() => {
    const base = new Date(Date.now() + 60 * 60 * 1000);
    base.setMinutes(Math.ceil(base.getMinutes() / 15) * 15, 0, 0);
    return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, "0")}-${String(base.getDate()).padStart(2, "0")}T${String(base.getHours()).padStart(2, "0")}:${String(base.getMinutes()).padStart(2, "0")}`;
  });
  const [automationRecurrence, setAutomationRecurrence] = useState("once");
  const [watchGoal, setWatchGoal] = useState("");
  const [watchConditionType, setWatchConditionType] = useState("file_exists");
  const [watchTarget, setWatchTarget] = useState("");
  const [watchMatchText, setWatchMatchText] = useState("");
  const [watchIntervalSeconds, setWatchIntervalSeconds] = useState(30);
  const [watchAllowRepeat, setWatchAllowRepeat] = useState(false);
  const [emailPanelStatus, setEmailPanelStatus] = useState<EmailStatusPayload | null>(null);
  const [emailThreads, setEmailThreads] = useState<EmailThreadSummary[]>([]);
  const [emailSelectedThread, setEmailSelectedThread] = useState<EmailThreadSummary | null>(null);
  const [emailDrafts, setEmailDrafts] = useState<EmailDraftSummary[]>([]);
  const [emailDraftDetail, setEmailDraftDetail] = useState<EmailDraftsPayload | null>(null);
  const [emailQuery, setEmailQuery] = useState("");
  const [emailBusy, setEmailBusy] = useState("");
  const [emailReplyGuidance, setEmailReplyGuidance] = useState("");
  const [emailReplyContext, setEmailReplyContext] = useState("");
  const [emailForwardTo, setEmailForwardTo] = useState("");
  const [emailForwardNote, setEmailForwardNote] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [runDetailBusy, setRunDetailBusy] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [approving, setApproving] = useState<"" | "approve" | "reject">("");
  const [taskActionBusy, setTaskActionBusy] = useState<"" | "run" | "stop" | "handoff" | "commit">("");
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>(SLASH_COMMANDS);
  const [availableSkills, setAvailableSkills] = useState<SkillSummary[]>([]);
  const [availableExtensions, setAvailableExtensions] = useState<ExtensionSummary[]>([]);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [artifactViewer, setArtifactViewer] = useState<ArtifactViewerState>({
    open: false,
    loading: false,
    requestedEvidenceId: "",
    sourceLabel: "",
    heading: "",
    artifact: null,
    previewUrl: "",
    imageStatus: "idle",
    error: "",
  });
  const [streamState, setStreamState] = useState<"connecting" | "live" | "reconnecting" | "offline">("connecting");
  const [streamNote, setStreamNote] = useState("Waiting for live updates");
  const [isNearTranscriptBottom, setIsNearTranscriptBottom] = useState(true);
  const [pendingNewMessageCount, setPendingNewMessageCount] = useState(0);
  const deferredQuery = useDeferredValue(query);
  const draftKey = getDraftKey(selectedSessionId);
  const draft = draftsBySession[draftKey] || "";
  const parsedSlashCommand = parseSlashCommandInput(draft);
  const commandSuggestions = getSlashCommandSuggestions(draft, slashCommands);
  const activeCommandSuggestion =
    commandSuggestions[Math.min(selectedCommandIndex, Math.max(commandSuggestions.length - 1, 0))] || null;
  const selectedSessionRef = useRef("");
  const detailsOpenRef = useRef(false);
  const refreshTimerRef = useRef<number | null>(null);
  const streamRef = useRef<EventSource | null>(null);
  const lastEventIdsRef = useRef<Record<string, string>>({});
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const lastTranscriptSignatureRef = useRef("");
  const lastStatusFingerprintRef = useRef("");
  const lastSessionFingerprintRef = useRef("");
  const sessionDetailRef = useRef<SessionDetail | null>(null);
  const messagesRef = useRef<SessionMessage[]>([]);
  const runFocusLockedRef = useRef(false);
  const runFocusSessionRef = useRef("");
  const conversationLoadVersionRef = useRef(0);
  const pendingStreamFrameRef = useRef<PendingStreamFrame>({
    messages: [],
    activity: [],
    shouldRefreshControls: false,
    critical: false,
  });
  const streamFlushTimerRef = useRef<number | null>(null);

  useEffect(() => {
    selectedSessionRef.current = selectedSessionId;
    if (selectedSessionId) {
      window.localStorage.setItem("ai-operator:selected-session", selectedSessionId);
    }
  }, [selectedSessionId]);

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
    document.documentElement.style.colorScheme = themeMode;
    window.localStorage.setItem(THEME_STORAGE_KEY, themeMode);
  }, [themeMode]);

  useEffect(() => {
    window.localStorage.setItem(DRAFTS_STORAGE_KEY, JSON.stringify(draftsBySession));
  }, [draftsBySession]);

  useEffect(() => {
    detailsOpenRef.current = detailsOpen;
  }, [detailsOpen]);

  useEffect(() => {
    sessionDetailRef.current = sessionDetail;
  }, [sessionDetail]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    const focus = (status?.run_focus || sessionDetail?.operator?.run_focus || {}) as RunFocus;
    const phase = String(status?.run_phase || sessionDetail?.operator?.run_phase || "idle").toLowerCase();
    runFocusLockedRef.current = Boolean(focus.locked) && phase === "executing";
    runFocusSessionRef.current = String(focus.session_id || "").trim();
  }, [status, sessionDetail]);

  useEffect(() => {
    return () => {
      if (streamFlushTimerRef.current) {
        window.clearTimeout(streamFlushTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    shouldStickToBottomRef.current = true;
    setIsNearTranscriptBottom(true);
    setPendingNewMessageCount(0);
    lastTranscriptSignatureRef.current = "";
    lastStatusFingerprintRef.current = "";
    lastSessionFingerprintRef.current = "";
  }, [selectedSessionId]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = "0px";
    const nextHeight = Math.min(Math.max(textarea.scrollHeight, 92), COMPOSER_MAX_HEIGHT);
    textarea.style.height = `${nextHeight}px`;
  }, [draft, selectedSessionId]);

  useEffect(() => {
    setSelectedCommandIndex(0);
  }, [draft]);

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) {
        window.clearTimeout(refreshTimerRef.current);
      }
      streamRef.current?.close();
    };
  }, []);

  useEffect(() => {
    if (!apiBaseUrl || bootState !== "ready") {
      return;
    }
    if (activeSurface === "gmail") {
      void refreshEmailPanel();
      return;
    }
    if (activeSurface !== "chat") {
      void refreshControlData(selectedSessionRef.current);
    }
  }, [activeSurface, apiBaseUrl, bootState, selectedSessionId]);

  useEffect(() => {
    if (activeSurface !== "runs" || !apiBaseUrl) {
      return;
    }
    if (selectedRunId) {
      return;
    }
    const nextRunId = controlData.recentRuns[0]?.run_id;
    if (nextRunId) {
      void loadRunDetail(nextRunId);
    }
  }, [activeSurface, apiBaseUrl, controlData.recentRuns, selectedRunId]);

  function updateDraft(nextValue: string, sessionId = selectedSessionRef.current) {
    const nextKey = getDraftKey(sessionId);
    setDraftsBySession((current) => {
      if (!nextValue.trim()) {
        if (!(nextKey in current)) {
          return current;
        }
        const next = { ...current };
        delete next[nextKey];
        return next;
      }
      return { ...current, [nextKey]: nextValue };
    });
  }

  function clearDraft(sessionId = selectedSessionRef.current) {
    updateDraft("", sessionId);
  }

  function addLocalActivity(label: string, detail: string, tone: ActivityTone = "info") {
    const entry: ActivityEntry = {
      id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      label,
      detail,
      tone,
      timestamp: new Date().toISOString(),
    };
    setActivity((current) => [entry, ...current].slice(0, 30));
  }

  function applyCommandSuggestion(suggestion: SlashCommandSuggestion | null) {
    if (!suggestion) {
      return;
    }
    updateDraft(applySlashCommandSuggestion(draft, suggestion.command), selectedSessionId);
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }

  function scrollTranscriptToLatest(behavior: ScrollBehavior = "smooth") {
    const transcript = transcriptRef.current;
    if (!transcript) {
      return;
    }
    transcript.scrollTo({ top: transcript.scrollHeight, behavior });
    shouldStickToBottomRef.current = true;
    setIsNearTranscriptBottom(true);
    setPendingNewMessageCount(0);
  }

  function handleTranscriptScroll() {
    const nearBottom = isTranscriptNearBottom(transcriptRef.current);
    shouldStickToBottomRef.current = nearBottom;
    setIsNearTranscriptBottom(nearBottom);
    if (nearBottom) {
      setPendingNewMessageCount(0);
    }
  }

  async function refreshSidebar(preferredSessionId = ""): Promise<string> {
    if (!apiBaseUrl) {
      return "";
    }
    const payload = await listSessions(apiBaseUrl, 32);
    const nextSessions = payload.sessions || payload.items || [];
    setSessions((current) => reconcileSessionList(current, nextSessions));
    const lockedSessionId =
      !preferredSessionId && runFocusLockedRef.current
        ? runFocusSessionRef.current || selectedSessionRef.current
        : "";
    const preferred =
      preferredSessionId ||
      lockedSessionId ||
      selectedSessionRef.current ||
      window.localStorage.getItem("ai-operator:selected-session") ||
      "";
    const resolved =
      nextSessions.find((item) => item.session_id === preferred)?.session_id ||
      nextSessions[0]?.session_id ||
      "";
    if (resolved && resolved !== selectedSessionRef.current) {
      startTransition(() => setSelectedSessionId(resolved));
    }
    if (!resolved) {
      setSessionDetail(null);
      setMessages([]);
    }
    return resolved;
  }

  async function loadConversation(
    sessionId: string,
    options: {
      preserveVisibleState?: boolean;
    } = {},
  ) {
    if (!apiBaseUrl || !sessionId) {
      setSessionDetail(null);
      setMessages([]);
      setStatus(null);
      setAlerts([]);
      return;
    }
    const requestVersion = conversationLoadVersionRef.current + 1;
    conversationLoadVersionRef.current = requestVersion;
    const preserveVisibleState =
      Boolean(options.preserveVisibleState) || sessionDetailRef.current?.session_id === sessionId;
    setLoadingConversation(true);
    try {
      const [detailPayload, messagesPayload, statusPayload, alertsPayload] = await Promise.all([
        getSession(apiBaseUrl, sessionId),
        getSessionMessages(apiBaseUrl, sessionId, 40),
        getStatus(apiBaseUrl, sessionId),
        getAlerts(apiBaseUrl, sessionId, 8),
      ]);
      if (conversationLoadVersionRef.current !== requestVersion || selectedSessionRef.current !== sessionId) {
        return;
      }
      const fetchedMessages = normalizeMessages(messagesPayload.messages || messagesPayload.items || detailPayload.session.messages || []);
      setSessionDetail((current) =>
        current?.session_id === sessionId && preserveVisibleState ? mergeSessionDetail(current, detailPayload.session) : detailPayload.session,
      );
      setMessages((current) =>
        preserveVisibleState && selectedSessionRef.current === sessionId
          ? replaceMessagesPreservingIdentity(current, fetchedMessages)
          : replaceMessagesPreservingIdentity([], fetchedMessages),
      );
      setStatus(statusPayload);
      setAlerts(alertsPayload.items || []);
      setSessions((current) => upsertSession(current, detailPayload.session));
    } finally {
      if (conversationLoadVersionRef.current === requestVersion) {
        setLoadingConversation(false);
      }
    }
  }

  async function refreshControlData(sessionId = selectedSessionRef.current) {
    if (!apiBaseUrl) {
      return;
    }
    const runsSessionId = activeSurface === "runs" ? "" : sessionId;
    const [queue, scheduled, watches, runs, desktopEvidence, toolCatalog, extensionCatalog] = await Promise.all([
      getQueueState(apiBaseUrl),
      getScheduledState(apiBaseUrl),
      getWatchState(apiBaseUrl),
      getRecentRuns(apiBaseUrl, runsSessionId, 10),
      getDesktopEvidence(apiBaseUrl, 6),
      getToolCatalog(apiBaseUrl),
      getExtensionCatalog(apiBaseUrl),
    ]);
    setAvailableExtensions(extensionCatalog.items || []);
    setControlData({
      queue,
      scheduled,
      watches,
      recentRuns: runs.items || [],
      desktopEvidence: desktopEvidence.recent_summaries || [],
      tools: toolCatalog.items || [],
      extensions: extensionCatalog.items || [],
    });
  }

  async function refreshCommandCatalog(baseUrl = apiBaseUrl) {
    if (!baseUrl) {
      return;
    }
    try {
      const [commandCatalog, skillCatalog, extensionCatalog] = await Promise.all([
        getSlashCommands(baseUrl),
        getSkillCatalog(baseUrl),
        getExtensionCatalog(baseUrl),
      ]);
      setSlashCommands(commandCatalog.items?.length ? commandCatalog.items : SLASH_COMMANDS);
      setAvailableSkills(skillCatalog.items || []);
      setAvailableExtensions(extensionCatalog.items || []);
    } catch (_error) {
      setSlashCommands(SLASH_COMMANDS);
      setAvailableSkills([]);
      setAvailableExtensions([]);
    }
  }

  async function refreshEmailPanel(options: { threadId?: string; draftId?: string } = {}) {
    if (!apiBaseUrl) {
      return;
    }
    const [statusPayload, threadsPayload, draftsPayload] = await Promise.all([
      getEmailStatus(apiBaseUrl),
      listEmailThreads(apiBaseUrl, { limit: 18, query: emailQuery.trim() || undefined, labelIds: ["INBOX"] }),
      listEmailDrafts(apiBaseUrl, "", 12),
    ]);
    setEmailPanelStatus(statusPayload);
    setEmailThreads(threadsPayload.items || []);
    setEmailDrafts(draftsPayload.items || []);

    const nextThreadId =
      options.threadId ||
      emailSelectedThread?.thread_id ||
      threadsPayload.thread?.thread_id ||
      threadsPayload.items?.[0]?.thread_id ||
      "";
    if (nextThreadId) {
      try {
        const threadPayload = await readEmailThread(apiBaseUrl, nextThreadId, 10);
        setEmailSelectedThread(threadPayload.thread || null);
      } catch (_error) {
        setEmailSelectedThread(null);
      }
    } else {
      setEmailSelectedThread(null);
    }

    const nextDraftId =
      options.draftId ||
      String(emailDraftDetail?.draft?.draft_id || "").trim() ||
      draftsPayload.items?.[0]?.draft_id ||
      "";
    if (nextDraftId) {
      try {
        const draftPayload = await getEmailDraft(apiBaseUrl, nextDraftId);
        setEmailDraftDetail(draftPayload);
      } catch (_error) {
        setEmailDraftDetail(null);
      }
    } else {
      setEmailDraftDetail(null);
    }
  }

  async function loadEmailThreadDetail(threadId: string) {
    if (!apiBaseUrl || !threadId) {
      return;
    }
    setEmailBusy("thread");
    try {
      const payload = await readEmailThread(apiBaseUrl, threadId, 10);
      setEmailSelectedThread(payload.thread || null);
    } finally {
      setEmailBusy("");
    }
  }

  async function loadRunDetail(runId: string) {
    if (!apiBaseUrl || !runId) {
      setSelectedRun(null);
      return;
    }
    setRunDetailBusy(true);
    try {
      const payload = await getRunDetail(apiBaseUrl, runId, activeSurface === "runs" ? "" : selectedSessionId);
      setSelectedRunId(runId);
      setSelectedRun(payload.run || null);
    } finally {
      setRunDetailBusy(false);
    }
  }

  async function handleCreateScheduledAutomation() {
    if (!apiBaseUrl || !automationGoal.trim() || !automationRunAt.trim()) {
      return;
    }
    setAutomationBusy("scheduled:create");
    try {
      const payload = await createScheduledAutomation(apiBaseUrl, {
        goal: automationGoal.trim(),
        run_at: automationRunAt,
        recurrence: automationRecurrence,
      });
      setControlData((current) => ({
        ...current,
        queue: payload.queue || current.queue,
        scheduled: payload.scheduled || current.scheduled,
      }));
      addLocalActivity("Automation created", plainTextPreview(String(payload.result?.message || "Created the scheduled automation."), 180), "success");
      setAutomationGoal("");
    } finally {
      setAutomationBusy("");
    }
  }

  async function handleScheduledAutomationAction(task: ScheduledTask, action: "pause" | "resume" | "delete") {
    if (!apiBaseUrl || !task.scheduled_id) {
      return;
    }
    setAutomationBusy(`scheduled:${action}:${task.scheduled_id}`);
    try {
      const payload = await updateScheduledAutomation(apiBaseUrl, task.scheduled_id, action);
      setControlData((current) => ({
        ...current,
        queue: payload.queue || current.queue,
        scheduled: payload.scheduled || current.scheduled,
      }));
      addLocalActivity("Scheduled automation updated", plainTextPreview(String(payload.result?.message || "Updated the scheduled automation."), 180), action === "delete" ? "warning" : "info");
    } finally {
      setAutomationBusy("");
    }
  }

  async function handleCreateWatchAutomation() {
    if (!apiBaseUrl || !watchGoal.trim()) {
      return;
    }
    setAutomationBusy("watch:create");
    try {
      const payload = await createWatchAutomation(apiBaseUrl, {
        goal: watchGoal.trim(),
        condition_type: watchConditionType,
        target: watchTarget.trim(),
        match_text: watchMatchText.trim(),
        interval_seconds: watchIntervalSeconds,
        allow_repeat: watchAllowRepeat,
      });
      setControlData((current) => ({
        ...current,
        queue: payload.queue || current.queue,
        watches: payload.watches || current.watches,
      }));
      addLocalActivity("Watch created", plainTextPreview(String(payload.result?.message || "Created the watch automation."), 180), "success");
      setWatchGoal("");
      setWatchTarget("");
      setWatchMatchText("");
    } finally {
      setAutomationBusy("");
    }
  }

  async function handleWatchAutomationAction(task: WatchTask, action: "pause" | "resume" | "delete") {
    if (!apiBaseUrl || !task.watch_id) {
      return;
    }
    setAutomationBusy(`watch:${action}:${task.watch_id}`);
    try {
      const payload = await updateWatchAutomation(apiBaseUrl, task.watch_id, action);
      setControlData((current) => ({
        ...current,
        queue: payload.queue || current.queue,
        watches: payload.watches || current.watches,
      }));
      addLocalActivity("Watch updated", plainTextPreview(String(payload.result?.message || "Updated the watch automation."), 180), action === "delete" ? "warning" : "info");
    } finally {
      setAutomationBusy("");
    }
  }

  async function handleConnectGmailSurface() {
    if (!apiBaseUrl) {
      return;
    }
    setEmailBusy("connect");
    try {
      await connectGmail(apiBaseUrl);
      await refreshEmailPanel();
      addLocalActivity("Gmail connected", "The Gmail workspace is ready for inbox review and draft actions.", "success");
    } finally {
      setEmailBusy("");
    }
  }

  async function handleReplyDraft() {
    if (!apiBaseUrl || !emailSelectedThread?.thread_id) {
      return;
    }
    setEmailBusy("reply");
    try {
      const payload = await prepareEmailReplyDraft(apiBaseUrl, {
        thread_id: emailSelectedThread.thread_id,
        guidance: emailReplyGuidance.trim(),
        user_context: emailReplyContext.trim(),
      });
      setEmailDraftDetail(payload);
      await refreshEmailPanel({ threadId: emailSelectedThread.thread_id, draftId: String(payload.draft?.draft_id || "").trim() });
      addLocalActivity("Reply draft prepared", plainTextPreview(String(payload.message || (typeof payload.summary === "string" ? payload.summary : payload.summary?.summary) || "Prepared a reply draft."), 180), payload.needs_context ? "warning" : "success");
    } finally {
      setEmailBusy("");
    }
  }

  async function handleForwardDraft() {
    if (!apiBaseUrl || !emailSelectedThread?.thread_id || !emailForwardTo.trim()) {
      return;
    }
    setEmailBusy("forward");
    try {
      const payload = await prepareEmailForwardDraft(apiBaseUrl, {
        thread_id: emailSelectedThread.thread_id,
        to: emailForwardTo
          .split(/[,\n;]/)
          .map((item) => item.trim())
          .filter(Boolean),
        note: emailForwardNote.trim(),
      });
      setEmailDraftDetail(payload);
      await refreshEmailPanel({ threadId: emailSelectedThread.thread_id, draftId: String(payload.draft?.draft_id || "").trim() });
      addLocalActivity("Forward draft prepared", plainTextPreview(String(payload.message || (typeof payload.summary === "string" ? payload.summary : payload.summary?.summary) || "Prepared a forward draft."), 180), "success");
    } finally {
      setEmailBusy("");
    }
  }

  async function handleSendPreparedDraft() {
    if (!apiBaseUrl) {
      return;
    }
    const draftId = String(
      emailDraftDetail?.draft?.draft_id ||
        (emailDraftDetail?.summary && typeof emailDraftDetail.summary !== "string" ? emailDraftDetail.summary.draft_id : "") ||
        "",
    ).trim();
    if (!draftId) {
      return;
    }
    setEmailBusy("send");
    try {
      const payload = await sendEmailDraft(apiBaseUrl, draftId, "approved");
      setEmailDraftDetail(payload);
      await refreshEmailPanel({ draftId });
      addLocalActivity("Draft sent", plainTextPreview(String(payload.message || "Sent the approved Gmail draft."), 180), payload.ok ? "success" : "warning");
    } finally {
      setEmailBusy("");
    }
  }

  async function handleRejectPreparedDraft() {
    if (!apiBaseUrl) {
      return;
    }
    const draftId = String(
      emailDraftDetail?.draft?.draft_id ||
        (emailDraftDetail?.summary && typeof emailDraftDetail.summary !== "string" ? emailDraftDetail.summary.draft_id : "") ||
        "",
    ).trim();
    if (!draftId) {
      return;
    }
    setEmailBusy("reject-draft");
    try {
      const payload = await rejectEmailDraft(apiBaseUrl, draftId, "Rejected from the Gmail workspace.");
      setEmailDraftDetail(payload);
      await refreshEmailPanel({ draftId });
      addLocalActivity("Draft rejected", plainTextPreview(String(payload.message || "Rejected the Gmail draft."), 180), "warning");
    } finally {
      setEmailBusy("");
    }
  }

  async function loadEmailDraftDetail(draftId: string) {
    if (!apiBaseUrl || !draftId) {
      return;
    }
    setEmailBusy("draft");
    try {
      const payload = await getEmailDraft(apiBaseUrl, draftId);
      setEmailDraftDetail(payload);
    } finally {
      setEmailBusy("");
    }
  }

  function handleUseTargetProposal(proposal: DesktopTargetProposal) {
    const promptParts = [
      "Use this desktop target for the next bounded step.",
      proposal.target_kind ? `Target kind: ${proposal.target_kind}.` : "",
      proposal.window_title ? `Window: ${proposal.window_title}.` : "",
      proposal.summary ? `Reason: ${proposal.summary}.` : "",
      proposal.suggested_next_actions?.length ? `Suggested next action: ${proposal.suggested_next_actions[0]}.` : "",
    ].filter(Boolean);
    setActiveSurface("chat");
    updateDraft(promptParts.join(" "), selectedSessionId);
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }

  function flushPendingStreamFrame() {
    streamFlushTimerRef.current = null;
    const pending = pendingStreamFrameRef.current;
    pendingStreamFrameRef.current = {
      messages: [],
      activity: [],
      shouldRefreshControls: false,
      critical: false,
    };

    if (pending.activity.length) {
      setActivity((current) => [...pending.activity, ...current].slice(0, 30));
    }
    if (pending.messages.length) {
      setMessages((current) => mergeMessages(current, pending.messages));
    }
    if (pending.session?.session_id) {
      const sessionFingerprint = fingerprintValue(pending.session);
      if (sessionFingerprint !== lastSessionFingerprintRef.current) {
        lastSessionFingerprintRef.current = sessionFingerprint;
        setSessions((current) => upsertSession(current, pending.session as SessionSummary));
        setSessionDetail((current) => mergeSessionDetail(current, pending.session as SessionSummary | SessionDetail));
      }
    }
    if (pending.snapshot) {
      const snapshotFingerprint = fingerprintValue(pending.snapshot);
      if (snapshotFingerprint !== lastStatusFingerprintRef.current) {
        lastStatusFingerprintRef.current = snapshotFingerprint;
        setStatus((current) => mergeStatusPayload(current, pending.snapshot));
      }
    }
    if (pending.alerts) {
      setAlerts(pending.alerts);
    }
    if (pending.shouldRefreshControls && (detailsOpenRef.current || activeSurface !== "chat")) {
      void refreshControlData(selectedSessionRef.current);
    }
  }

  function queueStreamFrame(update: Partial<PendingStreamFrame>) {
    const pending = pendingStreamFrameRef.current;
    if (update.session?.session_id) {
      pending.session = update.session;
    }
    if (update.snapshot) {
      pending.snapshot = update.snapshot;
    }
    if (update.alerts) {
      pending.alerts = update.alerts;
    }
    if (update.messages?.length) {
      pending.messages.push(...update.messages);
    }
    if (update.activity?.length) {
      pending.activity.push(...update.activity);
    }
    if (update.shouldRefreshControls) {
      pending.shouldRefreshControls = true;
    }
    if (update.critical) {
      pending.critical = true;
    }
    const delayMs = pending.critical ? 18 : 42;
    if (streamFlushTimerRef.current) {
      if (!pending.critical) {
        return;
      }
      window.clearTimeout(streamFlushTimerRef.current);
      streamFlushTimerRef.current = null;
    }
    streamFlushTimerRef.current = window.setTimeout(() => {
      startTransition(() => flushPendingStreamFrame());
    }, delayMs);
  }

  function scheduleConversationRefresh(
    sessionId = selectedSessionRef.current,
    options: {
      includeConversation?: boolean;
      includeControls?: boolean;
    } = {},
  ) {
    if (!sessionId) {
      return;
    }
    if (refreshTimerRef.current) {
      window.clearTimeout(refreshTimerRef.current);
    }
    refreshTimerRef.current = window.setTimeout(() => {
      void refreshSidebar(sessionId);
      if (options.includeConversation) {
        void loadConversation(sessionId, { preserveVisibleState: true });
      }
      if (options.includeControls || detailsOpenRef.current || activeSurface !== "chat") {
        void refreshControlData(sessionId);
      }
      refreshTimerRef.current = null;
    }, options.includeConversation ? 180 : 120);
  }

  useEffect(() => {
    let alive = true;
    async function bootstrap() {
      setBootState("booting");
      setBootMessage("Connecting to the local operator...");
      setApiBaseUrl("");
      setDesktopRuntimeStatus(null);
      try {
        const ready = await ensureLocalApi();
        if (!alive) {
          return;
        }
        setApiBaseUrl(ready.baseUrl);
        setApiManagedByDesktop(ready.managedByDesktop);
        setDesktopRuntimeStatus(ready.runtimeStatus || null);
        setBootMessage(
          ready.runtimeStatus?.detail ||
            (ready.started ? "Starting the local operator..." : "Loading your conversations..."),
        );
      } catch (error) {
        if (!alive) {
          return;
        }
        setBootState("error");
        setBootMessage(error instanceof Error ? error.message : "Unable to reach the local operator.");
      }
    }
    void bootstrap();
    return () => {
      alive = false;
    };
  }, [bootstrapTick]);

  useEffect(() => {
    let alive = true;
    if (!apiBaseUrl || bootState === "error") {
      return;
    }
    async function hydrate() {
      try {
        const resolvedSession = await refreshSidebar();
        if (!alive) {
          return;
        }
        if (resolvedSession) {
          await loadConversation(resolvedSession, { preserveVisibleState: true });
        } else {
          const [operatorStatus, operatorAlerts] = await Promise.all([getStatus(apiBaseUrl), getAlerts(apiBaseUrl, "", 8)]);
          if (!alive) {
            return;
          }
          setStatus(operatorStatus);
          setAlerts(operatorAlerts.items || []);
        }
        await refreshCommandCatalog(apiBaseUrl);
        if (!alive) {
          return;
        }
        setBootState("ready");
      } catch (error) {
        if (!alive) {
          return;
        }
        setBootState("error");
        setBootMessage(error instanceof Error ? error.message : "Unable to load conversations.");
      }
    }
    void hydrate();
    return () => {
      alive = false;
    };
  }, [apiBaseUrl, bootstrapTick]);

  useEffect(() => {
    if (!apiBaseUrl || !selectedSessionId || bootState !== "ready") {
      return;
    }
    const currentSessionId = sessionDetailRef.current?.session_id || "";
    const hasVisibleState = currentSessionId === selectedSessionId && messagesRef.current.length > 0;
    if (hasVisibleState) {
      return;
    }
    void loadConversation(selectedSessionId, { preserveVisibleState: true });
  }, [apiBaseUrl, selectedSessionId, bootState]);

  useEffect(() => {
    if (!apiBaseUrl || !detailsOpen || bootState !== "ready") {
      return;
    }
    void refreshControlData(selectedSessionId);
  }, [apiBaseUrl, detailsOpen, selectedSessionId, bootState]);

  useEffect(() => {
    if (loadingConversation) {
      return;
    }
    const lastMessage = messages[messages.length - 1];
    const signature = `${selectedSessionId}:${messages.length}:${lastMessage?.message_id || ""}:${lastMessage?.created_at || ""}:${lastMessage?.kind || ""}:${(lastMessage?.content || "").length}`;
    if (signature === lastTranscriptSignatureRef.current) {
      return;
    }
    const shouldAutoScroll =
      shouldStickToBottomRef.current ||
      isTranscriptNearBottom(transcriptRef.current) ||
      !lastMessage ||
      messageDisplayKind(lastMessage) === "user";
    lastTranscriptSignatureRef.current = signature;
    if (shouldAutoScroll) {
      const behavior: ScrollBehavior = messages.length <= 2 ? "auto" : "smooth";
      window.requestAnimationFrame(() => scrollTranscriptToLatest(behavior));
      return;
    }
    if (lastMessage && messageDisplayKind(lastMessage) !== "user") {
      setPendingNewMessageCount((current) => current + 1);
    }
  }, [messages, loadingConversation, selectedSessionId]);

  useEffect(() => {
    if (!apiBaseUrl || bootState !== "ready") {
      return;
    }
    setStreamState("connecting");
    setStreamNote("Connecting live updates...");
    const sessionId = selectedSessionId;
    const lastEventId = lastEventIdsRef.current[sessionId || "__operator__"] || "";
    const source = openSessionEventStream(apiBaseUrl, {
      sessionId,
      lastEventId,
    });
    streamRef.current = source;

    const handleStreamEvent = (rawEvent: Event) => {
      const event = rawEvent as MessageEvent<string>;
      if (!event.data) {
        return;
      }
      try {
        const payload = JSON.parse(event.data) as StreamEvent<Record<string, unknown>>;
        const eventId = payload.event_id || "";
        const key = sessionId || "__operator__";
        if (eventId) {
          lastEventIdsRef.current[key] = eventId;
        }
        setStreamState("live");
        setStreamNote("Live updates connected");
        const activityEntry = timelineFromEvent(payload);
        const activityUpdate = activityEntry ? [activityEntry] : [];

        if (payload.event === "session.message") {
          const nextMessage = (payload.data.message || {}) as SessionMessage;
          if (nextMessage.message_id || nextMessage.content) {
            queueStreamFrame({
              messages: [nextMessage],
              activity: activityUpdate,
              critical: true,
            });
            return;
          }
        }

        if (
          payload.event === "session.sync" ||
          payload.event === "operator.sync" ||
          payload.event === "session.frame" ||
          payload.event === "operator.frame"
        ) {
          const syncData = payload.data as StreamFramePayload;
          queueStreamFrame({
            session: syncData.session,
            snapshot: syncData.snapshot,
            alerts: syncData.alerts,
            activity: activityUpdate,
            shouldRefreshControls: shouldRefreshControlDataFromFrame(syncData),
            critical: Boolean(syncData.critical) || isCriticalSnapshotUpdate(syncData.snapshot),
          });
          return;
        }

        if (payload.event === "session.updated") {
          const session = (payload.data.session || {}) as SessionSummary;
          if (session.session_id) {
            queueStreamFrame({
              session,
              activity: activityUpdate,
              critical: session.session_id === selectedSessionRef.current,
            });
            return;
          }
        }

        if (payload.event === "runtime.updated") {
          queueStreamFrame({
            snapshot: { runtime: (payload.data.runtime || {}) as StatusPayload["runtime"] },
            activity: activityUpdate,
            shouldRefreshControls: true,
            critical: true,
          });
          return;
        }

        if (payload.event === "infrastructure.updated") {
          queueStreamFrame({
            snapshot: { infrastructure: (payload.data.infrastructure || {}) as StatusPayload["infrastructure"] },
            activity: activityUpdate,
            shouldRefreshControls: true,
            critical: true,
          });
          return;
        }

        if (payload.event === "alert") {
          const alert = (payload.data.alert || {}) as AlertItem;
          if (alert.alert_id || alert.message) {
            setAlerts((current) => [alert, ...current.filter((item) => item.alert_id !== alert.alert_id)].slice(0, 8));
          }
          if (activityUpdate.length) {
            queueStreamFrame({ activity: activityUpdate, critical: true });
          }
          return;
        }

        if (activityUpdate.length) {
          queueStreamFrame({ activity: activityUpdate });
        }

        if (payload.event === "stream.reset") {
          scheduleConversationRefresh(sessionId, { includeConversation: true });
        }
      } catch (_error) {
        setStreamState("reconnecting");
        setStreamNote("Resynchronizing live updates...");
      }
    };

    STREAM_EVENTS.forEach((eventName) => source.addEventListener(eventName, handleStreamEvent));
    source.onerror = () => {
      setStreamState("reconnecting");
      setStreamNote("Reconnecting live updates...");
    };

    return () => {
      STREAM_EVENTS.forEach((eventName) => source.removeEventListener(eventName, handleStreamEvent));
      source.close();
      if (streamRef.current === source) {
        streamRef.current = null;
      }
    };
  }, [apiBaseUrl, selectedSessionId, bootState]);

  const filteredSessions = sessions.filter((session) => sessionMatchesQuery(session, deferredQuery));
  const { recent: recentSessions, history: historicalSessions } = partitionSessionsForSidebar(filteredSessions, selectedSessionId);
  const showHistorySection = Boolean(historyOpen || deferredQuery.trim());

  const pendingApproval = sessionDetail?.pending_approval?.kind
    ? sessionDetail.pending_approval
    : sessionDetail?.operator?.pending_approval?.kind
      ? sessionDetail.operator.pending_approval
      : status?.pending_approval || null;
  const runPhase = String(status?.run_phase || sessionDetail?.operator?.run_phase || "idle").toLowerCase();
  const runFocus = (status?.run_focus || sessionDetail?.operator?.run_focus || null) as RunFocus | null;
  const runFocusLocked = Boolean(runFocus?.locked) && runPhase === "executing";
  const runtimeModel = status?.runtime?.active_model || "";
  const runtimeEffort = status?.runtime?.reasoning_effort || "";
  const runtimeEffortLabel =
    runtimeEffort && status?.runtime?.reasoning_effort_applies_to_tool_calls === false
      ? `${runtimeEffort} (chat/final)`
      : runtimeEffort;
  const activeTask = sessionDetail?.operator?.active_task || status?.active_task || null;
  const selectedDesktopEvidence = status?.desktop?.selected_evidence || null;
  const checkpointDesktopEvidence = status?.desktop?.checkpoint_evidence || null;
  const selectedTargetProposalContext = status?.desktop?.selected_target_proposals || null;
  const checkpointTargetProposalContext = status?.desktop?.checkpoint_target_proposals || null;
  const runtimePolicy = status?.runtime?.tool_policy || null;
  const infrastructure = (status?.infrastructure || {}) as Record<string, unknown>;
  const infrastructureServices = [
    { key: "scheduler", label: "Scheduler", service: infrastructure.scheduler },
    { key: "file_watch", label: "File watch", service: infrastructure.file_watch },
    { key: "desktop_capture", label: "Desktop capture", service: infrastructure.desktop_capture },
    { key: "desktop", label: "Desktop backends", service: infrastructure.desktop },
    { key: "email", label: "Email", service: infrastructure.email },
  ].filter((item) => item.service);
  const highlightedTools = [...controlData.tools].sort((left, right) => {
    const order = ["high", "medium", "low", "unknown"];
    const leftRisk = String(left.policy?.risk_level || "unknown").toLowerCase();
    const rightRisk = String(right.policy?.risk_level || "unknown").toLowerCase();
    return order.indexOf(leftRisk) - order.indexOf(rightRisk);
  });
  const pendingApprovalEvidence = pendingApproval?.evidence_preview || checkpointDesktopEvidence || null;
  const distinctCheckpointEvidence =
    checkpointDesktopEvidence?.evidence_id && checkpointDesktopEvidence.evidence_id === selectedDesktopEvidence?.evidence_id
      ? null
      : checkpointDesktopEvidence;
  const activeTargetProposalContext: DesktopTargetProposalContext | null =
    pendingApproval?.kind && checkpointTargetProposalContext?.proposal_count
      ? checkpointTargetProposalContext
      : checkpointTargetProposalContext?.state === "approval_context" && checkpointTargetProposalContext?.proposal_count
        ? checkpointTargetProposalContext
        : selectedTargetProposalContext;
  const title = sessionDetail?.title || "New conversation";
  const showConversationSkeleton = loadingConversation && !messages.length;
  const emptyState = !messages.length && !loadingConversation;
  const transcriptItems = buildTranscriptItems(messages);
  const slashCommandHint = activeCommandSuggestion
    ? `/${activeCommandSuggestion.command.name}${
        activeCommandSuggestion.command.argumentHint ? ` ${activeCommandSuggestion.command.argumentHint}` : ""
      } - ${activeCommandSuggestion.command.description}`
    : "Use slash commands for quick operator actions and canned prompts.";
  const commandMenuVisible = Boolean(parsedSlashCommand);
  const composerHint =
    sending
      ? "Sending your message to the operator..."
      : parsedSlashCommand
        ? `Tab to autocomplete. Enter to run. ${slashCommandHint}`
      : pendingApproval?.kind
        ? "Approval is waiting on the right rail. You can still add context here."
        : "Enter to send. Shift+Enter for a newline.";
  const composerPlaceholder =
    bootState !== "ready"
      ? "Connecting to the operator..."
      : parsedSlashCommand
        ? "Run a slash command like /new, /refresh, or /architecture..."
      : pendingApproval?.kind
        ? "Add context or resolve the approval..."
        : "Message the operator...";
  const emailService = infrastructure.email as Record<string, unknown> | undefined;
  const emailConnected = Boolean(emailService && String(emailService.active || "").toLowerCase() === "connected");
  const pendingApprovalToolName = String(pendingApproval?.tool || "").trim().toLowerCase();
  const approvalTool = pendingApprovalToolName
    ? controlData.tools.find((tool) => String(tool.name || "").trim().toLowerCase() === pendingApprovalToolName) || null
    : null;
  const activeDraftId = String(
    emailDraftDetail?.draft?.draft_id ||
      (emailDraftDetail?.summary && typeof emailDraftDetail.summary !== "string" ? emailDraftDetail.summary.draft_id : "") ||
      "",
  ).trim();
  const groupedSkills = availableSkills.reduce<Record<string, SkillSummary[]>>((groups, skill) => {
    const key = String(skill.tags?.[0] || skill.source || "general").trim() || "general";
    if (!groups[key]) {
      groups[key] = [];
    }
    groups[key].push(skill);
    return groups;
  }, {});
  const topbarPrimaryLabel =
    String(activeTask?.status || sessionDetail?.status || status?.status || "idle").toLowerCase() === "running" ||
    String(activeTask?.status || sessionDetail?.status || status?.status || "idle").toLowerCase() === "queued"
      ? taskActionBusy === "stop"
        ? "Stopping..."
        : "Stop"
      : String(activeTask?.status || sessionDetail?.status || status?.status || "idle").toLowerCase() === "paused" ||
          String(activeTask?.status || sessionDetail?.status || status?.status || "idle").toLowerCase() === "needs_attention"
        ? taskActionBusy === "run"
          ? "Resuming..."
          : "Resume"
        : ["completed", "failed", "blocked", "stopped", "incomplete"].includes(
              String(activeTask?.status || sessionDetail?.status || status?.status || "idle").toLowerCase(),
            )
          ? taskActionBusy === "run"
            ? "Retrying..."
            : "Retry"
          : draft.trim()
            ? sending
              ? "Running..."
              : "Run"
            : "Run";

  async function handleNewChat() {
    if (!apiBaseUrl) {
      return;
    }
    setSending(true);
    try {
      const result = await createSession(apiBaseUrl);
      const nextSession = result.session;
      if (!nextSession?.session_id) {
        await refreshSidebar();
        return;
      }
      setSessions((current) => upsertSession(current, nextSession));
      setSessionDetail(nextSession);
      setMessages((current) => replaceMessagesPreservingIdentity(current, nextSession.messages || []));
      startTransition(() => setSelectedSessionId(nextSession.session_id));
      clearDraft(nextSession.session_id);
      setActivity([]);
      shouldStickToBottomRef.current = true;
      window.requestAnimationFrame(() => textareaRef.current?.focus());
    } finally {
      setSending(false);
    }
  }

  async function sendMessageContent(rawContent: string) {
    const content = rawContent.trim();
    if (!content || !apiBaseUrl || sending) {
      return;
    }

    const currentDraftKey = draftKey;
    setSending(true);
    clearDraft(selectedSessionId);
    shouldStickToBottomRef.current = true;
    setIsNearTranscriptBottom(true);
    setPendingNewMessageCount(0);
    try {
      let nextSessionId = selectedSessionId;
      let result;
      if (!nextSessionId) {
        result = await createSession(apiBaseUrl, { message: content });
        nextSessionId = result.session?.session_id || "";
      } else {
        result = await sendSessionMessage(apiBaseUrl, nextSessionId, content);
      }
      if (result?.session) {
        const nextSession = result.session;
        setSessionDetail((current) =>
          current?.session_id === nextSession.session_id ? mergeSessionDetail(current, nextSession as SessionDetail) : nextSession,
        );
        setMessages((current) => replaceMessagesPreservingIdentity(current, nextSession.messages || []));
        setSessions((current) => upsertSession(current, nextSession as SessionSummary));
      }
      if (nextSessionId && nextSessionId !== selectedSessionId) {
        startTransition(() => setSelectedSessionId(nextSessionId));
      }
      if (nextSessionId) {
        if (currentDraftKey === NEW_SESSION_DRAFT_KEY) {
          clearDraft("");
        }
        scheduleConversationRefresh(nextSessionId, { includeControls: detailsOpenRef.current });
      } else {
        await refreshSidebar();
      }
      window.requestAnimationFrame(() => textareaRef.current?.focus());
    } finally {
      setSending(false);
    }
  }

  async function executeLocalSlashCommand(command: LocalSlashCommand, args: string) {
    const normalizedArgs = args.trim().toLowerCase();

    if (command.action === "new-chat") {
      await handleNewChat();
      addLocalActivity("Command executed", "Started a new conversation.", "success");
      return;
    }

    if (command.action === "refresh") {
      await handleRefresh();
      addLocalActivity("Command executed", "Refreshed the operator view.", "success");
      return;
    }

    if (command.action === "toggle-details") {
      let nextState = !detailsOpenRef.current;
      if (normalizedArgs === "show") {
        nextState = true;
      } else if (normalizedArgs === "hide") {
        nextState = false;
      }
      setDetailsOpen(nextState);
      addLocalActivity(
        "Command executed",
        nextState ? "Opened the operator details rail." : "Collapsed the operator details rail.",
        "success",
      );
      return;
    }

    if (command.action === "toggle-theme") {
      let nextTheme: ThemeMode = themeMode === "light" ? "dark" : "light";
      if (normalizedArgs === "light" || normalizedArgs === "dark") {
        nextTheme = normalizedArgs;
      }
      setThemeMode(nextTheme);
      addLocalActivity("Command executed", `Switched the UI theme to ${nextTheme}.`, "success");
      return;
    }

    if (command.action === "show-tools") {
      if (!apiBaseUrl) {
        addLocalActivity("Command unavailable", "The tool catalog needs the local API to be connected.", "warning");
        return;
      }
      const toolCatalog = await getToolCatalog(apiBaseUrl);
      addLocalActivity("Registered tools", formatToolCatalogDetail(toolCatalog.items || []), "info");
      return;
    }

    if (command.action === "connect-gmail") {
      if (!apiBaseUrl) {
        addLocalActivity("Command unavailable", "Gmail connect needs the local API to be connected.", "warning");
        return;
      }
      const result = await connectGmail(apiBaseUrl);
      const statusPayload = await getEmailStatus(apiBaseUrl).catch(() => null);
      await handleRefresh();
      addLocalActivity(
        "Gmail connection",
        formatEmailStatusDetail(statusPayload) || String(result.message || "Gmail connect flow completed."),
        statusPayload?.authenticated ? "success" : "info",
      );
      return;
    }

    if (command.action === "show-email-status") {
      if (!apiBaseUrl) {
        addLocalActivity("Command unavailable", "Email status needs the local API to be connected.", "warning");
        return;
      }
      const emailStatus = await getEmailStatus(apiBaseUrl);
      addLocalActivity(
        "Email status",
        formatEmailStatusDetail(emailStatus),
        emailStatus.authenticated ? "success" : emailStatus.enabled ? "info" : "warning",
      );
      return;
    }

    if (command.action === "show-inbox") {
      if (!apiBaseUrl) {
        addLocalActivity("Command unavailable", "Inbox access needs the local API to be connected.", "warning");
        return;
      }
      const inbox = await listEmailThreads(apiBaseUrl, { limit: 8, labelIds: ["INBOX"] });
      addLocalActivity("Inbox", formatEmailThreadsDetail(inbox), inbox.error ? "warning" : "info");
      return;
    }

    if (command.action === "show-email-drafts") {
      if (!apiBaseUrl) {
        addLocalActivity("Command unavailable", "Draft access needs the local API to be connected.", "warning");
        return;
      }
      const drafts = await listEmailDrafts(apiBaseUrl, "", 12);
      addLocalActivity("Email drafts", formatEmailDraftsDetail(drafts), drafts.error ? "warning" : "info");
      return;
    }

    if (command.action === "show-extensions") {
      const detail = availableExtensions.length
        ? availableExtensions
            .map((extension) => {
              const title = String(extension.title || extension.slug || "extension").trim();
              const description = String(extension.description || "Local extension manifest.").trim();
              const commands = extensionCommandPreview(extension);
              return commands ? `${title} - ${description}\nCommands: ${commands}` : `${title} - ${description}`;
            })
            .join("\n")
        : "No local extension manifests are loaded right now.";
      addLocalActivity("Local extensions", detail, "info");
      return;
    }

    if (command.action === "approve") {
      await handleApproval("approve");
      addLocalActivity("Command executed", "Approved the pending step.", "success");
      return;
    }
    if (command.action === "reject") {
      await handleApproval("reject");
      addLocalActivity("Command executed", "Rejected the pending step.", "warning");
      return;
    }

    addLocalActivity("Command unavailable", `/${command.name} is not executable from the local UI router.`, "warning");
  }

  async function handleClientCommandAction(action: string, args: string) {
    const command = slashCommands.find(
      (item) => item.type === "local" && String(item.action || "").trim() === String(action || "").trim(),
    ) as LocalSlashCommand | undefined;
    if (!command) {
      addLocalActivity("Command unavailable", `The client action ${action || "unknown"} is not registered in the UI.`, "warning");
      return;
    }
    await executeLocalSlashCommand(command, args);
  }

  async function tryHandleBackendSlashCommand() {
    if (!parsedSlashCommand || !apiBaseUrl) {
      return false;
    }

    try {
      const payload = await executeSlashCommand(apiBaseUrl, draft, selectedSessionId);
      const execution = payload.execution || {};
      const kind = String(execution.kind || "").trim();
      if (!kind || kind === "none") {
        return false;
      }

      if (execution.clear_draft) {
        clearDraft(selectedSessionId);
      }

      if (kind === "activity") {
        addLocalActivity(
          String(execution.title || "Slash command").trim() || "Slash command",
          String(execution.detail || "").trim() || "Command completed.",
          (String(execution.tone || "info").trim() as ActivityTone) || "info",
        );
        return true;
      }

      if (kind === "prompt") {
        const promptText = String(execution.prompt_text || "").trim();
        if (!promptText) {
          return false;
        }
        await sendMessageContent(promptText);
        addLocalActivity(
          "Command executed",
          String(execution.success_message || "Sent the prompt command.").trim(),
          "success",
        );
        return true;
      }

      if (kind === "client_action") {
        await handleClientCommandAction(String(execution.action || "").trim(), String(execution.args || "").trim());
        return true;
      }

      if (kind === "operator_action") {
        if (execution.status) {
          setStatus(execution.status);
        }
        if (execution.session?.session_id) {
          setSessions((current) => upsertSession(current, execution.session as SessionSummary));
        }
        addLocalActivity(
          String(execution.title || "Command executed").trim() || "Command executed",
          String(execution.detail || "The operator handled the requested action.").trim(),
          (String(execution.tone || "info").trim() as ActivityTone) || "info",
        );
        scheduleConversationRefresh(selectedSessionId, { includeConversation: true, includeControls: detailsOpenRef.current });
        return true;
      }
    } catch (_error) {
      return false;
    }
    return false;
  }

  async function tryHandleSlashCommand() {
    if (await tryHandleBackendSlashCommand()) {
      return true;
    }

    const plan = buildSlashCommandPlan({
      input: draft,
      commands: slashCommands,
      activeSuggestion: activeCommandSuggestion?.command || null,
      skills: availableSkills,
      extensions: availableExtensions,
      runtime: status?.runtime || null,
      pendingApprovalKind: pendingApproval?.kind || "",
    });

    if (plan.kind === "none") {
      return false;
    }

    if (plan.clearDraft) {
      clearDraft(selectedSessionId);
    }

    if (plan.kind === "activity") {
      addLocalActivity(plan.title, plan.detail, plan.tone);
      return true;
    }

    if (plan.kind === "prompt") {
      await sendMessageContent(plan.promptText);
      addLocalActivity("Command executed", plan.successMessage, "success");
      return true;
    }

    await executeLocalSlashCommand(plan.command, plan.args);
    return true;
  }

  async function handleSendMessage() {
    if (await tryHandleSlashCommand()) {
      return;
    }
    await sendMessageContent(draft);
  }

  async function handleApproval(action: "approve" | "reject") {
    if (!apiBaseUrl || !pendingApproval?.kind) {
      return;
    }
    setApproving(action);
    try {
      const result = action === "approve" ? await approvePending(apiBaseUrl, selectedSessionId) : await rejectPending(apiBaseUrl, selectedSessionId);
      if (result.session) {
        const nextSession = result.session;
        setSessionDetail((current) =>
          current?.session_id === nextSession.session_id ? mergeSessionDetail(current, nextSession as SessionDetail) : nextSession,
        );
        setMessages((current) => replaceMessagesPreservingIdentity(current, nextSession.messages || []));
        setSessions((current) => upsertSession(current, nextSession as SessionSummary));
      }
      if (result.status) {
        setStatus(result.status);
      }
      scheduleConversationRefresh(selectedSessionId, { includeControls: detailsOpenRef.current });
    } finally {
      setApproving("");
    }
  }

  function applyOperatorMutationResult(result: { session?: SessionDetail; status?: StatusPayload } | null | undefined) {
    if (result?.session) {
      const nextSession = result.session;
      setSessionDetail((current) =>
        current?.session_id === nextSession.session_id ? mergeSessionDetail(current, nextSession as SessionDetail) : nextSession,
      );
      setMessages((current) => replaceMessagesPreservingIdentity(current, nextSession.messages || []));
      setSessions((current) => upsertSession(current, nextSession as SessionSummary));
    }
    if (result?.status) {
      setStatus(result.status);
    }
  }

  async function handlePrimaryTaskAction() {
    if (!apiBaseUrl || !selectedSessionId || taskActionBusy) {
      return;
    }

    const normalizedStatus = String(activeTask?.status || sessionDetail?.status || status?.status || "idle").toLowerCase();

    if (normalizedStatus === "running" || normalizedStatus === "queued") {
      setTaskActionBusy("stop");
      try {
        const result = await stopTask(apiBaseUrl, selectedSessionId);
        applyOperatorMutationResult(result);
        addLocalActivity("Run stopped", plainTextPreview(result.result?.message || "Stopped the active run.", 180), "warning");
        scheduleConversationRefresh(selectedSessionId, { includeConversation: true, includeControls: true });
      } finally {
        setTaskActionBusy("");
      }
      return;
    }

    if (normalizedStatus === "paused" || normalizedStatus === "needs_attention") {
      setTaskActionBusy("run");
      try {
        const result = await resumeTask(apiBaseUrl, selectedSessionId);
        applyOperatorMutationResult(result);
        addLocalActivity("Run resumed", plainTextPreview(result.result?.message || "Resumed the active run.", 180), "success");
        scheduleConversationRefresh(selectedSessionId, { includeConversation: true, includeControls: true });
      } finally {
        setTaskActionBusy("");
      }
      return;
    }

    if (["completed", "failed", "blocked", "stopped", "incomplete"].includes(normalizedStatus)) {
      setTaskActionBusy("run");
      try {
        const result = await retryTask(apiBaseUrl, selectedSessionId);
        applyOperatorMutationResult(result);
        addLocalActivity("Run retried", plainTextPreview(result.result?.message || "Started a retry for this run.", 180), "info");
        scheduleConversationRefresh(selectedSessionId, { includeConversation: true, includeControls: true });
      } finally {
        setTaskActionBusy("");
      }
      return;
    }

    window.requestAnimationFrame(() => textareaRef.current?.focus());
    if (draft.trim()) {
      await handleSendMessage();
      return;
    }
    addLocalActivity("Ready to run", "Focus is back in the composer. Add a message or use a slash command to start.", "info");
  }

  async function handleViewEvidenceArtifact(preview: EvidenceSummary | null | undefined, sourceLabel: string) {
    if (!apiBaseUrl || !preview?.evidence_id) {
      return;
    }
    setArtifactViewer({
      open: true,
      loading: true,
      requestedEvidenceId: preview.evidence_id,
      sourceLabel,
      heading: evidenceSummaryText(preview),
      artifact: null,
      previewUrl: "",
      imageStatus: "loading",
      error: "",
    });
    try {
      const payload = await getDesktopEvidenceArtifact(apiBaseUrl, preview.evidence_id);
      const previewUrl = resolveDesktopEvidenceArtifactPreviewUrl(apiBaseUrl, payload.artifact || null);
      const previewImage = Boolean(previewUrl) && isDesktopEvidenceArtifactImage(payload.artifact || null);
      setArtifactViewer({
        open: true,
        loading: false,
        requestedEvidenceId: preview.evidence_id,
        sourceLabel,
        heading: evidenceSummaryText(preview),
        artifact: payload.artifact || null,
        previewUrl: previewImage ? previewUrl : "",
        imageStatus: previewImage ? "loading" : "idle",
        error: "",
      });
    } catch (error) {
      setArtifactViewer({
        open: true,
        loading: false,
        requestedEvidenceId: preview.evidence_id,
        sourceLabel,
        heading: evidenceSummaryText(preview),
        artifact: null,
        previewUrl: "",
        imageStatus: "error",
        error: error instanceof Error ? error.message : "Unable to load the retained artifact.",
      });
    }
  }

  function closeArtifactViewer() {
    setArtifactViewer({
      open: false,
      loading: false,
      requestedEvidenceId: "",
      sourceLabel: "",
      heading: "",
      artifact: null,
      previewUrl: "",
      imageStatus: "idle",
      error: "",
    });
  }

  async function handleRefresh() {
    if (bootState !== "ready") {
      setBootstrapTick((current) => current + 1);
      return;
    }
    const resolved = await refreshSidebar(selectedSessionId);
    await refreshCommandCatalog();
    if (resolved) {
      await loadConversation(resolved, { preserveVisibleState: true });
    }
    if (detailsOpen || activeSurface !== "chat") {
      await refreshControlData(resolved);
    }
    if (activeSurface === "gmail") {
      await refreshEmailPanel();
    }
    if (activeSurface === "runs" && selectedRunId) {
      await loadRunDetail(selectedRunId);
    }
  }

  function renderWorkspaceSurface() {
    if (activeSurface === "automations") {
      return (
        <div className="surface-view">
          <div className="surface-header">
            <div>
              <div className="eyebrow">Operator automations</div>
              <h3>Automations</h3>
              <p className="secondary-copy">
                Schedule follow-up work, create lightweight watches, and keep an eye on what is queued behind the active conversation.
              </p>
            </div>
            <div className="surface-header-meta">
              <span className="meta-pill">{controlData.scheduled?.tasks?.length || 0} scheduled</span>
              <span className="meta-pill">{controlData.watches?.tasks?.length || 0} watches</span>
              <span className="meta-pill">{controlData.queue?.queued_tasks?.length || 0} queued</span>
            </div>
          </div>

          <div className="surface-grid surface-grid-automations">
            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Create automation</h4>
                <div className="segmented-control">
                  <button
                    className={clsx("segmented-control-button", automationComposerMode === "scheduled" && "is-active")}
                    onClick={() => setAutomationComposerMode("scheduled")}
                    type="button"
                  >
                    Schedule
                  </button>
                  <button
                    className={clsx("segmented-control-button", automationComposerMode === "watch" && "is-active")}
                    onClick={() => setAutomationComposerMode("watch")}
                    type="button"
                  >
                    Watch
                  </button>
                </div>
              </div>

              {automationComposerMode === "scheduled" ? (
                <div className="surface-form">
                  <label className="field">
                    <span>Goal</span>
                    <textarea
                      value={automationGoal}
                      onChange={(event) => setAutomationGoal(event.target.value)}
                      placeholder="Check the repo every morning and summarize anything blocked."
                      rows={3}
                    />
                  </label>
                  <div className="surface-form-grid">
                    <label className="field">
                      <span>Run at</span>
                      <input type="datetime-local" value={automationRunAt} onChange={(event) => setAutomationRunAt(event.target.value)} />
                    </label>
                    <label className="field">
                      <span>Recurrence</span>
                      <select value={automationRecurrence} onChange={(event) => setAutomationRecurrence(event.target.value)}>
                        <option value="once">Once</option>
                        <option value="daily">Daily</option>
                      </select>
                    </label>
                  </div>
                  <div className="surface-actions">
                    <button className="send-button" disabled={automationBusy === "scheduled:create" || !automationGoal.trim() || !automationRunAt.trim()} onClick={() => void handleCreateScheduledAutomation()} type="button">
                      {automationBusy === "scheduled:create" ? "Creating..." : "Create schedule"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="surface-form">
                  <label className="field">
                    <span>Goal</span>
                    <textarea
                      value={watchGoal}
                      onChange={(event) => setWatchGoal(event.target.value)}
                      placeholder="Alert me when the project inspection changes in a meaningful way."
                      rows={3}
                    />
                  </label>
                  <div className="surface-form-grid">
                    <label className="field">
                      <span>Condition</span>
                      <select value={watchConditionType} onChange={(event) => setWatchConditionType(event.target.value)}>
                        <option value="file_exists">File exists</option>
                        <option value="file_changed">File changed</option>
                        <option value="browser_text_contains">Browser text contains</option>
                        <option value="inspect_project_changed">Project inspection changed</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>Check every</span>
                      <input
                        type="number"
                        min={2}
                        max={3600}
                        value={watchIntervalSeconds}
                        onChange={(event) => setWatchIntervalSeconds(Math.max(2, Number(event.target.value || 10)))}
                      />
                    </label>
                  </div>
                  <label className="field">
                    <span>{watchConditionType === "browser_text_contains" ? "Browser target" : "Target"}</span>
                    <input
                      value={watchTarget}
                      onChange={(event) => setWatchTarget(event.target.value)}
                      placeholder={watchConditionType === "inspect_project_changed" ? "." : "Path, URL, or target"}
                    />
                  </label>
                  {watchConditionType === "browser_text_contains" ? (
                    <label className="field">
                      <span>Match text</span>
                      <input value={watchMatchText} onChange={(event) => setWatchMatchText(event.target.value)} placeholder="Success, Uploaded, or other expected text" />
                    </label>
                  ) : null}
                  <label className="checkbox-row">
                    <input checked={watchAllowRepeat} onChange={(event) => setWatchAllowRepeat(event.target.checked)} type="checkbox" />
                    <span>Allow repeat triggers after the first match</span>
                  </label>
                  <div className="surface-actions">
                    <button className="send-button" disabled={automationBusy === "watch:create" || !watchGoal.trim()} onClick={() => void handleCreateWatchAutomation()} type="button">
                      {automationBusy === "watch:create" ? "Creating..." : "Create watch"}
                    </button>
                  </div>
                </div>
              )}
            </section>

            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Queued tasks</h4>
                <span className="muted-label">{controlData.queue?.queued_tasks?.length || 0}</span>
              </div>
              <div className="surface-list">
                {(controlData.queue?.queued_tasks || []).map((task) => (
                  <article key={task.task_id || task.goal} className={clsx("surface-list-item", `tone-${statusTone(task.status)}`)}>
                    <div className="surface-list-head">
                      <span className="surface-list-title">{plainTextPreview(task.goal || "Queued task", 88)}</span>
                      <span className="mini-list-time">{plainTextPreview(task.status || "queued", 24)}</span>
                    </div>
                    <p className="surface-list-copy">{plainTextPreview(task.last_message || "Waiting behind the active task.", 180)}</p>
                  </article>
                ))}
                {!(controlData.queue?.queued_tasks || []).length ? <p className="secondary-copy">No queued tasks right now.</p> : null}
              </div>
            </section>

            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Scheduled</h4>
                <span className="muted-label">{controlData.scheduled?.tasks?.length || 0}</span>
              </div>
              <div className="surface-list">
                {(controlData.scheduled?.tasks || []).map((task) => (
                  <article key={task.scheduled_id || task.goal} className={clsx("surface-list-item", `tone-${statusTone(task.status)}`)}>
                    <div className="surface-list-head">
                      <span className="surface-list-title">{plainTextPreview(task.goal || "Scheduled task", 88)}</span>
                      <span className="mini-list-time">{plainTextPreview(task.recurrence || "once", 20)}</span>
                    </div>
                    <p className="surface-list-copy">{plainTextPreview(task.last_message || task.goal || "Scheduled task", 180)}</p>
                    <div className="surface-meta-row">
                      <span className="evidence-chip evidence-chip-soft">{plainTextPreview(task.next_run_at || task.scheduled_for || "unscheduled", 36)}</span>
                      <span className="evidence-chip">{plainTextPreview(task.status || "scheduled", 20)}</span>
                    </div>
                    <div className="surface-actions">
                      <button className="ghost-button" disabled={!task.available_actions?.pause || automationBusy === `scheduled:pause:${task.scheduled_id}` } onClick={() => void handleScheduledAutomationAction(task, "pause")} type="button">Pause</button>
                      <button className="ghost-button" disabled={!task.available_actions?.resume || automationBusy === `scheduled:resume:${task.scheduled_id}` } onClick={() => void handleScheduledAutomationAction(task, "resume")} type="button">Resume</button>
                      <button className="ghost-button" disabled={!task.available_actions?.delete || automationBusy === `scheduled:delete:${task.scheduled_id}` } onClick={() => void handleScheduledAutomationAction(task, "delete")} type="button">Delete</button>
                    </div>
                  </article>
                ))}
                {!(controlData.scheduled?.tasks || []).length ? <p className="secondary-copy">No scheduled automations yet.</p> : null}
              </div>
            </section>

            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Watches</h4>
                <span className="muted-label">{controlData.watches?.tasks?.length || 0}</span>
              </div>
              <div className="surface-list">
                {(controlData.watches?.tasks || []).map((task) => (
                  <article key={task.watch_id || task.goal} className={clsx("surface-list-item", `tone-${statusTone(task.status)}`)}>
                    <div className="surface-list-head">
                      <span className="surface-list-title">{plainTextPreview(task.goal || "Watch", 88)}</span>
                      <span className="mini-list-time">{plainTextPreview(task.condition_label || task.condition_type || "watch", 24)}</span>
                    </div>
                    <p className="surface-list-copy">{plainTextPreview(task.last_message || task.target || "Waiting for the watch condition.", 180)}</p>
                    <div className="surface-meta-row">
                      {task.target ? <span className="evidence-chip evidence-chip-soft">{plainTextPreview(task.target, 36)}</span> : null}
                      <span className="evidence-chip">{plainTextPreview(task.status || "watching", 20)}</span>
                      {typeof task.trigger_count === "number" ? <span className="evidence-chip">Triggers {task.trigger_count}</span> : null}
                    </div>
                    <div className="surface-actions">
                      <button className="ghost-button" disabled={!task.available_actions?.pause || automationBusy === `watch:pause:${task.watch_id}` } onClick={() => void handleWatchAutomationAction(task, "pause")} type="button">Pause</button>
                      <button className="ghost-button" disabled={!task.available_actions?.resume || automationBusy === `watch:resume:${task.watch_id}` } onClick={() => void handleWatchAutomationAction(task, "resume")} type="button">Resume</button>
                      <button className="ghost-button" disabled={!task.available_actions?.delete || automationBusy === `watch:delete:${task.watch_id}` } onClick={() => void handleWatchAutomationAction(task, "delete")} type="button">Delete</button>
                    </div>
                  </article>
                ))}
                {!(controlData.watches?.tasks || []).length ? <p className="secondary-copy">No watch conditions yet.</p> : null}
              </div>
            </section>
          </div>
        </div>
      );
    }
    if (activeSurface === "gmail") {
      const draftSummaryText =
        typeof emailDraftDetail?.summary === "string"
          ? emailDraftDetail.summary
          : emailDraftDetail?.summary?.summary || emailDraftDetail?.message || "";
      const draftQuestions =
        emailDraftDetail?.questions ||
        (typeof emailDraftDetail?.summary !== "string" ? emailDraftDetail?.summary?.questions : []) ||
        [];
      return (
        <div className="surface-view">
          <div className="surface-header">
            <div>
              <div className="eyebrow">Operator Gmail</div>
              <h3>Inbox</h3>
              <p className="secondary-copy">Read a thread, generate a draft, answer missing context questions, and only send when you explicitly approve it.</p>
            </div>
            <div className="surface-header-meta">
              {emailPanelStatus?.profile_email ? <span className="meta-pill">{plainTextPreview(emailPanelStatus.profile_email, 32)}</span> : null}
              <span className="meta-pill">
                {emailPanelStatus?.watch_enabled
                  ? `Inbox watch ${emailPanelStatus.poll_seconds || 60}s`
                  : "Manual inbox mode"}
              </span>
            </div>
            <div className="surface-actions">
              <button
                className="ghost-button"
                onClick={() => {
                  setActiveSurface("automations");
                  setAutomationComposerMode("scheduled");
                  setAutomationGoal(
                    `Review Gmail inbox${emailQuery.trim() ? ` for ${emailQuery.trim()}` : ""}, summarize what needs action, and queue follow-up if a reply or escalation is needed.`,
                  );
                }}
                type="button"
              >
                Create inbox automation
              </button>
              <button className="ghost-button" onClick={() => void refreshEmailPanel()} type="button">Refresh</button>
              {!emailPanelStatus?.authenticated ? (
                <button className="send-button" disabled={emailBusy === "connect"} onClick={() => void handleConnectGmailSurface()} type="button">
                  {emailBusy === "connect" ? "Connecting..." : "Connect Gmail"}
                </button>
              ) : null}
            </div>
          </div>

          <div className="surface-grid surface-grid-gmail">
            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Inbox</h4>
                <span className="muted-label">{emailThreads.length}</span>
              </div>
              <label className="field">
                <span>Search</span>
                <input value={emailQuery} onChange={(event) => setEmailQuery(event.target.value)} placeholder="from:alice@example.com newer_than:7d" />
              </label>
              <div className="surface-actions">
                <button className="ghost-button" onClick={() => void refreshEmailPanel()} type="button">Run query</button>
              </div>
              <div className="surface-list">
                {emailThreads.map((thread) => (
                  <button
                    key={thread.thread_id || `${thread.subject}:${thread.last_date}`}
                    className={clsx("surface-list-item surface-list-button", emailSelectedThread?.thread_id === thread.thread_id && "is-selected")}
                    onClick={() => void loadEmailThreadDetail(String(thread.thread_id || ""))}
                    type="button"
                  >
                    <div className="surface-list-head">
                      <span className="surface-list-title">{plainTextPreview(thread.subject || "Untitled thread", 72)}</span>
                      <span className="mini-list-time">{formatTime(thread.last_date)}</span>
                    </div>
                    <p className="surface-list-copy">{plainTextPreview(thread.snippet || thread.last_from || "Thread preview", 160)}</p>
                    <div className="surface-meta-row">
                      {thread.last_from ? <span className="evidence-chip evidence-chip-soft">{plainTextPreview(thread.last_from, 28)}</span> : null}
                      {thread.unread ? <span className="evidence-chip">Unread</span> : null}
                    </div>
                  </button>
                ))}
                {!emailThreads.length ? <p className="secondary-copy">No inbox threads match the current query.</p> : null}
              </div>
            </section>

            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Thread</h4>
                <span className="muted-label">{emailSelectedThread?.message_count || 0}</span>
              </div>
              {emailSelectedThread ? (
                <div className="surface-thread">
                  <div className="surface-thread-header">
                    <h5>{plainTextPreview(emailSelectedThread.subject || "Thread", 96)}</h5>
                    <p className="secondary-copy">{plainTextPreview(emailSelectedThread.snippet || "Selected Gmail thread.", 180)}</p>
                  </div>
                  <div className="surface-list surface-thread-messages">
                    {(emailSelectedThread.messages || []).map((message) => (
                      <article key={message.message_id || `${message.from}:${message.date}`} className={clsx("surface-list-item", message.sent_by_self ? "tone-info" : "tone-neutral")}>
                        <div className="surface-list-head">
                          <span className="surface-list-title">{plainTextPreview(message.from || message.from_address || "Message", 64)}</span>
                          <span className="mini-list-time">{formatDateTime(message.date)}</span>
                        </div>
                        <p className="surface-list-copy">{plainTextPreview(message.body_text || message.snippet || "Message preview", 420)}</p>
                      </article>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="secondary-copy">Select a thread to inspect the conversation and prepare a reply.</p>
              )}
            </section>

            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Draft</h4>
                <span className="muted-label">{emailDrafts.length}</span>
              </div>
              <div className="surface-form">
                <label className="field">
                  <span>Reply guidance</span>
                  <textarea value={emailReplyGuidance} onChange={(event) => setEmailReplyGuidance(event.target.value)} placeholder="Keep it short, confirm delivery, and ask for one next step." rows={3} />
                </label>
                <label className="field">
                  <span>User context</span>
                  <textarea value={emailReplyContext} onChange={(event) => setEmailReplyContext(event.target.value)} placeholder="Use facts the model needs before drafting." rows={3} />
                </label>
                <div className="surface-actions">
                  <button className="send-button" disabled={emailBusy === "reply" || !emailSelectedThread?.thread_id} onClick={() => void handleReplyDraft()} type="button">
                    {emailBusy === "reply" ? "Drafting..." : "Generate reply"}
                  </button>
                </div>
                <div className="surface-form-grid">
                  <label className="field">
                    <span>Forward to</span>
                    <input value={emailForwardTo} onChange={(event) => setEmailForwardTo(event.target.value)} placeholder="name@example.com, ops@example.com" />
                  </label>
                  <label className="field">
                    <span>Forward note</span>
                    <input value={emailForwardNote} onChange={(event) => setEmailForwardNote(event.target.value)} placeholder="Optional note above the forwarded message" />
                  </label>
                </div>
                <div className="surface-actions">
                  <button className="ghost-button" disabled={emailBusy === "forward" || !emailSelectedThread?.thread_id || !emailForwardTo.trim()} onClick={() => void handleForwardDraft()} type="button">
                    {emailBusy === "forward" ? "Preparing..." : "Prepare forward"}
                  </button>
                </div>
              </div>

              {emailDraftDetail ? (
                <div className="draft-preview-card">
                  <div className="surface-list-head">
                    <span className="surface-list-title">{plainTextPreview(typeof emailDraftDetail.summary === "string" ? "Draft guidance" : emailDraftDetail.summary?.subject || emailDraftDetail.draft?.subject || "Prepared draft", 88)}</span>
                    <span className="mini-list-time">{plainTextPreview(emailDraftDetail.confidence || (typeof emailDraftDetail.summary !== "string" ? emailDraftDetail.summary?.confidence : "") || "review", 18)}</span>
                  </div>
                  <p className="surface-list-copy">{plainTextPreview(draftSummaryText || "Draft details appear here after you prepare a reply or forward.", 220)}</p>
                  {draftQuestions.length ? (
                    <div className="surface-meta-row">
                      {draftQuestions.map((question) => (
                        <span key={question} className="evidence-chip">{plainTextPreview(question, 44)}</span>
                      ))}
                    </div>
                  ) : null}
                  {String(emailDraftDetail.draft?.body || emailDraftDetail.draft?.note || "").trim() ? (
                    <pre className="draft-preview-body">{String(emailDraftDetail.draft?.body || emailDraftDetail.draft?.note || "").trim()}</pre>
                  ) : null}
                  <div className="surface-actions">
                    <button className="send-button" disabled={!activeDraftId || emailBusy === "send"} onClick={() => void handleSendPreparedDraft()} type="button">
                      {emailBusy === "send" ? "Sending..." : "Approve & send"}
                    </button>
                    <button className="ghost-button" disabled={!activeDraftId || emailBusy === "reject-draft"} onClick={() => void handleRejectPreparedDraft()} type="button">
                      {emailBusy === "reject-draft" ? "Rejecting..." : "Reject draft"}
                    </button>
                  </div>
                </div>
              ) : null}

              <div className="surface-list">
                {emailDrafts.map((draft) => (
                  <button
                    key={draft.draft_id || `${draft.subject}:${draft.updated_at}`}
                    className={clsx("surface-list-item surface-list-button", activeDraftId && draft.draft_id === activeDraftId && "is-selected")}
                    onClick={() => void loadEmailDraftDetail(String(draft.draft_id || ""))}
                    type="button"
                  >
                    <div className="surface-list-head">
                      <span className="surface-list-title">{plainTextPreview(draft.subject || "Prepared draft", 72)}</span>
                      <span className="mini-list-time">{plainTextPreview(draft.status || "prepared", 20)}</span>
                    </div>
                    <p className="surface-list-copy">{plainTextPreview(draft.summary || "Draft summary", 160)}</p>
                  </button>
                ))}
                {!emailDrafts.length ? <p className="secondary-copy">No stored Gmail drafts yet.</p> : null}
              </div>
            </section>
          </div>
        </div>
      );
    }
    if (activeSurface === "workflows") {
      return (
        <div className="surface-view">
          <div className="surface-header">
            <div>
              <div className="eyebrow">Visible capabilities</div>
              <h3>Workflows & extensions</h3>
              <p className="secondary-copy">Browse the operator's built-in workflows, inspect local extensions, and drop a skill into chat without remembering the slash command first.</p>
            </div>
            <div className="surface-header-meta">
              <span className="meta-pill">{availableSkills.length} skills</span>
              <span className="meta-pill">{availableExtensions.length} extensions</span>
              <span className="meta-pill">{slashCommands.length} commands</span>
            </div>
          </div>
          <div className="surface-grid surface-grid-workflows">
            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Skills</h4>
                <span className="muted-label">{availableSkills.length}</span>
              </div>
              <div className="surface-list">
                {Object.entries(groupedSkills).map(([group, skills]) => (
                  <div key={group} className="surface-group">
                    <div className="surface-group-label">{plainTextPreview(group.replace(/_/g, " "), 32)}</div>
                    {skills.map((skill) => (
                      <article key={skill.slug || skill.commandName || skill.title} className="surface-list-item tone-neutral">
                        <div className="surface-list-head">
                          <span className="surface-list-title">{plainTextPreview(skill.title || skill.commandName || "Skill", 72)}</span>
                          {skill.commandName ? <span className="mini-list-time">/{skill.commandName}</span> : null}
                        </div>
                        <p className="surface-list-copy">{plainTextPreview(skill.description || skill.purpose || "Workflow skill", 180)}</p>
                        <div className="surface-actions">
                          <button
                            className="ghost-button"
                            onClick={() => {
                              setActiveSurface("chat");
                              updateDraft(skill.commandName ? `/${skill.commandName} ` : skill.promptText || "", selectedSessionId);
                              window.requestAnimationFrame(() => textareaRef.current?.focus());
                            }}
                            type="button"
                          >
                            Use in chat
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                ))}
                {!availableSkills.length ? <p className="secondary-copy">No repo-local skills are available.</p> : null}
              </div>
            </section>

            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Extensions</h4>
                <span className="muted-label">{availableExtensions.length}</span>
              </div>
              <div className="surface-list">
                {availableExtensions.map((extension) => (
                  <article key={extension.slug || extension.relativePath || extension.title} className="surface-list-item tone-info">
                    <div className="surface-list-head">
                      <span className="surface-list-title">{plainTextPreview(extension.title || extension.slug || "Extension", 72)}</span>
                      <span className="mini-list-time">{extension.commandCount || extension.commands?.length || 0} cmds</span>
                    </div>
                    <p className="surface-list-copy">{plainTextPreview(extension.description || "Local extension manifest.", 180)}</p>
                    {(extension.commands || []).length ? (
                      <div className="surface-meta-row">
                        {(extension.commands || []).slice(0, 4).map((command) => (
                          <span key={`${extension.slug}:${command.name}`} className="evidence-chip evidence-chip-soft">
                            /{plainTextPreview(command.name || "command", 20)}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </article>
                ))}
                {!availableExtensions.length ? <p className="secondary-copy">No local extensions are loaded.</p> : null}
              </div>
            </section>
          </div>
        </div>
      );
    }
    if (activeSurface === "runs") {
      return (
        <div className="surface-view">
          <div className="surface-header">
            <div>
              <div className="eyebrow">Traceability</div>
              <h3>Runs</h3>
              <p className="secondary-copy">Inspect recent runs, review what the operator decided, and replay the step history without leaving the conversation shell.</p>
            </div>
            <div className="surface-header-meta">
              <span className="meta-pill">{controlData.recentRuns.length} recent runs</span>
              {selectedRun?.final_status ? <span className={clsx("status-pill", `tone-${statusTone(selectedRun.final_status)}`)}>{selectedRun.final_status}</span> : null}
            </div>
          </div>
          <div className="surface-grid surface-grid-runs">
            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Recent runs</h4>
                <span className="muted-label">{controlData.recentRuns.length}</span>
              </div>
              <div className="surface-list">
                {controlData.recentRuns.map((run) => (
                  <button
                    key={run.run_id || `${run.goal}:${run.started_at}`}
                    className={clsx("surface-list-item surface-list-button", run.run_id === selectedRunId && "is-selected", `tone-${statusTone(run.final_status)}`)}
                    onClick={() => void loadRunDetail(String(run.run_id || ""))}
                    type="button"
                  >
                    <div className="surface-list-head">
                      <span className="surface-list-title">{plainTextPreview(run.goal || run.final_summary || "Run", 72)}</span>
                      <span className="mini-list-time">{formatDateTime(run.started_at)}</span>
                    </div>
                    <p className="surface-list-copy">{plainTextPreview(run.final_summary || "Recent operator run.", 180)}</p>
                  </button>
                ))}
                {!controlData.recentRuns.length ? <p className="secondary-copy">No recent runs are available yet.</p> : null}
              </div>
            </section>

            <section className="surface-card">
              <div className="surface-card-header">
                <h4>Replay</h4>
                <span className="muted-label">{selectedRun?.steps?.length || 0} steps</span>
              </div>
              {runDetailBusy ? (
                <p className="secondary-copy">Loading the selected run...</p>
              ) : selectedRun ? (
                <div className="surface-list">
                  <article className={clsx("surface-list-item", `tone-${statusTone(selectedRun.final_status)}`)}>
                    <div className="surface-list-head">
                      <span className="surface-list-title">{plainTextPreview(selectedRun.goal || selectedRun.final_summary || "Selected run", 88)}</span>
                      <span className="mini-list-time">{plainTextPreview(selectedRun.final_status || "run", 20)}</span>
                    </div>
                    <p className="surface-list-copy">{plainTextPreview(selectedRun.final_summary || selectedRun.result_message || "Run summary", 240)}</p>
                  </article>
                  {(selectedRun.steps || []).map((step) => (
                    <article key={`${selectedRun.run_id || "run"}:${step.index}`} className={clsx("surface-list-item", `tone-${statusTone(step.status)}`)}>
                      <div className="surface-list-head">
                        <span className="surface-list-title">
                          Step {typeof step.index === "number" ? step.index + 1 : "?"}: {plainTextPreview(step.tool || step.type || "step", 56)}
                        </span>
                        <span className="mini-list-time">{plainTextPreview(step.status || "unknown", 18)}</span>
                      </div>
                      <p className="surface-list-copy">{plainTextPreview(step.message || step.result_summary || "Recorded run step.", 220)}</p>
                      {step.approval ? (
                        <div className="surface-meta-row">
                          <span className="evidence-chip">Approval</span>
                          <span className="evidence-chip evidence-chip-soft">{plainTextPreview(String(step.approval.status || step.approval.required || "checkpoint"), 32)}</span>
                        </div>
                      ) : null}
                    </article>
                  ))}
                  {!selectedRun.steps?.length ? <p className="secondary-copy">This run does not have recorded steps yet.</p> : null}
                </div>
              ) : (
                <p className="secondary-copy">Select a run from the left to inspect the replay timeline.</p>
              )}
            </section>
          </div>
        </div>
      );
    }
    return null;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="sidebar-header">
            <div className="sidebar-brand">
              <div className="brand-mark">
                <UiIcon name="brand" />
              </div>
              <div>
                <div className="eyebrow">AI Operator</div>
                <h1>Local operator</h1>
                <p className="sidebar-subtitle">Chat-first desktop control surface</p>
              </div>
            </div>
            <CompactMenu label="View" icon="menu">
              <CompactMenuButton icon="details" onClick={() => setDetailsOpen((current) => !current)}>
                {detailsOpen ? "Hide details" : "Show details"}
              </CompactMenuButton>
              <CompactMenuButton icon="refresh" onClick={() => void handleRefresh()}>
                {bootState === "ready" ? "Refresh data" : "Retry startup"}
              </CompactMenuButton>
              <CompactMenuButton
                icon={themeMode === "light" ? "theme-dark" : "theme-light"}
                onClick={() => setThemeMode((current) => (current === "light" ? "dark" : "light"))}
              >
                {themeMode === "light" ? "Use dark theme" : "Use light theme"}
              </CompactMenuButton>
            </CompactMenu>
          </div>
          <div className="sidebar-actions">
            <button className="sidebar-primary-button sidebar-primary-button-wide" onClick={() => void handleNewChat()} disabled={sending} type="button">
              <UiIcon name="new" />
              <span>New chat</span>
            </button>
          </div>
        </div>

        <div className="sidebar-main">
          <label className="search-box search-box-compact">
            <span className="search-box-label">
              <UiIcon name="search" />
              <span>Search conversations</span>
            </span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter title, summary, status..."
            />
          </label>

          <section className="sidebar-section">
            <div className="sidebar-section-header">
              <SectionTitle icon="chat">Recent</SectionTitle>
              <span className="muted-label">{recentSessions.length}</span>
            </div>
            <div className="session-list session-list-recent">
              {recentSessions.map((session) => (
                <button
                  key={session.session_id}
                  className={clsx("session-row", session.session_id === selectedSessionId && "is-selected")}
                  onClick={() => startTransition(() => setSelectedSessionId(session.session_id))}
                  type="button"
                >
                  <div className="session-row-main">
                    <div className="session-row-titleline">
                      <StatusGlyph status={session.status} />
                      <span className="session-row-title">{session.title || "Untitled conversation"}</span>
                    </div>
                    <span className="session-row-time">{formatSessionStamp(session.updated_at)}</span>
                  </div>
                  <div className="session-row-preview">{sessionPreviewText(session)}</div>
                  <div className="session-row-meta">
                    {session.pending_approval?.kind ? <span className="session-row-flag">Approval</span> : null}
                    <span>{session.message_count || 0} msgs</span>
                  </div>
                </button>
              ))}
              {!recentSessions.length ? <div className="empty-sidebar">No recent conversations match this filter.</div> : null}
            </div>
          </section>

          <section className="sidebar-section">
            <div className="sidebar-section-header">
              <SectionTitle icon="history">History</SectionTitle>
              <button
                className="ghost-button sidebar-inline-button"
                onClick={() => setHistoryOpen((current) => !current)}
                type="button"
              >
                {showHistorySection ? "Hide" : `View all${historicalSessions.length ? ` (${historicalSessions.length})` : ""}`}
              </button>
            </div>
            {showHistorySection ? (
              <div className="session-list session-list-history">
                {historicalSessions.length ? (
                  historicalSessions.map((session) => (
                    <button
                      key={session.session_id}
                      className={clsx("session-row is-history", session.session_id === selectedSessionId && "is-selected")}
                      onClick={() => startTransition(() => setSelectedSessionId(session.session_id))}
                      type="button"
                    >
                      <div className="session-row-main">
                        <div className="session-row-titleline">
                          <StatusGlyph status={session.status} />
                          <span className="session-row-title">{session.title || "Untitled conversation"}</span>
                        </div>
                        <span className="session-row-time">{formatSessionStamp(session.updated_at)}</span>
                      </div>
                      <div className="session-row-preview">{sessionPreviewText(session)}</div>
                    </button>
                  ))
                ) : (
                  <div className="empty-sidebar">No older conversations to show right now.</div>
                )}
              </div>
            ) : (
              <p className="secondary-copy sidebar-section-copy">Keep the sidebar focused on the latest work, and expand history only when you need it.</p>
            )}
          </section>

          <section className="sidebar-section sidebar-capability-section">
            <div className="sidebar-section-header">
              <SectionTitle icon="tools">Workspace</SectionTitle>
              <span className="muted-label">{activeSurface}</span>
            </div>
            <div className="workspace-nav">
              {[
                { id: "chat", label: "Chat", icon: "chat" as const, count: recentSessions.length },
                {
                  id: "automations",
                  label: "Automations",
                  icon: "automations" as const,
                  count:
                    (controlData.scheduled?.tasks?.length || 0) +
                    (controlData.watches?.tasks?.length || 0) +
                    (controlData.queue?.queued_tasks?.length || 0),
                },
                { id: "gmail", label: "Gmail", icon: "gmail" as const, count: emailConnected ? emailThreads.length || 0 : 0 },
                { id: "workflows", label: "Workflows", icon: "workflows" as const, count: availableSkills.length + availableExtensions.length },
                { id: "runs", label: "Runs", icon: "history" as const, count: controlData.recentRuns.length },
              ].map((surface) => (
                <button
                  key={surface.id}
                  className={clsx("workspace-nav-button", activeSurface === surface.id && "is-active")}
                  onClick={() => setActiveSurface(surface.id as WorkspaceSurface)}
                  type="button"
                >
                  <span className="workspace-nav-button-main">
                    <UiIcon name={surface.icon} />
                    <span>{surface.label}</span>
                  </span>
                  <span className="workspace-nav-button-count">{surface.count}</span>
                </button>
              ))}
            </div>
            <div className="sidebar-capability-list">
              <span className="sidebar-capability-chip">
                <UiIcon name="desktop" />
                <span>{desktopRuntimeLabel(desktopRuntimeStatus) || "Desktop runtime"}</span>
              </span>
              <span className="sidebar-capability-chip">
                <UiIcon name="gmail" />
                <span>{emailConnected ? "Gmail connected" : "Gmail ready"}</span>
              </span>
              <span className="sidebar-capability-chip">
                <UiIcon name="tools" />
                <span>{controlData.tools.length} tools</span>
              </span>
              <span className="sidebar-capability-chip">
                <UiIcon name="chat" />
                <span>{slashCommands.length} commands</span>
              </span>
            </div>
          </section>
        </div>
      </aside>

      <main className="chat-layout">
        <header className="chat-header">
          <div className="chat-header-main">
            <div className="chat-header-kicker">
              <span className="eyebrow">Current task</span>
              <span className={clsx("connection-pill", `stream-${streamState}`)}>{streamNote}</span>
            </div>
            <div className="chat-title-row">
              <div className="chat-title-stack">
                <div className="chat-title-line">
                  <StatusGlyph status={sessionDetail?.status || status?.status} />
                  <h2>{title}</h2>
                  <span className={clsx("status-pill", `tone-${statusTone(sessionDetail?.status || status?.status)}`)}>
                    {sessionDetail?.status || status?.status || "idle"}
                  </span>
                </div>
                <p className="chat-subtitle">
                  {pendingApproval?.kind
                    ? plainTextPreview(approvalSummary(pendingApproval), 220)
                    : plainTextPreview(
                        activeTask?.last_message || status?.current_step || sessionDetail?.summary || "Start a conversation or continue an existing task.",
                        220,
                      )}
                </p>
              </div>
            </div>
            <div className="chat-meta-row">
              {desktopRuntimeLabel(desktopRuntimeStatus) ? <span className="meta-pill">{desktopRuntimeLabel(desktopRuntimeStatus)}</span> : null}
              {apiManagedByDesktop && !desktopRuntimeLabel(desktopRuntimeStatus) ? <span className="meta-pill">Desktop-managed API</span> : null}
              {runPhase !== "idle" ? <span className="meta-pill">Phase {runPhase.replace(/_/g, " ")}</span> : null}
              {runFocusLocked ? <span className="meta-pill">Run focus locked</span> : null}
              {runtimeModel ? <span className="meta-pill">Model {runtimeModel}</span> : null}
              {runtimeEffortLabel ? <span className="meta-pill">Reasoning {runtimeEffortLabel}</span> : null}
            </div>
          </div>
          <div className="chat-header-actions">
            <button
              className="send-button topbar-primary-action"
              onClick={() => void handlePrimaryTaskAction()}
              disabled={bootState !== "ready" || Boolean(taskActionBusy) || Boolean(pendingApproval?.kind && approving)}
              type="button"
            >
              <UiIcon
                name={
                  String(activeTask?.status || sessionDetail?.status || status?.status || "idle").toLowerCase() === "running" ||
                  String(activeTask?.status || sessionDetail?.status || status?.status || "idle").toLowerCase() === "queued"
                    ? "stop"
                    : "run"
                }
              />
              <span>{topbarPrimaryLabel}</span>
            </button>
            <button className="ghost-button topbar-action" onClick={() => void handleRefresh()} type="button">
              <UiIcon name="refresh" />
              <span>{bootState === "ready" ? "Refresh" : "Retry"}</span>
            </button>
            <button className="ghost-button topbar-action" disabled title="Future handoff flow" type="button">
              <UiIcon name="handoff" />
              <span>Handoff</span>
            </button>
            <button className="ghost-button topbar-action" disabled title="Future commit flow" type="button">
              <UiIcon name="commit" />
              <span>Commit</span>
            </button>
            <button className="ghost-button topbar-action" onClick={() => setDetailsOpen((current) => !current)} type="button">
              <UiIcon name="details" />
              <span>{detailsOpen ? "Hide context" : "Context"}</span>
            </button>
          </div>
        </header>

        <nav className="surface-switcher" aria-label="Workspace surfaces">
          {[
            { id: "chat", label: "Conversation", icon: "chat" as const },
            { id: "automations", label: "Automations", icon: "automations" as const },
            { id: "gmail", label: "Gmail", icon: "gmail" as const },
            { id: "workflows", label: "Workflows", icon: "workflows" as const },
            { id: "runs", label: "Runs", icon: "history" as const },
          ].map((surface) => (
            <button
              key={surface.id}
              className={clsx("surface-switcher-button", activeSurface === surface.id && "is-active")}
              onClick={() => setActiveSurface(surface.id as WorkspaceSurface)}
              type="button"
            >
              <UiIcon name={surface.icon} />
              <span>{surface.label}</span>
            </button>
          ))}
        </nav>

        <section className="conversation-frame">
          {activeSurface !== "chat" ? (
            renderWorkspaceSurface()
          ) : bootState === "booting" ? (
            <div className="boot-state">
              <div className="boot-card">
                <div className="spinner" />
                <h3>Preparing the operator</h3>
                <p>{bootMessage}</p>
              </div>
            </div>
          ) : bootState === "error" ? (
            <div className="boot-state">
              <div className="boot-card boot-card-error">
                <h3>Unable to load the operator</h3>
                <p>{bootMessage}</p>
                <div className="boot-actions">
                  <button className="ghost-button" onClick={() => void handleRefresh()} type="button">
                    Try again
                  </button>
                </div>
              </div>
            </div>
          ) : showConversationSkeleton ? (
            <SkeletonTranscript />
          ) : emptyState ? (
            <div className="empty-state">
              <div className="empty-card">
                <div className="eyebrow">Conversation first</div>
                <h3>Ask naturally. The operator handles the rest.</h3>
                <p>
                  Use chat as the primary surface. Task status, approvals, and alerts stay visible, but secondary, so the
                  answer stays front and center.
                </p>
                <div className="quick-prompts">
                  {QUICK_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      className="prompt-chip"
                      onClick={() => {
                        updateDraft(prompt, selectedSessionId);
                        window.requestAnimationFrame(() => textareaRef.current?.focus());
                      }}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="transcript" onScroll={handleTranscriptScroll} ref={transcriptRef}>
              {transcriptItems.map((item) =>
                item.type === "activity-group" ? (
                  <ActivityCluster key={item.key} messages={item.messages} />
                ) : (
                  <MemoMessageBubble key={item.key} message={item.message} />
                ),
              )}
            </div>
          )}
          {activeSurface === "chat" && !emptyState && !loadingConversation && !isNearTranscriptBottom ? (
            <button className="jump-to-latest" onClick={() => scrollTranscriptToLatest()} type="button">
              {pendingNewMessageCount > 0 ? `Jump to latest (${pendingNewMessageCount})` : "Jump to latest"}
            </button>
          ) : null}
        </section>

        {activeSurface === "chat" ? (
        <footer className="composer-shell">
          <textarea
            ref={textareaRef}
            value={draft}
            disabled={bootState !== "ready" || sending}
            onChange={(event) => updateDraft(event.target.value, selectedSessionId)}
            placeholder={composerPlaceholder}
            onKeyDown={(event) => {
              if (commandMenuVisible && commandSuggestions.length && event.key === "ArrowDown") {
                event.preventDefault();
                setSelectedCommandIndex((current) => (current + 1) % commandSuggestions.length);
                return;
              }
              if (commandMenuVisible && commandSuggestions.length && event.key === "ArrowUp") {
                event.preventDefault();
                setSelectedCommandIndex((current) => (current - 1 + commandSuggestions.length) % commandSuggestions.length);
                return;
              }
              if (commandMenuVisible && activeCommandSuggestion && event.key === "Tab") {
                event.preventDefault();
                applyCommandSuggestion(activeCommandSuggestion);
                return;
              }
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSendMessage();
              }
            }}
            rows={1}
          />
          {commandMenuVisible ? (
            <div aria-label="Slash commands" className="composer-command-menu" role="listbox">
              {commandSuggestions.length ? (
                commandSuggestions.map((suggestion, index) => (
                  <button
                    key={`${suggestion.command.name}:${suggestion.command.type}`}
                    aria-selected={index === selectedCommandIndex}
                    className={clsx("composer-command-item", index === selectedCommandIndex && "is-selected")}
                    onClick={() => applyCommandSuggestion(suggestion)}
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseEnter={() => setSelectedCommandIndex(index)}
                    type="button"
                  >
                    <span className="composer-command-meta">
                      <span className="composer-command-name">
                        /{suggestion.command.name}
                        {suggestion.command.argumentHint ? ` ${suggestion.command.argumentHint}` : ""}
                      </span>
                      <span
                        className={clsx(
                          "composer-command-kind",
                          suggestion.command.source === "repo_skill"
                            ? "is-skill"
                            : suggestion.command.source === "local_extension"
                              ? "is-prompt"
                              : `is-${suggestion.command.type}`,
                        )}
                      >
                        {suggestion.command.source === "repo_skill"
                          ? "Skill"
                          : suggestion.command.source === "local_extension"
                            ? "Extension"
                          : suggestion.command.type === "local"
                            ? "Local"
                            : "Prompt"}
                      </span>
                    </span>
                    <span className="composer-command-description">{suggestion.command.description}</span>
                  </button>
                ))
              ) : (
                <div className="composer-command-empty">
                  {parsedSlashCommand?.query
                    ? `No slash command matches /${parsedSlashCommand.query}.`
                    : "Type a slash command like /new, /refresh, or /architecture."}
                </div>
              )}
            </div>
          ) : null}
          <div className="composer-footer">
            <div className="composer-footer-main">
              <span className="composer-hint">{composerHint}</span>
              <div className="composer-capability-row">
                <span className="composer-capability-pill">
                  <UiIcon name="tools" />
                  Slash commands
                </span>
                <span className="composer-capability-pill">
                  <UiIcon name="desktop" />
                  Desktop evidence
                </span>
                <span className="composer-capability-pill">
                  <UiIcon name="gmail" />
                  Gmail
                </span>
              </div>
            </div>
            <button className="send-button" onClick={() => void handleSendMessage()} disabled={bootState !== "ready" || sending || !draft.trim()}>
              {sending ? "Sending..." : bootState !== "ready" ? "Unavailable" : "Send"}
            </button>
          </div>
        </footer>
        ) : null}
      </main>

      <aside className="right-rail">
        <section className="rail-card rail-card-approval">
          <div className="rail-card-header">
            <h3><SectionTitle icon="approval">Approval</SectionTitle></h3>
            {pendingApproval?.kind ? <span className="approval-dot">Needed</span> : <span className="muted-label">Clear</span>}
          </div>
          {pendingApproval?.kind ? (
            <>
              <p className="approval-kind">{plainTextPreview(pendingApproval.kind, 80)}</p>
              <p className="approval-detail">{plainTextPreview(approvalSummary(pendingApproval), 180)}</p>
              <div className="approval-explainer">
                <div className="inspector-stat">
                  <span className="stat-label">Why approval is required</span>
                  <p>
                    {plainTextPreview(
                      approvalTool?.policy?.summary ||
                        pendingApproval.reason ||
                        "This action crosses a tool boundary that requires an explicit operator confirmation.",
                      180,
                    )}
                  </p>
                </div>
                <div className="approval-context-grid">
                  <div className="inspector-stat">
                    <span className="stat-label">Risk</span>
                    <p>{plainTextPreview(approvalTool?.policy?.risk_level || "review", 48)}</p>
                  </div>
                  <div className="inspector-stat">
                    <span className="stat-label">If approved</span>
                    <p>{plainTextPreview(`The operator will resume the paused ${pendingApproval.tool || pendingApproval.kind || "action"} and continue the run.`, 120)}</p>
                  </div>
                </div>
              </div>
              {pendingApproval?.target || pendingApproval?.step ? (
                <div className="approval-context-grid">
                  {pendingApproval.target ? (
                    <div className="inspector-stat">
                      <span className="stat-label">Target</span>
                      <p>{plainTextPreview(String(pendingApproval.target), 96)}</p>
                    </div>
                  ) : null}
                  {pendingApproval.step ? (
                    <div className="inspector-stat">
                      <span className="stat-label">Step</span>
                      <p>{plainTextPreview(String(pendingApproval.step), 96)}</p>
                    </div>
                  ) : null}
                </div>
              ) : null}
              <EvidencePreviewCard
                title="Linked evidence"
                preview={pendingApprovalEvidence}
                onViewArtifact={(preview) => void handleViewEvidenceArtifact(preview, "Approval evidence")}
                artifactLoading={artifactViewer.loading && artifactViewer.requestedEvidenceId === pendingApprovalEvidence?.evidence_id}
                emptyText={
                  pendingApproval?.evidence_summary
                    ? plainTextPreview(pendingApproval.evidence_summary, 180)
                    : "No desktop evidence is linked to this checkpoint yet."
                }
              />
              <p className="approval-footnote">The operator is paused until you approve or reject this step.</p>
              <div className="approval-actions">
                <button className="approve-button" disabled={Boolean(approving)} onClick={() => void handleApproval("approve")}>
                  {approving === "approve" ? "Approving..." : "Approve"}
                </button>
                <button className="ghost-button" disabled={Boolean(approving)} onClick={() => void handleApproval("reject")}>
                  {approving === "reject" ? "Rejecting..." : "Reject"}
                </button>
              </div>
            </>
          ) : (
            <p className="secondary-copy">No approval is blocking the current conversation.</p>
          )}
        </section>

        <section className="rail-card">
          <div className="rail-card-header">
            <h3><SectionTitle icon="task">Active task</SectionTitle></h3>
            <span className={clsx("status-pill", `tone-${statusTone(activeTask?.status || status?.status)}`)}>
              {activeTask?.status || status?.status || "idle"}
            </span>
          </div>
          <div className="stat-stack stat-stack-compact">
            <div className="inspector-stat">
              <span className="stat-label">Current step</span>
              <p>{plainTextPreview(status?.current_step || activeTask?.last_message || "Waiting for your next request.", 140)}</p>
            </div>
            <div className="inspector-stat">
              <span className="stat-label">Workflow</span>
              <p>{plainTextPreview(status?.browser?.workflow_name || status?.browser?.task_name || "No browser workflow active.", 120)}</p>
            </div>
            <div className="inspector-stat">
              <span className="stat-label">Page</span>
              <p>{plainTextPreview(status?.browser?.current_title || status?.browser?.current_url || "No live browser page.", 140)}</p>
            </div>
          </div>
          <div className="mini-list inspector-mini-list">
            <article className={clsx("mini-list-item", `tone-${proposalTone(activeTargetProposalContext?.state)}`)}>
              <div className="mini-list-title">
                Target proposals
                <span className="mini-list-time">{activeTargetProposalContext?.proposal_count || 0}</span>
              </div>
              <div className="mini-list-detail">
                {plainTextPreview(
                  activeTargetProposalContext?.summary ||
                    "No compact desktop target proposals are available for the current task yet.",
                  180,
                )}
              </div>
            </article>
            {(activeTargetProposalContext?.proposals || []).slice(0, 2).map((proposal) => (
              <article key={proposal.target_id || proposal.summary || proposal.target_kind} className="mini-list-item tone-neutral">
                <div className="mini-list-title">
                  {plainTextPreview(proposalLabel(proposal), 52)}
                  <span className="mini-list-time">{plainTextPreview(proposal.confidence || "low", 16)}</span>
                </div>
                <div className="mini-list-detail">{plainTextPreview(proposal.summary || "Bounded desktop target proposal.", 160)}</div>
                <div className="evidence-preview-meta">
                  {proposal.target_kind ? <span className="evidence-chip">{plainTextPreview(proposal.target_kind, 24)}</span> : null}
                  {(proposal.suggested_next_actions || []).slice(0, 2).map((action) => (
                    <span key={`${proposal.target_id || proposal.summary}:${action}`} className="evidence-chip evidence-chip-soft">
                      {plainTextPreview(action, 28)}
                    </span>
                  ))}
                  {proposal.approval_required ? <span className="evidence-chip">Approval</span> : null}
                </div>
                <div className="surface-actions surface-actions-tight">
                  <button className="ghost-button" onClick={() => handleUseTargetProposal(proposal)} type="button">
                    Use in chat
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="rail-card">
          <div className="rail-card-header">
            <h3><SectionTitle icon="evidence">Evidence</SectionTitle></h3>
            <span className="muted-label">{controlData.desktopEvidence.length}</span>
          </div>
          <div className="rail-evidence-stack rail-evidence-stack-tight">
            <EvidencePreviewCard
              title="Selected evidence"
              preview={selectedDesktopEvidence}
              onViewArtifact={(preview) => void handleViewEvidenceArtifact(preview, "Selected desktop evidence")}
              artifactLoading={artifactViewer.loading && artifactViewer.requestedEvidenceId === selectedDesktopEvidence?.evidence_id}
              emptyText="No desktop evidence is selected for the current task."
            />
            {distinctCheckpointEvidence ? (
              <EvidencePreviewCard
                title="Checkpoint evidence"
                preview={distinctCheckpointEvidence}
                onViewArtifact={(preview) => void handleViewEvidenceArtifact(preview, "Checkpoint evidence")}
                artifactLoading={artifactViewer.loading && artifactViewer.requestedEvidenceId === distinctCheckpointEvidence?.evidence_id}
              />
            ) : null}
            <div className="mini-list inspector-mini-list">
              {controlData.desktopEvidence.slice(0, 3).map((item) => (
                <article
                  key={item.evidence_id || item.timestamp || item.summary}
                  className={clsx("mini-list-item", item.is_partial ? "tone-warning" : "tone-neutral")}
                >
                  <div className="mini-list-title">
                    {plainTextPreview(item.active_window_title || item.summary || "Evidence", 46)}
                    <span className="mini-list-time">{formatTime(item.timestamp)}</span>
                  </div>
                  <div className="mini-list-detail">{plainTextPreview(evidenceSummaryText(item), 120)}</div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="rail-card rail-card-grow">
          <div className="rail-card-header">
            <h3><SectionTitle icon="tools">Context</SectionTitle></h3>
            <button className="ghost-button sidebar-inline-button" onClick={() => setDetailsOpen(true)} type="button">
              Open details
            </button>
          </div>
          <div className="context-inspector-grid">
            <article className="mini-list-item tone-info">
              <div className="mini-list-title">
                Runtime
                <span className="mini-list-time">{plainTextPreview(status?.runtime?.reasoning_effort || "default", 18)}</span>
              </div>
              <div className="mini-list-detail">{plainTextPreview(status?.runtime?.active_model || "Runtime metadata not loaded yet.", 120)}</div>
            </article>
            <article className={clsx("mini-list-item", emailConnected ? "tone-success" : "tone-neutral")}>
              <div className="mini-list-title">
                Gmail
                <span className="mini-list-time">{emailConnected ? "connected" : "ready"}</span>
              </div>
              <div className="mini-list-detail">{plainTextPreview(backendServiceDetail(emailService), 120)}</div>
            </article>
            <article className="mini-list-item tone-neutral">
              <div className="mini-list-title">
                Tools
                <span className="mini-list-time">{controlData.tools.length}</span>
              </div>
              <div className="mini-list-detail">Slash commands, desktop controls, runtime tools, and Gmail are available from chat.</div>
            </article>
            <article className="mini-list-item tone-neutral">
              <div className="mini-list-title">
                Extensions
                <span className="mini-list-time">{controlData.extensions.length}</span>
              </div>
              <div className="mini-list-detail">{plainTextPreview(controlData.extensions[0]?.description || "Local extensions appear here when loaded.", 120)}</div>
            </article>
          </div>
        </section>
      </aside>

      {detailsOpen ? (
        <div className="details-drawer">
          <div className="details-drawer-backdrop" onClick={() => setDetailsOpen(false)} />
          <section className="details-panel">
            <header className="details-header">
              <div>
                <div className="eyebrow">Secondary controls</div>
                <h3>Operator details</h3>
              </div>
              <button className="ghost-button" onClick={() => setDetailsOpen(false)}>
                Close
              </button>
            </header>

            <div className="details-grid">
              <section className="details-card">
                <div className="rail-card-header">
                  <h4><SectionTitle icon="evidence">Desktop evidence</SectionTitle></h4>
                  <span className="muted-label">{controlData.desktopEvidence.length}</span>
                </div>
                <EvidencePreviewCard
                  title="Selected for this task"
                  preview={selectedDesktopEvidence}
                  onViewArtifact={(preview) => void handleViewEvidenceArtifact(preview, "Selected desktop evidence")}
                  artifactLoading={artifactViewer.loading && artifactViewer.requestedEvidenceId === selectedDesktopEvidence?.evidence_id}
                  emptyText="No selected desktop evidence for the current task."
                />
                {distinctCheckpointEvidence ? (
                  <EvidencePreviewCard
                    title="Linked checkpoint"
                    preview={distinctCheckpointEvidence}
                    onViewArtifact={(preview) => void handleViewEvidenceArtifact(preview, "Checkpoint evidence")}
                    artifactLoading={artifactViewer.loading && artifactViewer.requestedEvidenceId === distinctCheckpointEvidence?.evidence_id}
                  />
                ) : null}
                <div className="mini-list evidence-mini-list">
                  {controlData.desktopEvidence.map((item) => (
                    <article
                      key={item.evidence_id || item.timestamp || item.summary}
                      className={clsx("mini-list-item", item.is_partial ? "tone-warning" : "tone-neutral")}
                    >
                      <div className="mini-list-title">
                        {plainTextPreview(item.active_window_title || item.summary || item.evidence_id || "Desktop evidence", 56)}
                        <span className="mini-list-time">{formatDateTime(item.timestamp)}</span>
                      </div>
                      <div className="mini-list-detail">{plainTextPreview(evidenceSummaryText(item), 132)}</div>
                      <div className="evidence-list-footer">
                        {item.evidence_id ? <span className="evidence-reference">Ref {item.evidence_id}</span> : null}
                        {item.has_screenshot ? <span className="evidence-chip evidence-chip-soft">Screenshot</span> : null}
                        {item.is_partial ? <span className="evidence-chip">Partial</span> : null}
                        {item.evidence_id && (item.has_screenshot || item.has_artifact) ? (
                          <button className="ghost-button evidence-action-button" onClick={() => void handleViewEvidenceArtifact(item, "Recent evidence")} type="button">
                            View artifact
                          </button>
                        ) : null}
                      </div>
                    </article>
                  ))}
                  {!controlData.desktopEvidence.length ? (
                    <p className="secondary-copy">No recent desktop evidence has been retained for this operator session.</p>
                  ) : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Recent runs</h4>
                  <span className="muted-label">{controlData.recentRuns.length}</span>
                </div>
                <div className="mini-list">
                  {controlData.recentRuns.map((run) => (
                    <article key={run.run_id || run.started_at} className={clsx("mini-list-item", `tone-${statusTone(run.final_status)}`)}>
                      <div className="mini-list-title">{plainTextPreview(run.final_status || "run", 36)}</div>
                      <div className="mini-list-detail">{plainTextPreview(run.final_summary || run.goal || "Recent run", 120)}</div>
                    </article>
                  ))}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Queue</h4>
                  <span className="muted-label">
                    {Object.values(controlData.queue?.counts || {}).reduce((sum, value) => sum + Number(value || 0), 0)}
                  </span>
                </div>
                <div className="mini-list">
                  {(controlData.queue?.queued_tasks || []).slice(0, 6).map((task) => (
                    <article key={task.task_id || task.goal} className={clsx("mini-list-item", `tone-${statusTone(task.status)}`)}>
                      <div className="mini-list-title">{plainTextPreview(task.status || "queued", 36)}</div>
                      <div className="mini-list-detail">{plainTextPreview(task.goal || task.last_message || "Queued task", 120)}</div>
                    </article>
                  ))}
                  {!(controlData.queue?.queued_tasks || []).length ? <p className="secondary-copy">No queued tasks.</p> : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Scheduled</h4>
                  <span className="muted-label">{controlData.scheduled?.tasks?.length || 0}</span>
                </div>
                <div className="mini-list">
                  {(controlData.scheduled?.tasks || []).slice(0, 6).map((task) => (
                    <article key={task.scheduled_id || task.goal} className={clsx("mini-list-item", `tone-${statusTone(task.status)}`)}>
                      <div className="mini-list-title">{plainTextPreview(task.recurrence || "once", 36)}</div>
                      <div className="mini-list-detail">{plainTextPreview(task.goal || task.last_message || "Scheduled task", 120)}</div>
                    </article>
                  ))}
                  {!(controlData.scheduled?.tasks || []).length ? <p className="secondary-copy">No scheduled tasks.</p> : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Watches</h4>
                  <span className="muted-label">{controlData.watches?.tasks?.length || 0}</span>
                </div>
                <div className="mini-list">
                  {(controlData.watches?.tasks || []).slice(0, 6).map((task) => (
                    <article key={task.watch_id || task.goal} className={clsx("mini-list-item", `tone-${statusTone(task.status)}`)}>
                      <div className="mini-list-title">{plainTextPreview(task.condition_type || "watch", 40)}</div>
                      <div className="mini-list-detail">{plainTextPreview(task.goal || task.last_message || "Watch", 120)}</div>
                    </article>
                  ))}
                  {!(controlData.watches?.tasks || []).length ? <p className="secondary-copy">No active watches.</p> : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Recent alerts</h4>
                  <span className="muted-label">{alerts.length}</span>
                </div>
                <div className="mini-list">
                  {alerts.slice(0, 8).map((alert) => (
                    <article key={alert.alert_id || `${alert.created_at}:${alert.title}`} className={clsx("mini-list-item", `tone-${statusTone(alert.severity)}`)}>
                      <div className="mini-list-title">
                        {plainTextPreview(alert.title || alert.type || "Alert", 48)}
                        <span className="mini-list-time">{formatTime(alert.created_at)}</span>
                      </div>
                      <div className="mini-list-detail">{plainTextPreview(alert.message || "Operator alert", 150)}</div>
                    </article>
                  ))}
                  {!alerts.length ? <p className="secondary-copy">No recent alerts for this conversation.</p> : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Live activity</h4>
                  <span className="muted-label">{activity.length}</span>
                </div>
                <div className="mini-list">
                  {activity.slice(0, 12).map((entry) => (
                    <article key={entry.id} className={clsx("mini-list-item", `tone-${entry.tone}`)}>
                      <div className="mini-list-title">
                        {plainTextPreview(entry.label, 48)}
                        <span className="mini-list-time">{formatTime(entry.timestamp)}</span>
                      </div>
                      <div className="mini-list-detail">{plainTextPreview(entry.detail, 160)}</div>
                    </article>
                  ))}
                  {!activity.length ? <p className="secondary-copy">Live operator activity will appear here.</p> : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Runtime</h4>
                  <span className="muted-label">{status?.runtime?.settings_reload_count || 1}</span>
                </div>
                <div className="mini-list">
                  <article className="mini-list-item tone-info">
                    <div className="mini-list-title">
                      {plainTextPreview(status?.runtime?.active_model || "Unknown model", 40)}
                      <span className="mini-list-time">{plainTextPreview(status?.runtime?.reasoning_effort || "default", 20)}</span>
                    </div>
                    <div className="mini-list-detail">
                      {plainTextPreview(
                        `Version ${status?.runtime?.settings_version || "unknown"}${
                          status?.runtime?.settings_hot_reload?.enabled ? " | hot reload enabled" : ""
                        }`,
                        160,
                      )}
                    </div>
                  </article>
                  {runtimePolicy?.summary ? (
                    <article className="mini-list-item tone-neutral">
                      <div className="mini-list-title">Policy summary</div>
                      <div className="mini-list-detail">{plainTextPreview(runtimePolicy.summary, 160)}</div>
                      <div className="evidence-preview-meta">
                        {(runtimePolicy.explicit_approval_tools || []).slice(0, 3).map((tool) => (
                          <span key={`explicit:${tool}`} className="evidence-chip">
                            {plainTextPreview(tool, 24)}
                          </span>
                        ))}
                      </div>
                    </article>
                  ) : null}
                  {!status?.runtime ? <p className="secondary-copy">Runtime metadata is not available yet.</p> : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Infrastructure</h4>
                  <span className="muted-label">{infrastructureServices.length}</span>
                </div>
                <div className="mini-list">
                  {infrastructureServices.map((item) => (
                    <article key={item.key} className={clsx("mini-list-item", `tone-${backendServiceTone(item.service)}`)}>
                      <div className="mini-list-title">{item.label}</div>
                      <div className="mini-list-detail">{backendServiceDetail(item.service)}</div>
                      <div className="evidence-preview-meta">
                        <span className="evidence-chip evidence-chip-soft">{plainTextPreview(backendServiceStatus(item.service), 28)}</span>
                      </div>
                    </article>
                  ))}
                  {!infrastructureServices.length ? <p className="secondary-copy">Infrastructure details are not available yet.</p> : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Tool catalog</h4>
                  <span className="muted-label">{controlData.tools.length}</span>
                </div>
                <div className="mini-list">
                  {highlightedTools.slice(0, 8).map((tool) => (
                    <article key={tool.name || tool.description} className={clsx("mini-list-item", `tone-${statusTone(tool.policy?.risk_level)}`)}>
                      <div className="mini-list-title">
                        {plainTextPreview(tool.name || "tool", 40)}
                        <span className="mini-list-time">{plainTextPreview(tool.policy?.approval_mode || "unknown", 20)}</span>
                      </div>
                      <div className="mini-list-detail">{plainTextPreview(tool.policy?.summary || tool.description || "Registered tool", 150)}</div>
                    </article>
                  ))}
                  {!controlData.tools.length ? <p className="secondary-copy">No tool catalog is loaded yet.</p> : null}
                </div>
              </section>

              <section className="details-card">
                <div className="rail-card-header">
                  <h4>Extensions</h4>
                  <span className="muted-label">{controlData.extensions.length}</span>
                </div>
                <div className="mini-list">
                  {controlData.extensions.map((extension) => (
                    <article key={extension.slug || extension.relativePath || extension.title} className="mini-list-item tone-info">
                      <div className="mini-list-title">
                        {plainTextPreview(extension.title || extension.slug || "extension", 44)}
                        <span className="mini-list-time">{extension.commandCount || (extension.commands || []).length || 0}</span>
                      </div>
                      <div className="mini-list-detail">{plainTextPreview(extension.description || "Local extension manifest.", 150)}</div>
                      {extensionCommandPreview(extension) ? (
                        <div className="evidence-preview-meta">
                          <span className="evidence-chip evidence-chip-soft">{plainTextPreview(extensionCommandPreview(extension), 72)}</span>
                        </div>
                      ) : null}
                    </article>
                  ))}
                  {!controlData.extensions.length ? <p className="secondary-copy">No local extensions are loaded.</p> : null}
                </div>
              </section>
            </div>
          </section>
        </div>
      ) : null}

      {artifactViewer.open ? (
        <OverlayPortal>
          <div className="artifact-viewer">
            <div className="artifact-viewer-backdrop" onClick={closeArtifactViewer} />
            <section className="artifact-viewer-panel">
              <header className="artifact-viewer-header">
                <div>
                  <div className="eyebrow">{artifactViewer.sourceLabel || "Desktop evidence"}</div>
                  <h3>Evidence artifact</h3>
                  <p className="secondary-copy">{plainTextPreview(artifactViewer.heading || artifactViewer.artifact?.summary || "Desktop evidence artifact", 180)}</p>
                </div>
                <button className="ghost-button" onClick={closeArtifactViewer} type="button">
                  Close
                </button>
              </header>

              <div className="artifact-viewer-body">
                {artifactViewer.loading ? (
                  <div className="artifact-viewer-empty">
                    <div className="spinner" />
                    <p>Loading retained evidence...</p>
                  </div>
                ) : artifactViewer.error ? (
                  <div className="artifact-viewer-empty artifact-viewer-empty-error">
                    <h4>Unable to load the artifact</h4>
                    <p>{artifactViewer.error}</p>
                  </div>
                ) : artifactViewer.previewUrl ? (
                  <div className="artifact-preview-shell">
                    {artifactViewer.imageStatus !== "ready" ? (
                      <div className="artifact-preview-loading">
                        <div className="spinner" />
                        <p>Rendering retained screenshot...</p>
                      </div>
                    ) : null}
                    <img
                      alt={artifactViewer.heading || artifactViewer.artifact?.summary || "Desktop evidence artifact"}
                      className="artifact-preview-image"
                      onError={() =>
                        setArtifactViewer((current) =>
                          current.open
                            ? {
                                ...current,
                                previewUrl: "",
                                imageStatus: "error",
                                error:
                                  current.error || "The retained artifact metadata loaded, but the screenshot image itself could not be rendered.",
                              }
                            : current,
                        )
                      }
                      onLoad={() =>
                        setArtifactViewer((current) => (current.open ? { ...current, imageStatus: "ready" } : current))
                      }
                      src={artifactViewer.previewUrl}
                    />
                  </div>
                ) : (
                  <div className="artifact-viewer-empty">
                    <h4>{artifactViewer.artifact?.artifact_available ? "Preview unavailable" : "Artifact unavailable"}</h4>
                    <p>{artifactViewer.error || artifactStateMessage(artifactViewer.artifact)}</p>
                  </div>
                )}
                <section className="artifact-metadata-card">
                  <div className="artifact-metadata-header">
                    <span className="evidence-preview-title">Artifact metadata</span>
                    {artifactViewer.artifact?.summary ? (
                      <span className="muted-label">{plainTextPreview(artifactViewer.artifact.summary, 60)}</span>
                    ) : null}
                  </div>
                  <div className="artifact-metadata-chips">
                    {artifactViewer.artifact?.evidence_id ? <span className="evidence-reference">Ref {artifactViewer.artifact.evidence_id}</span> : null}
                    {artifactViewer.artifact?.artifact_name ? <span className="evidence-chip evidence-chip-soft">{artifactViewer.artifact.artifact_name}</span> : null}
                    {artifactViewer.artifact?.artifact_type ? <span className="evidence-chip">{artifactViewer.artifact.artifact_type}</span> : null}
                    {artifactViewer.artifact?.availability_state ? <span className="evidence-chip">{artifactViewer.artifact.availability_state}</span> : null}
                  </div>
                  <p className="artifact-metadata-copy">{plainTextPreview(artifactStateMessage(artifactViewer.artifact), 220) || "Metadata is available for this retained artifact."}</p>
                </section>
              </div>

              <footer className="artifact-viewer-footer">
                <span className="secondary-copy">Retained artifacts stay attached to their desktop evidence reference.</span>
                <button className="ghost-button" onClick={closeArtifactViewer} type="button">
                  Close
                </button>
              </footer>
            </section>
          </div>
        </OverlayPortal>
      ) : null}
    </div>
  );
}
