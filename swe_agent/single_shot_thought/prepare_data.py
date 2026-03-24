#!/usr/bin/env python3
"""
Data preparation script for Defects4J APR.
Prepares the data/defects4j folder with required files for each bug:
  - failing_tests: Raw D4J failure output
  - snippet.json: Buggy method snippets + is_bug flags
  - test_snippet.json: Test case snippets + metadata

Usage:
  python -m swe_agent.prepare_data --project Lang --bug 1 --d4j-home /opt/defects4j
  python -m swe_agent.prepare_data --batch benchmarks/defects4j_small.txt --d4j-home /opt/defects4j
"""
import argparse
import json
import os
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Optional

from .config import D4J_HOME, JDK_MAP, D4J_FOLDER


def prepare_bug_data(project: str, bug_id: str, d4j_home: str = None) -> bool:
    """
    Prepare data for a single bug.
    Creates data/defects4j/{Project}_{bug_id}/ with:
      - failing_tests
      - snippet.json
      - test_snippet.json
    """
    if d4j_home is None:
        d4j_home = D4J_HOME

    bug_name = f"{project}_{bug_id}"
    output_dir = Path(D4J_FOLDER) / bug_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create temporary workdir
    workdir = Path("/tmp/d4j_prepare") / f"{project}-{bug_id}"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        # Set up environment
        java_home = JDK_MAP.get(project, os.environ.get("JAVA_HOME", "/usr"))
        env = {
            **os.environ,
            "JAVA_HOME": java_home,
            "PATH": f"{d4j_home}/framework/bin:{os.environ.get('PATH', '')}"
        }

        # Step 1: Checkout buggy version
        print(f"  Checking out {bug_name}...")
        checkout_cmd = [
            "defects4j", "checkout",
            "-p", project,
            "-v", f"{bug_id}b",
            "-w", str(workdir)
        ]
        result = subprocess.run(checkout_cmd, capture_output=True, text=True, env=env, timeout=120)
        if result.returncode != 0:
            print(f"  ERROR: Checkout failed: {result.stderr[:200]}")
            return False

        # Step 2: Export failing tests
        print(f"  Exporting failing tests...")
        failing_tests_path = output_dir / "failing_tests"
        test_cmd = ["defects4j", "test", "-w", str(workdir)]
        result = subprocess.run(test_cmd, capture_output=True, text=True, env=env, timeout=600)
        
        # Parse failing tests from output
        failing_tests_content = parse_test_output(result.stdout + result.stderr)
        failing_tests_path.write_text(failing_tests_content)

        if not failing_tests_content.strip():
            print(f"  WARNING: No failing tests found for {bug_name}")
            # Still create empty file for consistency
            failing_tests_path.write_text("# No failing tests detected\n")

        # Step 3: Export trigger tests
        trigger_cmd = ["defects4j", "export", "-p", "tests.trigger", "-w", str(workdir)]
        result = subprocess.run(trigger_cmd, capture_output=True, text=True, env=env, timeout=60)
        trigger_tests = [l.strip() for l in result.stdout.splitlines() if l.strip()]

        # Step 4: Export modified classes
        modified_cmd = ["defects4j", "export", "-p", "classes.modified", "-w", str(workdir)]
        result = subprocess.run(modified_cmd, capture_output=True, text=True, env=env, timeout=60)
        modified_classes = [l.strip() for l in result.stdout.splitlines() if l.strip()]

        # Step 5: Generate snippet.json for modified classes
        print(f"  Generating snippet.json...")
        snippets = []
        for class_name in modified_classes:
            snippet_info = extract_class_snippet(workdir, class_name, modified_classes)
            if snippet_info:
                snippet_info["is_bug"] = True  # Mark as buggy
                snippets.append(snippet_info)

        # Also include potentially related classes (from stack traces)
        if failing_tests_path.exists():
            stack_classes = extract_classes_from_stack_trace(failing_tests_path.read_text())
            for class_name in stack_classes:
                if not any(s["name"] == class_name for s in snippets):
                    snippet_info = extract_class_snippet(workdir, class_name)
                    if snippet_info:
                        snippet_info["is_bug"] = False  # Not confirmed buggy
                        snippets.append(snippet_info)

        (output_dir / "snippet.json").write_text(json.dumps(snippets, indent=2))

        # Step 6: Generate test_snippet.json
        print(f"  Generating test_snippet.json...")
        test_snippets = []
        for trigger_test in trigger_tests:
            test_info = extract_test_snippet(workdir, trigger_test)
            if test_info:
                test_snippets.append(test_info)

        (output_dir / "test_snippet.json").write_text(json.dumps(test_snippets, indent=2))

        print(f"  SUCCESS: Prepared {bug_name}")
        return True

    except subprocess.TimeoutExpired:
        print(f"  ERROR: Timeout while preparing {bug_name}")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False
    finally:
        # Cleanup workdir
        if workdir.exists():
            shutil.rmtree(workdir)


