#!/usr/bin/env python3
"""The deterministic part of zuomeng: count sessions, compress transcripts into short text (with secrets masked),
split into batches, take snapshots, and record "reviewed up to here".
The judgment (which mistakes repeat, which memory is stale) isn't here. That's in SKILL.md, done by the agent.

Usage:
  python3 zuomeng.py scan                                  # new sessions per project, memory files changed
  python3 zuomeng.py digest --project=<dir> --out <out-dir> # compress new sessions + batches.json
  (project folder names start with -, so --project needs the = form, or argparse reads it as an option)
  python3 zuomeng.py snapshot --project=<dir>              # copy memory before writing to it
  python3 zuomeng.py mark --project=<dir> --ts <ISO time>  # after a review, record "reviewed up to here"

The transcript is Claude Code's internal format and can change between versions (per the official docs),
so lines it doesn't recognize are skipped, not treated as errors.
"""
import argparse, json, re, shutil, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path.home() / ".claude" / "projects"
DATA = Path.home() / ".claude" / "zuomeng"
STATE = DATA / "state.json"
DEFAULT_DAYS = 7          # first run (no review position yet): how many days back to look
ACTIVE_SECS = 300         # files written in the last 5 minutes count as the session in progress, skipped
MIN_SESSIONS = 5          # skip if fewer new sessions than this
BATCH_CHARS = 90_000      # roughly how many characters one subagent reads per batch

NOISE = re.compile(
    r"<(local-command-caveat|local-command-stdout|system-reminder|task-notification|command-message)>.*?</\1>",
    re.S,
)
# Masking: transcripts are plain text, and secrets that tools read get written in. Reports must not contain credentials.
SECRETS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[abpr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key|authorization)\"?\s*[:=]\s*\"?)[^\s\"',;]{6,}"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
]
ERR_HINT = re.compile(
    r"(?i)^(error|exit code [1-9]|traceback|fatal|permission denied|command not found)|"
    r"no such file|doesn't want to proceed|user rejected|was rejected|not been read yet|"
    r"file has been modified|string to replace not found|input validation|hook .*(blocked|denied)"
)


def redact(s):
    for p in SECRETS:
        s = p.sub(lambda m: (m.group(1) + "[REDACTED]") if p.groups else "[REDACTED]", s)
    return s


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"last_reviewed": {}}


def save_state(st):
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def since_ts(project, st):
    v = st.get("last_reviewed", {}).get(project)
    if v:
        try:
            return datetime.fromisoformat(v).timestamp()
        except Exception:
            pass
    return time.time() - DEFAULT_DAYS * 86400


def new_sessions(pdir, since):
    """Top-level *.jsonl files in a project folder are main sessions (subagent logs are in subfolders, not counted)."""
    now = time.time()
    out = []
    for f in pdir.glob("*.jsonl"):
        m = f.stat().st_mtime
        if m > since and now - m > ACTIVE_SECS:
            out.append((m, f))
    return sorted(out)


def cmd_scan(_a):
    st = load_state()
    week = time.time() - 7 * 86400
    rows = []
    for pdir in sorted(ROOT.iterdir()):
        if not pdir.is_dir() or "scratchpad" in pdir.name:  # scratchpad folders aren't real projects
            continue
        ns = new_sessions(pdir, since_ts(pdir.name, st))
        mem = pdir / "memory"
        files = [f for f in mem.glob("*") if f.is_file()] if mem.is_dir() else []
        changed = sum(1 for f in files if f.stat().st_mtime > week)
        if ns or files:
            rows.append((len(ns), pdir.name, len(files), changed, st.get("last_reviewed", {}).get(pdir.name, "never reviewed")))
    rows.sort(reverse=True)
    print(f"{'new':>5} | {'memory files':>12} | {'changed 7d':>10} | last review | project dir")
    for n, name, nf, ch, last in rows:
        flag = "  <= over threshold" if n >= MIN_SESSIONS else ""
        print(f"{n:>5} | {nf:>12} | {ch:>10} | {last} | {name}{flag}")
    print(f"\nThreshold: worth running at {MIN_SESSIONS}+ new sessions. Sessions written in the last {ACTIVE_SECS // 60} minutes are skipped.")


def text_of(content):
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "\n".join(parts)


def clean_user(t):
    name = re.search(r"<command-name>(.*?)</command-name>", t, re.S)
    args = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
    t = NOISE.sub("", t)
    t = re.sub(r"<command-(name|args)>.*?</command-\1>", "", t, flags=re.S).strip()
    if name:
        cmd = "/" + name.group(1).strip().lstrip("/")
        a = (args.group(1).strip() if args else "")
        t = (cmd + " " + a + " " + t).strip()
    return t


