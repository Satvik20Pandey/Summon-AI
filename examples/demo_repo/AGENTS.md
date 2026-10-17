# demo-api

Small FastAPI service. Run tests with `pytest -q`.

Summon AI is wired into this repo: when you finish a task, a Stop hook phones the
developer and may hand you their spoken instructions as your next prompt. Before
starting work, call the `summon_recall` MCP tool to read decisions the developer
already made on earlier calls (they are binding), and what other agents did.
