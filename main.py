from fastapi import FastAPI, Request, HTTPException, Header
from pydantic import BaseModel
import os
import httpx
import asyncio
from cachetools import TTLCache, cached
from typing import Optional

# Configuration via env
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ORCHESTRATOR_API_KEY = os.getenv("ORCHESTRATOR_API_KEY", "change_me")
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4")
DEFAULT_CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-2")
CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", "8"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))

if not OPENAI_API_KEY or not ANTHROPIC_API_KEY:
    print("Warning: OPENAI_API_KEY or ANTHROPIC_API_KEY not set. Set env vars before running.")

app = FastAPI(title="Orchestrator API (OpenAI + Anthropic)")

semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
cache = TTLCache(maxsize=1024, ttl=CACHE_TTL_SECONDS)

class OrchestrateRequest(BaseModel):
    prompt: str
    mode: Optional[str] = "vote"  # vote | pipeline | both
    max_tokens: Optional[int] = 500
    openai_model: Optional[str] = None
    claude_model: Optional[str] = None

class OrchestrateResponse(BaseModel):
    openai: Optional[str]
    claude: Optional[str]
    chosen: Optional[str]
    mode: str

async def call_openai(prompt: str, model: str, max_tokens: int) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        # Guard for both chat/completions and new api shapes
        if "choices" in data and len(data["choices"]) > 0:
            content = data["choices"][0].get("message", {}).get("content") or data["choices"][0].get("text")
            return content.strip()
        return str(data)

async def call_claude(prompt: str, model: str, max_tokens: int) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    url = "https://api.anthropic.com/v1/complete"
    headers = {"x-api-key": ANTHROPIC_API_KEY, "Content-Type": "application/json"}
    # Anthropic style prompt pattern (Human / Assistant)
    anthropic_prompt = f"\n\nHuman: {prompt}\n\nAssistant:"
    payload = {
        "model": model,
        "prompt": anthropic_prompt,
        "max_tokens_to_sample": max_tokens,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        # expected key "completion"
        completion = data.get("completion") or data.get("output") or str(data)
        return completion.strip()

def score_answer(answer: str, prompt: str) -> float:
    if not answer:
        return 0.0
    s = len(answer)
    # reward overlap with prompt keywords lightly
    prompt_words = set([w.lower() for w in prompt.split() if len(w) > 3])
    answer_words = set([w.lower().strip(".,!?;:()[]") for w in answer.split()])
    overlap = len(prompt_words.intersection(answer_words))
    return s + overlap * 10

@cached(cache)
async def generate_both(prompt: str, openai_model: str, claude_model: str, max_tokens: int):
    # run both in parallel
    async with semaphore:
        async with asyncio.TaskGroup() as tg:
            openai_task = tg.create_task(call_openai(prompt, openai_model, max_tokens))
            claude_task = tg.create_task(call_claude(prompt, claude_model, max_tokens))
        return openai_task.result(), claude_task.result()

async def pipeline_refine(prompt: str, first: str, second_model: str, max_tokens: int, second_is_claude: bool):
    # feed the first answer as context to the second to refine
    refined_prompt = f"Voici une réponse initiale:\n\n{first}\n\nPeux-tu améliorer, corriger et compléter cette réponse pour la question suivante : {prompt}"
    if second_is_claude:
        return await call_claude(refined_prompt, second_model, max_tokens)
    else:
        return await call_openai(refined_prompt, second_model, max_tokens)

@app.post("/api/orchestrate", response_model=OrchestrateResponse)
async def orchestrate(req: OrchestrateRequest, request: Request, x_api_key: Optional[str] = Header(None)):
    # Simple API key protection for the orchestrator
    if ORCHESTRATOR_API_KEY and x_api_key != ORCHESTRATOR_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid orchestrator API key")

    openai_model = req.openai_model or DEFAULT_OPENAI_MODEL
    claude_model = req.claude_model or DEFAULT_CLAUDE_MODEL
    mode = req.mode or "vote"

    try:
        if mode == "vote":
            openai_text, claude_text = await generate_both(req.prompt, openai_model, claude_model, req.max_tokens)
            s_open = score_answer(openai_text, req.prompt)
            s_claude = score_answer(claude_text, req.prompt)
            chosen = openai_text if s_open >= s_claude else claude_text
            return OrchestrateResponse(openai=openai_text, claude=claude_text, chosen=chosen, mode="vote")
        elif mode == "pipeline":
            # run the first model (OpenAI) then refine with Claude
            first = await call_openai(req.prompt, openai_model, req.max_tokens)
            refined = await pipeline_refine(req.prompt, first, claude_model, req.max_tokens, second_is_claude=True)
            return OrchestrateResponse(openai=first, claude=refined, chosen=refined, mode="pipeline")
        elif mode == "both":
            openai_text, claude_text = await generate_both(req.prompt, openai_model, claude_model, req.max_tokens)
            merged = f"--- OpenAI ---\n{openai_text}\n\n--- Claude ---\n{claude_text}\n\n--- Fusion (réécriture simple) ---\n{openai_text}\n\n{claude_text}"
            return OrchestrateResponse(openai=openai_text, claude=claude_text, chosen=merged, mode="both")
        else:
            raise HTTPException(status_code=400, detail="Invalid mode. Use 'vote', 'pipeline' or 'both'.")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Upstream API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok"}
