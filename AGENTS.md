# Instructions for Codex / Claude

This repository is public-safe by default. Do not add credentials, bot tokens,
real Telegram user IDs, server addresses, databases, logs or `.env` files.

- Run `scripts/verify-no-secrets.sh` (or `.ps1` on Windows) before every commit.
- Run `pytest` before every commit.
- Add examples only with placeholder values.
- User data lives in the local SQLite file (`HABITS_DB_PATH`), which is gitignored.
