# Offline gate: baseline commands, diff check, and negative path self-check.
.PHONY: verify sync diff-check gate tests gpu page preview demo

verify: sync diff-check gate tests gpu page preview demo

sync:
	uv sync --locked --group dev

diff-check:
	git diff --check
	git diff --cached --check

gate:
	.venv/bin/python -B scripts/verify_gate.py --self-check

tests:
	.venv/bin/python -B -m unittest discover -s tests -v

gpu:
	.venv/bin/python -B -m pytest -q gpu/test_server.py

page:
	node tests/test_call_page.js web/index.html

preview:
	.venv/bin/python -S -B -m her_agent preview --env-file .env.example

demo:
	.venv/bin/python -m her_agent.demo
