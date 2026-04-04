import type { RuntimeConfig, SkillSummary } from "./api";
import {
  type LocalSlashCommand,
  type PromptSlashCommand,
  type SlashCommand,
  findSlashCommand,
  parseSlashCommandInput,
  resolvePromptSlashCommand,
  slashCommandHelpText,
} from "./slashCommands";

export type CommandPlanTone = "neutral" | "info" | "success" | "warning" | "error";

type SlashCommandPlannerOptions = {
  input: string;
  commands: SlashCommand[];
  activeSuggestion?: SlashCommand | null;
  skills?: SkillSummary[];
  runtime?: RuntimeConfig | null;
  pendingApprovalKind?: string;
};

export type SlashCommandPlan =
  | { kind: "none" }
  | {
      kind: "activity";
      title: string;
      detail: string;
      tone: CommandPlanTone;
      clearDraft?: boolean;
    }
  | {
      kind: "prompt";
      command: PromptSlashCommand;
      promptText: string;
      successMessage: string;
      clearDraft: true;
    }
  | {
      kind: "local";
      command: LocalSlashCommand;
      args: string;
      clearDraft: true;
    };

export function formatSkillCatalogDetail(skills: SkillSummary[] = []): string {
  if (!skills.length) {
    return "No repo-local skills are available right now.";
  }
  return skills
    .map((skill) => {
      const name = String(skill.commandName || skill.slug || skill.title || "skill").trim();
      const description = String(skill.description || skill.purpose || "Repo-local skill").trim();
      return `/${name} - ${description}`;
    })
    .join("\n");
}

export function formatRuntimeCatalogDetail(runtime?: RuntimeConfig | null): string {
  const model = String(runtime?.active_model || "unknown").trim();
  const effort = String(runtime?.reasoning_effort || "default").trim();
  const sources =
    Array.isArray(runtime?.settings_sources) && runtime?.settings_sources.length
      ? runtime.settings_sources.join("\n")
      : String(runtime?.source || "config/settings.yaml").trim();
  const version = String(runtime?.settings_version || "").trim();
  const loadedAt = String(runtime?.settings_loaded_at || "").trim();
  const reloadCount = Number(runtime?.settings_reload_count || 0);
  const hotReloadEnabled = Boolean(runtime?.settings_hot_reload?.enabled);
  const policySummary = String(runtime?.tool_policy?.summary || "").trim();
  const lines = [
    `Model: ${model}`,
    `Reasoning effort: ${effort}`,
    `Hot reload: ${hotReloadEnabled ? "enabled" : "disabled"}`,
    `Reload count: ${reloadCount || 1}`,
  ];
  if (version) {
    lines.push(`Settings version: ${version}`);
  }
  if (loadedAt) {
    lines.push(`Loaded at: ${loadedAt}`);
  }
  if (policySummary) {
    lines.push(`Policy: ${policySummary}`);
  }
  lines.push("Sources:");
  lines.push(sources || "config/settings.yaml");
  return lines.join("\n");
}

export function buildSlashCommandPlan(options: SlashCommandPlannerOptions): SlashCommandPlan {
  const parsed = parseSlashCommandInput(options.input);
  if (!parsed) {
    return { kind: "none" };
  }

  if (!parsed.query && !parsed.args) {
    return {
      kind: "activity",
      title: "Slash commands",
      detail: slashCommandHelpText(options.commands),
      tone: "info",
    };
  }

  const selectedCommand =
    findSlashCommand(parsed.query, options.commands) ||
    options.activeSuggestion ||
    null;

  if (!selectedCommand) {
    return {
      kind: "activity",
      title: "Unknown command",
      detail: parsed.query
        ? `No slash command matches /${parsed.query}.`
        : "Type a command name after /. For example: /new or /architecture.",
      tone: "warning",
    };
  }

  if (selectedCommand.type === "prompt") {
    const promptText = resolvePromptSlashCommand(selectedCommand, parsed.args);
    if (!promptText) {
      return {
        kind: "activity",
        title: "Command unavailable",
        detail: `/${selectedCommand.name} is missing prompt text.`,
        tone: "warning",
      };
    }
    return {
      kind: "prompt",
      command: selectedCommand,
      promptText,
      successMessage: `Sent the ${selectedCommand.name} prompt.`,
      clearDraft: true,
    };
  }

  if (selectedCommand.action === "help") {
    return {
      kind: "activity",
      title: "Slash commands",
      detail: slashCommandHelpText(options.commands),
      tone: "info",
      clearDraft: true,
    };
  }

  if (selectedCommand.action === "show-skills") {
    return {
      kind: "activity",
      title: "Available skills",
      detail: formatSkillCatalogDetail(options.skills || []),
      tone: "info",
      clearDraft: true,
    };
  }

  if (selectedCommand.action === "show-runtime") {
    return {
      kind: "activity",
      title: "Runtime config",
      detail: formatRuntimeCatalogDetail(options.runtime || null),
      tone: "info",
      clearDraft: true,
    };
  }

  if ((selectedCommand.action === "approve" || selectedCommand.action === "reject") && !options.pendingApprovalKind) {
    return {
      kind: "activity",
      title: "Command unavailable",
      detail: `/${selectedCommand.name} needs a pending approval, but nothing is blocked right now.`,
      tone: "warning",
    };
  }

  return {
    kind: "local",
    command: selectedCommand,
    args: parsed.args,
    clearDraft: true,
  };
}
