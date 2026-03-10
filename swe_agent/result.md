# Defects4J Agent-Based APR Results

## Overall Summary

- **Total bugs attempted**: 15
- **Total repaired**: 11
- **Overall repair rate**: 73.3%
- **Total tokens used**: 239,367
- **Total time (sec)**: 2608.4
- **Total LLM calls**: 128

## Per-Baseline Performance

| Baseline | Bugs | Repaired | Rate | Tokens (med) | Time (med) | LLM Calls (med) |
|:---------|-----:|---------:|-----:|-------------:|-----------:|----------------:|
| agentless | 3 | 2 | 66.7% | 3,421 | 95.1s | 1 |
| claude_code | 3 | 3 | 100.0% | 14,681 | 146.5s | 5 |
| openclaw | 3 | 2 | 66.7% | 15,687 | 203.5s | 10 |
| openhands | 3 | 2 | 66.7% | 28,141 | 253.4s | 15 |
| swe_agent | 3 | 2 | 66.7% | 14,991 | 150.8s | 10 |

## Failure Case Analysis

### Failures by Reason

#### agentless - PATCH_APPLY_HUNK_FAILED
- Count: 1
- Bugs: Math_1

#### openclaw - EMPTY_DIFF
- Count: 1
- Bugs: Math_1

#### openhands - EMPTY_DIFF
- Count: 1
- Bugs: Lang_5

#### swe_agent - EMPTY_DIFF
- Count: 1
- Bugs: Math_1


## Detailed Results

### agentless

- ✓ **Lang_1**: repaired (tokens: 3421, time: 79.9s, calls: 1)
- ✓ **Lang_5**: repaired (tokens: 3331, time: 95.1s, calls: 1)
- ✗ **Math_1**: unrepaired (tokens: 13814, time: 179.2s, calls: 5)

### claude_code

- ✓ **Lang_1**: repaired (tokens: 9020, time: 116.0s, calls: 5)
- ✓ **Lang_5**: repaired (tokens: 23840, time: 191.0s, calls: 15)
- ✓ **Math_1**: repaired (tokens: 14681, time: 146.5s, calls: 5)

### openclaw

- ✓ **Lang_1**: repaired (tokens: 15687, time: 203.5s, calls: 6)
- ✓ **Lang_5**: repaired (tokens: 18696, time: 288.2s, calls: 10)
- ✗ **Math_1**: unrepaired (tokens: 11246, time: 199.1s, calls: 10)

### openhands

- ✓ **Lang_1**: repaired (tokens: 28141, time: 254.9s, calls: 15)
- ✗ **Lang_5**: unrepaired (tokens: 33180, time: 253.4s, calls: 15)
- ✓ **Math_1**: repaired (tokens: 13828, time: 142.4s, calls: 5)

### swe_agent

- ✓ **Lang_1**: repaired (tokens: 13330, time: 136.3s, calls: 10)
- ✓ **Lang_5**: repaired (tokens: 14991, time: 172.1s, calls: 10)
- ✗ **Math_1**: unrepaired (tokens: 22161, time: 150.8s, calls: 15)
