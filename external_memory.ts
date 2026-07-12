import { tool } from "@opencode-ai/plugin"
import path from "path"
import os from "node:os"
import { existsSync } from "fs"

// ---------------------------------------------------------------------------
// Helpers — mirror the playwright tool pattern
// ---------------------------------------------------------------------------

function findPython(): string {
    const candidates = [
        path.join(os.homedir(), ".config", "opencode", "tools", "venv", "bin", "python3"),
        path.join(os.homedir(), ".config", "opencode", "tools", "venv", "bin", "python"),
        "python3",
        "python",
    ]
    for (const p of candidates) {
        try {
            const proc = Bun.spawnSync([p, "--version"])
            if (proc.exitCode === 0) return p
        } catch (_) {}
    }
    return candidates[0]
}

function findScript(dir: string): string {
    const candidates = [
        path.join(os.homedir(), ".config", "opencode", "tools", "external_memory.py"),
        path.join(dir, "external_memory.py"),
        path.join(dir, ".opencode", "tools", "external_memory.py"),
    ]
    for (const p of candidates) {
        if (existsSync(p)) return p
    }
    return candidates[0]
}

async function callMemory(cmd: string, extra: Record<string, any> = {}, directory: string) {
    const python = findPython()
    const script = findScript(directory)
    const payload = JSON.stringify({ command: cmd, ...extra })

    let stdout = ""
    let stderr = ""
    let exitCode = 0

    try {
        const proc = Bun.spawn([python, script], {
            stdin: "pipe",
            stdout: "pipe",
            stderr: "pipe",
        })
        proc.stdin.write(payload)
        proc.stdin.end()

        exitCode = await proc.exited
        stdout = await new Response(proc.stdout).text()
        stderr = await new Response(proc.stderr).text()
    } catch (e: any) {
        return `external_memory_${cmd}: spawn failed: ${e.message || e}`
    }

    if (exitCode !== 0) {
        const detail = stderr.trim() || stdout.trim() || "(no output)"
        return `external_memory_${cmd}: exit ${exitCode}: ${detail}`
    }
    return stdout.trim()
}

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

export const save = tool({
    description:
        "Save a new entry to external memory. Stores topic, summary, full content, " +
        "and optional tags. Also generates an embedding vector for semantic search. " +
        "Returns the created entry with its ID.",
    args: {
        topic: tool.schema
            .string()
            .describe("Topic or category label for the entry (e.g. 'architecture', 'bug', 'decision')"),
        summary: tool.schema
            .string()
            .describe("Short description (1-2 sentences) for search result previews"),
        content: tool.schema
            .string()
            .describe("Full text content of the memory entry — this is what will be searched"),
        tags: tool.schema
            .array(tool.schema.string())
            .optional()
            .describe("Optional list of tags for categorization (e.g. ['python', 'sqlite'])"),
    },
    async execute(args, context) {
        return await callMemory("save", {
            topic: args.topic,
            summary: args.summary,
            content: args.content,
            tags: args.tags || [],
        }, context.directory)
    },
})

export const search = tool({
    description:
        "Search external memory entries. Supports three modes:\n" +
        "- 'text': full-text keyword search (fast, always available)\n" +
        "- 'semantic': embedding-based similarity search (fuzzy, meaning-aware)\n" +
        "- 'hybrid': combined text + semantic search with weighted ranking (default, recommended)\n" +
        "Returns matching entries with relevance scores.",
    args: {
        query: tool.schema
            .string()
            .describe("Search query — can be keywords, a phrase, or a conceptual description"),
        search_type: tool.schema
            .enum(["text", "semantic", "hybrid"])
            .optional()
            .describe("Search mode. 'hybrid' is recommended for most cases. Default: 'hybrid'"),
        limit: tool.schema
            .number()
            .int()
            .optional()
            .describe("Max results to return (1-100, default 10)"),
    },
    async execute(args, context) {
        return await callMemory("search", {
            query: args.query,
            search_type: args.search_type || "hybrid",
            limit: args.limit || 10,
        }, context.directory)
    },
})

export const get = tool({
    description:
        "Retrieve a full memory entry by its ID. Returns topic, summary, full content, " +
        "tags, and timestamps.",
    args: {
        id: tool.schema
            .number()
            .int()
            .describe("The entry ID (returned by external_memory_save or external_memory_search)"),
    },
    async execute(args, context) {
        return await callMemory("get", { id: args.id }, context.directory)
    },
})

export const update = tool({
    description:
        "Update an existing memory entry. Only the fields you provide will be changed — " +
        "omitted fields keep their current values. If 'content' is changed, the embedding " +
        "is regenerated automatically.",
    args: {
        id: tool.schema
            .number()
            .int()
            .describe("The entry ID to update"),
        topic: tool.schema
            .string()
            .optional()
            .describe("New topic (leave unset to keep current)"),
        summary: tool.schema
            .string()
            .optional()
            .describe("New summary (leave unset to keep current)"),
        content: tool.schema
            .string()
            .optional()
            .describe("New full content (leave unset to keep current)"),
        tags: tool.schema
            .array(tool.schema.string())
            .optional()
            .describe("New tags list (leave unset to keep current)"),
    },
    async execute(args, context) {
        return await callMemory("update", {
            id: args.id,
            topic: args.topic,
            summary: args.summary,
            content: args.content,
            tags: args.tags,
        }, context.directory)
    },
})

export const delete_ = tool({
    description:
        "Delete a memory entry permanently by its ID. Also removes its embedding vector. " +
        "This action cannot be undone.",
    args: {
        id: tool.schema
            .number()
            .int()
            .describe("The entry ID to delete"),
    },
    async execute(args, context) {
        return await callMemory("delete", { id: args.id }, context.directory)
    },
})

export const list = tool({
    description:
        "List memory entries, newest first. Useful for browsing all stored entries. " +
        "Supports pagination with limit and offset.",
    args: {
        limit: tool.schema
            .number()
            .int()
            .optional()
            .describe("Max entries to return (1-200, default 50)"),
        offset: tool.schema
            .number()
            .int()
            .optional()
            .describe("Number of entries to skip for pagination (default 0)"),
    },
    async execute(args, context) {
        return await callMemory("list", {
            limit: args.limit || 50,
            offset: args.offset || 0,
        }, context.directory)
    },
})

export const tags = tool({
    description:
        "List all unique tags currently used across all memory entries. " +
        "Useful for understanding what categories of information are stored.",
    args: {},
    async execute(args, context) {
        return await callMemory("tags", {}, context.directory)
    },
})

export const stats = tool({
    description:
        "Show memory store statistics: total entries, whether embeddings are enabled, " +
        "embedding dimension, and database path.",
    args: {},
    async execute(args, context) {
        return await callMemory("stats", {}, context.directory)
    },
})
