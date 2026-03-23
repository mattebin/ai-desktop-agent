import React, { startTransition, useDeferredValue, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import clsx from "clsx";
import {
  AlertItem,
  approvePending,
  BrowserState,
  createSession,
  DesktopRuntimeStatus,
  EvidenceArtifact,
  EvidenceSummary,
  ensureLocalApi,
  getAlerts,
  getDesktopEvidenceArtifact,
  getDesktopEvidenceArtifactContentUrl,
  getDesktopEvidence,
  getQueueState,
  getRecentRuns,
  getScheduledState,
  getSession,
  getSessionMessages,
  getStatus,
  getWatchState,
  listSessions,
  openSessionEventStream,
  rejectPending,
  RunEntry,
  sendSessionMessage,
  SessionDetail,
  SessionMessage,
  SessionSummary,
  StatusPayload,
  StreamEvent,
  type PendingApproval,
  type QueuePayload,
  type ScheduledPayload,
  type WatchPayload,
} from "./lib/api";

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
};

type ThemeMode = "light" | "dark";

type ArtifactViewerState = {
  open: boolean;
  loading: boolean;
  requestedEvidenceId: string;
  sourceLabel: string;
  heading: string;
  artifact: EvidenceArtifact | null;
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
const COMPOSER_MAX_HEIGHT = 220;

const STREAM_EVENTS = [
  "stream.hello",
  "stream.reset",
  "stream.heartbeat",
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
  messages.forEach((message, index) => {
    const key =
      message.message_id ||
      `${message.role || "assistant"}:${message.kind || "message"}:${message.created_at || index}:${message.content || ""}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    ordered.push(message);
  });
  return ordered;
}

function upsertSession(sessions: SessionSummary[], session: SessionSummary): SessionSummary[] {
  const next = sessions.filter((item) => item.session_id !== session.session_id);
  next.push(session);
  next.sort((left, right) => (right.updated_at || "").localeCompare(left.updated_at || ""));
  return next;
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
  });
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [approving, setApproving] = useState<"" | "approve" | "reject">("");
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [artifactViewer, setArtifactViewer] = useState<ArtifactViewerState>({
    open: false,
    loading: false,
    requestedEvidenceId: "",
    sourceLabel: "",
    heading: "",
    artifact: null,
    error: "",
  });
  const [streamState, setStreamState] = useState<"connecting" | "live" | "reconnecting" | "offline">("connecting");
  const [streamNote, setStreamNote] = useState("Waiting for live updates");
  const [isNearTranscriptBottom, setIsNearTranscriptBottom] = useState(true);
  const [pendingNewMessageCount, setPendingNewMessageCount] = useState(0);
  const deferredQuery = useDeferredValue(query);
  const draftKey = getDraftKey(selectedSessionId);
  const draft = draftsBySession[draftKey] || "";
  const selectedSessionRef = useRef("");
  const detailsOpenRef = useRef(false);
  const refreshTimerRef = useRef<number | null>(null);
  const streamRef = useRef<EventSource | null>(null);
  const lastEventIdsRef = useRef<Record<string, string>>({});
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const lastTranscriptSignatureRef = useRef("");

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
    shouldStickToBottomRef.current = true;
    setIsNearTranscriptBottom(true);
    setPendingNewMessageCount(0);
    lastTranscriptSignatureRef.current = "";
  }, [selectedSessionId]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = "0px";
    const nextHeight = Math.min(Math.max(textarea.scrollHeight, 118), COMPOSER_MAX_HEIGHT);
    textarea.style.height = `${nextHeight}px`;
  }, [draft, selectedSessionId]);

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) {
        window.clearTimeout(refreshTimerRef.current);
      }
      streamRef.current?.close();
    };
  }, []);

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
    setSessions(nextSessions);
    const preferred = preferredSessionId || selectedSessionRef.current || window.localStorage.getItem("ai-operator:selected-session") || "";
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

  async function loadConversation(sessionId: string) {
    if (!apiBaseUrl || !sessionId) {
      setSessionDetail(null);
      setMessages([]);
      setStatus(null);
      setAlerts([]);
      return;
    }
    setLoadingConversation(true);
    try {
      const [detailPayload, messagesPayload, statusPayload, alertsPayload] = await Promise.all([
        getSession(apiBaseUrl, sessionId),
        getSessionMessages(apiBaseUrl, sessionId, 40),
        getStatus(apiBaseUrl, sessionId),
        getAlerts(apiBaseUrl, sessionId, 8),
      ]);
      setSessionDetail(detailPayload.session);
      setMessages(normalizeMessages(messagesPayload.messages || messagesPayload.items || detailPayload.session.messages || []));
      setStatus(statusPayload);
      setAlerts(alertsPayload.items || []);
      setSessions((current) => upsertSession(current, detailPayload.session));
    } finally {
      setLoadingConversation(false);
    }
  }

  async function refreshControlData(sessionId = selectedSessionRef.current) {
    if (!apiBaseUrl) {
      return;
    }
    const [queue, scheduled, watches, runs, desktopEvidence] = await Promise.all([
      getQueueState(apiBaseUrl),
      getScheduledState(apiBaseUrl),
      getWatchState(apiBaseUrl),
      getRecentRuns(apiBaseUrl, sessionId, 10),
      getDesktopEvidence(apiBaseUrl, 6),
    ]);
    setControlData({
      queue,
      scheduled,
      watches,
      recentRuns: runs.items || [],
      desktopEvidence: desktopEvidence.recent_summaries || [],
    });
  }

  function scheduleConversationRefresh(sessionId = selectedSessionRef.current) {
    if (!sessionId) {
      return;
    }
    if (refreshTimerRef.current) {
      window.clearTimeout(refreshTimerRef.current);
    }
    refreshTimerRef.current = window.setTimeout(() => {
      void refreshSidebar(sessionId);
      void loadConversation(sessionId);
      if (detailsOpenRef.current) {
        void refreshControlData(sessionId);
      }
      refreshTimerRef.current = null;
    }, 180);
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
          await loadConversation(resolvedSession);
        } else {
          const [operatorStatus, operatorAlerts] = await Promise.all([getStatus(apiBaseUrl), getAlerts(apiBaseUrl, "", 8)]);
          if (!alive) {
            return;
          }
          setStatus(operatorStatus);
          setAlerts(operatorAlerts.items || []);
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
    void loadConversation(selectedSessionId);
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
        if (activityEntry) {
          setActivity((current) => [activityEntry, ...current].slice(0, 30));
        }

        if (payload.event === "session.message") {
          const nextMessage = (payload.data.message || {}) as SessionMessage;
          if (nextMessage.message_id || nextMessage.content) {
            setMessages((current) => normalizeMessages([...current, nextMessage]));
          }
        }

        if (payload.event === "session.sync" || payload.event === "operator.sync") {
          const syncData = payload.data as {
            session?: SessionDetail;
            snapshot?: StatusPayload;
            alerts?: AlertItem[];
          };
          if (syncData.session?.session_id) {
            setSessionDetail((current) =>
              current && current.session_id === syncData.session?.session_id
                ? { ...current, ...syncData.session }
                : current,
            );
            setSessions((current) => upsertSession(current, syncData.session as SessionSummary));
          }
          if (syncData.snapshot) {
            setStatus(syncData.snapshot);
          }
          if (syncData.alerts) {
            setAlerts(syncData.alerts);
          }
          return;
        }

        if (payload.event === "alert") {
          const alert = (payload.data.alert || {}) as AlertItem;
          if (alert.alert_id || alert.message) {
            setAlerts((current) => [alert, ...current.filter((item) => item.alert_id !== alert.alert_id)].slice(0, 8));
          }
        }

        if (
          payload.event.startsWith("task.") ||
          payload.event.startsWith("approval.") ||
          payload.event === "session.updated" ||
          payload.event === "browser.workflow" ||
          payload.event === "stream.reset"
        ) {
          scheduleConversationRefresh(sessionId);
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

  const pendingApproval = sessionDetail?.pending_approval?.kind
    ? sessionDetail.pending_approval
    : sessionDetail?.operator?.pending_approval?.kind
      ? sessionDetail.operator.pending_approval
      : status?.pending_approval || null;
  const runtimeModel = status?.runtime?.active_model || "";
  const runtimeEffort = status?.runtime?.reasoning_effort || "";
  const runtimeEffortLabel =
    runtimeEffort && status?.runtime?.reasoning_effort_applies_to_tool_calls === false
      ? `${runtimeEffort} (chat/final)`
      : runtimeEffort;
  const activeTask = sessionDetail?.operator?.active_task || status?.active_task || null;
  const selectedDesktopEvidence = status?.desktop?.selected_evidence || null;
  const checkpointDesktopEvidence = status?.desktop?.checkpoint_evidence || null;
  const pendingApprovalEvidence = pendingApproval?.evidence_preview || checkpointDesktopEvidence || null;
  const distinctCheckpointEvidence =
    checkpointDesktopEvidence?.evidence_id && checkpointDesktopEvidence.evidence_id === selectedDesktopEvidence?.evidence_id
      ? null
      : checkpointDesktopEvidence;
  const title = sessionDetail?.title || "New conversation";
  const emptyState = !messages.length && !loadingConversation;
  const composerHint =
    sending
      ? "Sending your message to the operator..."
      : pendingApproval?.kind
        ? "Approval is waiting on the right rail. You can still add context here."
        : "Enter to send. Shift+Enter for a newline.";
  const composerPlaceholder =
    bootState !== "ready"
      ? "Connecting to the operator..."
      : pendingApproval?.kind
        ? "Add context or resolve the approval..."
        : "Message the operator...";

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
      setMessages(normalizeMessages(nextSession.messages || []));
      startTransition(() => setSelectedSessionId(nextSession.session_id));
      clearDraft(nextSession.session_id);
      setActivity([]);
      shouldStickToBottomRef.current = true;
      window.requestAnimationFrame(() => textareaRef.current?.focus());
    } finally {
      setSending(false);
    }
  }

  async function handleSendMessage() {
    const content = draft.trim();
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
        setSessionDetail(result.session);
        setMessages(normalizeMessages(result.session.messages || []));
        setSessions((current) => upsertSession(current, result.session as SessionSummary));
      }
      if (nextSessionId && nextSessionId !== selectedSessionId) {
        startTransition(() => setSelectedSessionId(nextSessionId));
      }
      if (nextSessionId) {
        if (currentDraftKey === NEW_SESSION_DRAFT_KEY) {
          clearDraft("");
        }
        scheduleConversationRefresh(nextSessionId);
      } else {
        await refreshSidebar();
      }
      window.requestAnimationFrame(() => textareaRef.current?.focus());
    } finally {
      setSending(false);
    }
  }

  async function handleApproval(action: "approve" | "reject") {
    if (!apiBaseUrl || !pendingApproval?.kind) {
      return;
    }
    setApproving(action);
    try {
      const result = action === "approve" ? await approvePending(apiBaseUrl, selectedSessionId) : await rejectPending(apiBaseUrl, selectedSessionId);
      if (result.session) {
        setSessionDetail(result.session);
        setMessages(normalizeMessages(result.session.messages || []));
        setSessions((current) => upsertSession(current, result.session as SessionSummary));
      }
      if (result.status) {
        setStatus(result.status);
      }
      scheduleConversationRefresh(selectedSessionId);
    } finally {
      setApproving("");
    }
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
      error: "",
    });
    try {
      const payload = await getDesktopEvidenceArtifact(apiBaseUrl, preview.evidence_id);
      setArtifactViewer({
        open: true,
        loading: false,
        requestedEvidenceId: preview.evidence_id,
        sourceLabel,
        heading: evidenceSummaryText(preview),
        artifact: payload.artifact || null,
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
      error: "",
    });
  }

  async function handleRefresh() {
    if (bootState !== "ready") {
      setBootstrapTick((current) => current + 1);
      return;
    }
    const resolved = await refreshSidebar(selectedSessionId);
    if (resolved) {
      await loadConversation(resolved);
    }
    if (detailsOpen) {
      await refreshControlData(resolved);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div>
            <div className="eyebrow">AI Operator</div>
            <h1>Chat-first local operator</h1>
          </div>
          <div className="sidebar-header-actions">
            <button
              className="ghost-button theme-toggle"
              onClick={() => setThemeMode((current) => (current === "light" ? "dark" : "light"))}
              type="button"
            >
              {themeMode === "light" ? "Dark mode" : "Light mode"}
            </button>
            <button className="ghost-button" onClick={() => void handleNewChat()} disabled={sending}>
              New chat
            </button>
          </div>
        </div>

        <label className="search-box">
          <span>Search conversations</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter by title, summary, status..."
          />
        </label>

        <div className="session-list">
          {filteredSessions.map((session) => (
            <button
              key={session.session_id}
              className={clsx("session-card", session.session_id === selectedSessionId && "is-selected")}
              onClick={() => startTransition(() => setSelectedSessionId(session.session_id))}
            >
              <div className="session-card-header">
                <span className="session-title">{session.title || "Untitled conversation"}</span>
                <span className={clsx("status-pill", `tone-${statusTone(session.status)}`)}>{session.status || "idle"}</span>
              </div>
              <p className="session-preview">{sessionPreviewText(session)}</p>
              <div className="session-card-footer">
                <span>{session.message_count || 0} messages</span>
                {session.pending_approval?.kind ? <span className="approval-dot">Approval needed</span> : <span>{formatDateTime(session.updated_at)}</span>}
              </div>
            </button>
          ))}
          {!filteredSessions.length ? <div className="empty-sidebar">No conversations match this filter.</div> : null}
        </div>
      </aside>

      <main className="chat-layout">
        <header className="chat-header">
          <div>
            <div className="chat-title-row">
              <h2>{title}</h2>
              <span className={clsx("status-pill", `tone-${statusTone(sessionDetail?.status || status?.status)}`)}>
                {sessionDetail?.status || status?.status || "idle"}
              </span>
              {desktopRuntimeLabel(desktopRuntimeStatus) ? <span className="meta-pill">{desktopRuntimeLabel(desktopRuntimeStatus)}</span> : null}
              {apiManagedByDesktop && !desktopRuntimeLabel(desktopRuntimeStatus) ? <span className="meta-pill">Desktop-managed API</span> : null}
              {runtimeModel ? <span className="meta-pill">Model: {runtimeModel}</span> : null}
              {runtimeEffortLabel ? <span className="meta-pill">Reasoning: {runtimeEffortLabel}</span> : null}
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
          <div className="chat-header-actions">
            <span className={clsx("connection-pill", `stream-${streamState}`)}>{streamNote}</span>
            <span className="meta-pill">Theme: {themeMode}</span>
            <button className="ghost-button" onClick={() => setDetailsOpen((current) => !current)}>
              {detailsOpen ? "Hide details" : "Show details"}
            </button>
            <button className="ghost-button" onClick={() => void handleRefresh()}>
              {bootState === "ready" ? "Refresh" : "Retry"}
            </button>
          </div>
        </header>

        <section className="conversation-frame">
          {bootState === "booting" ? (
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
          ) : loadingConversation ? (
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
              {messages.map((message, index) => (
                <MessageBubble
                  key={message.message_id || `${message.created_at || index}:${message.role || "assistant"}:${message.kind || "message"}`}
                  message={message}
                />
              ))}
            </div>
          )}
          {!emptyState && !loadingConversation && !isNearTranscriptBottom ? (
            <button className="jump-to-latest" onClick={() => scrollTranscriptToLatest()} type="button">
              {pendingNewMessageCount > 0 ? `Jump to latest (${pendingNewMessageCount})` : "Jump to latest"}
            </button>
          ) : null}
        </section>

        <footer className="composer-shell">
          <textarea
            ref={textareaRef}
            value={draft}
            disabled={bootState !== "ready" || sending}
            onChange={(event) => updateDraft(event.target.value, selectedSessionId)}
            placeholder={composerPlaceholder}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSendMessage();
              }
            }}
            rows={1}
          />
          <div className="composer-footer">
            <span className="composer-hint">{composerHint}</span>
            <button className="send-button" onClick={() => void handleSendMessage()} disabled={bootState !== "ready" || sending || !draft.trim()}>
              {sending ? "Sending..." : bootState !== "ready" ? "Unavailable" : "Send"}
            </button>
          </div>
        </footer>
      </main>

      <aside className="right-rail">
        <section className="rail-card">
          <div className="rail-card-header">
            <h3>Approval</h3>
            {pendingApproval?.kind ? <span className="approval-dot">Needed</span> : <span className="muted-label">Clear</span>}
          </div>
          {pendingApproval?.kind ? (
            <>
              <p className="approval-kind">{plainTextPreview(pendingApproval.kind, 80)}</p>
              <p className="approval-detail">{plainTextPreview(approvalSummary(pendingApproval), 180)}</p>
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
            <h3>Active task</h3>
            <span className={clsx("status-pill", `tone-${statusTone(activeTask?.status || status?.status)}`)}>
              {activeTask?.status || status?.status || "idle"}
            </span>
          </div>
          <div className="stat-stack">
            <div>
              <span className="stat-label">Current step</span>
              <p>{plainTextPreview(status?.current_step || activeTask?.last_message || "Waiting for your next request.", 140)}</p>
            </div>
            <div>
              <span className="stat-label">Workflow</span>
              <p>{plainTextPreview(status?.browser?.workflow_name || status?.browser?.task_name || "No browser workflow active.", 120)}</p>
            </div>
            <div>
              <span className="stat-label">Page</span>
              <p>{plainTextPreview(status?.browser?.current_title || status?.browser?.current_url || "No live browser page.", 140)}</p>
            </div>
          </div>
          <div className="rail-evidence-stack">
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
          </div>
        </section>

        <section className="rail-card">
          <div className="rail-card-header">
            <h3>Recent alerts</h3>
            <span className="muted-label">{alerts.length}</span>
          </div>
          <div className="mini-list">
            {alerts.slice(0, 4).map((alert) => (
              <article key={alert.alert_id || `${alert.created_at}:${alert.title}`} className={clsx("mini-list-item", `tone-${statusTone(alert.severity)}`)}>
                <div className="mini-list-title">{plainTextPreview(alert.title || alert.type || "Alert", 52)}</div>
                <div className="mini-list-detail">{plainTextPreview(alert.message || "Operator alert", 120)}</div>
              </article>
            ))}
            {!alerts.length ? <p className="secondary-copy">No recent alerts for this conversation.</p> : null}
          </div>
        </section>

        <section className="rail-card rail-card-grow">
          <div className="rail-card-header">
            <h3>Live activity</h3>
            <span className="muted-label">{activity.length}</span>
          </div>
          <div className="mini-list">
            {activity.slice(0, 8).map((entry) => (
              <article key={entry.id} className={clsx("mini-list-item", `tone-${entry.tone}`)}>
                <div className="mini-list-title">
                  {plainTextPreview(entry.label, 48)}
                  <span className="mini-list-time">{formatTime(entry.timestamp)}</span>
                </div>
                <div className="mini-list-detail">{plainTextPreview(entry.detail, 140)}</div>
              </article>
            ))}
            {!activity.length ? <p className="secondary-copy">Live operator activity will appear here.</p> : null}
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
                  <h4>Desktop evidence</h4>
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
            </div>
          </section>
        </div>
      ) : null}

      {artifactViewer.open ? (
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
              ) : artifactViewer.artifact?.artifact_available && artifactViewer.artifact?.can_preview && artifactViewer.artifact?.evidence_id ? (
                <div className="artifact-preview-shell">
                  <img
                    alt={artifactViewer.heading || artifactViewer.artifact.summary || "Desktop evidence artifact"}
                    className="artifact-preview-image"
                    src={getDesktopEvidenceArtifactContentUrl(apiBaseUrl, artifactViewer.artifact.evidence_id)}
                  />
                </div>
              ) : (
                <div className="artifact-viewer-empty">
                  <h4>Artifact unavailable</h4>
                  <p>{artifactStateMessage(artifactViewer.artifact)}</p>
                </div>
              )}
            </div>

            <footer className="artifact-viewer-footer">
              {artifactViewer.artifact?.evidence_id ? <span className="evidence-reference">Ref {artifactViewer.artifact.evidence_id}</span> : null}
              {artifactViewer.artifact?.artifact_name ? <span className="evidence-chip evidence-chip-soft">{artifactViewer.artifact.artifact_name}</span> : null}
              {artifactViewer.artifact?.availability_state ? <span className="evidence-chip">{artifactViewer.artifact.availability_state}</span> : null}
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}
