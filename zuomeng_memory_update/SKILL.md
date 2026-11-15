---
name: zuomeng_memory_update
description: Dreaming + memory update (once a week). Reads your main project's recent sessions and finds mistakes that show up again and again across different sessions. Also checks memory for entries that are out of date, wrong, duplicated or contradict each other. Both lists come with evidence, and nothing in memory changes until the user approves each item. Based on Anthropic's Dreams design. Only use when the user types /zuomeng_memory_update.
disable-model-invocation: true
---

# zuomeng_memory_update (dreaming + memory update)

<!-- "zuomeng" (做梦) is Chinese for "dreaming". See "Sources" at the end for which parts come from Anthropic and which were added. -->

**In one line: once a week, look back at the user's past sessions, find the mistakes that keep happening, and clean out memory that's stale or wrong.** Every change goes into a list first. Nothing is written until the user approves it.

Why: an agent in one session can't see other sessions, so the same mistake comes back in a fresh context. Memory is written by the agent bit by bit, and over time it collects duplicates, contradictions and stale entries. MEMORY.md is loaded at the start of every session (only the first 200 lines or 25KB). Anthropic's Dreams was designed for exactly these two problems. This skill does the same thing locally.

## Scope

- By default it only reviews one main project. Replace `<main-project-dir>` below with the folder name of your most used project under `~/.claude/projects/` (for example `-Users-you-code-myapp`). Its memory is in `~/.claude/projects/<main-project-dir>/memory/`.
- Other projects are added only if the user says yes (step 1 asks).

Reply in the user's language. Don't use em-dashes.

## Steps

The script is `~/.claude/skills/zuomeng_memory_update/scripts/zuomeng.py`. Project folder names start with `-`, so always write the argument as `--project=<dir>`.

All data (last review position, snapshots, reports, change log) lives in `~/.claude/zuomeng/`.

### 0. Guidance block (the user can edit this part)

