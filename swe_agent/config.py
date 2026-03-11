# swe_agent/config.py
import os

# ── Paths ──────────────────────────────────────────────────────────────────
D4J_HOME         = os.environ.get("D4J_HOME", "/opt/defects4j")
# Use absolute path based on script location
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D4J_FOLDER     = os.environ.get("D4J_FOLDER", os.path.join(_SCRIPT_DIR, "data/defects4j"))
REPOS_DIR        = os.environ.get("REPOS_DIR",  os.path.join(_SCRIPT_DIR, "data/repos"))
WORKSPACE_ROOT   = os.environ.get("WORKSPACE_ROOT", os.path.join(_SCRIPT_DIR, "outputs"))

# ── LLM endpoint (NEVER hardcode; always read from env) ────────────────────
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY",      "11")
OPENAI_API_BASE_URL = os.environ.get("OPENAI_API_BASE_URL", "http://157.10.162.82:443/v1/")
GPT_MODEL           = os.environ.get("GPT_MODEL",           "gpt-5.1")

# ── Budget (identical across ALL baselines — enforced by BudgetManager) ────
MAX_ATTEMPTS_PER_BUG       = int(os.environ.get("MAX_ATTEMPTS", "5"))
MAX_LLM_CALLS_PER_ATTEMPT  = 5  # Increased from 3 to allow more exploration
MAX_LLM_CALLS_PER_BUG      = 15
MAX_TOKENS_PER_BUG         = 200_000
MAX_PATCH_LINES            = 200
MAX_FILES_CHANGED          = 2
CONTEXT_LINES_PER_LOCATION = 200
MAX_LOCATIONS_PER_ATTEMPT  = 3

# ── Timeouts (seconds) ─────────────────────────────────────────────────────
TIMEOUT_PATCH_GEN  = 60
TIMEOUT_COMPILE    = 120
TIMEOUT_FUNC_TEST  = 180
TIMEOUT_REG_TEST   = 600

# ── JDK routing ────────────────────────────────────────────────────────────
# Map project names to their required JDK versions
JDK_MAP = {
    # Java 8 projects
    "Lang":    os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Math":    os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Time":    os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Chart":   os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Closure": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Codec":   os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Compress": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Crypto":  os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "JacksonCore": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "JacksonDatabind": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "JacksonXml": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Gson":    os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Jsoup":   os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "JxPath":  os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Cli":     os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    # Java 7 projects
    "JFreeChart": os.environ.get("JAVA7_HOME", "/usr/lib/jvm/java-7-openjdk-amd64"),
    # Fallback to JAVA8 or system java
    "default": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
}

# ── Baseline names (use these strings everywhere) ──────────────────────────
# Original 5 APR agent baselines
BASELINES_AGENT = ["agentless", "swe_agent", "openhands", "openclaw", "claude_code"]

# Prompting-strategy baselines (all adapted from the prompting literature)
#   standard         — no scaffold, direct patch request          (control)
#   zero_shot_cot    — "Let's think step by step"                 Kojima et al. NeurIPS 2022
#   few_shot_cot     — hand-written reasoning demonstrations      Wei et al. NeurIPS 2022
#   react            — Thought / Action / Observation loop        Yao et al. ICLR 2023
#   reflexion        — multi-trial verbal RL + memory             Shinn et al. NeurIPS 2023
#   self_consistency — N samples + majority / judge vote          Wang et al. ICLR 2023
#   tot              — branch + evaluate + backtrack (BFS)        Yao et al. NeurIPS 2023
#   got              — graph ops: generate + aggregate + refine   Besta et al. AAAI 2024
#   pot              — model writes executable Python fix         Chen et al. TMLR 2023
#   function_calling — structured tool-use via JSON schemas       OpenAI API (2023)
#   cot              — step-by-step reasoning scaffold            Wei et al. / Kojima et al. 2022
BASELINES_PROMPTING = [
    "standard",
    "zero_shot_cot",
    "few_shot_cot",
    "react",
    "reflexion",
    "self_consistency",
    "tot",
    "got",
    "pot",
    "function_calling",
    "cot",
]

# Combined list used by runner.py and eval.py
BASELINES = BASELINES_AGENT + BASELINES_PROMPTING

# ── Docker configuration ───────────────────────────────────────────────────
D4J_DOCKER_CONTAINER = os.environ.get("D4J_DOCKER_CONTAINER", "defects4j-multi")
D4J_DOCKER_WORKSPACE = "/workspace"
D4J_USE_DOCKER = os.environ.get("D4J_USE_DOCKER", "true").lower() == "true"
