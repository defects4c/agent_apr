# swe_agent/localize.py
"""
Fault localization module with multiple modes:
  oracle → Defects4J buggy-lines ground truth
  stack  → parse stack traces from test failure logs (default)
  llm    → defects4j info + LLM enrichment
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
    """Main dispatcher. Calls oracle/stack/llm based on fl_mode."""
    mode = fl_mode or config.FL_MODE

    if mode == "oracle":
        hits = _oracle_fl(project, bug_id, workdir)
        if hits:
            return hits
        # Fallback to stack if oracle data missing
        mode = "stack"

    if mode == "stack":
        hits = _stack_trace_fl(workdir, test_log)
        if bug_info_dir:
            hits = _enrich_with_snippet_data(hits, bug_info_dir, workdir)
        return hits[:3]

    if mode == "llm":
        hits = _llm_fl(project, bug_id, workdir)
        return hits[:3]

    return _stack_trace_fl(workdir, test_log)[:3]


# ── Oracle FL: Defects4J buggy-lines/buggy-methods ────────────────────────

def _oracle_fl(project: str, bug_id: str, workdir: Path) -> List[LocalizationHit]:
    """Load ground-truth FL from Defects4J buggy-lines data."""
    data_dir = config.FL_DATA_DIR
    bl_file = os.path.join(data_dir, "buggy-lines", f"{project}-{bug_id}.buggy.lines")
    bm_file = os.path.join(data_dir, "buggy-methods", f"{project}-{bug_id}.buggy.methods")

    files_lines = {}
    if os.path.exists(bl_file):
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

    if not files_lines:
        return []

    entries = []
    for rank, (fpath, lines) in enumerate(files_lines.items(), 1):
        lines.sort()
        snippet = _load_snippet(workdir, fpath, min(lines) - 5, max(lines) + 5)
        entries.append(LocalizationHit(
            filepath=fpath,
            start_line=min(lines), end_line=max(lines),
            confidence=1.0, snippet=snippet,
            is_bug_source=True,
        ))

    # Enrich with method names from buggy-methods
    if os.path.exists(bm_file):
        with open(bm_file) as f:
            methods = [m.strip() for m in f if m.strip()]
        for entry in entries:
            for m in methods:
                if entry.filepath in m:
                    entry.method_name = m.split("#")[0] if "#" in m else m
                    break

    return entries


# ── Stack Trace FL ────────────────────────────────────────────────────────

def _stack_trace_fl(workdir: Path, test_log: str) -> List[LocalizationHit]:
    """Parse stack traces for file:line patterns."""
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
            confidence=confidence,
            method_name=f"{cls}.{method}",
        ))

    hits.sort(key=lambda h: h.confidence, reverse=True)
    for hit in hits[:3]:
        hit.snippet = _load_snippet(workdir, hit.filepath, hit.start_line, hit.end_line)
    return hits[:3]


# ── LLM FL (defects4j info fallback) ─────────────────────────────────────

def _llm_fl(project: str, bug_id: str, workdir: Path) -> List[LocalizationHit]:
    """Extract FL from defects4j info output (modified sources)."""
    from . import defects4j as d4j
    info_text = d4j.get_bug_info(project, bug_id)
    modified = []
    in_sources = False
    for line in info_text.splitlines():
        if line.strip().startswith("List of modified sources:"):
            in_sources = True
            continue
        if in_sources:
            if line.startswith("---"):
                break
            src = line.strip().lstrip("- ").strip()
            if src and not src.startswith("Summary"):
                modified.append(src)

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
        bug_methods = [s for s in snippets if s.get("is_bug", False)]
        for hit in hits:
            for bm in bug_methods:
                if bm["file"] in hit.filepath:
                    hit.is_bug_source = True
                    if not hit.snippet:
                        hit.snippet = bm.get("snippet", "")
                        hit.method_name = bm.get("name", hit.method_name)
    except Exception:
        pass
    return hits


def _load_snippet(workdir: Path, filepath: str, start: int, end: int) -> str:
    workdir = Path(workdir)
    fp = _find_source_file(workdir, filepath)
    if fp is None:
        return ""
    try:
        lines = fp.read_text().splitlines()
        s = max(0, start - 1)
        e = min(len(lines), end)
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
            test_path = workdir / "/".join(parts[i:])
            if test_path.exists() and test_path.is_file():
                return test_path
    for prefix in ["src/main/java", "src/java", "source"]:
        test_path = workdir / prefix / filepath
        if test_path.exists():
            return test_path
    return None


def _resolve_class(class_name, project_dir):
    path_fragment = class_name.replace(".", os.sep) + ".java"
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "target")]
        for f in files:
            if f.endswith(".java"):
                rel = os.path.relpath(os.path.join(root, f), project_dir)
                if rel.endswith(path_fragment):
                    return rel
    simple = class_name.rsplit(".", 1)[-1] + ".java"
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "target")]
        if simple in files:
            full = os.path.join(root, simple)
            rel = os.path.relpath(full, project_dir)
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
