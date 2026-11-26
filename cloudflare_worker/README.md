# NeonRoute Bug Reporter — Cloudflare Worker

Изолированный HTTP-прокси на Cloudflare Workers для безопасной отправки отчётов об ошибках из десктопного клиента NeonRoute в GitHub Issues без хранения `GITHUB_TOKEN` в клиентском коде.

## Требования

- Node.js 18+
- Аккаунт Cloudflare
- GitHub Personal Access Token (Fine-grained PAT с правом `Issues: Read and write` для целевого репозитория, либо Classic PAT с `public_repo` / `repo`)

## 1. Настройка целевого репозитория

Проверьте значения `[vars]` в `wrangler.toml`:

```toml
[vars]
GITHUB_OWNER = "ZabotaOblizkih"
GITHUB_REPO = "NeonRoute"
```

## 2. Авторизация в Cloudflare и установка секрета

Выполните команды из папки `cloudflare_worker/`:

```bash
cd cloudflare_worker
npx wrangler login
npx wrangler secret put GITHUB_TOKEN
```

Вставьте GitHub PAT при запросе. Токен хранится зашифрованным в Cloudflare Secrets и никогда не попадает в репозиторий.

## 3. Деплой воркера

```bash
npx wrangler deploy
```

После деплоя Wrangler выведет URL воркера (например, `https://neonroute-bug-reporter.<account>.workers.dev`).
Укажите этот URL в переменной окружения `NEONROUTE_BUG_REPORT_URL` или в настройке `bug_report_endpoint_url` клиента NeonRoute.

## 4. Ручная проверка POST-запросом

### Bash / curl

```bash
curl -i -X POST "https://neonroute-bug-reporter.<account>.workers.dev" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "[Test] Bug reporter smoke check",
    "body": "Test report body from manual verification.",
    "app_version": "0.9.8-beta",
    "category": "manual"
  }'
```

### PowerShell

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://neonroute-bug-reporter.<account>.workers.dev" `
  -ContentType "application/json" `
  -Body '{"title":"[Test] Bug reporter smoke check","body":"Test report body","app_version":"0.9.8-beta","category":"manual"}'
```

Ожидаемый ответ: HTTP `201` с JSON вида:

```json
{
  "ok": true,
  "issue_number": 123,
  "issue_url": "https://github.com/ZabotaOblizkih/NeonRoute/issues/123"
}
```
