# CARD-19 | CI/CD Pipeline

**Phase:** Cross-cutting  
**Priority:** P2  
**Estimated effort:** 1 day  
**Depends on:** CARD-17 (health check สำหรับ verify deploy)  
**Blocks:** —

---

## Goal

auto test + build + deploy เมื่อ push to main โดยไม่มี manual step ระบบต้องผ่าน health check ก่อนถือว่า deploy สำเร็จ

---

## Tasks

### 1. GitHub Actions: CI workflow

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Lint (ruff)
        run: ruff check src/ tests/

      - name: Type check (mypy)
        run: mypy src/

      - name: Run tests
        run: pytest tests/ -v --tb=short --cov=src --cov-report=term-missing
        env:
          ENV: test
          BOT_TOKEN: dummy_token_for_testing
          GEMINI_API_KEY: dummy_key_for_testing
          ALLOWED_USER_IDS: "123456789"

      - name: Check coverage >= 80%
        run: pytest tests/ --cov=src --cov-fail-under=80
```

### 2. GitHub Actions: Docker build + push

```yaml
# .github/workflows/deploy.yml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    needs: test   # ต้องผ่าน CI ก่อน
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          push: true
          tags: |
            ghcr.io/${{ github.repository }}:latest
            ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            docker pull ghcr.io/${{ github.repository }}:latest
            docker stop personal-bot || true
            docker rm personal-bot || true
            docker run -d \
              --name personal-bot \
              --restart unless-stopped \
              --env-file /home/bot/.env \
              -v /home/bot/data:/app/data \
              ghcr.io/${{ github.repository }}:latest
            
            # Verify deploy ด้วย health check
            sleep 5
            curl -f http://localhost:8080/health || exit 1
```

### 3. Dockerfile แบบ production-ready

```dockerfile
# Dockerfile
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim AS runtime

# สร้าง non-root user
RUN useradd -m -u 1000 botuser
WORKDIR /app

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Data directory สำหรับ SQLite + ChromaDB
RUN mkdir -p /app/data && chown botuser:botuser /app/data
VOLUME ["/app/data"]

USER botuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "-m", "src.bot.main"]
```

### 4. Secret management

Secrets ที่ต้องตั้งใน GitHub:

| Secret | คำอธิบาย |
|--------|----------|
| `VPS_HOST` | IP หรือ hostname ของ VPS |
| `VPS_USER` | SSH username |
| `VPS_SSH_KEY` | Private SSH key |

`.env` บน VPS (ไม่ commit ใน git):

```
BOT_TOKEN=real_token_here
GEMINI_API_KEY=real_key_here
ALLOWED_USER_IDS=123456789
WEBHOOK_URL=https://yourdomain.com/webhook
WEBHOOK_PORT=8080
LOG_LEVEL=INFO
ENV=prod
```

### 5. `.github/dependabot.yml` — auto update dependencies

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
```

### 6. Pre-commit hooks (local dev)

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.3.0
    hooks:
      - id: ruff
        args: [--fix]
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.9.0
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
```

Setup: `pre-commit install`

---

## ลำดับการทดสอบ CI pipeline

```
push to main
    ↓
[CI] lint (ruff) → ถ้า fail: block
    ↓
[CI] type check (mypy) → ถ้า fail: block
    ↓
[CI] pytest + coverage ≥ 80% → ถ้า fail: block
    ↓
[Deploy] docker build + push
    ↓
[Deploy] SSH to VPS: pull → stop old → run new
    ↓
[Deploy] health check: curl /health → ถ้า fail: rollback
    ↓
✅ Deploy complete
```

---

## Definition of Done

- [ ] push to main → CI รัน lint + typecheck + tests อัตโนมัติ
- [ ] tests ผ่าน → Docker image ถูก build และ push ไป ghcr.io
- [ ] image ถูก deploy ไป VPS อัตโนมัติ
- [ ] health check ยืนยัน deploy ก่อน mark success
- [ ] secrets ไม่มีใน repository
- [ ] PR ไม่ผ่าน CI → merge ไม่ได้ (branch protection)

---

## Edge Cases

| Case | การจัดการ |
|------|-----------|
| Health check fail หลัง deploy | script exit 1 → GitHub Actions mark failed (แต่ container เก่าไม่ได้ rollback auto — ต้อง manual) |
| VPS disk เต็ม (Docker images เก่า) | เพิ่ม `docker image prune -f` ก่อน pull |
| Deploy ตอน bot กำลังตอบ user | `docker stop` ส่ง SIGTERM → bot shutdown gracefully ตาม CARD-01 |
| API key หมดอายุ / เปลี่ยน | update `.env` บน VPS แล้ว `docker restart personal-bot` |
