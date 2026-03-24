# Defects4J Agent-Based APR Results

## Overall Summary

- **Total bugs attempted**: 12
- **Total repaired**: 1
- **Overall repair rate**: 8.3%
- **Total tokens used**: 3,490
- **Total time (sec)**: 41.5
- **Total LLM calls**: 1

## Per-Baseline Performance

| Baseline | Bugs | Repaired | Rate | Tokens (med) | Time (med) | LLM Calls (med) |
|:---------|-----:|---------:|-----:|-------------:|-----------:|----------------:|
| agentless | 3 | 1 | 33.3% | 0 | 0.0s | 0 |
| cot | 3 | 0 | 0.0% | 0 | 0.0s | 0 |
| openhands | 3 | 0 | 0.0% | 0 | 0.0s | 0 |
| reflexion | 3 | 0 | 0.0% | 0 | 0.0s | 0 |

## Failure Case Analysis

### Failures by Reason

#### agentless - Error code: 502
- Count: 2
- Bugs: Lang_5, Math_1

#### cot - 'NoneType' object is not subscriptable
- Count: 1
- Bugs: Lang_1

#### cot - Error code: 502
- Count: 2
- Bugs: Lang_5, Math_1

#### openhands - Error code: 502
- Count: 3
- Bugs: Lang_1, Lang_5, Math_1

#### reflexion - Error code: 502
- Count: 3
- Bugs: Lang_1, Lang_5, Math_1


## Detailed Results

### agentless

- ✓ **Lang_1**: repaired (tokens: 3490, time: 41.5s, calls: 1)
- ✗ **Lang_5**: error (tokens: 0, time: 0.0s, calls: 0)
- ✗ **Math_1**: error (tokens: 0, time: 0.0s, calls: 0)

### cot

- ✗ **Lang_1**: error (tokens: 0, time: 0.0s, calls: 0)
- ✗ **Lang_5**: error (tokens: 0, time: 0.0s, calls: 0)
- ✗ **Math_1**: error (tokens: 0, time: 0.0s, calls: 0)

### openhands

- ✗ **Lang_1**: error (tokens: 0, time: 0.0s, calls: 0)
- ✗ **Lang_5**: error (tokens: 0, time: 0.0s, calls: 0)
- ✗ **Math_1**: error (tokens: 0, time: 0.0s, calls: 0)

### reflexion

- ✗ **Lang_1**: error (tokens: 0, time: 0.0s, calls: 0)
- ✗ **Lang_5**: error (tokens: 0, time: 0.0s, calls: 0)
- ✗ **Math_1**: error (tokens: 0, time: 0.0s, calls: 0)
