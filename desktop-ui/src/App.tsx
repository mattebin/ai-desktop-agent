import React, { createContext, startTransition, useContext, useDeferredValue, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import clsx from "clsx";
import {
  AlertItem,
  approvePending,
  BrowserState,
  createSession,
  DesktopTargetProposal,
  DesktopTargetProposalContext,
  DesktopRuntimeStatus,
  EvidenceArtifact,
  EvidenceSummary,
  ensureLocalApi,
  ExtensionSummary,
  executeSlashCommand,
  getExtensionCatalog,
  getAlerts,
  getDesktopEvidenceArtifact,
  getSkillCatalog,
  getSlashCommands,
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
  listSessions,
  openSessionEventStream,
  rejectPending,
  resolveDesktopEvidenceArtifactPreviewUrl,
  RunFocus,
  RunEntry,
  sendSessionMessage,
  SessionDetail,
  SessionMessage,
  SessionSummary,
  SkillSummary,
  StatusPayload,
  StreamEvent,
  ToolSummary,
  type PendingApproval,
  type QueuePayload,
  type ScheduledPayload,
  type WatchPayload,
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

function UiIcon({
  name,
  className,
}: {
  name:
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
    | "details";
  className?: string;
}) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className: clsx("ui-icon", className),
    "aria-hidden": true,
  };

  switch (name) {
    case "brand":
      return (
        <svg {...common}>
          <path d="M12 3 5 7v10l7 4 7-4V7l-7-4Z" />
          <path d="M8.5 10.5 12 8l3.5 2.5v3L12 16l-3.5-2.5v-3Z" />
        </svg>
      );
    case "menu":
      return (
        <svg {...common}>
          <path d="M5 7h14" />
          <path d="M5 12h14" />
          <path d="M5 17h14" />
        </svg>
      );
    case "refresh":
      return (
        <svg {...common}>
          <path d="M20 11a8 8 0 1 0 2 5.5" />
          <path d="M20 4v7h-7" />
        </svg>
      );
    case "theme-light":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2.5v2.5" />
          <path d="M12 19v2.5" />
          <path d="m4.9 4.9 1.8 1.8" />
          <path d="m17.3 17.3 1.8 1.8" />
          <path d="M2.5 12H5" />
          <path d="M19 12h2.5" />
          <path d="m4.9 19.1 1.8-1.8" />
          <path d="m17.3 6.7 1.8-1.8" />
        </svg>
      );
    case "theme-dark":
      return (
        <svg {...common}>
          <path d="M20 14.3A7.5 7.5 0 0 1 9.7 4 8.5 8.5 0 1 0 20 14.3Z" />
        </svg>
      );
    case "new":
      return (
        <svg {...common}>
          <path d="M12 5v14" />
          <path d="M5 12h14" />
        </svg>
      );
    case "chat":
      return (
        <svg {...common}>
          <path d="M5 6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v6A2.5 2.5 0 0 1 16.5 15H10l-4 4v-4H7.5A2.5 2.5 0 0 1 5 12.5v-6Z" />
        </svg>
      );
    case "approval":
      return (
        <svg {...common}>
          <path d="M12 3 6 5.8v5.4c0 4 2.6 7.7 6 9 3.4-1.3 6-5 6-9V5.8L12 3Z" />
          <path d="m9.5 12 1.8 1.8 3.2-3.6" />
        </svg>
      );
    case "task":
      return (
        <svg {...common}>
          <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" />
        </svg>
      );
    case "alert":
      return (
        <svg {...common}>
          <path d="M12 4a4 4 0 0 0-4 4v2.6c0 .7-.2 1.4-.6 2L6 15h12l-1.4-2.4a4 4 0 0 1-.6-2V8a4 4 0 0 0-4-4Z" />
          <path d="M10 18a2 2 0 0 0 4 0" />
        </svg>
      );
    case "activity":
      return (
        <svg {...common}>
          <path d="M3 12h4l2-5 4 10 2-5h6" />
        </svg>
      );
    case "evidence":
      return (
        <svg {...common}>
          <rect x="4" y="5" width="16" height="14" rx="2.5" />
          <path d="m8 14 2.5-2.5 2 2 2.5-3 3 3.5" />
          <circle cx="9" cy="9.5" r="1" />
        </svg>
      );
    case "details":
      return (
        <svg {...common}>
          <rect x="4" y="5" width="16" height="14" rx="2.5" />
          <path d="M10 9h6" />
          <path d="M10 12h6" />
          <path d="M10 15h4" />
          <path d="M7.5 9h.01" />
          <path d="M7.5 12h.01" />
          <path d="M7.5 15h.01" />
        </svg>
      );
  }
}

