SHELL := /bin/bash
VENV ?= .venv
SYSTEM_PYTHON := $(shell which /Users/atharvpaharia100/.local/bin/python3.11 2>/dev/null || which python3.11 2>/dev/null || which python3)
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: all venv install warm preflight demo demo-reject verify clean

all: install

venv:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment with $(SYSTEM_PYTHON)..."; \
		$(SYSTEM_PYTHON) -m venv $(VENV); \
		$(PIP) install --upgrade pip setuptools wheel; \
	fi

install: venv
	@echo "Installing pinned dependencies from requirements.txt..."
	$(PIP) install -r requirements.txt

warm:
	$(PYTHON) scripts/warm_models.py

preflight:
	$(PYTHON) scripts/preflight.py

demo:
	$(PYTHON) main.py --image samples/image.png --consent --expect-domain instagram.com

demo-reject:
	$(PYTHON) main.py --image samples/unindexed.png --consent

verify:
	@if [ -z "$(TX)" ]; then \
		echo "Usage: make verify TX=0x..."; \
		exit 1; \
	fi
	$(PYTHON) -m pipeline.verify --tx $(TX)

clean:
	rm -rf $(VENV) out/* __pycache__ pipeline/__pycache__ scripts/__pycache__ *.pyc
