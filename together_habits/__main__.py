from __future__ import annotations

import logging
import sys

from together_habits.config import ConfigError, load_config, load_dotenv_file


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv_file()
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    from together_habits.bot import run_bot

    run_bot(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