function SectionTitle({
  icon,
  children,
}: {
  icon: Parameters<typeof UiIcon>[0]["name"];
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
  icon: Parameters<typeof UiIcon>[0]["name"];
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
  icon: Parameters<typeof UiIcon>[0]["name"];
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
  const value = (service || {}) as { active?: string; reason?: string; message?: string };
  return String(value.active || value.reason || value.message || "unknown").trim() || "unknown";
}

function backendServiceTone(service: unknown): ActivityTone {
  const value = (service || {}) as { available?: boolean; active?: string; reason?: string };
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
  const value = (service || {}) as { message?: string; reason?: string };
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
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const [approving, setApproving] = useState<"" | "approve" | "reject">("");
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
    const [queue, scheduled, watches, runs, desktopEvidence, toolCatalog, extensionCatalog] = await Promise.all([
      getQueueState(apiBaseUrl),
      getScheduledState(apiBaseUrl),
      getWatchState(apiBaseUrl),
      getRecentRuns(apiBaseUrl, sessionId, 10),
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
    if (pending.shouldRefreshControls && detailsOpenRef.current) {
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
      if (options.includeControls || detailsOpenRef.current) {
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
    if (detailsOpen) {
      await refreshControlData(resolved);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
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
          <div className="sidebar-actions">
            <button className="sidebar-primary-button" onClick={() => void handleNewChat()} disabled={sending} type="button">
              <UiIcon name="new" />
              <span>New chat</span>
            </button>
            <CompactMenu label="Workspace" icon="menu">
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
        </div>

        <label className="search-box">
          <span className="search-box-label">
            <SectionTitle icon="chat">Conversations</SectionTitle>
          </span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter title, status, summary..."
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
                <span>{formatDateTime(session.updated_at) || "Just now"}</span>
                {session.pending_approval?.kind ? <span className="approval-dot">Needs approval</span> : <span>{session.message_count || 0} msgs</span>}
              </div>
            </button>
          ))}
          {!filteredSessions.length ? <div className="empty-sidebar">No conversations match this filter.</div> : null}
        </div>
      </aside>

      <main className="chat-layout">
        <header className="chat-header">
          <div className="chat-header-main">
            <div className="chat-title-row">
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
            <div className="chat-meta-row">
              {desktopRuntimeLabel(desktopRuntimeStatus) ? <span className="meta-pill">{desktopRuntimeLabel(desktopRuntimeStatus)}</span> : null}
              {apiManagedByDesktop && !desktopRuntimeLabel(desktopRuntimeStatus) ? <span className="meta-pill">Desktop-managed API</span> : null}
              {runPhase !== "idle" ? <span className="meta-pill">Phase {runPhase.replace(/_/g, " ")}</span> : null}
              {runFocusLocked ? <span className="meta-pill">Run focus locked</span> : null}
              {runtimeModel ? <span className="meta-pill">Model {runtimeModel}</span> : null}
              {runtimeEffortLabel ? <span className="meta-pill">Reasoning {runtimeEffortLabel}</span> : null}
              <span className={clsx("connection-pill", `stream-${streamState}`)}>{streamNote}</span>
            </div>
          </div>
          <div className="chat-header-actions">
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
              {messages.map((message) => (
                <MemoMessageBubble
                  key={messageStableKey(message)}
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
            <h3><SectionTitle icon="approval">Approval</SectionTitle></h3>
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
            <h3><SectionTitle icon="task">Active task</SectionTitle></h3>
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
          <div className="mini-list">
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
              </article>
            ))}
          </div>
        </section>

        <section className="rail-card">
          <div className="rail-card-header">
            <h3><SectionTitle icon="alert">Recent alerts</SectionTitle></h3>
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
            <h3><SectionTitle icon="activity">Live activity</SectionTitle></h3>
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
              </div>

              <footer className="artifact-viewer-footer">
                {artifactViewer.artifact?.evidence_id ? <span className="evidence-reference">Ref {artifactViewer.artifact.evidence_id}</span> : null}
                {artifactViewer.artifact?.artifact_name ? <span className="evidence-chip evidence-chip-soft">{artifactViewer.artifact.artifact_name}</span> : null}
                {artifactViewer.artifact?.artifact_type ? <span className="evidence-chip">{artifactViewer.artifact.artifact_type}</span> : null}
                {artifactViewer.artifact?.availability_state ? <span className="evidence-chip">{artifactViewer.artifact.availability_state}</span> : null}
              </footer>
            </section>
          </div>
        </OverlayPortal>
      ) : null}
    </div>
  );
}
