# Provider Configuration

Discord Pals supports OpenAI-compatible Chat Completions and native Responses, Anthropic Messages, and Gemini endpoints. Configure them under **Config → Providers**. Keep secrets in environment variables or `.env`, or use the dashboard's API key field.

## Quick Setup

Choose a provider preset, enter its name, exact model ID, API key, and base URL, then save. Presets fill connection defaults; they do not choose a model for you. For a custom gateway, choose its API format in **Advanced settings**.

Advanced settings keep environment-variable keys, keyless endpoints, reasoning, generation limits, timeouts, capabilities, headers, and JSON/YAML overrides available. Opening and saving a provider preserves these settings. Chat providers are tried in list order; character-specific preferences remain available below the list. Image providers have a separate quick setup and optional advanced controls.

### Test the Saved Model

**Test model** sends a small text-generation request using the selected provider's saved model, API format, credentials, and request parameters. Save or reload pending edits first. The test does not retry or switch to another configured provider. It may consume provider tokens but sends nothing to Discord.

Results distinguish **Saved settings**, **Model reply**, and untested capabilities. The test rejects empty responses, blocked replies, and replies stopped at the output limit, and checks for thinking-only output. Errors identify the next setting to check without displaying provider response bodies or credentials. A model-list response or valid local configuration is never enough to pass.

The test uses the saved timeout up to a 30-second ceiling; normal conversations may use a longer timeout. A passing result verifies this saved text request, not image input, tool calling, every conversation, or provider availability indefinitely. Image generation retains its separate test.

Known v2.8.0 limitations:

- Gateway-side model fallback can produce a passing result for a different model. The test preserves custom `models` fallback lists and does not compare the returned model identity. Remove model fallback overrides before checking a specific model; a pass with those overrides does not verify the selected model.
- Thinking-only text containing both an explicit thinking block and a `SYSTEM:` marker can pass the reply check. A passing test is not a guarantee that every reasoning format was removed.

The raw JSON editor remains available for advanced configuration. Provider definitions are stored in `providers.json`.

## Basic Shape

```json
{
  "providers": [
    {
      "name": "DeepSeek",
      "url": "https://api.deepseek.com/v1",
      "key_env": "DEEPSEEK_API_KEY",
      "model": "deepseek-chat"
    }
  ],
  "timeout": 60
}
```

The bot tries providers in list order until one succeeds. You can add, reorder, and edit chat and image providers from the dashboard Config page.

For unattended deployments, `python startup.py --init-configs` creates a starter `providers.json` that reads `OPENAI_API_KEY` from the process environment. Docker, Compose, systemd, and host shells can provide those variables without a populated `.env` file.

Common provider fields:

| Field | Use |
| --- | --- |
| `name` | Friendly dashboard/log name. |
| `url` | API base URL. `base_url` is also accepted for compatibility. |
| `key_env` | Environment variable containing the API key. |
| `model` | Model name sent to the provider. |
| `timeout` | Optional per-provider timeout in seconds. |
| `supports_vision` | Set `false` for text-only models. Defaults to vision-capable. |
| `extra_body` | Extra JSON merged into the request body. |
| `include_body` | YAML object merged into the request body. |
| `exclude_body` | YAML list of request-body keys to remove. |

## Local LLMs

For llama.cpp, Ollama, LM Studio, or another local compatible server:

```json
{
  "providers": [
    {
      "name": "Local LLM",
      "url": "http://localhost:8080/v1",
      "model": "local-model",
      "timeout": 600,
      "requires_key": false
    }
  ],
  "timeout": 60
}
```

If the local endpoint does not need a key, omit `key_env` or set `requires_key` to `false`.

## Fallback Chains

Use multiple entries for redundancy:

```json
{
  "providers": [
    {
      "name": "Primary Local",
      "url": "http://localhost:8080/v1",
      "model": "llama-3",
      "requires_key": false,
      "timeout": 600
    },
    {
      "name": "Fallback DeepSeek",
      "url": "https://api.deepseek.com/v1",
      "key_env": "DEEPSEEK_API_KEY",
      "model": "deepseek-chat"
    }
  ],
  "timeout": 60
}
```

