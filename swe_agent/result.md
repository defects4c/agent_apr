# Defects4J Agent-Based APR Results

## Overall Summary

- **Total bugs attempted**: 30
- **Total repaired**: 12
- **Overall repair rate**: 40.0%
- **Total tokens used**: 465,123
- **Total time (sec)**: 3968.6
- **Total LLM calls**: 259

## Per-Baseline Performance

| Baseline | Bugs | Repaired | Rate | Tokens (med) | Time (med) | LLM Calls (med) |
|:---------|-----:|---------:|-----:|-------------:|-----------:|----------------:|
| agentless | 3 | 2 | 66.7% | 3,237 | 79.4s | 1 |
| claude_code | 3 | 2 | 66.7% | 18,372 | 100.2s | 15 |
| cot | 3 | 1 | 33.3% | 8,686 | 129.3s | 5 |
| got | 3 | 0 | 0.0% | 11,420 | 137.0s | 5 |
| openclaw | 3 | 1 | 33.3% | 11,912 | 141.4s | 10 |
| openhands | 3 | 2 | 66.7% | 31,264 | 132.6s | 15 |
| reflexion | 3 | 1 | 33.3% | 7,009 | 86.4s | 5 |
| self_consistency | 3 | 0 | 0.0% | 18,759 | 192.3s | 15 |
| swe_agent | 3 | 3 | 100.0% | 8,329 | 93.9s | 5 |
| tot | 3 | 0 | 0.0% | 23,541 | 200.8s | 15 |

## Failure Case Analysis

### Failures by Reason

#### agentless - PATCH_APPLY_HUNK_FAILED
- Count: 1
- Bugs: Math_1

#### claude_code - EMPTY_DIFF
- Count: 1
- Bugs: Lang_5

#### cot - EMPTY_DIFF
- Count: 2
- Bugs: Lang_5, Math_1

#### got - EMPTY_DIFF
- Count: 3
- Bugs: Lang_1, Lang_5, Math_1

#### openclaw - EMPTY_DIFF
- Count: 2
- Bugs: Lang_5, Math_1

#### openhands - EMPTY_DIFF
- Count: 1
- Bugs: Lang_5

#### reflexion - EMPTY_DIFF
- Count: 2
- Bugs: Lang_5, Math_1

#### self_consistency - EMPTY_DIFF
- Count: 2
- Bugs: Lang_5, Math_1

#### self_consistency - PATCH_APPLY_HUNK_FAILED
- Count: 1
- Bugs: Lang_1

#### tot - EMPTY_DIFF
- Count: 2
- Bugs: Lang_5, Math_1

#### tot - PATCH_APPLY_HUNK_FAILED
- Count: 1
- Bugs: Lang_1


## Detailed Results

### agentless

- ✓ **Lang_1**: repaired (tokens: 3237, time: 67.0s, calls: 1)
- ✓ **Lang_5**: repaired (tokens: 2894, time: 79.4s, calls: 1)
- ✗ **Math_1**: unrepaired (tokens: 13055, time: 106.4s, calls: 5)

### claude_code

- ✓ **Lang_1**: repaired (tokens: 14013, time: 100.2s, calls: 10)
- ✗ **Lang_5**: unrepaired (tokens: 18372, time: 77.9s, calls: 15)
- ✓ **Math_1**: repaired (tokens: 44159, time: 168.4s, calls: 15)

### cot

- ✓ **Lang_1**: repaired (tokens: 7085, time: 129.3s, calls: 3)
- ✗ **Lang_5**: unrepaired (tokens: 8686, time: 112.4s, calls: 5)
- ✗ **Math_1**: unrepaired (tokens: 12937, time: 158.0s, calls: 5)

### got

- ✗ **Lang_1**: unrepaired (tokens: 9935, time: 125.4s, calls: 5)
- ✗ **Lang_5**: unrepaired (tokens: 11648, time: 148.3s, calls: 6)
- ✗ **Math_1**: unrepaired (tokens: 11420, time: 137.0s, calls: 5)

### openclaw

- ✓ **Lang_1**: repaired (tokens: 5088, time: 85.4s, calls: 2)
- ✗ **Lang_5**: unrepaired (tokens: 19215, time: 174.9s, calls: 10)
- ✗ **Math_1**: unrepaired (tokens: 11912, time: 141.4s, calls: 10)

### openhands

- ✓ **Lang_1**: repaired (tokens: 31264, time: 170.9s, calls: 15)
- ✗ **Lang_5**: unrepaired (tokens: 32485, time: 132.6s, calls: 15)
- ✓ **Math_1**: repaired (tokens: 14993, time: 116.7s, calls: 5)

### reflexion

- ✓ **Lang_1**: repaired (tokens: 1423, time: 71.6s, calls: 1)
- ✗ **Lang_5**: unrepaired (tokens: 7009, time: 86.4s, calls: 5)
- ✗ **Math_1**: unrepaired (tokens: 9537, time: 106.6s, calls: 5)

### self_consistency

- ✗ **Lang_1**: unrepaired (tokens: 18233, time: 167.9s, calls: 15)
- ✗ **Lang_5**: unrepaired (tokens: 18759, time: 192.3s, calls: 15)
- ✗ **Math_1**: unrepaired (tokens: 24898, time: 212.7s, calls: 15)

### swe_agent

- ✓ **Lang_1**: repaired (tokens: 8188, time: 82.5s, calls: 5)
- ✓ **Lang_5**: repaired (tokens: 8329, time: 93.9s, calls: 5)
- ✓ **Math_1**: repaired (tokens: 24961, time: 131.7s, calls: 15)

### tot

- ✗ **Lang_1**: unrepaired (tokens: 23541, time: 200.8s, calls: 15)
- ✗ **Lang_5**: unrepaired (tokens: 20690, time: 184.3s, calls: 15)
- ✗ **Math_1**: unrepaired (tokens: 27157, time: 206.3s, calls: 15)
