#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import webbrowser


REPO_ROOT = Path(os.environ.get("WEB_DOCS_ROOT", Path.cwd())).expanduser().resolve()
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8090
DEFAULT_TITLE = "在线文档"
DEFAULT_GIT_PULL_INTERVAL = 300
DEFAULT_GIT_PULL_TIMEOUT = 120
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
MAX_SEARCH_RESULTS = 60
MAX_SNIPPETS_PER_FILE = 3
SEARCH_SNIPPET_RADIUS = 90
SEARCH_MAX_QUERY_LENGTH = 200
EXCLUDED_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".pytest_cache",
    "build",
    ".venv",
    "node_modules",
}


def env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def env_positive_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def compact_command_output(value: str, limit: int = 4000) -> str:
    output = value.strip()
    if len(output) <= limit:
        return output
    return output[:limit].rstrip() + "\n... output truncated ..."


class GitAutoPuller:
    def __init__(
        self,
        root: Path,
        interval_seconds: int,
        remote: str,
        branch: str,
        timeout_seconds: int,
    ) -> None:
        self.root = root
        self.interval_seconds = interval_seconds
        self.remote = remote.strip()
        self.branch = branch.strip()
        self.timeout_seconds = timeout_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.git_available = True

    def start(self) -> None:
        if not self.is_git_worktree():
            print(f"Git auto-pull disabled: {self.root} is not a git worktree.")
            return

        self.thread = threading.Thread(target=self.run, name="git-auto-pull", daemon=True)
        self.thread.start()
        print(f"Git auto-pull enabled every {self.interval_seconds} seconds.")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.pull_once()
            self.stop_event.wait(self.interval_seconds)

    def pull_once(self) -> None:
        command = ["pull", "--ff-only"]
        if self.remote or self.branch:
            command.append(self.remote or "origin")
        if self.branch:
            command.append(self.branch)

        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        result = self.run_git(command, timeout=self.timeout_seconds)
        if result is None:
            return

        output = compact_command_output(result.stdout or "")
        if result.returncode == 0:
            message = output or "Already up to date."
            print(f"[{started_at}] Git auto-pull succeeded:\n{message}")
            return

        print(f"[{started_at}] Git auto-pull failed with exit code {result.returncode}.")
        if output:
            print(output)

    def is_git_worktree(self) -> bool:
        result = self.run_git(["rev-parse", "--is-inside-work-tree"], timeout=10, quiet=True)
        if result is None or result.returncode != 0:
            return False
        return (result.stdout or "").strip() == "true"

    def run_git(
        self,
        args: list[str],
        timeout: int,
        quiet: bool = False,
    ) -> subprocess.CompletedProcess[str] | None:
        command = [
            "git",
            "-C",
            str(self.root),
            "-c",
            f"safe.directory={self.root}",
            *args,
        ]
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            return subprocess.run(
                command,
                cwd=self.root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            if self.git_available:
                print("Git auto-pull disabled: git executable was not found.")
                self.git_available = False
            return None
        except subprocess.TimeoutExpired:
            if not quiet:
                print(f"Git auto-pull timed out after {timeout} seconds.")
            return None
        except OSError as exc:
            if not quiet:
                print(f"Git auto-pull could not start git: {exc}")
            return None


def normalized_repo_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def resolve_repo_file(value: str) -> Path | None:
    if not value:
        return None
    candidate = (REPO_ROOT / unquote(value)).resolve()
    try:
        candidate.relative_to(REPO_ROOT)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def markdown_files() -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for root, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDED_DIRS]
        for filename in filenames:
            path = Path(root) / filename
            if path.suffix.lower() not in MARKDOWN_EXTENSIONS:
                continue
            rel_parts = path.relative_to(REPO_ROOT).parts
            if any(part in EXCLUDED_DIRS for part in rel_parts):
                continue
            rel = normalized_repo_path(path)
            files.append(
                {
                    "path": rel,
                    "name": path.name,
                    "directory": str(Path(rel).parent).replace("\\", "/"),
                }
            )
    files.sort(key=lambda item: item["path"].lower())
    return files


def search_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in query.casefold().split():
        if term and term not in seen:
            terms.append(term)
            seen.add(term)
    return terms


