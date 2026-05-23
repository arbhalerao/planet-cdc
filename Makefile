.PHONY: help venv install fmt fmt-check

VENV := .venv
PIP := $(VENV)/bin/pip

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  venv        Create virtual environment (.venv)"
	@echo "  install     Install all dependencies into .venv"
	@echo "  fmt         Format code with Black"
	@echo "  fmt-check   Check formatting without modifying files"

venv:
	python3 -m venv $(VENV)

install:
	$(PIP) install --upgrade pip
	$(PIP) install \
		-r backend/requirements.app.txt \
		-r backend/requirements.worker.txt \
		-r backend/requirements.dev.txt

fmt:
	$(VENV)/bin/black backend/

fmt-check:
	$(VENV)/bin/black backend/ --check
