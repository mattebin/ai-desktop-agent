import Fuse from "fuse.js";

const SEPARATORS = /[:_-]/g;

export type PromptSlashCommand = {
  type: "prompt";
  name: string;
  description: string;
  aliases?: string[];
  argumentHint?: string;
  category?: string;
  source?: string;
  promptText?: string;
  skillSlug?: string;
  relativePath?: string;
  buildPrompt?: (args: string) => string;
};

export type LocalSlashCommand = {
  type: "local";
  name: string;
  description: string;
  aliases?: string[];
  argumentHint?: string;
  category?: string;
  source?: string;
  action:
    | "approve"
    | "help"
    | "new-chat"
    | "refresh"
    | "reject"
    | "show-extensions"
    | "show-runtime"
    | "show-skills"
    | "show-tools"
    | "toggle-details"
    | "toggle-theme";
};

export type SlashCommand = PromptSlashCommand | LocalSlashCommand;

type CommandSearchItem = {
  command: SlashCommand;
  commandName: string;
  aliasKey?: string[];
  descriptionKey: string[];
  partKey?: string[];
};

export type SlashCommandSuggestion = {
  command: SlashCommand;
  fullCommand: string;
};

export type ParsedSlashCommand = {
  raw: string;
  query: string;
  args: string;
};

const DEFAULT_PROMPTS = {
  architecture: "Inspect this project and explain the main architecture.",
  compareLoop: "Compare the main loop and agent files and summarize the differences.",
  operatorState: "Suggest exact read-only commands to inspect the operator state.",
};

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    type: "local",
    name: "new",
    aliases: ["new-chat"],
    description: "Create a new conversation and focus it immediately.",
    action: "new-chat",
    category: "session",
    source: "built_in",
  },
  {
    type: "local",
    name: "refresh",
    aliases: ["reload"],
    description: "Refresh conversations, status, and the current operator view.",
    action: "refresh",
    category: "session",
    source: "built_in",
  },
  {
    type: "local",
    name: "details",
    description: "Show, hide, or toggle the right-side operator details rail.",
    argumentHint: "[show|hide|toggle]",
    action: "toggle-details",
    category: "view",
    source: "built_in",
  },
  {
    type: "local",
    name: "theme",
    description: "Switch the desktop UI theme.",
    argumentHint: "[light|dark|toggle]",
    action: "toggle-theme",
    category: "view",
    source: "built_in",
  },
  {
    type: "local",
    name: "approve",
    description: "Approve the current blocked step if an approval is waiting.",
    action: "approve",
    category: "approval",
    source: "built_in",
  },
  {
    type: "local",
    name: "reject",
    aliases: ["deny"],
    description: "Reject the current blocked step if an approval is waiting.",
    action: "reject",
    category: "approval",
    source: "built_in",
  },
  {
    type: "local",
    name: "skills",
    description: "Show the repo-local skills and their slash aliases.",
    action: "show-skills",
    category: "catalog",
    source: "built_in",
  },
  {
    type: "local",
    name: "runtime",
    description: "Show the active runtime model, effort, and merged config sources.",
    action: "show-runtime",
    category: "catalog",
    source: "built_in",
  },
  {
    type: "local",
    name: "tools",
    description: "Show the registered tools and their approval policy levels.",
    action: "show-tools",
    category: "catalog",
    source: "built_in",
  },
  {
    type: "local",
    name: "extensions",
    aliases: ["plugins"],
    description: "Show the local extension manifests and the commands they add.",
    action: "show-extensions",
    category: "catalog",
    source: "built_in",
  },
  {
    type: "local",
    name: "help",
    aliases: ["commands"],
    description: "Show a quick summary of the available slash commands.",
    action: "help",
    category: "catalog",
    source: "built_in",
  },
  {
    type: "prompt",
    name: "architecture",
    aliases: ["arch"],
    description: "Ask the operator for a high-level architecture walkthrough.",
    promptText: DEFAULT_PROMPTS.architecture,
    category: "inspection",
    source: "built_in",
    buildPrompt: () => DEFAULT_PROMPTS.architecture,
  },
  {
    type: "prompt",
    name: "compare-loop",
    aliases: ["compare"],
    description: "Ask the operator to compare the loop and agent implementation.",
    promptText: DEFAULT_PROMPTS.compareLoop,
    category: "inspection",
    source: "built_in",
    buildPrompt: () => DEFAULT_PROMPTS.compareLoop,
  },
  {
    type: "prompt",
    name: "operator-state",
    aliases: ["state"],
    description: "Ask for concrete read-only commands to inspect runtime state.",
    promptText: DEFAULT_PROMPTS.operatorState,
    category: "inspection",
    source: "built_in",
    buildPrompt: () => DEFAULT_PROMPTS.operatorState,
  },
];

