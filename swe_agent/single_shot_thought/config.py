# swe_agent/config.py
import os

# ── Paths ──────────────────────────────────────────────────────────────────
D4J_HOME         = os.environ.get("D4J_HOME", "/opt/defects4j")
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D4J_FOLDER     = os.environ.get("D4J_FOLDER", os.path.join(_SCRIPT_DIR, "data/defects4j"))
REPOS_DIR        = os.environ.get("REPOS_DIR",  os.path.join(_SCRIPT_DIR, "data/repos"))
WORKSPACE_ROOT   = os.environ.get("WORKSPACE_ROOT", os.path.join(_SCRIPT_DIR, "outputs"))

# ── Defects4J Docker Web API ───────────────────────────────────────────────
D4J_URL              = os.environ.get("D4J_URL", "http://127.0.0.1:8090")
D4J_LOCAL_WORKSPACE  = os.environ.get("D4J_LOCAL_WORKSPACE", os.path.join(_SCRIPT_DIR, "data/repos"))
D4J_CONTAINER_WORKSPACE = os.environ.get("D4J_CONTAINER_WORKSPACE", "/workspace")
D4J_REQUEST_TIMEOUT  = int(os.environ.get("D4J_REQUEST_TIMEOUT", "1800"))

# ── LLM endpoint ──────────────────────────────────────────────────────────
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY",      "11")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.ai2wj.com/v1/")
GPT_MODEL           = os.environ.get("GPT_MODEL",           "Qwen/Qwen3.5-397B-A17B-FP8")

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
# CLI --fl-mode: "oracle" | "stack" | "llm"
FL_MODE = os.environ.get("FL_MODE", "stack")
FL_DATA_DIR = os.environ.get("FL_DATA_DIR", os.path.join(_SCRIPT_DIR, "data"))

# ── JDK routing ───────────────────────────────────────────────────────────
JDK_MAP = {
    "Lang": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Math": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Time": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Chart": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Closure": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Mockito": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Codec": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Compress": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Gson": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Jsoup": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "JxPath": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Cli": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "JacksonCore": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "JacksonDatabind": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "default": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
}

# ── Baseline names ────────────────────────────────────────────────────────
BASELINES_PROMPTING = [
    "standard", "zero_shot_cot", "few_shot_cot", "cot",
    "react", "reflexion", "self_consistency", "tot", "got", "pot",
    "function_calling",
]
BASELINES = BASELINES_PROMPTING