Per-character provider preferences are stored by the dashboard, not in character Markdown. The first tiers are named `primary`, `secondary`, and `fallback`; later tiers use `tier_3`, `tier_4`, and so on.

Request preparation failures mark the affected tier as failed and continue to later providers. For example, an unsupported endpoint name with automatic reasoning no longer aborts the whole chain. A configuration error does not disable image support or trigger a text-only retry; correct the API format in the provider editor to restore that tier.

## Reasoning Options

Some providers expose reasoning effort with different request shapes. Discord Pals normalizes the common dashboard fields and sends the selected shape through the compatible request body:

```json
{
  "providers": [
    {
      "name": "GPT 5.5",
      "url": "https://api.linkapi.ai/v1",
      "key_env": "OPENAI_API_KEY",
      "model": "gpt-5.5",
      "temperature": 1,
      "reasoning_effort": "xhigh",
      "reasoning_format": "openai_chat"
    }
  ]
}
```

Supported `reasoning_format` values:

| Value | Request shape |
| --- | --- |
| `openai_chat` | Top-level `reasoning_effort`. |
| `openai_responses` | `reasoning: {"effort": "..."}`. |
| `claude` | `output_config: {"effort": "..."}`. |
| `effort` | Top-level `effort`. |
| `thinking` | `thinking: {"type": "adaptive", "effort": "..."}`. |
| `gemini` | `generationConfig.thinkingConfig.thinkingLevel` for native Gemini thinking levels. |

Values in `extra_body` override normalized fields.

With **Auto**, the native Gemini and Responses endpoints select their corresponding reasoning format. An explicit format remains an override. For Google's native Gemini 3 API, keep the model ID and thinking level separate:

```json
{
  "name": "Gemini",
  "url": "https://generativelanguage.googleapis.com",
  "key_env": "GEMINI_API_KEY",
  "endpoint_type": "gemini",
  "model": "gemini-3.8-flash",
  "reasoning_effort": "high"
}
```

