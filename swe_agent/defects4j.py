# swe_agent/defects4j.py
"""
Defects4J CLI wrapper with Docker support.
Supports both local and Docker-based execution.
"""
import os
import subprocess
from pathlib import Path
from .config import D4J_HOME, REPOS_DIR, JDK_MAP, TIMEOUT_COMPILE, TIMEOUT_REG_TEST

BASH_SCRIPT = os.path.join(os.path.dirname(__file__), "tasks/utils/defects4j.sh")

# Docker configuration
D4J_DOCKER_CONTAINER = os.environ.get("D4J_DOCKER_CONTAINER", "defects4j-multi")
D4J_DOCKER_WORKSPACE = "/workspace"
D4J_USE_DOCKER = os.environ.get("D4J_USE_DOCKER", "true").lower() == "true"


def _run_docker_cmd(cmd_in_container: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a command inside the Docker container."""
    docker_cmd = [
        "docker", "exec", "-t", D4J_DOCKER_CONTAINER,
        "bash", "-c", cmd_in_container
    ]
    return subprocess.run(
        docker_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=timeout
    )


def _env(project: str) -> dict:
    """Build subprocess env with correct JAVA_HOME for the project."""
    java_home = JDK_MAP.get(project, os.environ.get("JAVA_HOME", "/usr"))
    return {**os.environ, "JAVA_HOME": java_home,
            "PATH": f"{D4J_HOME}/framework/bin:{os.environ['PATH']}"}


def _run(cmd: list[str], project: str, timeout: int, log_path: Path | None = None):
    """Run a command either locally or in Docker."""
    if D4J_USE_DOCKER:
        # Convert local workdir to docker workspace path
        cmd_str = " ".join(cmd)
        result = _run_docker_cmd(cmd_str, timeout)
    else:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout, env=_env(project)
        )
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(result.stdout + "\n---STDERR---\n" + result.stderr)
    return result


def checkout(project: str, bug_id: int | str, version: str, workdir: Path,
             log_path: Path | None = None):
    """defects4j checkout -p {project} -v {bug_id}{b|f} -w {workdir}"""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if D4J_USE_DOCKER:
        # Use Docker workspace path
        docker_workdir = f"{D4J_DOCKER_WORKSPACE}/{project}-{bug_id}"
        cmd = f"defects4j checkout -p {project} -v {bug_id}{version} -w {docker_workdir}"
        r = _run_docker_cmd(cmd, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"Checkout failed: {r.stderr[:300]}")
        # Sync back to local workdir
        sync_from_docker(docker_workdir, workdir)
    else:
        cmd = ["defects4j", "checkout",
               "-p", project, "-v", f"{bug_id}{version}", "-w", str(workdir)]
        r = _run(cmd, project, timeout=120, log_path=log_path)
        if r.returncode != 0:
            raise RuntimeError(f"Checkout failed: {r.stderr[:300]}")


def compile(workdir: Path, project: str, log_path: Path | None = None):
    """Returns (success: bool, log: str)"""
    if D4J_USE_DOCKER:
        docker_workdir = f"{D4J_DOCKER_WORKSPACE}/{workdir.name}"
        cmd = f"cd {docker_workdir} && defects4j compile"
        r = _run_docker_cmd(cmd, TIMEOUT_COMPILE)
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(r.stdout + "\n---STDERR---\n" + r.stderr)
        return r.returncode == 0, r.stdout + r.stderr
    else:
        r = _run(["defects4j", "compile", "-w", str(workdir)],
                 project, TIMEOUT_COMPILE, log_path)
        return r.returncode == 0, r.stdout + r.stderr


def test(workdir: Path, project: str,
         log_path: Path | None = None) -> tuple[int, list[str], str]:
    """Returns (failing_count, failing_test_names, full_log)"""
    if D4J_USE_DOCKER:
        docker_workdir = f"{D4J_DOCKER_WORKSPACE}/{workdir.name}"
        cmd = f"cd {docker_workdir} && defects4j test"
        r = _run_docker_cmd(cmd, TIMEOUT_REG_TEST)
        log = r.stdout + r.stderr
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(log)
    else:
        r = _run(["defects4j", "test", "-w", str(workdir)],
                 project, TIMEOUT_REG_TEST, log_path)
        log = r.stdout + r.stderr

    # Parse "Failing tests: N" and "  - ClassName::method"
    failing_count = 0
    failing_tests = []
    for line in log.splitlines():
        if line.startswith("Failing tests:"):
            try:
                failing_count = int(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif line.strip().startswith("- "):
            failing_tests.append(line.strip()[2:])
    return failing_count, failing_tests, log


def export_trigger_tests(workdir: Path, project: str) -> list[str]:
    """Export trigger test names."""
    if D4J_USE_DOCKER:
        docker_workdir = f"{D4J_DOCKER_WORKSPACE}/{workdir.name}"
        cmd = f"cd {docker_workdir} && defects4j export -p tests.trigger"
        r = _run_docker_cmd(cmd, timeout=60)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    else:
        r = _run(["defects4j", "export", "-p", "tests.trigger", "-w", str(workdir)],
                 project, timeout=60)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def get_modified_classes(workdir: Path, project: str) -> list[str]:
    """Export modified class names."""
    if D4J_USE_DOCKER:
        docker_workdir = f"{D4J_DOCKER_WORKSPACE}/{workdir.name}"
        cmd = f"cd {docker_workdir} && defects4j export -p classes.modified"
        r = _run_docker_cmd(cmd, timeout=60)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    else:
        r = _run(["defects4j", "export", "-p", "classes.modified", "-w", str(workdir)],
                 project, timeout=60)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def sync_to_docker(local_path: Path, docker_path: str = None):
    """Sync files from local to Docker container."""
    if docker_path is None:
        docker_path = f"{D4J_DOCKER_WORKSPACE}/{local_path.name}"

    # Create directory in container
    subprocess.run(
        ["docker", "exec", D4J_DOCKER_CONTAINER, "mkdir", "-p", docker_path],
        capture_output=True
    )

    # Copy files
    subprocess.run(
        ["docker", "cp", f"{local_path}/.", f"{D4J_DOCKER_CONTAINER}:{docker_path}"],
        capture_output=True
    )


def sync_from_docker(docker_path: str, local_path: Path):
    """Sync files from Docker container to local."""
    local_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["docker", "cp", f"{D4J_DOCKER_CONTAINER}:{docker_path}/.", str(local_path)],
        capture_output=True
    )


def run_bash(function: str, project: str, bug_id: str,
             extra_arg1=None, extra_arg2=None):
    """
    Direct bridge to defects4j.sh — mirrors reference implementation's run_bash.
    Used by AutomatedProgramRepair.validate().
    """
    work_dir = os.path.join(REPOS_DIR, f"{project}-{bug_id}")
    java_home = JDK_MAP.get(project, "/usr")

    if D4J_USE_DOCKER:
        docker_work_dir = f"{D4J_DOCKER_WORKSPACE}/{project}-{bug_id}"
        cmd_in_container = f"bash /defects4j/framework/bug-mining/defects4j.sh {function} {project} {bug_id} {docker_work_dir} {java_home} /defects4j {extra_arg1 or ''} {extra_arg2 or ''}"
        result = _run_docker_cmd(cmd_in_container)
    else:
        cmd = ["bash", BASH_SCRIPT, function, project, bug_id,
               work_dir, java_home, D4J_HOME,
               str(extra_arg1) if extra_arg1 else "",
               str(extra_arg2) if extra_arg2 else ""]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)

    if result.stdout.endswith("\n"):
        result.stdout = result.stdout[:-1]
    return result