def brief_tool(b):
    name, inp = b.get("name", "?"), b.get("input") or {}
    if name == "Bash":
        detail = str(inp.get("command", ""))[:160]
    elif name in ("Read", "Edit", "Write"):
        detail = str(inp.get("file_path", ""))
    elif name == "Skill":
        detail = str(inp.get("skill", ""))
    elif name in ("Agent", "Task"):
        detail = str(inp.get("description", ""))
    else:
        detail = ",".join(list(inp.keys())[:4])
    return f"{name}({detail})"


def digest_file(f):
    title, first, last, cwd, nuser = "", "", "", "", 0
    lines = []
    for raw in f.open(errors="replace"):
        try:
            o = json.loads(raw)
        except Exception:
            continue
        t = o.get("type")
        ts = o.get("timestamp")
        if ts:
            first = first or ts
            last = ts
        if t == "ai-title":
            title = o.get("aiTitle", title)
            continue
        cwd = cwd or o.get("cwd", "")
        msg = o.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if t == "user":
            if o.get("isMeta"):
                continue
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    body = b.get("content")
                    body = text_of(body) if not isinstance(body, str) else body
                    body = (body or "").strip()
                    if b.get("is_error") is True or ERR_HINT.search(body[:300]):
                        lines.append("X: " + body[:220].replace("\n", " "))
                continue
            txt = clean_user(text_of(content))
            if txt:
                nuser += 1
                lines.append("U: " + (txt if len(txt) <= 1500 else txt[:1500] + " ...[cut]"))
        elif t == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    lines.append("A: " + b["text"].strip()[:300].replace("\n", " "))
                elif b.get("type") == "tool_use":
                    lines.append("T: " + brief_tool(b))
    body = "\n".join(lines)
    if len(body) > 120_000:  # too long: drop the assistant's words first, keep user words, tool calls and errors
        body = "\n".join(l for l in lines if not l.startswith("A: "))
    head = f"# session {f.stem} | {title or '(no title)'} | {first} -> {last} | cwd={cwd} | user messages {nuser}\n"
    return redact(head + body), nuser


def cmd_digest(a):
    pdir = ROOT / a.project
    st = load_state()
    since = since_ts(a.project, st) if not a.since else datetime.fromisoformat(a.since).timestamp()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sessions = []
    for m, f in new_sessions(pdir, since):
        text, nuser = digest_file(f)
        if nuser == 0:
            continue
        p = out / f"{f.stem}.txt"
        p.write_text(text)
        sessions.append({"id": f.stem, "mtime": iso(m), "chars": len(text), "file": str(p)})
    batches, cur, size = [], [], 0
    for s in sessions:
        if cur and size + s["chars"] > BATCH_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(s["file"])
        size += s["chars"]
    if cur:
        batches.append(cur)
    (out / "batches.json").write_text(json.dumps({"batches": batches, "sessions": sessions}, ensure_ascii=False, indent=1))
    newest = sessions[-1]["mtime"] if sessions else None
    print(f"project {a.project}: {len(sessions)} sessions with content, {len(batches)} batches, newest {newest}")
    print(f"batch list: {out / 'batches.json'}")
    if len(sessions) < MIN_SESSIONS:
        print(f"note: fewer than {MIN_SESSIONS} new sessions. By the rule, skip and tell the user.")


def cmd_snapshot(a):
    src = ROOT / a.project / "memory"
    if not src.is_dir():
        sys.exit(f"no memory folder: {src}")
    dst = DATA / "snapshots" / datetime.now().strftime("%Y%m%d-%H%M%S") / a.project
    shutil.copytree(src, dst / "memory")
    print(f"snapshot saved to {dst / 'memory'}")
    print(f"rollback: rm -r {src} && cp -R {dst / 'memory'} {src}")


def cmd_mark(a):
    st = load_state()
    st.setdefault("last_reviewed", {})[a.project] = a.ts
    save_state(st)
    print(f"recorded {a.project} reviewed up to {a.ts}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan").set_defaults(fn=cmd_scan)
    d = sub.add_parser("digest")
    d.add_argument("--project", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--since", default=None)
    d.set_defaults(fn=cmd_digest)
    s = sub.add_parser("snapshot")
    s.add_argument("--project", required=True)
    s.set_defaults(fn=cmd_snapshot)
    m = sub.add_parser("mark")
    m.add_argument("--project", required=True)
    m.add_argument("--ts", required=True)
    m.set_defaults(fn=cmd_mark)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
