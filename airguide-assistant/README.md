# AirGuide – Urban Air Quality Assistant

**Challenge vertical:** Challenge 6 – Urban Air Quality Assistant.

AirGuide helps city residents decide how to plan their day (walk, commute, exercise, stay in) based on the live air quality where they are and who they are protecting (children, older adults, asthma, heart conditions, pregnancy).

## Approach and logic

The assistant separates **decisions** from **wording**:

| Layer | File | Responsibility |
|---|---|---|
| AQI maths | `airguide/aqi.py` | US EPA PM2.5/PM10 breakpoints (2024), truncation rules, max sub-index. Pure functions. |
| State | `airguide/context.py` | `UserContext` (location, AQI, PM, sensitivity, activity, stage). Validates and clamps everything, including state coming back from the browser. |
| Routing engine | `airguide/router.py` | Deterministic rules → a `Directive` (ASK one question, or ADVISE with a verdict, mask guidance and action points). |
| Prompt | `airguide/prompt.py` | System prompt template (persona, dynamic state, routing directive, rules, guardrails). |
| Live data | `airguide/data_source.py` | Optional Open-Meteo lookup (no API key). Falls back to manual input on any failure. |
| Gen-AI phrasing | `airguide/llm.py` | Optional Claude call to phrase the directive. Falls back to the deterministic reply on error, missing key or a >150-word answer. |
| Orchestration | `airguide/assistant.py` | Parse message → update state → live fetch → route → respond. |
| Web/API | `airguide/server.py`, `web/` | Dependency-free server and accessible UI. |

### Decision rules

1. **No AQI yet** → ask for a city (live lookup) or a typed AQI/PM2.5 value.
2. **AQI 51–200 and unknown details** → ask **one** missing question at a time (sensitive groups, then planned activity). Good air (≤50) and very bad air (>200) need no extra questions.
3. **Effective level** = AQI category (0 Good … 5 Hazardous), +1 if a sensitive group is present (AQI > 50), +1 for vigorous exertion when AQI > 150, capped at 5.
4. Effective level maps to `GO`, `GO_WITH_CARE`, `MODIFY`, `AVOID`, `STAY_INDOORS`. Choosing to stay in gives `INDOORS_OK` with indoor-air tips.
5. Mask guidance recommends a fitted N95/FFP2 only (cloth/surgical masks don't stop PM2.5).
6. Severe-symptom phrases (chest pain, can't breathe…) short-circuit to an urgent-care message.

The LLM never calculates or decides; it only rephrases the directive (rule: "deterministic numbers only"). Without an API key the app runs fully offline.

## How it works

```
user message ─► parse ─► state ─► [live fetch] ─► router.decide() ─► Directive
                                                           │
                              LLM (optional) ◄─ system prompt + directive
                                      └── fallback: deterministic reply
```

Example: *"What's the air like in Delhi?" → "none" → "I want to go for a run"* → AQI 168 live, no sensitive groups, vigorous exertion → **AVOID** outdoor exertion, N95 if going out.

## Run it

Requires Python 3.10+. No third-party packages.

```bash
python cli.py                 # terminal chat
python -m airguide.server     # web UI at http://127.0.0.1:8000
python -m unittest discover -s tests -t .   # run tests
```

Optional: `export ANTHROPIC_API_KEY=...` to enable Gen-AI phrasing (`AIRGUIDE_MODEL` overrides the model).

## Quality, security, accessibility

- **Testing:** 35+ unit/integration tests (AQI boundaries, routing matrix, conversation flows, live-data failure, server round-trip, validation, rate limiting).
- **Security:** strict input validation and length limits; client state re-validated server-side; location text sanitised before reaching the prompt (injection defence); UI uses `textContent` (no XSS); CSP, `nosniff`, `X-Frame-Options`; static-file whitelist (no path traversal); per-client rate limiting; https-only outbound calls with timeouts and size caps; no secrets in code; no user text in logs.
- **Efficiency:** stdlib only, stateless server (state travels with the request), tiny payloads, 8 s network timeouts, one outbound call pair per new location.
- **Accessibility:** semantic landmarks, labelled input, skip link, `role="log"` live region, status shown as text (not colour alone), WCAG-AA contrast with dark mode, visible focus, reduced-motion support.

## Assumptions

- AQI uses the US EPA scale; only PM2.5 and PM10 are computed from raw readings (ozone/NO₂/etc. are used only through the live feed's `us_aqi`).
- Live data comes from Open-Meteo's free air-quality API (model-based, city-level); users can always override with a local reading.
- Guidance is general wellbeing advice, not medical advice; no diagnoses, medicines or product brands.
- Natural-language parsing is keyword-based by design (predictable, testable); the LLM is not needed to understand input.

## Gen-AI usage

The code was built with Claude. At runtime Claude is optional and only phrases the routing directive.