def parse_test_output(test_output: str) -> str:
    """Parse defects4j test output to extract failing test info."""
    lines = test_output.splitlines()
    result_lines = []
    current_test = None
    current_error = []
    in_stack_trace = False

    for line in lines:
        if line.startswith("  - "):
            # Save previous test if exists
            if current_test and current_error:
                result_lines.append(f"--- {current_test}")
                result_lines.extend(current_error)
            
            current_test = line.strip()[2:]  # Remove "  - " prefix
            current_error = []
            in_stack_trace = False
        elif current_test and line.strip():
            if line.startswith("\tat"):
                in_stack_trace = True
            current_error.append(line)

    # Save last test
    if current_test and current_error:
        result_lines.append(f"--- {current_test}")
        result_lines.extend(current_error)

    return "\n".join(result_lines)


def extract_class_snippet(workdir: Path, class_name: str, modified_classes: List[str] = None) -> Optional[Dict]:
    """Extract method snippet for a class."""
    # Convert class name to file path
    # e.g., org.apache.commons.lang3.MathUtils -> src/main/java/org/apache/commons/lang3/MathUtils.java
    parts = class_name.split(".")
    java_file = "/".join(parts) + ".java"
    
    # Try different source directories
    for src_dir in ["src/main/java", "src/java", "src"]:
        file_path = workdir / src_dir / java_file
        if file_path.exists():
            content = file_path.read_text()
            lines = content.splitlines()
            
            # Find method boundaries (simplified - just return full class for now)
            # In a real implementation, we'd use AST parsing
            return {
                "name": class_name,
                "file": f"{src_dir}/{java_file}",
                "begin_line": 1,
                "end_line": len(lines),
                "snippet": content[:2000],  # Truncate for brevity
                "is_bug": class_name in (modified_classes or [])
            }
    return None


def extract_test_snippet(workdir: Path, test_signature: str) -> Optional[Dict]:
    """Extract test method snippet."""
    # Parse test signature: org.apache.commons.lang3.MathUtilsTest::testMin
    if "::" not in test_signature:
        return None
    
    class_part, method_part = test_signature.split("::")
    method_name = method_part.rstrip("()")
    
    parts = class_part.split(".")
    java_file = "/".join(parts) + ".java"
    
    for src_dir in ["src/test/java", "test"]:
        file_path = workdir / src_dir / java_file
        if file_path.exists():
            content = file_path.read_text()
            lines = content.splitlines()
            
            # Find method start
            method_start = None
            method_end = None
            for i, line in enumerate(lines):
                if method_name in line and ("public void" in line or "@Test" in line):
                    method_start = max(0, i - 2)
                if method_start is not None and method_end is None:
                    if line.strip().startswith("}") and i > method_start:
                        method_end = i + 1
                        break
            
            if method_start is None:
                method_start = 0
            if method_end is None:
                method_end = min(len(lines), method_start + 50)
            
            snippet = "\n".join(lines[method_start:method_end])
            
            return {
                "signature": test_signature,
                "file": f"{src_dir}/{java_file}",
                "begin_line": method_start + 1,
                "end_line": method_end,
                "snippet": snippet,
                "child_classes": [],
                "child_ranges": []
            }
    return None


def extract_classes_from_stack_trace(failing_content: str) -> List[str]:
    """Extract class names from stack trace."""
    import re
    classes = set()
    pattern = r"at\s+([\w.$]+)\."
    for match in re.findall(pattern, failing_content):
        # Filter out test classes and JDK internals
        if not match.startswith(("sun.", "java.", "org.junit", "junit.")):
            classes.add(match.rsplit(".", 1)[0] if "." in match else match)
    return list(classes)


def prepare_batch(bug_list_file: str, d4j_home: str = None):
    """Prepare data for a batch of bugs."""
    with open(bug_list_file) as f:
        bug_lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    
    success = 0
    failure = 0
    for bug_line in bug_lines:
        sep = "_" if "_" in bug_line else "-"
        if sep not in bug_line:
            print(f"  SKIP: Invalid format: {bug_line}")
            continue
        
        project, bug_id = bug_line.split(sep, 1)
        if prepare_bug_data(project, bug_id, d4j_home):
            success += 1
        else:
            failure += 1
    
    print(f"\nPrepared {success} bugs, {failure} failures")


def main():
    parser = argparse.ArgumentParser(description="Prepare Defects4J data for APR")
    parser.add_argument("--project", help="Project name (e.g., Lang)")
    parser.add_argument("--bug", help="Bug ID (e.g., 1)")
    parser.add_argument("--batch", help="File with list of bugs")
    parser.add_argument("--d4j-home", default=os.environ.get("D4J_HOME", "/opt/defects4j"),
                       help="Defects4J home directory")
    
    args = parser.parse_args()
    
    if args.project and args.bug:
        prepare_bug_data(args.project, args.bug, args.d4j_home)
    elif args.batch:
        prepare_batch(args.batch, args.d4j_home)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
