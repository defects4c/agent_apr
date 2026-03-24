# swe_agent/localize.py
"""
Fault localization with multiple modes:
  oracle → buggy-lines ground truth (local data OR from Docker container)
  stack  → parse stack traces from test failure logs
  llm    → defects4j info → modified sources → resolve files
"""
import csv
import json
import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
from . import config

@dataclass
class LocalizationHit:
    filepath: str
    start_line: int
    end_line: int
    confidence: float
    snippet: str = ""
    method_name: str = ""
    is_bug_source: bool = False


def localize(workdir: Path, project: str, test_log: str,
             bug_info_dir: Optional[str] = None,
             fl_mode: str = None, bug_id: str = "") -> List[LocalizationHit]:
    """Main dispatcher."""
    mode = fl_mode or config.FL_MODE

    if mode == "oracle":
        hits = _oracle_fl(project, bug_id, workdir)
        if hits:
            return hits
        # Fallback: try llm mode (defects4j info → modified sources)
        print(f"  ⚠ Oracle FL data not found for {project}-{bug_id}, falling back to llm FL")
        hits = _llm_fl(project, bug_id, workdir)
        if hits:
            return hits
        # Last resort: stack trace
        print(f"  ⚠ LLM FL also empty, falling back to stack trace FL")
        return _stack_trace_fl(workdir, test_log)[:3]

    if mode == "llm":
        hits = _llm_fl(project, bug_id, workdir)
        if hits:
            return hits
        return _stack_trace_fl(workdir, test_log)[:3]

    # Default: stack
    hits = _stack_trace_fl(workdir, test_log)
    if bug_info_dir and os.path.isdir(bug_info_dir):
        hits = _enrich_with_snippet_data(hits, bug_info_dir, workdir)
    return hits[:3]


# ── Oracle FL ─────────────────────────────────────────────────────────────

def _oracle_fl(project: str, bug_id: str, workdir: Path) -> List[LocalizationHit]:
    """Load ground-truth FL.
    Try 1: local buggy-lines files (FL_DATA_DIR/buggy-lines/Chart-1.buggy.lines)
    Try 2: get modified classes from Docker container via defects4j export
    """
    # Try local data files first
    data_dir = config.FL_DATA_DIR
    bl_file = os.path.join(data_dir, "buggy-lines", f"{project}-{bug_id}.buggy.lines")
    if os.path.exists(bl_file):
        return _parse_buggy_lines(bl_file, project, bug_id, workdir)

    # Try alternate naming
    bl_file2 = os.path.join(data_dir, "buggy-lines", f"{project}_{bug_id}.buggy.lines")
    if os.path.exists(bl_file2):
        return _parse_buggy_lines(bl_file2, project, bug_id, workdir)

    # No local data → fall through (caller will fallback to llm/stack)
    return []


