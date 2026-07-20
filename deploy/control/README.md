# Charon Control deployment

DNS’da `CONTROL_DOMAIN`ni VPS IP manziliga yo‘naltiring. VPS’da Docker va Docker
Compose o‘rnatilgach:

```bash
cd deploy/control
cp .env.example .env
# .env ichidagi uchta qiymatni production qiymatlariga almashtiring
docker compose up -d --build
```

Caddy TLS sertifikatni avtomatik oladi. Admin panel:

```text
https://CONTROL_DOMAIN/admin
```

Client installer buildida ayni URL’ni bake qiling:

```bash
LIVE_TRANSLATOR_CONTROL_URL=https://CONTROL_DOMAIN ./build_product_macos.sh
```

`.env`, SQLite volume va Caddy data backup qilinishi kerak. Public launchdan
oldin admin MFA, rate limiting, structured audit log va managed databasega
migratsiya qilish talab etiladi.
