#!/usr/bin/env python3
"""
smoke_test.py — Verify environment + trace a known patch end-to-end.

Usage:
    python smoke_test.py                                # basic checks
    python smoke_test.py --deep --project Chart --bug 1 # full pipeline test
"""
import argparse, json, os, sys, time
import requests

D4J_URL = os.environ.get("D4J_URL", "http://127.0.0.1:8091")
D4J_LOCAL_WS = os.environ.get("D4J_LOCAL_WORKSPACE", "")
D4J_CONTAINER_WS = os.environ.get("D4J_CONTAINER_WORKSPACE", "/workspace")


def shell(cmd, cwd=None):
    cwd = cwd or D4J_CONTAINER_WS
    try:
        r = requests.post(f"{D4J_URL}/api/exec-shell",
                          json={"cmd": cmd, "cwd": cwd}, timeout=600)
        d = r.json()
        return d.get("returncode", 1), d.get("stdout", ""), d.get("stderr", "")
    except Exception as e:
        return 1, "", str(e)


def d4j(args, cwd=None):
    cwd = cwd or D4J_CONTAINER_WS
    r = requests.post(f"{D4J_URL}/api/exec",
                      json={"args": args, "cwd": cwd}, timeout=600)
    d = r.json()
    return d.get("returncode", 1), d.get("stdout", ""), d.get("stderr", "")


def ok(m):  print(f"  ✓ {m}")
def fail(m): print(f"  ✗ {m}")
def info(m): print(f"  ℹ {m}")


def basic_checks():
    print("\n=== 1. Environment ===")
    for var in ["D4J_URL", "D4J_LOCAL_WORKSPACE", "D4J_CONTAINER_WORKSPACE",
                "OPENAI_API_KEY", "GPT_MODEL"]:
        v = os.environ.get(var, "")
        (ok if v else fail)(f"{var}={'...' if 'KEY' in var and v else v or '(not set)'}")

    print("\n=== 2. Docker Web API ===")
    try:
        r = requests.get(f"{D4J_URL}/health", timeout=5)
        ok(f"{D4J_URL} → HTTP {r.status_code}")
    except Exception as e:
        fail(f"Cannot reach {D4J_URL}: {e}")
        return False

    print("\n=== 3. Shell Execution ===")
    rc, out, _ = shell("echo OK && which defects4j")
    if "OK" in out:
        ok(f"Shell works. defects4j at: {out.splitlines()[-1].strip() if len(out.splitlines())>1 else '?'}")
    else:
        fail("Shell execution failed")
        return False

    print("\n=== 4. Volume Mount ===")
    if not D4J_LOCAL_WS:
        fail("D4J_LOCAL_WORKSPACE not set — cannot test volume mount")
        return True

    marker_host = os.path.join(D4J_LOCAL_WS, ".smoke_marker")
    marker_container = f"{D4J_CONTAINER_WS}/.smoke_marker"

    # HOST → Docker
    open(marker_host, "w").write("HOST_WROTE")
    rc, out, _ = shell(f"cat {marker_container}")
    if "HOST_WROTE" in out:
        ok("HOST → Docker file visible")
    else:
        fail("HOST → Docker: file NOT visible!")

    # Docker → HOST
    shell(f"echo DOCKER_WROTE > {D4J_CONTAINER_WS}/.smoke_marker2")
    m2 = os.path.join(D4J_LOCAL_WS, ".smoke_marker2")
    if os.path.exists(m2) and "DOCKER_WROTE" in open(m2).read():
        ok("Docker → HOST file visible")
    else:
        fail("Docker → HOST: file NOT visible!")

    for f in [marker_host, m2]:
        if os.path.exists(f): os.unlink(f)
    shell(f"rm -f {D4J_CONTAINER_WS}/.smoke_marker*")
    return True


