# Cordon. Two build surfaces: the host CLI (this machine) and the QNX target
# (needs QNX SDP 8.0 and qcc, which the current build environment does not have).

PYTHON ?= python3

.PHONY: help host-test host-claims target clean

help:
	@echo "make host-test     run the gate rule tests (no QNX needed)"
	@echo "make host-claims   print the claim ledger"
	@echo "make target        cross build target/ with qcc (needs QNX SDP 8.0)"
	@echo "make clean         remove build output"

host-test:
	$(PYTHON) tests/test_gate.py

host-claims:
	$(PYTHON) host/cordon claims

# Placeholder until the SDP toolchain exists. Kept visible so the gap is not
# mistaken for missing wiring.
target:
	@if [ -z "$$QNX_TARGET" ]; then \
	  echo "QNX_TARGET is not set. Source qnxsdp-env.sh from a QNX SDP 8.0 install first."; \
	  exit 2; \
	fi
	@echo "target build not written yet (see README status)"

clean:
	rm -rf build
