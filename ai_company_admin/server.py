from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


AGENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,40}$")


def env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def root_dir() -> Path:
    return env_path("AI_COMPANY_ROOT", "/root/AI--compony")


def state_dir() -> Path:
    return env_path("AI_COMPANY_STATE_DIR", str(root_dir() / "team-data" / "state"))


def config_file() -> Path:
    return env_path("AI_COMPANY_CONFIG", str(root_dir() / "team-data" / "claudeteam.toml"))


def compose_file() -> Path:
    return env_path("AI_COMPANY_COMPOSE", str(root_dir() / "deploy" / "server" / "compose.yaml"))


def docker_bin() -> str:
    return os.environ.get("AI_COMPANY_DOCKER_BIN", "/usr/bin/docker").strip() or "/usr/bin/docker"


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def validate_agent(name: str) -> str:
    name = str(name or "").strip()
    if not AGENT_RE.match(name):
        raise ValueError("agent name must start with a letter and use letters, numbers, _ or -")
    return name


def load_team() -> dict:
    cf = config_file()
    try:
        data = tomllib.loads(cf.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {"session": "AI-Company", "default_model": "", "agents": {}}
    team = data.get("team") or {}
    return {
        "session": team.get("session") or "AI-Company",
        "default_model": team.get("default_model", ""),
        "agents": dict(team.get("agents") or {}),
    }


def toml_escape(value: str) -> str:
    value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return value


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return f'"{toml_escape(str(value))}"'


def agent_block(name: str, cfg: dict) -> str:
    lines = [f"[team.agents.{name}]\n"]
    for key in ("cli", "model", "reasoning_effort", "role", "specialty", "playbook", "card_color", "tone", "notes", "lazy"):
        if key in cfg and cfg[key] not in ("", [], None):
            lines.append(f"{key} = {toml_value(cfg[key])}\n")
    return "".join(lines)


def agent_span(lines: list[str], name: str) -> tuple[int, int] | None:
    header = f"[team.agents.{name}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            end = i
            break
    return start, end


def remove_agent_block(text: str, name: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    span = agent_span(lines, name)
    if span is None:
        return text, False
    del lines[span[0]:span[1]]
    return "".join(lines), True


def insert_agent_block(text: str, block: str) -> str:
    if not block.endswith("\n"):
        block += "\n"
    lines = text.splitlines(keepends=True)
    last_end = None
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("[team.agents."):
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("["):
                j += 1
            last_end = j
            i = j
        else:
            i += 1
    if last_end is None:
        sep = "" if text == "" or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    insert = block if (last_end > 0 and lines[last_end - 1].strip() == "") else "\n" + block
    lines.insert(last_end, insert)
    return "".join(lines)


def save_agent_config(name: str, cfg: dict) -> None:
    cf = config_file()
    text = cf.read_text(encoding="utf-8") if cf.exists() else "[team]\nsession = \"AI-Company\"\n"
    text, _ = remove_agent_block(text, name)
    write_text_atomic(cf, insert_agent_block(text, agent_block(name, cfg)))


def delete_agent_config(name: str) -> bool:
    cf = config_file()
    text = cf.read_text(encoding="utf-8") if cf.exists() else ""
    text, found = remove_agent_block(text, name)
    if found:
        write_text_atomic(cf, text)
    return found


def config_from_payload(payload: dict[str, Any], current: dict | None = None) -> dict:
    cfg = dict(current or {})
    for key in ("cli", "model", "reasoning_effort", "role", "playbook", "card_color", "tone", "notes"):
        if key in payload:
            value = payload[key]
            if value is None:
                cfg.pop(key, None)
            else:
                cfg[key] = str(value).strip()
    if "lazy" in payload:
        cfg["lazy"] = bool(payload["lazy"])
    if "specialty" in payload:
        value = payload["specialty"]
        if isinstance(value, str):
            cfg["specialty"] = [x.strip() for x in value.split(",") if x.strip()]
        elif isinstance(value, list):
            cfg["specialty"] = [str(x).strip() for x in value if str(x).strip()]
        else:
            cfg["specialty"] = []
    cfg.setdefault("cli", "codex-cli")
    return {k: v for k, v in cfg.items() if v not in ("", [], None)}


def facts_dir() -> Path:
    return state_dir() / "facts"


def agent_dir(agent: str) -> Path:
    return state_dir() / "agents" / agent


def list_messages(agent: str, limit: int = 30) -> list[dict]:
    rows = [m for m in read_json(facts_dir() / "inbox.json", {"messages": []}).get("messages", []) if m.get("to") == agent]
    rows.sort(key=lambda r: r.get("created_at") or 0)
    return rows[-limit:]


def list_logs(agent: str, limit: int = 80) -> list[dict]:
    rows = [r for r in read_jsonl(facts_dir() / "logs.jsonl") if r.get("agent") == agent]
    return rows[-limit:]


def list_tasks(agent: str = "") -> list[dict]:
    rows = list(read_json(state_dir() / "tasks.json", {"tasks": []}).get("tasks", []))
    if agent:
        rows = [r for r in rows if r.get("assignee") == agent]
    rows.sort(key=lambda r: safe_int(str(r.get("id", "T-0")).split("-")[-1], 0))
    return rows


def list_radio(agent: str) -> list[dict]:
    data = read_json(agent_dir(agent) / "radio.json", {"threads": {}})
    rows: list[dict] = []
    for tid, thread in (data.get("threads") or {}).items():
        if thread.get("archived"):
            continue
        for update in thread.get("updates") or []:
            row = dict(update)
            row["task_id"] = tid
            rows.append(row)
    rows.sort(key=lambda r: r.get("created_at") or 0)
    return rows[-30:]


def detect_container() -> str:
    explicit = os.environ.get("AI_COMPANY_CONTAINER", "").strip()
    if explicit:
        return explicit
    candidates = [
        [docker_bin(), "ps", "--filter", "name=claudeteam", "--format", "{{.Names}}"],
        [docker_bin(), "ps", "--filter", "ancestor=ai-company:local", "--format", "{{.Names}}"],
    ]
    for cmd in candidates:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
        names = [x.strip() for x in proc.stdout.splitlines() if x.strip()]
        if names:
            return names[0]
    return ""


def docker_exec(args: list[str], *, timeout: int = 20) -> dict[str, Any]:
    container = detect_container()
    if not container:
        return {"ok": False, "rc": 127, "stdout": "", "stderr": "claudeteam container not found"}
    proc = subprocess.run([docker_bin(), "exec", container, *args], text=True,
                          capture_output=True, timeout=timeout, check=False)
    return {"ok": proc.returncode == 0, "rc": proc.returncode,
            "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:],
            "container": container}


def pane_text(agent: str, lines: int = 160) -> str:
    team = load_team()
    session = team.get("session") or "AI-Company"
    out = docker_exec(["tmux", "capture-pane", "-pt", f"{session}:{agent}", "-S", f"-{max(20, min(lines, 500))}"])
    if not out["ok"]:
        return out.get("stderr") or out.get("stdout") or "(pane unavailable)"
    return out["stdout"]


def claudeteam_cmd(args: list[str], *, timeout: int = 30) -> dict[str, Any]:
    return docker_exec(["claudeteam", *args], timeout=timeout)


def state() -> dict[str, Any]:
    team = load_team()
    status = read_json(facts_dir() / "status.json", {"agents": {}}).get("agents", {})
    hb = read_json(facts_dir() / "heartbeats.json", {})
    inbox = read_json(facts_dir() / "inbox.json", {"messages": []}).get("messages", [])
    tasks = list_tasks()
    doctor = read_json(state_dir() / "doctor-last.json", None)
    learn = read_json(state_dir() / "learn" / "drafts.json", {"drafts": []}).get("drafts", [])
    agents = []
    for name, cfg in (team.get("agents") or {}).items():
        unread = [m for m in inbox if m.get("to") == name and not m.get("read")]
        active = [t for t in tasks if t.get("assignee") == name and t.get("status") in {"进行中", "需审批", "后台中"}]
        last = safe_int(hb.get(name), 0)
        agents.append({
            "agent": name,
            "config": cfg,
            "status": (status.get(name) or {}).get("status", "unknown"),
            "task": (status.get(name) or {}).get("task", ""),
            "model": cfg.get("model", ""),
            "reasoning_effort": cfg.get("reasoning_effort", ""),
            "unread_count": len(unread),
            "active_task": active[0].get("id") if active else "",
            "heartbeat_age_sec": int((now_ms() - last) / 1000) if last > 0 else None,
        })
    q = {
        "pending": sum(1 for t in tasks if t.get("status") == "待处理"),
        "in_progress": sum(1 for t in tasks if t.get("status") == "进行中"),
        "background": sum(1 for t in tasks if t.get("status") == "后台中"),
        "needs_approval": sum(1 for t in tasks if t.get("status") == "需审批"),
    }
    return {
        "ok": True,
        "generated_at_ms": now_ms(),
        "root": str(root_dir()),
        "state_dir": str(state_dir()),
        "config_file": str(config_file()),
        "container": detect_container(),
        "roster": team,
        "agents": agents,
        "queue": q,
        "learning": {"drafts": sum(1 for d in learn if d.get("status") == "draft"), "recent": learn[-10:]},
        "doctor": doctor,
    }


def agent_detail(agent: str, lines: int = 160) -> dict[str, Any]:
    agent = validate_agent(agent)
    team = load_team()
    status = read_json(facts_dir() / "status.json", {"agents": {}}).get("agents", {}).get(agent, {})
    return {
        "agent": agent,
        "config": (team.get("agents") or {}).get(agent, {}),
        "status": status,
        "heartbeat_ms": read_json(facts_dir() / "heartbeats.json", {}).get(agent),
        "pane": {"exists": True, "text": pane_text(agent, lines=lines)},
        "tasks": list_tasks(agent),
        "inbox": list_messages(agent),
        "radio_updates": list_radio(agent),
        "logs": list_logs(agent),
    }


def create_agent(payload: dict[str, Any]) -> dict[str, Any]:
    name = validate_agent(payload.get("name", ""))
    team = load_team()
    if name in (team.get("agents") or {}):
        raise ValueError(f"agent already exists: {name}")
    cfg = config_from_payload(payload)
    # Prefer the runtime's native bring-back path first. If the agent was
    # previously fired, `claudeteam hire <agent>` restores the archived
    # roster block + workspace via the exact fire → archive → hire flow.
    # Only fall back to "save config then hire" when the runtime explicitly
    # reports "not in roster, no archive to restore" (brand-new agent).
    action = claudeteam_cmd(["hire", name])
    config_saved = False
    stdout = str(action.get("stdout") or "")
    stderr = str(action.get("stderr") or "")
    combined = f"{stdout}\n{stderr}"
    if not action.get("ok") and "not in roster, no archive to restore" in combined:
        save_agent_config(name, cfg)
        config_saved = True
        action = claudeteam_cmd(["hire", name])
    return {
        "ok": bool(action.get("ok")),
        "agent": name,
        "config": cfg,
        "action": action,
        "config_saved": config_saved,
    }


def update_agent(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    agent = validate_agent(agent)
    team = load_team()
    current = (team.get("agents") or {}).get(agent)
    if current is None:
        raise ValueError(f"unknown agent: {agent}")
    cfg = config_from_payload(payload, current=current)
    save_agent_config(agent, cfg)
    action = {"ok": True, "stdout": "config saved"}
    if payload.get("restart"):
        action = claudeteam_cmd(["restart", agent])
    return {"ok": True, "agent": agent, "config": cfg, "action": action}


def delete_agent(agent: str) -> dict[str, Any]:
    agent = validate_agent(agent)
    if agent == "manager":
        raise ValueError("manager cannot be deleted")
    action = claudeteam_cmd(["fire", agent])
    if not action.get("ok"):
        delete_agent_config(agent)
    return {"ok": bool(action.get("ok")), "agent": agent, "action": action}


def agent_action(agent: str, action: str) -> dict[str, Any]:
    agent = validate_agent(agent)
    if action not in {"hire", "fire", "restart"}:
        raise ValueError(f"unknown action: {action}")
    if agent == "manager" and action == "fire":
        raise ValueError("manager cannot be fired")
    result = claudeteam_cmd([action, agent])
    return {"ok": bool(result.get("ok")), "agent": agent, "action": action, "result": result}


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Company Admin</title>
  <style>
    :root{color-scheme:dark;--bg:#101114;--panel:#181a1f;--line:#2a2e37;--text:#e8e9ec;--muted:#9aa0aa;--good:#5fd08f;--warn:#e7bd55;--bad:#ef7777;--accent:#7fb4ff}
    *{box-sizing:border-box} body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text)}
    header{height:56px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;border-bottom:1px solid var(--line);background:#14161a;position:sticky;top:0;z-index:2}
    h1{margin:0;font-size:18px} button,input,select,textarea{font:inherit} button{border:1px solid var(--line);background:#20242b;color:var(--text);border-radius:6px;padding:7px 10px;cursor:pointer}
    button.primary{background:#1e4f8d;border-color:#2e68ad} button.danger{background:#552226;border-color:#7c3439}
    input,select,textarea{width:100%;border:1px solid var(--line);border-radius:6px;background:#111318;color:var(--text);padding:8px} textarea{min-height:78px;resize:vertical}
    main{display:grid;grid-template-columns:280px 1fr 360px;min-height:calc(100vh - 56px)} aside,section{border-right:1px solid var(--line);padding:14px;overflow:auto} section:last-child{border-right:0}
    .toolbar{display:flex;gap:8px;align-items:center}.cards{display:grid;grid-template-columns:repeat(4,minmax(100px,1fr));gap:10px;margin-bottom:12px}.metric{border:1px solid var(--line);border-radius:8px;padding:10px;background:var(--panel)}.metric .label,.muted{color:var(--muted);font-size:12px}.metric .value{font-size:22px;margin-top:4px}
    .agent{display:block;width:100%;text-align:left;margin-bottom:8px}.agent.active{border-color:var(--accent);background:#1b2738}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:1px 7px;font-size:12px;color:var(--muted)}.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
    pre{margin:0;white-space:pre-wrap;word-break:break-word;background:#0c0e12;border:1px solid var(--line);border-radius:8px;padding:12px;min-height:360px;max-height:58vh;overflow:auto} table{width:100%;border-collapse:collapse;margin-top:10px}td,th{border-bottom:1px solid var(--line);padding:7px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}.grid{display:grid;gap:10px}.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}.tabs{display:flex;gap:8px;margin:10px 0}.tabs button.active{border-color:var(--accent)}.hidden{display:none}.modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.65);display:none;align-items:center;justify-content:center;z-index:50}.modal-backdrop.open{display:flex}.modal{width:min(720px,92vw);max-height:88vh;overflow:auto;background:#171a20;border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 18px 60px rgba(0,0,0,.45)}@media(max-width:1080px){main{grid-template-columns:1fr}aside,section{border-right:0;border-bottom:1px solid var(--line)}}
  </style>
</head>
<body>
<header><h1>AI Company Admin</h1><div class="toolbar"><span id="stamp" class="muted">loading</span><button onclick="loadAll()">Refresh</button></div></header>
<main>
<aside><div class="toolbar" style="justify-content:space-between;margin-bottom:10px"><strong>Agents</strong><button class="primary" onclick="newAgent()">New</button></div><div id="agents"></div></aside>
<section><div class="cards" id="cards"></div><div class="toolbar" style="justify-content:space-between"><div><strong id="detailTitle">Select an agent</strong><div id="detailMeta" class="muted"></div></div><div class="toolbar"><button onclick="restartAgent()">Restart</button><button class="danger" onclick="deleteAgent()">Delete</button></div></div>
<div class="tabs"><button id="tabPane" class="active" onclick="showTab('pane')">Pane</button><button id="tabTasks" onclick="showTab('tasks')">Tasks</button><button id="tabInbox" onclick="showTab('inbox')">Inbox</button><button id="tabRadio" onclick="showTab('radio')">Radio</button><button id="tabLogs" onclick="showTab('logs')">Logs</button></div>
<pre id="pane"></pre><div id="tasks" class="hidden"></div><div id="inbox" class="hidden"></div><div id="radio" class="hidden"></div><div id="logs" class="hidden"></div></section>
<section><strong>Agent Editor</strong><div class="grid" style="margin-top:10px"><label>Name<input id="fName"/></label><div class="row"><label>CLI<input id="fCli" placeholder="codex-cli"/></label><label>Model<input id="fModel" placeholder="gpt-5.4-mini"/></label></div><div class="row"><label>Reasoning<input id="fReasoning" placeholder="low / medium / high"/></label><label>Playbook<input id="fPlaybook" placeholder="ops.md"/></label></div><label>Role<textarea id="fRole"></textarea></label><label>Specialty<textarea id="fSpecialty" placeholder="日志分析, Docker Compose"></textarea></label><label>Notes<textarea id="fNotes"></textarea></label><label><input id="fLazy" type="checkbox" style="width:auto"/> Lazy start</label><label><input id="fRestart" type="checkbox" style="width:auto"/> Restart after save</label><div class="toolbar"><button class="primary" onclick="saveAgent()">Save</button><button onclick="loadSelectedIntoForm()">Reset</button></div><div id="formMsg" class="muted"></div></div></section>
</main>
<div id="createModal" class="modal-backdrop" onclick="if(event.target===this) closeCreateModal()">
  <div class="modal">
    <div class="toolbar" style="justify-content:space-between;margin-bottom:12px"><strong>New Agent</strong><button onclick="closeCreateModal()">Close</button></div>
    <div class="grid">
      <label>Name<input id="cName"/></label>
      <div class="row"><label>CLI<input id="cCli" placeholder="codex-cli"/></label><label>Model<input id="cModel" placeholder="gpt-5.4-mini"/></label></div>
      <div class="row"><label>Reasoning<input id="cReasoning" placeholder="low / medium / high"/></label><label>Playbook<input id="cPlaybook" placeholder="ops.md"/></label></div>
      <label>Role<textarea id="cRole"></textarea></label>
      <label>Specialty<textarea id="cSpecialty" placeholder="日志分析, Docker Compose"></textarea></label>
      <label>Notes<textarea id="cNotes"></textarea></label>
      <label><input id="cLazy" type="checkbox" style="width:auto"/> Lazy start</label>
      <div class="toolbar"><button class="primary" onclick="saveNewAgent()">Create Agent</button><button onclick="resetCreateForm()">Reset</button></div>
      <div id="createMsg" class="muted"></div>
    </div>
  </div>
</div>
<script>
let state=null,selected="",detail=null,isCreating=false; const $=id=>document.getElementById(id);
function age(sec){if(sec==null)return"never";if(sec<60)return sec+"s";if(sec<3600)return Math.floor(sec/60)+"m";return Math.floor(sec/3600)+"h"}
async function api(path,opts){const r=await fetch(path,opts||{cache:"no-store"});const t=await r.text();let d;try{d=JSON.parse(t)}catch{d={ok:false,error:t}}if(!r.ok)throw new Error(d.error||r.statusText);return d}
async function loadAll(){state=await api("/api/admin/state");$("stamp").textContent=new Date(state.generated_at_ms).toLocaleString()+` · ${state.container||"no container"}`;renderCards();renderAgents();const exists=state.agents.some(a=>a.agent===selected);if(!isCreating&&!selected&&state.agents[0])selected=state.agents[0].agent;if(selected&&!exists&&!isCreating)selected=state.agents[0]?state.agents[0].agent:"";if(selected&&!isCreating)await loadDetail(selected)}
function renderCards(){const q=state.queue,doctor=state.doctor?`${state.doctor.counts.fail}/${state.doctor.counts.warn}`:"none",learning=state.learning?state.learning.drafts:0;const rows=[["Overall",state.ok?"OK":"Check",state.ok?"good":"bad"],["Doctor",doctor,state.doctor&&state.doctor.counts.fail?"bad":""],["Learning",learning,learning?"warn":""],["Pending",q.pending,""],["Running",q.in_progress,""],["Background",q.background||0,""],["Unread",state.agents.reduce((n,a)=>n+a.unread_count,0),""]];$("cards").innerHTML=rows.map(x=>`<div class="metric"><div class="label">${x[0]}</div><div class="value ${x[2]}">${x[1]}</div></div>`).join("")}
function renderAgents(){$("agents").innerHTML=state.agents.map(a=>`<button class="agent ${a.agent===selected?"active":""}" onclick="selectAgent('${a.agent}')"><strong>${a.agent}</strong> <span class="pill">${a.status}</span><br><span class="muted">${a.model||""} · ${a.reasoning_effort||""} · hb ${age(a.heartbeat_age_sec)}</span><br><span class="muted">${a.active_task||a.task||"ready"}</span></button>`).join("")}
async function selectAgent(name){isCreating=false;closeCreateModal();selected=name;renderAgents();await loadDetail(name)}
async function loadDetail(name){detail=await api(`/api/admin/agents/${encodeURIComponent(name)}?lines=220`);$("detailTitle").textContent=name;$("detailMeta").textContent=`${detail.config.cli||""} · ${detail.config.model||""} · ${detail.status.status||"unknown"}`;$("pane").textContent=detail.pane.text||"(no pane output)";$("tasks").innerHTML=table(detail.tasks,["id","status","title","created_at"]);$("inbox").innerHTML=table(detail.inbox,["local_id","from","priority","task_id","read","content"]);$("radio").innerHTML=table(detail.radio_updates,["task_id","from","local_id","acked","summary"]);$("logs").innerHTML=table(detail.logs,["type","ref","content"]);loadSelectedIntoForm()}
function table(rows,keys){if(!rows||!rows.length)return'<div class="muted">No rows</div>';return`<table><thead><tr>${keys.map(k=>`<th>${k}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${keys.map(k=>`<td>${String(r[k]??"").slice(0,500)}</td>`).join("")}</tr>`).join("")}</tbody></table>`}
function showTab(name){["pane","tasks","inbox","radio","logs"].forEach(x=>{$(x).classList.toggle("hidden",x!==name);$("tab"+x[0].toUpperCase()+x.slice(1)).classList.toggle("active",x===name)})}
function loadSelectedIntoForm(){const cfg=detail?detail.config:{};$("fName").value=selected||"";$("fName").disabled=!!selected;$("fCli").value=cfg.cli||"codex-cli";$("fModel").value=cfg.model||"";$("fReasoning").value=cfg.reasoning_effort||"";$("fPlaybook").value=cfg.playbook||"";$("fRole").value=cfg.role||"";$("fSpecialty").value=(cfg.specialty||[]).join(", ");$("fNotes").value=cfg.notes||"";$("fLazy").checked=!!cfg.lazy;$("fRestart").checked=false}
function payload(){return{name:$("fName").value,cli:$("fCli").value,model:$("fModel").value,reasoning_effort:$("fReasoning").value,playbook:$("fPlaybook").value,role:$("fRole").value,specialty:$("fSpecialty").value,notes:$("fNotes").value,lazy:$("fLazy").checked,restart:$("fRestart").checked}}
function createPayload(){return{name:$("cName").value,cli:$("cCli").value,model:$("cModel").value,reasoning_effort:$("cReasoning").value,playbook:$("cPlaybook").value,role:$("cRole").value,specialty:$("cSpecialty").value,notes:$("cNotes").value,lazy:$("cLazy").checked}}
function resetCreateForm(){$("cName").value="";$("cCli").value="codex-cli";$("cModel").value="";$("cReasoning").value="";$("cPlaybook").value="";$("cRole").value="";$("cSpecialty").value="";$("cNotes").value="";$("cLazy").checked=false;$("createMsg").textContent=""}
function openCreateModal(){$("createModal").classList.add("open")}
function closeCreateModal(){$("createModal").classList.remove("open")}
function newAgent(){isCreating=true;resetCreateForm();openCreateModal()}
async function saveAgent(){try{const body=JSON.stringify(payload()),path=selected?`/api/admin/agents/${encodeURIComponent(selected)}`:"/api/admin/agents",method=selected?"PUT":"POST";const res=await api(path,{method,headers:{"Content-Type":"application/json"},body});$("formMsg").textContent=(res.action&&res.action.stderr)||"saved";selected=res.agent;await loadAll()}catch(e){$("formMsg").textContent=e.message}}
async function saveNewAgent(){try{const body=JSON.stringify(createPayload());const res=await api("/api/admin/agents",{method:"POST",headers:{"Content-Type":"application/json"},body});if(!res.ok){$("createMsg").textContent=(res.action&&res.action.stderr)||"create failed";return}selected=res.agent;isCreating=false;closeCreateModal();$("createMsg").textContent="";await loadAll()}catch(e){$("createMsg").textContent=e.message}}
async function restartAgent(){if(!selected)return;await api(`/api/admin/agents/${encodeURIComponent(selected)}/action`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"restart"})});await loadAll()}
async function deleteAgent(){if(!selected||!confirm(`Delete ${selected}?`))return;await api(`/api/admin/agents/${encodeURIComponent(selected)}`,{method:"DELETE"});selected="";await loadAll()}
loadAll().catch(e=>{$("stamp").textContent=e.message});setInterval(()=>{if(!isCreating&&selected)loadDetail(selected).catch(()=>{});loadAll().catch(()=>{})},5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_body(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, data: Any) -> None:
        self.send_body(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    def json_body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def fail(self, status: int, exc: Exception) -> None:
        self.send_json(status, {"ok": False, "error": str(exc)})

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/admin"}:
                self.send_body(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/admin/state":
                self.send_json(200, state())
                return
            m = re.match(r"^/api/admin/agents/([^/]+)$", parsed.path)
            if m:
                qs = parse_qs(parsed.query)
                lines = int((qs.get("lines") or ["160"])[0])
                self.send_json(200, agent_detail(m.group(1), lines=lines))
                return
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as exc:
            self.fail(400, exc)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            payload = self.json_body()
            if parsed.path == "/api/admin/agents":
                self.send_json(200, create_agent(payload))
                return
            m = re.match(r"^/api/admin/agents/([^/]+)/action$", parsed.path)
            if m:
                self.send_json(200, agent_action(m.group(1), str(payload.get("action") or "")))
                return
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as exc:
            self.fail(400, exc)

    def do_PUT(self) -> None:
        try:
            m = re.match(r"^/api/admin/agents/([^/]+)$", urlparse(self.path).path)
            if m:
                self.send_json(200, update_agent(m.group(1), self.json_body()))
                return
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as exc:
            self.fail(400, exc)

    def do_DELETE(self) -> None:
        try:
            m = re.match(r"^/api/admin/agents/([^/]+)$", urlparse(self.path).path)
            if m:
                self.send_json(200, delete_agent(m.group(1)))
                return
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")
        except Exception as exc:
            self.fail(400, exc)


def serve(host: str, port: int) -> int:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"AI Company admin listening on http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("AI_COMPANY_ADMIN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AI_COMPANY_ADMIN_PORT", "8766")))
    ns = parser.parse_args(argv)
    return serve(ns.host, ns.port)


if __name__ == "__main__":
    raise SystemExit(main())
