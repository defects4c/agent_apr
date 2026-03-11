#!/usr/bin/env bash
set -euo pipefail

ANTHROPIC_BASE_URL="http://157.10.162.82:443" \
ANTHROPIC_API_KEY="dummy" \
ANTHROPIC_AUTH_TOKEN="dummy" \
ANTHROPIC_DEFAULT_OPUS_MODEL="gpt-5.1" \
ANTHROPIC_DEFAULT_SONNET_MODEL="gpt-5.1" \
ANTHROPIC_DEFAULT_HAIKU_MODEL="gpt-5.1" \
claude -p --dangerously-skip-permissions \
"Use readme.md  as the primary guide and .claude/guidance.md  .claude/tutorial.md   to complete the  defects4j agent-based APR job, "


