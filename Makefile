PYTHON ?= python3

.PHONY: install test validate demo

install:
	$(PYTHON) -m pip install -e .

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

validate:
	PYTHONPATH=src $(PYTHON) -m control_plane_lab validate examples/trading_fabric.json

demo:
	PYTHONPATH=src $(PYTHON) -m control_plane_lab summary examples/trading_fabric.json
	PYTHONPATH=src $(PYTHON) -m control_plane_lab path examples/trading_fabric.json ny5-core-a 198.18.10.10
	PYTHONPATH=src $(PYTHON) -m control_plane_lab validate examples/trading_fabric.json
	PYTHONPATH=src $(PYTHON) -m control_plane_lab incident examples/trading_fabric.json --scenario examples/market_failover.json