**What counts:**
- The user corrects the agent: "no", "don't do that", "what I meant was", "you did it again"
- The user asks for the same thing more than once (so memory or CLAUDE.md is either missing it or it isn't working)
- A tool fails again and again, gets rejected by the user, or gets blocked by a hook
- The agent breaks a rule that's already in memory or CLAUDE.md
- The agent keeps looking up the same thing it should already know

**Doesn't count:** how hard a one-off task was, normal new requests from the user, normal exploring and trial and error.

### 1. Count first (decide whether to run, and on which projects)

```bash
python3 -I ~/.claude/skills/zuomeng_memory_update/scripts/zuomeng.py scan
```

- Fewer than 5 new sessions in the main project: tell the user "only N new sessions this week, skipping by the rule". Do **not** record a review position. Stop.
- Another project has 5 or more new sessions: list them to the user in plain text and ask if they should be included this time. Don't use a multi-select box (easy to misclick). For each project, give the real folder name and the session count. Non-ASCII characters in the folder name turn into a row of `-`, so use `ls` to find the real name. If the user says yes, that project goes through the same steps, and its list is labeled with the project name. Don't mention projects under the threshold.
- The script already skips any session written to in the last 5 minutes (that's the current one).

### 2. Compress the transcripts

```bash
python3 -I ~/.claude/skills/zuomeng_memory_update/scripts/zuomeng.py digest --project=<dir> --out <scratchpad>/zm_<date>
```

The script only reads sessions newer than the last review position (the first time, the last 7 days). Each one becomes a short text file: the user's own words (not cut, so corrections and repeated requests are all there), the assistant's words (shortened), which tools were called, and tool errors and rejections. **Secrets are masked by the script.** Don't quote any credentials in the report either. Transcripts are stored as plain text, so any secret a tool read ends up in there.

It also writes `batches.json` to the output folder, which splits the sessions into batches by size.

### 3. Part A: find mistakes that repeat across sessions

The main session is the orchestrator. Send one subagent per batch (in parallel, with the Agent tool). Don't read all the digests yourself, they won't fit in context. The task for each subagent:

> You're helping the user review their conversations with Claude Code. Below are compressed records of a batch of sessions (U = the user's own words, A = assistant, T = tool call, X = tool error or rejection). First read `~/.claude/projects/<dir>/memory/MEMORY.md` and `~/.claude/CLAUDE.md` so you know which rules already exist. Then read this batch and find the cases listed under "What counts" in the guidance block. Output each candidate in this format, one per line, nothing else:
> `type | one sentence describing the mistake | first 8 chars of session id | quote as evidence (60 chars max, no secrets) | severity high/medium/low | existing rule? (yes: which one / no)`
> type must be one of: correction, repeated request, tool failure, broke existing rule, kept looking up the same thing.
> severity: high = the user had to redo work or something nearly went wrong; medium = wasted a round of conversation; low = small friction.
> Only report what you actually see evidence for in the records. If there's nothing, output "nothing in this batch". Don't guess, don't pad.

As subagents report back one by one, reply to the user with only "got N/total" each time. Don't repeat the content. Merge once all are in.

When all are in, merge:
1. First pass: group candidates that describe the same thing.
2. Second pass: go through the ungrouped candidates one by one again and group them by what the agent actually did, not by whether the words look alike. Example: "the user asked to open it on the GitHub page, the agent pasted the text instead" and "the user asked to use Chrome, the agent installed a CLI first" belong to the same group: "the way the user asked for got swapped".
3. **Keep only groups that show up in at least 2 different sessions.** Many times in one session still counts as one session, because that's more likely a problem with that one task. List every group over the threshold, no cap. Start the list with "N groups over the threshold".
4. Check for existing fixes: read MEMORY.md and CLAUDE.md. If the user has already settled on a fix for this kind of problem (example: answers too long, and the user cuts them by hand with /jingjian), put it at the end of the list under "listed only, no change". Report the count, don't propose a change.
5. Order: score = number of sessions × severity (high 3, medium 2, low 1). Put the score next to the count as a reference. Order the list by your recommendation: change > could go either way > don't recommend. Mark each one with your recommendation and one line of reason. The user wants to know how much you think it's worth changing, so a high score that isn't worth changing goes lower.
6. Write each group as one proposal: the pattern in one sentence, how many times and in how many sessions, two examples (first 8 chars of session id plus a short quote), which kind of cause (not in memory / in memory but not working / skill is unclear), how to fix it and the one or two sentences you'd write. **Every proposal must say what kind of place and which file**: global CLAUDE.md (which section), memory (which file, full name, plus one line on what that memory is for), or a skill (which SKILL.md). "Add a self-check" without a location is something the user can't act on. Things that could come up in any project go in CLAUDE.md. Things tied to one task or one course go in memory (example: "ask before opening a payment page" can come up in other projects, so it goes in CLAUDE.md). Don't decide for the user.
7. Read the top week of `~/.claude/zuomeng/changelog.md` (if it exists). Rules changed last time that are still being broken this week get their own flag. This is how you check whether this skill is working.

### 4. Part B: check memory

Read it yourself (no subagents, memory files are small): MEMORY.md plus every file in the memory folder. Use the system date for today. Check:

- **Size:** how many lines and bytes MEMORY.md has, compared to the load limit of the first 200 lines or 25KB.
- **Stale:** a deadline or date in the description has already passed, but the entry still says to-do / in progress / open.
- **Facts:** whether the file paths, skills, commands and repos an entry mentions still exist (check with ls and grep, not from memory).
- **Duplicates:** two entries say the same thing.
- **Contradictions:** two entries reach opposite conclusions, or one goes against something the user said in a recent transcript.
- **Closed:** entries marked closed / bought / done. Are they still worth a line in the index?
- **Index mismatch:** a link in MEMORY.md points to a file that doesn't exist, or the description doesn't match the file's own description. Skip files that the latest `~/.claude/zuomeng/reports/<date>.md` says were left out of the index on purpose. Don't propose adding them back.

Note: feedback entries (the user's preferences and working rules) and user entries don't expire just because they're old. Only touch them when something newer overrides them. Time-sensitive project and reference entries are the ones to check for staleness. **Don't propose deleting a file** unless you can say what the file is and what's lost by deleting it. If it has value, ask the user first. Prefer "merge", "update one line" or "remove from index but keep the file".

Each proposal says: which file, what it says now, what the problem is (with evidence), what to change it to.

### 5. Show the user, approve one by one

Number the two lists (A1, A2... and B1, B2...). Each item has evidence and says two things in plain words: what goes wrong now, and what happens after the change. Write out the full sentence you'd add, so the user can decide without asking. For technical problems (shell errors, tool failures), first say in one line how it affects the user. Use tables when you can. The user can just reply "A1 A3 B2 yes, the rest no". **Nothing that isn't approved gets changed.**

End the list with one separate line: the analysis prompt (the one given to subagents in step 3) isn't Anthropic's, so if the findings look off, suspect it first.

### 6. Write (approved items only)

1. Snapshot first: `python3 -I ~/.claude/skills/zuomeng_memory_update/scripts/zuomeng.py snapshot --project=<dir>`, and put the rollback command it prints into the report. The snapshot sits outside the memory folder, so it won't be loaded as memory.
2. Edit the memory files for the approved items, and update the matching lines in MEMORY.md. One at a time. When done, check MEMORY.md's line count and size again.
3. Changes to CLAUDE.md or other skills: only when the user clearly approved that item.
4. Write the report to `~/.claude/zuomeng/reports/<date>.md`: what was approved, what was rejected, where the snapshot is, how many times each pattern showed up.
5. Add a section to the top of `~/.claude/zuomeng/changelog.md` (create it if it doesn't exist) for the Part A items the user decided to change. One section per week, with the date and how many sessions were read in the heading. Each item has exactly three points: **the mistake** (the pattern in one sentence, how many sessions, one example), **why it changed** (the user's own words in quotes; if the agent proposed it and the user only agreed, mark it "agent proposed"), **what it changed to** (which file and section, plus the new text). Part B isn't logged here. Next week, step 3 reads this file to compare.
6. Record the review position, using the "newest" time from the digest output: `python3 -I ~/.claude/skills/zuomeng_memory_update/scripts/zuomeng.py mark --project=<dir> --ts <that time>`. Do this even if the user rejected everything, or next week will read the same batch again.

### 7. Wrap up

Tell the user: how many sessions were read, how many items were proposed, how many were approved, where the snapshot is. If an Auto-dream switch ever shows up in the user's `/memory`, Part B will overlap with the built-in feature. Remind the user to decide then which one to keep.

## Sources: what comes from Anthropic, what was added

"Anthropic" here means the Dreams design (Lamis's talk, plus platform.claude.com/docs/en/managed-agents/dreams). Dreams is a Managed Agents API and can't read local files, so this skill rebuilds its approach locally.

| Design point | Source |
|---|---|
| Input is current memory plus a batch of past transcripts, including tool calls and metadata | Anthropic |
| An orchestrator sends a team of subagents to analyze | Anthropic (talk) |
| You can write guidance on what matters (step 0) | Anthropic |
| Only propose a change when a pattern shows up often enough | Anthropic (talk) |
| Every proposal comes with transcript examples and counts | Anthropic (talk) |
| Original memory isn't touched; it outputs proposals and a person decides | Anthropic |
| Merge duplicates, replace stale or contradicting entries with the latest, pull out new findings | Anthropic |
| Keep versions (snapshots) and limit write access (approve before writing) | Anthropic (versioning and permissioning in the talk) |
| Once a week; skip if fewer than 5 new sessions | Added (5 is the built-in auto-dream default) |
| "At least 2 different sessions to count as a pattern" | Added (Anthropic only says "common enough") |
| Batch sessions by size instead of one subagent per session | Added (a week can have 100+ sessions, one each costs too much) |
| Last review position; secrets masked by the script; skip the session in progress | Added |
| Ask about other projects once they hit 5 sessions | Added |
| Compare with last week's change log (changelog.md) to see if changed rules are still being broken | Added |
| Two-pass merge; existing fixes listed only; order by recommendation; every proposal names its file | Added |
| **The subagent analysis prompt (step 3) and the memory checks (step 4)** | **Added, Anthropic hasn't published theirs** |