def search_snippets(content: str, terms: list[str], phrase: str) -> list[dict[str, object]]:
    snippets: list[dict[str, object]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        compact = " ".join(line.strip().split())
        if not compact:
            continue
        compact_lower = compact.casefold()
        phrase_index = compact_lower.find(phrase) if phrase else -1
        term_indexes = [compact_lower.find(term) for term in terms if compact_lower.find(term) >= 0]
        if phrase_index < 0 and not term_indexes:
            continue
        first_index = phrase_index if phrase_index >= 0 else min(term_indexes)
        start = max(0, first_index - SEARCH_SNIPPET_RADIUS)
        end = min(len(compact), first_index + SEARCH_SNIPPET_RADIUS)
        snippets.append(
            {
                "line": line_number,
                "text": f"{'...' if start else ''}{compact[start:end].strip()}{'...' if end < len(compact) else ''}",
            }
        )
        if len(snippets) >= MAX_SNIPPETS_PER_FILE:
            break
    return snippets


def search_markdown_files(query: str) -> list[dict[str, object]]:
    normalized_query = " ".join(query.strip().split())[:SEARCH_MAX_QUERY_LENGTH]
    terms = search_terms(normalized_query)
    if not terms:
        return []

    phrase = normalized_query.casefold()
    results: list[dict[str, object]] = []
    for doc in markdown_files():
        path = REPO_ROOT / doc["path"]
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        path_lower = doc["path"].casefold()
        name_lower = doc["name"].casefold()
        directory_lower = doc["directory"].casefold()
        content_lower = content.casefold()
        searchable = "\n".join((path_lower, name_lower, directory_lower, content_lower))
        if not all(term in searchable for term in terms):
            continue

        path_hits = sum(path_lower.count(term) + name_lower.count(term) + directory_lower.count(term) for term in terms)
        content_hits = sum(content_lower.count(term) for term in terms)
        phrase_hits = content_lower.count(phrase) + path_lower.count(phrase)
        score = (phrase_hits * 50) + (path_hits * 12) + min(content_hits, 200)
        results.append(
            {
                **doc,
                "matchCount": path_hits + content_hits,
                "snippets": search_snippets(content, terms, phrase),
                "_score": score,
            }
        )

    results.sort(key=lambda item: (-int(item["_score"]), str(item["path"]).lower()))
    for item in results:
        item.pop("_score", None)
    return results[:MAX_SEARCH_RESULTS]


def build_index_html(title: str, mermaid_url: str, marked_url: str) -> bytes:
    safe_title = html.escape(title)
    payload = {
        "title": title,
        "mermaidUrl": mermaid_url,
        "markedUrl": marked_url,
    }
    config_json = json.dumps(payload, ensure_ascii=False)
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #1769aa;
      --code: #f2f4f7;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 15px/1.6 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(260px, 320px) minmax(0, 1fr) minmax(220px, 280px);
      min-height: 100vh;
    }}
    .layout.sidebar-collapsed {{
      grid-template-columns: 48px minmax(0, 1fr) minmax(220px, 280px);
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: var(--panel);
      padding: 18px 14px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }}
    .layout.sidebar-collapsed aside {{
      padding: 12px 7px;
      overflow: hidden;
    }}
    .sidebar-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .icon-button {{
      appearance: none;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      min-width: 32px;
      height: 32px;
      padding: 0 9px;
      font: inherit;
      cursor: pointer;
    }}
    .icon-button:hover {{
      background: #f8fafc;
    }}
    main {{
      padding: 28px;
      overflow: auto;
    }}
    h1, h2, h3, h4 {{ line-height: 1.25; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .brand {{
      font-weight: 700;
      font-size: 18px;
      min-width: 0;
    }}
    .layout.sidebar-collapsed .brand,
    .layout.sidebar-collapsed .search,
    .layout.sidebar-collapsed .search-status,
    .layout.sidebar-collapsed .doc-list {{
      display: none;
    }}
    .search {{
      width: 100%;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font: inherit;
      margin-bottom: 6px;
    }}
    .search-status {{
      min-height: 18px;
      margin: 0 2px 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .doc-list {{
      display: flex;
      flex-direction: column;
      gap: 3px;
    }}
    .doc-tree,
    .doc-tree-children {{
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    .doc-tree-children {{
      border-left: 1px solid var(--line);
      margin-left: 12px;
      padding-left: 12px;
    }}
    .doc-tree-node {{
      margin: 1px 0;
      position: relative;
    }}
    .doc-tree-children > .doc-tree-node::before {{
      border-top: 1px solid var(--line);
      content: "";
      left: -12px;
      position: absolute;
      top: 16px;
      width: 10px;
    }}
    .doc-folder {{
      margin: 0;
    }}
    .doc-folder-summary {{
      align-items: center;
      border-radius: 6px;
      color: #334155;
      cursor: pointer;
      display: flex;
      gap: 6px;
      font-weight: 600;
      list-style: none;
      padding: 6px;
      user-select: none;
    }}
    .doc-folder-summary::-webkit-details-marker {{
      display: none;
    }}
    .doc-folder-summary::before {{
      color: var(--muted);
      content: "▸";
      display: inline-block;
      font-size: 11px;
      line-height: 1;
      width: 11px;
    }}
    .doc-folder[open] > .doc-folder-summary::before {{
      content: "▾";
    }}
    .doc-folder-summary:hover {{
      background: #f8fafc;
    }}
    .doc-link {{
      display: block;
      border-radius: 6px;
      padding: 7px 8px;
      color: var(--text);
      word-break: break-word;
    }}
    .doc-tree .doc-link {{
      align-items: center;
      display: flex;
      gap: 6px;
      margin: 1px 0;
      padding: 6px 8px;
    }}
    .doc-link.active {{
      background: #e8f2fb;
      color: #0f4c81;
      font-weight: 600;
    }}
    .doc-link mark {{
      background: #fff1a8;
      color: inherit;
      border-radius: 3px;
      padding: 0 1px;
    }}
    .doc-dir {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 400;
    }}
    .doc-file-icon {{
      color: var(--muted);
      flex: 0 0 auto;
      font-size: 13px;
      width: 11px;
    }}
    .doc-meta {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }}
    .doc-snippets {{
      display: flex;
      flex-direction: column;
      gap: 3px;
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      font-weight: 400;
    }}
    .doc-snippet {{
      display: block;
    }}
    .doc-line {{
      color: #475467;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      margin-right: 6px;
    }}
    .empty-state {{
      color: var(--muted);
      font-size: 13px;
      padding: 8px;
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .path {{
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      word-break: break-all;
    }}
    .content {{
      max-width: 1060px;
      width: 100%;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 30px;
    }}
    .layout.sidebar-collapsed .content {{
      max-width: none;
    }}
    .content h1,
    .content h2,
    .content h3 {{
      scroll-margin-top: 24px;
    }}
    .content img {{ max-width: 100%; }}
    .content table {{
      border-collapse: collapse;
      width: 100%;
      margin: 16px 0;
      display: block;
      overflow-x: auto;
    }}
    .content th, .content td {{
      border: 1px solid var(--line);
      padding: 8px 10px;
      vertical-align: top;
    }}
    .content th {{ background: #f8fafc; }}
    pre {{
      background: var(--code);
      border-radius: 7px;
      overflow: auto;
      padding: 13px;
    }}
    code {{
      background: var(--code);
      border-radius: 4px;
      padding: 1px 4px;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 0.92em;
    }}
    pre code {{ padding: 0; background: transparent; }}
    blockquote {{
      border-left: 4px solid var(--line);
      color: var(--muted);
      margin-left: 0;
      padding-left: 14px;
    }}
    .mermaid {{
      background: #fff;
      min-width: max-content;
      padding: 14px;
      text-align: center;
      transform-origin: top left;
    }}
    .diagram-shell {{
      border: 1px solid var(--line);
      border-radius: 8px;
      margin: 18px 0;
      background: #fff;
      overflow: hidden;
    }}
    .diagram-toolbar {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 6px;
      border-bottom: 1px solid var(--line);
      padding: 7px 8px;
      background: #f8fafc;
    }}
    .diagram-body {{
      overflow: auto;
      padding: 8px;
    }}
    .diagram-fullscreen {{
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: none;
      background: rgba(15, 23, 42, 0.72);
      padding: 28px;
    }}
    .diagram-fullscreen.open {{
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 10px;
    }}
    .diagram-fullscreen-bar {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
    }}
    .diagram-fullscreen-content {{
      overflow: auto;
      border-radius: 8px;
      background: #fff;
      padding: 16px;
      display: flex;
      align-items: flex-start;
      justify-content: flex-start;
      cursor: grab;
      user-select: none;
      touch-action: none;
    }}
    .diagram-fullscreen-content.dragging {{
      cursor: grabbing;
    }}
    .diagram-fullscreen-content .mermaid {{
      display: inline-block;
    }}
    .error {{
      border: 1px solid #f4b4b4;
      background: #fff5f5;
      border-radius: 8px;
      padding: 14px;
      color: #8a1f1f;
    }}
    .outline {{
      border-left: 1px solid var(--line);
      background: var(--panel);
      padding: 18px 14px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }}
    .outline-title {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      margin-bottom: 8px;
      text-transform: uppercase;
    }}
    .outline-list {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}
    .outline-link {{
      border-radius: 6px;
      color: var(--muted);
      display: block;
      font-size: 13px;
      line-height: 1.35;
      padding: 5px 8px;
      word-break: break-word;
    }}
    .outline-link:hover {{
      background: #f8fafc;
      color: var(--text);
      text-decoration: none;
    }}
    .outline-link.active {{
      background: #e8f2fb;
      color: #0f4c81;
      font-weight: 600;
    }}
    .outline-link.level-2 {{
      padding-left: 18px;
    }}
    .outline-link.level-3 {{
      padding-left: 30px;
    }}
    .outline-empty {{
      color: var(--muted);
      font-size: 13px;
      padding: 6px 8px;
    }}
    @media (max-width: 820px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .layout.sidebar-collapsed {{ grid-template-columns: 1fr; }}
      aside {{ position: relative; height: auto; max-height: 42vh; }}
      .layout.sidebar-collapsed aside {{ max-height: 56px; }}
      main {{ padding: 14px; }}
      .content {{ padding: 18px; }}
      .outline {{
        border-left: 0;
        border-top: 1px solid var(--line);
        height: auto;
        max-height: 38vh;
        position: relative;
      }}
    }}
  </style>
</head>
<body>
  <div id="layout" class="layout">
    <aside>
      <div class="sidebar-header">
        <div class="brand">{safe_title}</div>
        <button id="sidebar-toggle" class="icon-button" type="button" title="Collapse sidebar" aria-label="Collapse sidebar">‹</button>
      </div>
      <input id="search" class="search" type="search" placeholder="Search Markdown content" autocomplete="off">
      <div id="search-status" class="search-status" aria-live="polite"></div>
      <nav id="doc-list" class="doc-list"></nav>
    </aside>
    <main>
      <div class="toolbar">
        <div id="current-path" class="path"></div>
        <a id="raw-link" href="#" target="_blank" rel="noreferrer">Raw</a>
      </div>
      <article id="content" class="content">Loading...</article>
    </main>
    <nav id="outline" class="outline" aria-label="Markdown outline"></nav>
  </div>
  <div id="diagram-fullscreen" class="diagram-fullscreen" role="dialog" aria-modal="true" aria-label="Diagram fullscreen preview">
    <div class="diagram-fullscreen-bar">
      <button id="diagram-fullscreen-zoom-out" class="icon-button" type="button" title="Zoom out">−</button>
      <button id="diagram-fullscreen-zoom-reset" class="icon-button" type="button" title="Reset to fit">Fit</button>
      <button id="diagram-fullscreen-zoom-in" class="icon-button" type="button" title="Zoom in">+</button>
      <button id="diagram-fullscreen-close" class="icon-button" type="button" title="Close fullscreen">×</button>
    </div>
    <div id="diagram-fullscreen-content" class="diagram-fullscreen-content"></div>
  </div>
  <script>window.DOCS_CONFIG = {config_json};</script>
  <script src="{html.escape(marked_url)}"></script>
  <script type="module">
    import mermaid from "{html.escape(mermaid_url)}";

    const config = window.DOCS_CONFIG;
    const listEl = document.getElementById("doc-list");
    const layoutEl = document.getElementById("layout");
    const sidebarToggleEl = document.getElementById("sidebar-toggle");
    const searchEl = document.getElementById("search");
    const searchStatusEl = document.getElementById("search-status");
    const contentEl = document.getElementById("content");
    const outlineEl = document.getElementById("outline");
    const currentPathEl = document.getElementById("current-path");
    const rawLinkEl = document.getElementById("raw-link");
    const fullscreenEl = document.getElementById("diagram-fullscreen");
    const fullscreenContentEl = document.getElementById("diagram-fullscreen-content");
    const fullscreenCloseEl = document.getElementById("diagram-fullscreen-close");
    const fullscreenZoomInEl = document.getElementById("diagram-fullscreen-zoom-in");
    const fullscreenZoomOutEl = document.getElementById("diagram-fullscreen-zoom-out");
    const fullscreenZoomResetEl = document.getElementById("diagram-fullscreen-zoom-reset");
    let docs = [];
    let searchResults = [];
    let searchTimer = null;
    let searchRequestId = 0;
    let activeFullscreenDiagram = null;
    let fullscreenDrag = null;
    let outlineHeadings = [];
    let outlineScrollPending = false;
    let activeDocPath = "";

    mermaid.initialize({{ startOnLoad: false, securityLevel: "strict", theme: "default" }});
    marked.setOptions({{ gfm: true, breaks: false, mangle: false, headerIds: true }});

    function docFromLocation() {{
      const params = new URLSearchParams(window.location.search);
      return params.get("doc") || "";
    }}

    function setDocLocation(path) {{
      const url = new URL(window.location.href);
      if (path) url.searchParams.set("doc", path);
      else url.searchParams.delete("doc");
      url.hash = "";
      history.pushState({{ path }}, "", url);
    }}

    function renderList() {{
      const query = searchEl.value.trim();
      const isSearchMode = query.length > 0;
      const active = activeDocPath || docFromLocation();
      listEl.innerHTML = "";
      if (isSearchMode && searchResults === null) {{
        listEl.innerHTML = `<div class="empty-state">Searching...</div>`;
        return;
      }}
      if (isSearchMode) {{
        renderSearchResults(searchResults, active, query);
        return;
      }}
      renderDocTree(docs, active);
    }}

    function renderSearchResults(items, active, query) {{
      if (!items.length) {{
        listEl.innerHTML = `<div class="empty-state">No matches</div>`;
        return;
      }}
      items.forEach((doc) => {{
        const link = document.createElement("a");
        const matchCount = Number(doc.matchCount || 0);
        const snippets = Array.isArray(doc.snippets) && doc.snippets.length
          ? `<span class="doc-snippets">${{doc.snippets.map((snippet) => `
              <span class="doc-snippet"><span class="doc-line">L${{snippet.line}}</span>${{highlightText(snippet.text || "", query)}}</span>
            `).join("")}}</span>`
          : "";
        const meta = `<span class="doc-meta">${{matchCount}} match${{matchCount === 1 ? "" : "es"}}</span>`;
        link.href = `/?doc=${{encodeURIComponent(doc.path)}}`;
        link.className = "doc-link" + (doc.path === active ? " active" : "");
        link.innerHTML = `
          <strong>${{highlightText(doc.name, query)}}</strong>
          <span class="doc-dir">${{highlightText(doc.directory, query)}}</span>
          ${{meta}}
          ${{snippets}}
        `;
        link.addEventListener("click", (event) => {{
          event.preventDefault();
          setDocLocation(doc.path);
          loadDoc(doc.path);
        }});
        listEl.appendChild(link);
      }});
    }}

    function renderDocTree(items, active) {{
      if (!items.length) {{
        listEl.innerHTML = `<div class="empty-state">No Markdown files</div>`;
        return;
      }}
      const tree = buildDocTree(items);
      const treeEl = document.createElement("ul");
      treeEl.className = "doc-tree";
      renderTreeChildren(tree, treeEl, active, false);
      listEl.appendChild(treeEl);
    }}

    function buildDocTree(items) {{
      const root = createTreeNode("", "");
      items.forEach((doc) => {{
        const parts = doc.path.split("/");
        parts.pop();
        let current = root;
        const pathParts = [];
        parts.forEach((part) => {{
          pathParts.push(part);
          if (!current.folders.has(part)) {{
            current.folders.set(part, createTreeNode(part, pathParts.join("/")));
          }}
          current = current.folders.get(part);
        }});
        current.files.push(doc);
      }});
      sortTree(root);
      return root;
    }}

    function createTreeNode(name, path) {{
      return {{
        name,
        path,
        folders: new Map(),
        sortedFolders: [],
        files: [],
      }};
    }}

    function sortTree(node) {{
      node.sortedFolders = Array.from(node.folders.values())
        .sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
      node.files.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
      node.sortedFolders.forEach(sortTree);
    }}

    function renderTreeChildren(node, parent, active, forceOpen) {{
      node.sortedFolders.forEach((folder) => {{
        const item = document.createElement("li");
        item.className = "doc-tree-node doc-folder-node";

        const details = document.createElement("details");
        details.className = "doc-folder";
        details.open = forceOpen || folderContainsActive(folder, active);

        const summary = document.createElement("summary");
        summary.className = "doc-folder-summary";
        summary.title = folder.path;
        const label = document.createElement("span");
        label.textContent = folder.name;
        summary.appendChild(label);

        const children = document.createElement("ul");
        children.className = "doc-tree-children";
        renderTreeChildren(folder, children, active, forceOpen);

        details.appendChild(summary);
        details.appendChild(children);
        item.appendChild(details);
        parent.appendChild(item);
      }});

      node.files.forEach((doc) => {{
        parent.appendChild(createDocFileNode(doc, active));
      }});
    }}

    function createDocFileNode(doc, active) {{
      const item = document.createElement("li");
      item.className = "doc-tree-node doc-file-node";
      item.appendChild(createDocLink(doc, active));
      return item;
    }}

    function createDocLink(doc, active) {{
      const link = document.createElement("a");
      link.href = `/?doc=${{encodeURIComponent(doc.path)}}`;
      link.className = "doc-link" + (doc.path === active ? " active" : "");
      link.title = doc.path;
      const icon = document.createElement("span");
      icon.className = "doc-file-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "•";
      const label = document.createElement("span");
      label.textContent = doc.name;
      link.appendChild(icon);
      link.appendChild(label);
      link.addEventListener("click", (event) => {{
        event.preventDefault();
        setDocLocation(doc.path);
        loadDoc(doc.path);
      }});
      return link;
    }}

    function folderContainsActive(node, active) {{
      if (!active) return false;
      if (node.files.some((doc) => doc.path === active)) return true;
      return node.sortedFolders.some((folder) => folderContainsActive(folder, active));
    }}

    function scheduleSearch() {{
      const query = searchEl.value.trim();
      if (searchTimer) window.clearTimeout(searchTimer);
      searchRequestId += 1;
      if (!query) {{
        searchResults = [];
        searchStatusEl.textContent = "";
        renderList();
        return;
      }}
      searchResults = null;
      searchStatusEl.textContent = "Searching...";
      renderList();
      const requestId = searchRequestId;
      searchTimer = window.setTimeout(() => runSearch(query, requestId), 180);
    }}

    async function runSearch(query, requestId) {{
      try {{
        const response = await fetch(`/api/search?q=${{encodeURIComponent(query)}}`);
        if (!response.ok) throw new Error(`Search failed with HTTP ${{response.status}}`);
        const results = await response.json();
        if (requestId !== searchRequestId || searchEl.value.trim() !== query) return;
        searchResults = results;
        searchStatusEl.textContent = results.length === 1 ? "1 result" : `${{results.length}} results`;
        renderList();
      }} catch (error) {{
        if (requestId !== searchRequestId) return;
        searchResults = [];
        searchStatusEl.textContent = "Search failed";
        listEl.innerHTML = `<div class="empty-state">${{escapeHtml(error.message || String(error))}}</div>`;
      }}
    }}

    async function loadDocs() {{
      const response = await fetch("/api/docs");
      docs = await response.json();
      const requested = docFromLocation();
      const initial = pickDocPath(requested);
      if (requested && initial !== requested) setDocLocation("");
      renderList();
      await loadDoc(initial);
    }}

    function pickDocPath(requested) {{
      if (requested && docs.some((doc) => doc.path === requested)) return requested;
      return (docs[0] || {{ path: "" }}).path;
    }}

    async function loadDoc(path) {{
      activeDocPath = path || "";
      renderList();
      currentPathEl.textContent = path;
      contentEl.innerHTML = "Loading...";
      renderOutline();
      if (!path) {{
        rawLinkEl.removeAttribute("href");
        contentEl.innerHTML = `<div class="empty-state">No Markdown files found.</div>`;
        renderOutline();
        return;
      }}
      rawLinkEl.href = `/raw?path=${{encodeURIComponent(path)}}`;
      const response = await fetch(`/raw?path=${{encodeURIComponent(path)}}`);
      if (!response.ok) {{
        contentEl.innerHTML = `<div class="error">Failed to load ${{escapeHtml(path)}}.</div>`;
        renderOutline();
        return;
      }}
      const markdown = await response.text();
      contentEl.innerHTML = marked.parse(markdown);
      rewriteRelativeLinks(path);
      rewriteRelativeImages(path);
      renderOutline();
      convertMermaidBlocks();
      await mermaid.run({{ querySelector: ".mermaid" }});
      wireDiagramControls(contentEl);
      scrollToLocationHash();
      updateOutlineActive();
    }}

    function renderOutline() {{
      outlineHeadings = Array.from(contentEl.querySelectorAll("h1, h2, h3"))
        .map((heading, index) => {{
          const text = heading.textContent.trim();
          if (!text) return null;
          heading.id = uniqueHeadingId(heading.id || slugifyHeading(text) || `heading-${{index + 1}}`, heading);
          return {{
            id: heading.id,
            level: Number(heading.tagName.slice(1)),
            text,
            node: heading,
          }};
        }})
        .filter(Boolean);

      outlineEl.innerHTML = "";
      const title = document.createElement("div");
      title.className = "outline-title";
      title.textContent = "Outline";
      outlineEl.appendChild(title);

      if (!outlineHeadings.length) {{
        const empty = document.createElement("div");
        empty.className = "outline-empty";
        empty.textContent = "No headings";
        outlineEl.appendChild(empty);
        return;
      }}

      const list = document.createElement("div");
      list.className = "outline-list";
      outlineHeadings.forEach((heading) => {{
        const link = document.createElement("a");
        link.href = `#${{encodeURIComponent(heading.id)}}`;
        link.className = `outline-link level-${{heading.level}}`;
        link.dataset.headingId = heading.id;
        link.textContent = heading.text;
        link.title = heading.text;
        link.addEventListener("click", (event) => {{
          event.preventDefault();
          heading.node.scrollIntoView({{ behavior: "smooth", block: "start" }});
          const url = new URL(window.location.href);
          url.hash = heading.id;
          history.replaceState({{ path: activeDocPath, hash: heading.id }}, "", url);
          setActiveOutlineLink(heading.id);
        }});
        list.appendChild(link);
      }});
      outlineEl.appendChild(list);
      updateOutlineActive();
    }}

    function uniqueHeadingId(baseId, heading) {{
      const used = new Set();
      contentEl.querySelectorAll("[id]").forEach((node) => {{
        if (node !== heading) used.add(node.id);
      }});
      let candidate = baseId;
      let counter = 2;
      while (used.has(candidate)) {{
        candidate = `${{baseId}}-${{counter}}`;
        counter += 1;
      }}
      return candidate;
    }}

    function slugifyHeading(value) {{
      return value
        .trim()
        .toLowerCase()
        .replace(/\\s+/g, "-")
        .replace(/[\\u0000-\\u002f\\u003a-\\u0040\\u005b-\\u0060\\u007b-\\u007f]+/g, "")
        .replace(/-+/g, "-")
        .replace(/^-|-$/g, "");
    }}

    function setActiveOutlineLink(id) {{
      outlineEl.querySelectorAll(".outline-link").forEach((link) => {{
        link.classList.toggle("active", link.dataset.headingId === id);
      }});
    }}

    function updateOutlineActive() {{
      if (!outlineHeadings.length) return;
      let active = outlineHeadings[0];
      outlineHeadings.forEach((heading) => {{
        if (heading.node.getBoundingClientRect().top <= 96) active = heading;
      }});
      setActiveOutlineLink(active.id);
    }}

    function scheduleOutlineActiveUpdate() {{
      if (outlineScrollPending) return;
      outlineScrollPending = true;
      requestAnimationFrame(() => {{
        outlineScrollPending = false;
        updateOutlineActive();
      }});
    }}

    function scrollToLocationHash() {{
      if (!window.location.hash) return;
      const targetId = decodeURIComponent(window.location.hash.slice(1));
      if (!targetId) return;
      const target = document.getElementById(targetId);
      if (!target) return;
      target.scrollIntoView({{ block: "start" }});
      setActiveOutlineLink(targetId);
    }}

    function convertMermaidBlocks() {{
      contentEl.querySelectorAll("pre code.language-mermaid").forEach((code) => {{
        const shell = document.createElement("section");
        shell.className = "diagram-shell";
        shell.dataset.zoom = "1";
        shell.innerHTML = `
          <div class="diagram-toolbar">
            <button class="icon-button" type="button" data-diagram-action="zoom-out" title="Zoom out">−</button>
            <button class="icon-button" type="button" data-diagram-action="zoom-reset" title="Reset zoom">100%</button>
            <button class="icon-button" type="button" data-diagram-action="zoom-in" title="Zoom in">+</button>
            <button class="icon-button" type="button" data-diagram-action="fullscreen" title="Fullscreen">⛶</button>
          </div>
          <div class="diagram-body"></div>
        `;
        const div = document.createElement("div");
        div.className = "mermaid";
        div.textContent = code.textContent;
        shell.querySelector(".diagram-body").appendChild(div);
        code.closest("pre").replaceWith(shell);
      }});
    }}

    function wireDiagramControls(root) {{
      root.querySelectorAll(".diagram-shell").forEach((shell) => {{
        const diagram = shell.querySelector(".mermaid");
        shell.querySelectorAll("[data-diagram-action]").forEach((button) => {{
          button.addEventListener("click", () => {{
            const action = button.dataset.diagramAction;
            if (action === "fullscreen") {{
              openDiagramFullscreen(diagram);
              return;
            }}
            const current = Number(shell.dataset.zoom || "1");
            const next = action === "zoom-in"
              ? current * 1.25
              : action === "zoom-out"
                ? current / 1.25
                : 1;
            setDiagramZoom(shell, next);
          }});
        }});
      }});
    }}

    function setDiagramZoom(shell, zoom) {{
      const next = Math.min(12, Math.max(0.2, zoom));
      shell.dataset.zoom = String(next);
      const diagram = shell.querySelector(".mermaid");
      if (diagram) {{
        diagram.style.transform = `scale(${{next}})`;
        diagram.style.marginBottom = `${{Math.max(0, diagram.offsetHeight * (next - 1))}}px`;
        diagram.style.marginRight = `${{Math.max(0, diagram.offsetWidth * (next - 1))}}px`;
      }}
      const resetButton = shell.querySelector('[data-diagram-action="zoom-reset"]');
      if (resetButton) resetButton.textContent = formatZoom(next);
    }}

    function openDiagramFullscreen(diagram) {{
      if (!diagram) return;
      activeFullscreenDiagram = {{
        zoom: 1,
        fitZoom: 1,
        node: diagram.cloneNode(true),
      }};
      activeFullscreenDiagram.node.style.transform = "scale(1)";
      activeFullscreenDiagram.node.style.marginBottom = "0";
      activeFullscreenDiagram.node.style.marginRight = "0";
      fullscreenContentEl.innerHTML = "";
      fullscreenContentEl.appendChild(activeFullscreenDiagram.node);
      fullscreenEl.classList.add("open");
      requestAnimationFrame(() => fitFullscreenDiagram());
    }}

    function setFullscreenZoom(zoom) {{
      if (!activeFullscreenDiagram) return;
      activeFullscreenDiagram.zoom = Math.min(12, Math.max(0.1, zoom));
      const node = activeFullscreenDiagram.node;
      node.style.transform = `scale(${{activeFullscreenDiagram.zoom}})`;
      node.style.marginBottom = `${{Math.max(0, node.offsetHeight * (activeFullscreenDiagram.zoom - 1))}}px`;
      node.style.marginRight = `${{Math.max(0, node.offsetWidth * (activeFullscreenDiagram.zoom - 1))}}px`;
      fullscreenZoomResetEl.textContent = formatZoom(activeFullscreenDiagram.zoom);
      fullscreenZoomResetEl.title = `Reset to fit (${{formatZoom(activeFullscreenDiagram.fitZoom || 1)}})`;
    }}

    function fitFullscreenDiagram() {{
      if (!activeFullscreenDiagram) return;
      const node = activeFullscreenDiagram.node;
      node.style.transform = "scale(1)";
      node.style.marginBottom = "0";
      node.style.marginRight = "0";
      const bounds = node.getBoundingClientRect();
      const naturalWidth = Math.max(node.scrollWidth, node.offsetWidth, bounds.width);
      const naturalHeight = Math.max(node.scrollHeight, node.offsetHeight, bounds.height);
      const availableWidth = Math.max(1, fullscreenContentEl.clientWidth - 32);
      const availableHeight = Math.max(1, fullscreenContentEl.clientHeight - 32);
      if (!naturalWidth || !naturalHeight) {{
        activeFullscreenDiagram.fitZoom = 1;
        setFullscreenZoom(activeFullscreenDiagram.fitZoom);
        return;
      }}
      const fit = Math.min(availableWidth / naturalWidth, availableHeight / naturalHeight);
      activeFullscreenDiagram.fitZoom = Math.min(12, Math.max(0.1, fit));
      setFullscreenZoom(activeFullscreenDiagram.fitZoom);
    }}

    function resetFullscreenZoomToFit() {{
      if (!activeFullscreenDiagram) return;
      setFullscreenZoom(activeFullscreenDiagram.fitZoom || 1);
    }}

    function formatZoom(zoom) {{
      return `${{Math.round(zoom * 100)}}%`;
    }}

    function closeDiagramFullscreen() {{
      fullscreenEl.classList.remove("open");
      fullscreenContentEl.innerHTML = "";
      fullscreenContentEl.classList.remove("dragging");
      activeFullscreenDiagram = null;
      fullscreenDrag = null;
      fullscreenZoomResetEl.textContent = "Fit";
      fullscreenZoomResetEl.title = "Reset to fit";
    }}

    function startFullscreenDrag(event) {{
      if (!fullscreenEl.classList.contains("open") || event.button !== 0) return;
      fullscreenDrag = {{
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        scrollLeft: fullscreenContentEl.scrollLeft,
        scrollTop: fullscreenContentEl.scrollTop,
      }};
      fullscreenContentEl.classList.add("dragging");
      fullscreenContentEl.setPointerCapture(event.pointerId);
      event.preventDefault();
    }}

    function moveFullscreenDrag(event) {{
      if (!fullscreenDrag || fullscreenDrag.pointerId !== event.pointerId) return;
      fullscreenContentEl.scrollLeft = fullscreenDrag.scrollLeft - (event.clientX - fullscreenDrag.startX);
      fullscreenContentEl.scrollTop = fullscreenDrag.scrollTop - (event.clientY - fullscreenDrag.startY);
      event.preventDefault();
    }}

    function endFullscreenDrag(event) {{
      if (!fullscreenDrag || fullscreenDrag.pointerId !== event.pointerId) return;
      fullscreenContentEl.classList.remove("dragging");
      fullscreenContentEl.releasePointerCapture(event.pointerId);
      fullscreenDrag = null;
      event.preventDefault();
    }}

    function rewriteRelativeLinks(currentPath) {{
      const baseDir = currentPath.split("/").slice(0, -1).join("/");
      contentEl.querySelectorAll("a[href]").forEach((link) => {{
        const href = link.getAttribute("href") || "";
        if (/^[a-z]+:/i.test(href) || href.startsWith("#") || href.startsWith("/")) return;
        const [target, hash = ""] = href.split("#");
        if (!target.toLowerCase().endsWith(".md")) return;
        const resolved = normalizePath(`${{baseDir}}/${{target}}`);
        link.href = `/?doc=${{encodeURIComponent(resolved)}}${{hash ? "#" + hash : ""}}`;
        link.addEventListener("click", (event) => {{
          event.preventDefault();
          setDocLocation(resolved);
          loadDoc(resolved).then(() => {{
            if (hash) document.getElementById(hash)?.scrollIntoView();
          }});
        }});
      }});
    }}

    function rewriteRelativeImages(currentPath) {{
      const baseDir = currentPath.split("/").slice(0, -1).join("/");
      contentEl.querySelectorAll("img[src]").forEach((img) => {{
        const src = img.getAttribute("src") || "";
        if (/^[a-z]+:/i.test(src) || src.startsWith("/") || src.startsWith("data:")) return;
        const resolved = normalizePath(`${{baseDir}}/${{src}}`);
        img.src = `/file/${{encodeURIComponent(resolved)}}`;
      }});
    }}

    function normalizePath(path) {{
      const parts = [];
      path.split("/").forEach((part) => {{
        if (!part || part === ".") return;
        if (part === "..") parts.pop();
        else parts.push(part);
      }});
      return parts.join("/");
    }}

    function termsForHighlight(query) {{
      return query
        .trim()
        .toLowerCase()
        .split(/\\s+/)
        .filter(Boolean)
        .sort((left, right) => right.length - left.length);
    }}

    function highlightText(value, query) {{
      const text = String(value || "");
      const terms = termsForHighlight(query);
      if (!terms.length) return escapeHtml(text);

      const lower = text.toLowerCase();
      const ranges = [];
      terms.forEach((term) => {{
        let index = lower.indexOf(term);
        while (index >= 0) {{
          ranges.push([index, index + term.length]);
          index = lower.indexOf(term, index + Math.max(1, term.length));
        }}
      }});
      if (!ranges.length) return escapeHtml(text);

      ranges.sort((left, right) => left[0] - right[0] || right[1] - left[1]);
      const merged = [];
      ranges.forEach((range) => {{
        const previous = merged[merged.length - 1];
        if (!previous || range[0] > previous[1]) {{
          merged.push([...range]);
        }} else {{
          previous[1] = Math.max(previous[1], range[1]);
        }}
      }});

      let result = "";
      let cursor = 0;
      merged.forEach((range) => {{
        result += escapeHtml(text.slice(cursor, range[0]));
        result += `<mark>${{escapeHtml(text.slice(range[0], range[1]))}}</mark>`;
        cursor = range[1];
      }});
      return result + escapeHtml(text.slice(cursor));
    }}

    function escapeHtml(value) {{
      return value.replace(/[&<>"']/g, (ch) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }}[ch]));
    }}

    searchEl.addEventListener("input", scheduleSearch);
    sidebarToggleEl.addEventListener("click", () => {{
      const collapsed = layoutEl.classList.toggle("sidebar-collapsed");
      sidebarToggleEl.textContent = collapsed ? "›" : "‹";
      sidebarToggleEl.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
      sidebarToggleEl.setAttribute("aria-label", sidebarToggleEl.title);
    }});
    fullscreenCloseEl.addEventListener("click", closeDiagramFullscreen);
    fullscreenEl.addEventListener("click", (event) => {{
      if (event.target === fullscreenEl) closeDiagramFullscreen();
    }});
    fullscreenContentEl.addEventListener("pointerdown", startFullscreenDrag);
    fullscreenContentEl.addEventListener("pointermove", moveFullscreenDrag);
    fullscreenContentEl.addEventListener("pointerup", endFullscreenDrag);
    fullscreenContentEl.addEventListener("pointercancel", endFullscreenDrag);
    fullscreenContentEl.addEventListener("lostpointercapture", () => {{
      fullscreenContentEl.classList.remove("dragging");
      fullscreenDrag = null;
    }});
    fullscreenZoomInEl.addEventListener("click", () => setFullscreenZoom((activeFullscreenDiagram?.zoom || 1) * 1.25));
    fullscreenZoomOutEl.addEventListener("click", () => setFullscreenZoom((activeFullscreenDiagram?.zoom || 1) / 1.25));
    fullscreenZoomResetEl.addEventListener("click", resetFullscreenZoomToFit);
    window.addEventListener("scroll", scheduleOutlineActiveUpdate, {{ passive: true }});
    window.addEventListener("keydown", (event) => {{
      if (event.key === "Escape" && fullscreenEl.classList.contains("open")) closeDiagramFullscreen();
    }});
    window.addEventListener("resize", () => {{
      if (fullscreenEl.classList.contains("open")) fitFullscreenDiagram();
    }});
    window.addEventListener("popstate", () => loadDoc(pickDocPath(docFromLocation())));
    loadDocs().catch((error) => {{
      contentEl.innerHTML = `<div class="error">${{escapeHtml(error.message || String(error))}}</div>`;
    }});
  </script>
</body>
</html>
"""
    return page.encode("utf-8")


class DocsHandler(BaseHTTPRequestHandler):
    server_version = "WebDocs/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self.send_bytes(
                build_index_html(
                    self.server.docs_title,  # type: ignore[attr-defined]
                    self.server.mermaid_url,  # type: ignore[attr-defined]
                    self.server.marked_url,  # type: ignore[attr-defined]
                ),
                "text/html; charset=utf-8",
            )
            return
        if parsed.path == "/api/docs":
            self.send_json(markdown_files())
            return
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self.send_json(search_markdown_files(query))
            return
        if parsed.path == "/raw":
            query = parse_qs(parsed.query)
            target = resolve_repo_file(query.get("path", [""])[0])
            if target is None or target.suffix.lower() not in MARKDOWN_EXTENSIONS:
                self.send_error(404, "Markdown file not found")
                return
            content = target.read_bytes()
            self.send_bytes(content, "text/markdown; charset=utf-8")
            return
        if parsed.path.startswith("/file/"):
            target = resolve_repo_file(parsed.path.removeprefix("/file/"))
            if target is None:
                self.send_error(404, "File not found")
                return
            mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_bytes(target.read_bytes(), mime_type)
            return
        self.send_error(404, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))

    def send_json(self, value: object) -> None:
        self.send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def send_bytes(self, content: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve repository Markdown documents with Mermaid rendering.",
    )
    parser.set_defaults(git_pull=env_flag("WEB_DOCS_GIT_PULL", True))
    parser.add_argument("--host", default=os.environ.get("WEB_DOCS_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_DOCS_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    parser.add_argument("--title", default=os.environ.get("WEB_DOCS_TITLE", DEFAULT_TITLE))
    parser.add_argument("--git-pull", dest="git_pull", action="store_true", help="Enable background git pull.")
    parser.add_argument("--no-git-pull", dest="git_pull", action="store_false", help="Disable background git pull.")
    parser.add_argument(
        "--git-pull-interval",
        type=positive_int,
        default=env_positive_int("WEB_DOCS_GIT_PULL_INTERVAL", DEFAULT_GIT_PULL_INTERVAL),
        help="Seconds between background git pull attempts.",
    )
    parser.add_argument(
        "--git-pull-remote",
        default=os.environ.get("WEB_DOCS_GIT_PULL_REMOTE", ""),
        help="Optional git remote for pull. Defaults to the current branch upstream.",
    )
    parser.add_argument(
        "--git-pull-branch",
        default=os.environ.get("WEB_DOCS_GIT_PULL_BRANCH", ""),
        help="Optional git branch for pull. When set without a remote, origin is used.",
    )
    parser.add_argument(
        "--git-pull-timeout",
        type=positive_int,
        default=env_positive_int("WEB_DOCS_GIT_PULL_TIMEOUT", DEFAULT_GIT_PULL_TIMEOUT),
        help="Seconds before a git pull attempt times out.",
    )
    parser.add_argument(
        "--mermaid-url",
        default=os.environ.get(
            "WEB_DOCS_MERMAID_URL",
            "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs",
        ),
        help="Browser module URL for Mermaid.",
    )
    parser.add_argument(
        "--marked-url",
        default=os.environ.get(
            "WEB_DOCS_MARKED_URL",
            "https://cdn.jsdelivr.net/npm/marked@12/marked.min.js",
        ),
        help="Browser script URL for marked.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DocsHandler)
    server.docs_title = args.title  # type: ignore[attr-defined]
    server.mermaid_url = args.mermaid_url  # type: ignore[attr-defined]
    server.marked_url = args.marked_url  # type: ignore[attr-defined]
    git_puller: GitAutoPuller | None = None

    url = f"http://{args.host}:{args.port}/"
    print(f"Serving Markdown docs from {REPO_ROOT}")
    print(f"Open {url}")
    print("Press Ctrl+C to stop.")
    if args.git_pull:
        git_puller = GitAutoPuller(
            REPO_ROOT,
            args.git_pull_interval,
            args.git_pull_remote,
            args.git_pull_branch,
            args.git_pull_timeout,
        )
        git_puller.start()
    else:
        print("Git auto-pull disabled by configuration.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping docs server.")
    finally:
        if git_puller is not None:
            git_puller.stop()
        server.server_close()


if __name__ == "__main__":
    main()
