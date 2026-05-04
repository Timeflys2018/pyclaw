# Prompt Budget Configuration Examples

## Default (ratio mode, auto-adapts to any model)

```json
{
  "agent": {
    "max_context_tokens": 200000,
    "promptBudget": {
      "system_zone_tokens": 4096,
      "dynamic_zone_tokens": 4096
    }
  }
}
```

`output_reserve` auto-detected from model max output. History = everything remaining.

## Explicit output reserve (recommended for Opus 4.6)

```json
{
  "agent": {
    "max_context_tokens": 1000000,
    "promptBudget": {
      "system_zone_tokens": 4096,
      "dynamic_zone_tokens": 4096,
      "output_reserve_tokens": 128000
    }
  }
}
```

Breakdown: remaining=991808, output=128000, history=863808, compaction at 691046.

## Full manual control

```json
{
  "agent": {
    "max_context_tokens": 200000,
    "promptBudget": {
      "system_zone_tokens": 8192,
      "dynamic_zone_tokens": 8192,
      "output_reserve_tokens": 64000,
      "output_reserve_ratio": 0.3
    }
  }
}
```

All values explicit. `output_reserve_ratio` is ignored when `output_reserve_tokens` is set.

## Budget calculation formula

```
remaining      = max_context_tokens - system_zone_tokens - dynamic_zone_tokens
output_reserve = output_reserve_tokens ?? model_max_output ?? int(remaining * output_reserve_ratio)
history_budget = remaining - output_reserve
compaction     = history_budget * compaction.historyThreshold
```

## Typical model configurations

| Model | max_context | max_output | Suggested output_reserve |
|-------|------------|-----------|-------------------------|
| Claude Opus 4.6 | 1M | 128K | 128000 |
| Claude Sonnet 4.6 | 1M | 64K | 64000 |
| GPT-4o | 128K | 16K | 16000 |
| Gemini 2.5 Pro | 1M | 65K | 65000 |
