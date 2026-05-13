# TradingBot

Application code and docs for the Schwab-integrated scanner and execution stack live under **`schwab_skill/`**.

## Where to start

- **Operator / feature documentation:** [`schwab_skill/README.md`](schwab_skill/README.md)
- **Local validation (strict, single command of record):** from `schwab_skill/`, run  
  `python scripts/validate_all.py --profile local --strict`  
  From the repo root you can use **`make validate`**, **`just validate`**, or the same command with `cd schwab_skill` as in the Makefile.

## Windows developer paths

- Prefer **`just check`** or **`make check`** for a fast gate (ruff, pytest, typecheck ratchet), or run those commands from a shell where `python` points at your venv.
- GNU **`make`** on Windows is usually via [Chocolatey](https://chocolatey.org/) (`choco install make`) or Git Bash; **`just`** avoids Make’s Unix heredocs — see [`Justfile`](Justfile).
