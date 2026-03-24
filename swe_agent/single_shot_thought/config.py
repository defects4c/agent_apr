# swe_agent/config.py
import os

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Defects4J Docker Web API (the ONLY way we talk to defects4j) ───────────
# All defects4j commands (checkout, compile, test) go through the webapp.
# NO local defects4j installation needed.
D4J_URL              = os.environ.get("D4J_URL", "http://127.0.0.1:8090")
D4J_LOCAL_WORKSPACE  = os.environ.get("D4J_LOCAL_WORKSPACE", "")
D4J_CONTAINER_WORKSPACE = os.environ.get("D4J_CONTAINER_WORKSPACE", "/workspace")
D4J_REQUEST_TIMEOUT  = int(os.environ.get("D4J_REQUEST_TIMEOUT", "1800"))

# ── Paths ──────────────────────────────────────────────────────────────────
# REPOS_DIR: host-side directory where Docker workspace is mounted.
# Must equal D4J_LOCAL_WORKSPACE so we can read checked-out files.
REPOS_DIR = os.environ.get("REPOS_DIR",
    D4J_LOCAL_WORKSPACE or os.path.join(_SCRIPT_DIR, "data/repos"))
WORKSPACE_ROOT = os.environ.get("WORKSPACE_ROOT", os.path.join(_SCRIPT_DIR, "outputs"))

# D4J_FOLDER: local dir with per-bug info (failing_tests, snippet.json).
# If not available, we fetch this data from the Docker container instead.
D4J_FOLDER = os.environ.get("D4J_FOLDER", os.path.join(_SCRIPT_DIR, "data/defects4j"))

# ── LLM endpoint ──────────────────────────────────────────────────────────
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY", "11")
OPENAI_API_BASE_URL = os.environ.get("OPENAI_API_BASE_URL", "https://api.ai2wj.com/v1/")
GPT_MODEL           = os.environ.get("GPT_MODEL", "gpt-5.1")

# ── Budget (pass@k: k = MAX_ATTEMPTS_PER_BUG) ─────────────────────────────
MAX_ATTEMPTS_PER_BUG       = int(os.environ.get("MAX_ATTEMPTS", "30"))
MAX_LLM_CALLS_PER_ATTEMPT  = 5
MAX_LLM_CALLS_PER_BUG      = int(os.environ.get("MAX_LLM_CALLS", "150"))
MAX_TOKENS_PER_BUG         = int(os.environ.get("MAX_TOKENS", "500000"))
MAX_PATCH_LINES            = 200
MAX_FILES_CHANGED          = 2
CONTEXT_LINES_PER_LOCATION = 200
MAX_LOCATIONS_PER_ATTEMPT  = 3

# ── Timeouts ──────────────────────────────────────────────────────────────
TIMEOUT_PATCH_GEN  = 60
TIMEOUT_COMPILE    = 120
TIMEOUT_FUNC_TEST  = 180
TIMEOUT_REG_TEST   = 600

# ── Fault Localization Mode ───────────────────────────────────────────────
FL_MODE = os.environ.get("FL_MODE", "stack")
FL_DATA_DIR = os.environ.get("FL_DATA_DIR", os.path.join(_SCRIPT_DIR, "data"))

# ── JDK (only used by container, not host — kept for compatibility) ───────
JDK_MAP = {k: "/usr/lib/jvm/java-8-openjdk-amd64" for k in [
    "Lang", "Math", "Time", "Chart", "Closure", "Mockito", "Codec",
    "Compress", "Gson", "Jsoup", "JxPath", "Cli", "JacksonCore",
    "JacksonDatabind", "default",
]}

# ── Legacy compat (used by tasks/ but not by the web API pipeline) ─────
D4J_HOME = os.environ.get("D4J_HOME", "/opt/defects4j")  # only for legacy tasks/

# ── Baselines ─────────────────────────────────────────────────────────────
BASELINES_PROMPTING = [
    "standard", "zero_shot_cot", "few_shot_cot", "cot",
    "react", "reflexion", "self_consistency", "tot", "got", "pot",
    "function_calling",
]
BASELINES = BASELINES_PROMPTING

# ── Print config on import for debugging ──────────────────────────────────
print(f"OPENAI_API_KEY {OPENAI_API_KEY[:4]}... OPENAI_BASE_URL: {OPENAI_API_BASE_URL}")
