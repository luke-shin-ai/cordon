# Cordon. Two build surfaces: the QNX target (cross built with qcc from the SDP
# 8.0 install) and the host CLI (this machine, Python 3).

PYTHON ?= python3

.PHONY: help host-test host-claims target images clean

help:
	@echo "make host-test     run the gate and judging rule tests (no QNX needed)"
	@echo "make host-claims   print the claim ledger"
	@echo "make target        cross build the four target binaries with qcc"
	@echo "make images        build the normal and the misordered QNX image"
	@echo "make clean         remove build output"

host-test:
	$(PYTHON) tests/test_gate.py
	$(PYTHON) tests/test_run.py

host-claims:
	$(PYTHON) host/cordon claims

target:
	@if [ -z "$$QNX_TARGET" ]; then \
	  echo "QNX_TARGET is not set. Source qnxsdp-env.sh from a QNX SDP 8.0 install first."; \
	  exit 2; \
	fi
	mkdir -p target/build
	qcc -Wall -Wextra -O1 -o target/build/cordon           target/resmgr/cordon.c             -lsecpol -lslog2
	qcc -Wall -Wextra -O1 -o target/build/cordon-supervise target/supervise/cordon-supervise.c -lsecpol -lslog2
	qcc -Wall -Wextra -O1 -o target/build/cordon-client    target/client/cordon-client.c      -lsecpol
	qcc -Wall -Wextra -O1 -o target/build/cordon-view      target/client/cordon-view.c        -lsecpol
	secpolcompile -o target/build/secpol.bin target/secpol/cordon.policy
	@ls -l target/build

images: target
	target/boot/build-g2-image.sh normal
	target/boot/build-g2-image.sh violated

clean:
	rm -rf target/build out