def _parse_buggy_lines(bl_file: str, project: str, bug_id: str, workdir: Path) -> List[LocalizationHit]:
    files_lines = {}
    with open(bl_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("#")
            if len(parts) >= 2:
                fpath = parts[0]
                try:
                    lineno = int(parts[1])
                except ValueError:
                    continue
                files_lines.setdefault(fpath, []).append(lineno)

    entries = []
    for fpath, lines in files_lines.items():
        lines.sort()
        snippet = _load_snippet(workdir, fpath, min(lines) - 5, max(lines) + 5)
        entries.append(LocalizationHit(
            filepath=fpath,
            start_line=min(lines), end_line=max(lines),
            confidence=1.0, snippet=snippet, is_bug_source=True,
        ))
    return entries


# ── Stack Trace FL ────────────────────────────────────────────────────────

def _stack_trace_fl(workdir: Path, test_log: str) -> List[LocalizationHit]:
    hits = []
    pattern = r"at\s+([\w.$]+)\.(\w+)\(([\w/.]+\.java):(\d+)\)"
    matches = re.findall(pattern, test_log)

    seen = set()
    for cls, method, filepath, line_no in matches:
        if "/test/" in filepath or "Test" in filepath or filepath.startswith("java/"):
            continue
        key = f"{filepath}:{line_no}"
        if key in seen:
            continue
        seen.add(key)

        confidence = max(0.1, 1.0 - len(hits) * 0.15)
        start_line = max(1, int(line_no) - 10)
        end_line = int(line_no) + 30

        hits.append(LocalizationHit(
            filepath=filepath,
            start_line=start_line, end_line=end_line,
            confidence=confidence, method_name=f"{cls}.{method}",
        ))

    hits.sort(key=lambda h: h.confidence, reverse=True)
    for hit in hits[:3]:
        hit.snippet = _load_snippet(workdir, hit.filepath, hit.start_line, hit.end_line)
    return hits[:3]


# ── LLM FL (defects4j info → modified sources) ───────────────────────────

def _llm_fl(project: str, bug_id: str, workdir: Path) -> List[LocalizationHit]:
    """Get modified sources from defects4j, resolve to actual files."""
    from . import defects4j as d4j

    # Get modified classes from Docker container
    modified = d4j.get_modified_classes(workdir, project)
    if not modified:
        # Fallback: parse defects4j info
        info = d4j.get_bug_info(project, bug_id)
        modified = []
        in_sources = False
        for line in info.splitlines():
            if "List of modified sources:" in line:
                in_sources = True
                continue
            if in_sources:
                if line.startswith("---"):
                    break
                src = line.strip().lstrip("- ").strip()
                if src and not src.startswith("Summary"):
                    modified.append(src)

    if not modified:
        return []

    entries = []
    for rank, class_name in enumerate(modified, 1):
        rel_path = _resolve_class(class_name, workdir)
        if rel_path:
            abs_path = workdir / rel_path
            start, end = _find_class_range(abs_path, class_name)
            snippet = _load_snippet(workdir, rel_path, start, end)
        else:
            rel_path = class_name.replace(".", "/") + ".java"
            start, end, snippet = 1, 100, ""

        entries.append(LocalizationHit(
            filepath=rel_path, start_line=start, end_line=end,
            confidence=0.8, snippet=snippet,
            method_name=class_name.rsplit(".", 1)[-1] if "." in class_name else class_name,
        ))
    return entries


# ── Helpers ───────────────────────────────────────────────────────────────

def _enrich_with_snippet_data(hits, bug_info_dir, workdir):
    snippet_path = os.path.join(bug_info_dir, "snippet.json")
    if not os.path.exists(snippet_path):
        return hits
    try:
        with open(snippet_path) as f:
            snippets = json.load(f)
        for hit in hits:
            for bm in [s for s in snippets if s.get("is_bug", False)]:
                if bm["file"] in hit.filepath:
                    hit.is_bug_source = True
                    if not hit.snippet:
                        hit.snippet = bm.get("snippet", "")
    except Exception:
        pass
    return hits


def _load_snippet(workdir: Path, filepath: str, start: int, end: int) -> str:
    fp = _find_source_file(workdir, filepath)
    if fp is None:
        return ""
    try:
        lines = fp.read_text().splitlines()
        s, e = max(0, start - 1), min(len(lines), end)
        return "\n".join(lines[s:e])
    except Exception:
        return ""


def _find_source_file(workdir, filepath):
    workdir = Path(workdir)
    direct = workdir / filepath
    if direct.exists() and direct.is_file():
        return direct
    if "/" not in filepath and "\\" not in filepath:
        for found in workdir.rglob(filepath):
            if found.is_file() and "/test/" not in str(found) and "Test" not in found.name:
                return found
    if "/" in filepath:
        parts = filepath.split("/")
        for i in range(len(parts)):
            p = workdir / "/".join(parts[i:])
            if p.exists() and p.is_file():
                return p
    for prefix in ["src/main/java", "src/java", "source"]:
        p = workdir / prefix / filepath
        if p.exists():
            return p
    return None


def _resolve_class(class_name, project_dir):
    path_frag = class_name.replace(".", os.sep) + ".java"
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "target")]
        for f in files:
            if f.endswith(".java"):
                rel = os.path.relpath(os.path.join(root, f), project_dir)
                if rel.endswith(path_frag):
                    return rel
    simple = class_name.rsplit(".", 1)[-1] + ".java"
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "target")]
        if simple in files:
            rel = os.path.relpath(os.path.join(root, simple), project_dir)
            if "test" not in rel.lower():
                return rel
    return None


def _find_class_range(file_path, class_name):
    try:
        lines = Path(file_path).read_text().splitlines()
        simple = class_name.rsplit(".", 1)[-1]
        for i, line in enumerate(lines, 1):
            if re.search(rf'\bclass\s+{re.escape(simple)}\b', line):
                return max(1, i - 2), len(lines)
        return 1, len(lines)
    except Exception:
        return 1, 100
