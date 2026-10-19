# Orchestrator API (OpenAI + Anthropic)

Un petit serveur FastAPI qui appelle OpenAI et Anthropic (Claude), propose 3 modes de fusion et est prêt à être déployé en Docker.

Variables d'environnement requises
- OPENAI_API_KEY - clé OpenAI (Bearer)
- ANTHROPIC_API_KEY - clé Anthropic (x-api-key)
- ORCHESTRATOR_API_KEY - clé de protection pour ton API orchestrateur (header X-Api-Key)
- OPENAI_MODEL (optionnel) - ex: gpt-4
- CLAUDE_MODEL (optionnel) - ex: claude-2
- CONCURRENCY_LIMIT (optionnel) - nombre max d'appels concurrents (default 8)
- CACHE_TTL_SECONDS (optionnel) - TTL cache en secondes (default 60)

Endpoints
- POST /api/orchestrate
  - Body JSON: { "prompt": "Ta question", "mode": "vote"|"pipeline"|"both", "max_tokens": 500 }
  - Header: X-Api-Key: <ORCHESTRATOR_API_KEY>
  - Retourne OpenAI, Claude et la réponse choisie/fusionnée.

- GET /health

Mode de fonctionnement
- vote: appelle les deux modèles en parallèle et choisit la réponse avec un score heuristique.
- pipeline: OpenAI -> Claude (Claude raffine la réponse).
- both: retourne les deux réponses et une concaténation simple.

Déploiement
- Local (Docker):
  1. docker build -t orchestrator .
  2. docker run -e OPENAI_API_KEY=... -e ANTHROPIC_API_KEY=... -e ORCHESTRATOR_API_KEY=... -p 8000:8000 orchestrator

- Production:
  - Déployer comme un service Docker sur un VPS, ou utiliser Cloud Run / ECS / Azure Container Instances.
  - Pour disponibilité longue durée: exécuter dans un orchestrateur (systemd, docker-compose, k8s) et surveiller l'utilisation et coûts.

Sécurité & coûts
- Les appels doublés augmentent les coûts et la latence.
- Ne pas exposer OPENAI/ANTHROPIC keys publiquement et restreindre ORCHESTRATOR_API_KEY.
- Ajouter quotas, authentification robuste (JWT, OAuth) et monitoring en production.

Améliorations possibles
- Utiliser un troisième modèle pour évaluer ou réécrire (méta-arbitre).
- Ajouter un vrai moteur de scoring factuel (fact‑check via recherche externe).
- Persistance des logs, usage et facturation par utilisateur.
- Limitation par utilisateur (rate limits), métriques, retries exponentiels.

Si tu veux, je peux :
- créer un repo GitHub et y pousser ces fichiers,
- ajouter CI/CD (build docker + test),
- écrire une version Node.js / TypeScript,
- ou déployer le service pour toi (donne accès au cloud ou à un repo).
