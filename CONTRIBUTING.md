# Contributing to cuea

Short, direct, practical.

## Quick workflow
1. Fork the repo.
2. Create a branch: `git checkout -b feat/<short-name>`
3. Implement code and tests.
4. Run tests and linters locally:
   ```bash
   pip install -e . pytest ruff mypy
   pytest -q
   ruff check .
   mypy src
   ```
5. Commit with a clear message. Push branch and open PR.
6. PR must include:
   - Short description of change.
   - Example usage or unit test demonstrating behavior.
   - Notes about breaking changes, if any.

## Coding style
- Keep public adapter surface small and stable.
- Use `async`/`await` for I/O.
- Use `Decimal` for numeric money values.
- Keep adapters defensive. Map unknown payloads to `raw` in models.
- Add unit tests for mapping functions and public adapter methods.

## Adding an adapter
- Add package under `src/cuea/adapters/<exchange>`.
- Provide an `ExchangeAdapter` with markets as attributes (`spot`, `futures`, `options`).
- Implement `transport.make_transport` or reuse existing transport pattern.
- Implement:
  - `fetch_ticker(symbol)`
  - `create_order(req: OrderRequest)`
  - `cancel_order(order_id, symbol=None)`
  - `fetch_open_orders(symbol=None)` (recommended)
  - `fetch_balances()` (if applicable)
  - user-data WS helpers if you need private event streaming.
- Add unit tests under `tests/` and fixtures under `tests/fixtures/`.

## Tests
- Tests must not hit real network. Use monkeypatch/mocks.
- Add fixtures for WS payloads to validate mapping.
- Keep tests deterministic and fast.

## CI
- Push PR triggers CI which runs tests, ruff and mypy.
- Fix CI failures before merge.

## Commits and PRs
- Use imperative commit messages, e.g. `add binance listen-key keepalive`.
- Keep PRs focused. One feature or fix per PR.

## Support
- If you introduce runtime dependency (e.g. `marketspec`, `pyyaml`) list it in `pyproject` and document optional usage in README.

## License and CLA
- Contributor must accept repo license. No CLA required unless repo policy changes.