Do not append `-high` to Google's model ID. Use a model returned by the account's [model catalog](https://ai.google.dev/api/models). Model families differ in supported thinking settings: Gemini 3 uses thinking levels; older Gemini models may require `thinkingBudget`. For those models, leave Reasoning Effort at Default and set `generationConfig.thinkingConfig.thinkingBudget` in Extra Body. See [Google's generation configuration](https://ai.google.dev/api/generate-content#ThinkingConfig). Gemini thought parts are retained separately from reply text and are never sent as the character's reply.

## Vision Support

Providers are assumed to support image input unless configured otherwise. For text-only models:

```json
{
  "name": "DeepSeek Reasoner",
  "url": "https://api.deepseek.com/v1",
  "key_env": "DEEPSEEK_API_KEY",
  "model": "deepseek-reasoner",
  "supports_vision": false
}
```

When a user sends an image, vision providers receive multimodal content. Text-only providers receive the text plus an omission note. If a provider is marked vision-capable but rejects image input, the bot retries that request as text-only and treats that provider tier as text-only for the rest of the process.

Emoji and shortcode context remains text-only.

## Endpoint Types

Providers default to the OpenAI-compatible Chat Completions path through the OpenAI SDK, which supports custom params (`include_body`, `exclude_body`, `include_headers`, `extra_body`). Setting `endpoint_type` to another endpoint family routes that provider through the native endpoint adapter instead:

```json
{
  "providers": [
    {
      "name": "Anthropic (native Messages API)",
      "url": "https://api.anthropic.com",
      "key_env": "ANTHROPIC_API_KEY",
      "endpoint_type": "anthropic-messages",
      "model": "claude-sonnet-5",
      "supports_reasoning": true,
      "supports_vision": true
    }
  ]
}
```

Supported `endpoint_type` values are `openai-chat` (default, SDK lane), `openai-responses`, `anthropic-messages`, and `gemini`. The adapter lane also honors `include_body`, `exclude_body`, `include_headers`, and `extra_body`.

Endpoint defaults:

| Endpoint type | URL policy | Auth header |
| --- | --- | --- |
| `openai-chat` | Appends `/v1`, then posts `/chat/completions`. | `Authorization: Bearer ...` |
| `openai-responses` | Appends `/v1`, then posts `/responses`. | `Authorization: Bearer ...` |
| `anthropic-messages` | Does not append a base version; posts `/v1/messages` unless the base already ends in `/v1`. | `x-api-key`, plus `anthropic-version: 2023-06-01` |
| `gemini` | Appends `/v1beta`, then posts `/models/{model}:generateContent`. | `x-goog-api-key` |

The Anthropic Messages API rejects any request without `anthropic-version`, so the adapter always sends it. To pin a different version, set `include_headers` on that provider (for example `anthropic-version: 2026-01-01`) — `include_headers` is applied after the auth headers, so it overrides the default.

Set `append_base_path` to `false` when `url` is already the exact base path the adapter should use. Set `requires_key` to `false` for local gateways that do not need authentication.

`provider_protocol: newapi` is a removed legacy field: configs that still carry it keep routing through the endpoint adapter, and the dashboard clears the field the next time the provider is saved.

## Image Generation Providers

Autonomous DM images use a separate `image_providers` list in `providers.json`. Entries are OpenAI-compatible image-generation clients, tried in order unless `dm_image_generation_preferred_tier` selects a tier from the dashboard. The dashboard Providers tab can manage this list directly; raw JSON is only the advanced fallback.

```json
{
  "image_providers": [
    {
      "name": "OpenAI Images",
      "url": "https://api.openai.com/v1",
      "key_env": "OPENAI_API_KEY",
      "model": "gpt-image-1",
      "size": "1024x1024",
      "timeout": 120
    }
  ]
}
```

Common image provider fields:

| Field | Use |
| --- | --- |
| `name` | Friendly dashboard/log name. |
| `url` | OpenAI-compatible base URL. `base_url` is also accepted. |
| `key_env` | Environment variable containing the API key. |
| `model` | Image model name. Defaults to `gpt-image-1`. |
| `size` | Image size, such as `1024x1024`. |
| `quality`, `style`, `output_format`, `background`, `moderation` | Optional provider-specific image parameters. |
| `response_format` | Optional response format for providers that need it. GPT image models return base64 by default. |
| `extra_body` | Extra JSON passed through to the image generation call. |
| `timeout` | Per-image timeout in seconds. |

## Provider-Specific Bodies

Use `extra_body` for JSON:

```json
{
  "name": "Custom Provider",
  "url": "https://api.example.com/v1",
  "key_env": "API_KEY",
  "model": "model-name",
  "extra_body": {
    "top_k": 20,
    "repetition_penalty": 1.1
  }
}
```

Use `include_body` and `exclude_body` for SillyTavern-style YAML:

```json
{
  "name": "GLM Reasoning Disabled",
  "url": "https://api.z.ai/api/paas/v4",
  "key_env": "ZAI_API_KEY",
  "model": "glm-4.7",
  "include_body": "thinking:\n  type: disabled",
  "exclude_body": "- frequency_penalty\n- presence_penalty"
}
```

## OpenRouter

OpenRouter is detected when the provider URL contains `openrouter.ai`. Discord Pals injects the expected attribution headers and merges an optional `openrouter` object into the request body:

```json
{
  "providers": [
    {
      "name": "Claude via OpenRouter",
      "url": "https://openrouter.ai/api/v1",
      "key_env": "OPENROUTER_API_KEY",
      "model": "anthropic/claude-sonnet-4",
      "openrouter": {
        "provider": {
          "order": ["anthropic"],
          "allow_fallbacks": true,
          "data_collection": "deny"
        },
        "models": ["anthropic/claude-sonnet-4", "openai/gpt-4o"],
        "transforms": ["middle-out"]
      }
    }
  ],
  "timeout": 60
}
```

Useful OpenRouter fields:

| Field | Use |
| --- | --- |
| `provider.order` | Preferred backend providers. |
| `provider.allow_fallbacks` | Allow OpenRouter backend fallback. |
| `provider.ignore` | Block specific backend providers. |
| `provider.data_collection` | Use `"deny"` to avoid data-storing backends. |
| `provider.sort` | Sort by `price`, `throughput`, or `latency`. |
| `models` | Model fallback list. |
| `transforms` | Optional transforms such as `middle-out`. |

These fields are also editable through the dashboard.

## Diagnostics

Run:

```bash
python diagnose.py
```

The script checks config files, environment variables, provider connectivity, and model availability.