def deep_test(project, bug_id):
    cdir = f"{D4J_CONTAINER_WS}/{project}-{bug_id}"
    host_dir = os.path.join(D4J_LOCAL_WS, f"{project}-{bug_id}") if D4J_LOCAL_WS else ""

    print(f"\n{'='*60}")
    print(f"  DEEP TEST: {project}-{bug_id}")
    print(f"  Container: {cdir}")
    print(f"  Host:      {host_dir}")
    print(f"{'='*60}")

    # ── Checkout ──
    print("\n--- Checkout ---")
    shell(f"rm -rf {cdir}")
    rc, out, err = d4j(["checkout", "-p", project, "-v", f"{bug_id}b", "-w", cdir])
    if rc != 0:
        fail(f"Checkout: {err[:200]}")
        return
    ok("Checkout")

    # ── Pre-patch test ──
    print("\n--- Pre-patch test ---")
    rc, out, _ = shell(f"cd {cdir} && defects4j test")
    for line in out.splitlines():
        if "Failing tests:" in line:
            info(line.strip())
            break

    # ── Find the target file ──
    if project == "Chart" and bug_id == "1":
        target = "source/org/jfree/chart/renderer/category/AbstractCategoryItemRenderer.java"
    else:
        rc, out, _ = shell(f"cd {cdir} && defects4j export -p classes.modified")
        cls = out.strip().splitlines()[0] if out.strip() else ""
        info(f"Modified class: {cls}")
        target = None  # Can't do known patch for arbitrary bugs

    if not target:
        info("Skipping known-patch test (not Chart-1)")
        shell(f"rm -rf {cdir}")
        return

    # ── Verify buggy line exists ──
    print("\n--- Buggy code ---")
    rc, out, _ = shell(f"grep -n 'dataset != null' {cdir}/{target}")
    info(f"Buggy line: {out.strip()}")

    # ══════════════════════════════════════════════════════════════
    # TEST A: Apply patch INSIDE Docker (no volume mount dependency)
    # ══════════════════════════════════════════════════════════════
    print("\n--- TEST A: Patch inside Docker ---")
    rc, _, _ = shell(f"sed -i 's/if (dataset != null)/if (dataset == null)/' {cdir}/{target}")
    ok("sed applied inside Docker")

    rc, out, _ = shell(f"cd {cdir} && git diff")
    if "dataset == null" in out:
        ok(f"git diff shows change ({len(out.splitlines())} lines)")
        for line in out.splitlines():
            if line.startswith("+") and "dataset" in line:
                info(f"  {line}")
            elif line.startswith("-") and "dataset" in line:
                info(f"  {line}")
    else:
        fail("git diff EMPTY after sed!")

    # Clean + compile
    shell(f"cd {cdir} && rm -rf build/classes build/tests 2>/dev/null; true")
    rc, out, err = shell(f"cd {cdir} && defects4j compile")
    if rc == 0 and "BUILD FAILED" not in out:
        ok("Compile")
    else:
        fail(f"Compile: {(out+err)[-200:]}")
        shell(f"cd {cdir} && git checkout -- .")
        return

    # Trigger test
    rc, out, _ = shell(f"cd {cdir} && defects4j test")
    for line in out.splitlines():
        if "Failing tests:" in line:
            count = line.split(":")[-1].strip()
            if count == "0":
                ok(f"★ TEST A PASSES: {line.strip()}")
            else:
                fail(f"TEST A still fails: {line.strip()}")
                # Show error
                for l2 in out.splitlines():
                    if l2.strip().startswith("- "):
                        info(f"  {l2.strip()}")
            break

    # Revert
    shell(f"cd {cdir} && git checkout -- .")

    # ══════════════════════════════════════════════════════════════
    # TEST B: Apply patch on HOST, verify Docker sees it
    # ══════════════════════════════════════════════════════════════
    print("\n--- TEST B: Patch on HOST → compile in Docker ---")
    if not host_dir or not os.path.isdir(host_dir):
        fail(f"Host dir does not exist: {host_dir}")
        return

    host_file = os.path.join(host_dir, target)
    if not os.path.exists(host_file):
        fail(f"Host file does not exist: {host_file}")
        return

    content = open(host_file).read()
    if "dataset != null" not in content:
        fail("Buggy pattern not found in host file (already patched?)")
        return

    # Apply on HOST
    new_content = content.replace("if (dataset != null)", "if (dataset == null)", 1)
    open(host_file, "w").write(new_content)
    ok("Patch written on HOST")

    # Check Docker sees it
    rc, out, _ = shell(f"grep -c 'dataset == null' {cdir}/{target}")
    if out.strip() and int(out.strip()) > 0:
        ok("Docker sees HOST change via volume mount")
    else:
        fail("Docker does NOT see HOST change!")
        # Revert and return
        open(host_file, "w").write(content)
        return

    # git diff inside Docker
    rc, diff_out, _ = shell(f"cd {cdir} && git diff")
    if diff_out.strip():
        ok(f"git diff inside Docker: {len(diff_out.splitlines())} lines")
    else:
        fail("git diff EMPTY in Docker even though grep found the change!")
        info("This means git doesn't detect the change — timestamp/inode issue")

    # Touch the file to update timestamp
    shell(f"touch {cdir}/{target}")

    # Clean + compile
    shell(f"cd {cdir} && rm -rf build/classes build/tests 2>/dev/null; true")
    rc, out, err = shell(f"cd {cdir} && defects4j compile")
    if rc == 0 and "BUILD FAILED" not in out:
        ok("Compile from HOST patch")
    else:
        fail(f"Compile failed: {(out+err)[-200:]}")
        open(host_file, "w").write(content)
        return

    # Test
    rc, out, _ = shell(f"cd {cdir} && defects4j test")
    for line in out.splitlines():
        if "Failing tests:" in line:
            count = line.split(":")[-1].strip()
            if count == "0":
                ok(f"★ TEST B PASSES: {line.strip()}")
            else:
                fail(f"TEST B still fails: {line.strip()}")
                for l2 in out.splitlines():
                    if l2.strip().startswith("- "):
                        info(f"  {l2.strip()}")
            break

    # Revert
    open(host_file, "w").write(content)
    shell(f"cd {cdir} && git checkout -- .")
    ok("Reverted")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--deep", action="store_true")
    p.add_argument("--project", default="Chart")
    p.add_argument("--bug", default="1")
    args = p.parse_args()

    if basic_checks():
        if args.deep:
            deep_test(args.project, args.bug)
    print()