let fuseCache:
  | {
      commands: SlashCommand[];
      fuse: Fuse<CommandSearchItem>;
    }
  | null = null;

function cleanWord(word: string): string {
  return word.toLowerCase().replace(/[^a-z0-9:_-]/g, "");
}

function getCommandFuse(commands: SlashCommand[]): Fuse<CommandSearchItem> {
  if (fuseCache?.commands === commands) {
    return fuseCache.fuse;
  }

  const commandData: CommandSearchItem[] = commands.map((command) => {
    const parts = command.name.split(SEPARATORS).filter(Boolean);
    return {
      command,
      commandName: command.name,
      aliasKey: command.aliases,
      descriptionKey: command.description
        .split(" ")
        .map((word) => cleanWord(word))
        .filter(Boolean),
      partKey: parts.length > 1 ? parts : undefined,
    };
  });

  const fuse = new Fuse(commandData, {
    includeScore: true,
    threshold: 0.34,
    location: 0,
    distance: 100,
    keys: [
      { name: "commandName", weight: 3 },
      { name: "partKey", weight: 2 },
      { name: "aliasKey", weight: 2 },
      { name: "descriptionKey", weight: 0.5 },
    ],
  });

  fuseCache = { commands, fuse };
  return fuse;
}

export function parseSlashCommandInput(input: string): ParsedSlashCommand | null {
  if (!input.startsWith("/")) {
    return null;
  }
  const raw = input.slice(1);
  const trimmed = raw.trim();
  if (!trimmed) {
    return { raw, query: "", args: "" };
  }
  const firstSpace = raw.indexOf(" ");
  if (firstSpace === -1) {
    return {
      raw,
      query: raw.trim(),
      args: "",
    };
  }
  return {
    raw,
    query: raw.slice(0, firstSpace).trim(),
    args: raw.slice(firstSpace + 1).trim(),
  };
}

export function getSlashCommandSuggestions(
  input: string,
  commands: SlashCommand[] = SLASH_COMMANDS,
): SlashCommandSuggestion[] {
  const parsed = parseSlashCommandInput(input);
  if (!parsed) {
    return [];
  }

  const query = parsed.query.trim().toLowerCase();
  if (!query) {
    return commands.slice(0, 8).map((command) => ({
      command,
      fullCommand: command.name,
    }));
  }

  const fuse = getCommandFuse(commands);
  return fuse.search(query).slice(0, 8).map((result) => ({
    command: result.item.command,
    fullCommand: result.item.commandName,
  }));
}

export function findSlashCommand(
  query: string,
  commands: SlashCommand[] = SLASH_COMMANDS,
): SlashCommand | null {
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    return null;
  }
  for (const command of commands) {
    if (command.name.toLowerCase() === normalized) {
      return command;
    }
    if (command.aliases?.some((alias) => alias.toLowerCase() === normalized)) {
      return command;
    }
  }
  return null;
}

export function applySlashCommandSuggestion(input: string, command: SlashCommand): string {
  const parsed = parseSlashCommandInput(input);
  const args = parsed?.args || "";
  if (args) {
    return `/${command.name} ${args}`;
  }
  return command.argumentHint ? `/${command.name} ` : `/${command.name}`;
}

export function resolvePromptSlashCommand(command: PromptSlashCommand, args: string): string {
  const trimmedArgs = args.trim();
  const basePrompt =
    typeof command.buildPrompt === "function"
      ? command.buildPrompt(trimmedArgs)
      : String(command.promptText || "").trim();
  if (!basePrompt) {
    return "";
  }
  if (!trimmedArgs) {
    return basePrompt;
  }
  return `${basePrompt}\n\nAdditional context: ${trimmedArgs}`;
}

export function slashCommandHelpText(commands: SlashCommand[] = SLASH_COMMANDS): string {
  return commands
    .map((command) => {
      const hint = command.argumentHint ? ` ${command.argumentHint}` : "";
      return `/${command.name}${hint} - ${command.description}`;
    })
    .join("\n");
}
