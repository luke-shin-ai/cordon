# Cordon. Two build surfaces: the QNX target (cross built with qcc from the SDP
# 8.0 install) and the host CLI (this machine, Python 3).

PYTHON ?= python3

.PHONY: help host-test host-claims target images evidence codelab check clean

help:
	@echo "make host-test     run the gate, judging, note and codelab tests (no QNX needed)"
	@echo "make host-claims   print the claim ledger"
	@echo "make note          print a release note draft for a bundle (BUNDLE=dir)"
	@echo "make target        cross build the four target binaries with qcc"
	@echo "make images        build the normal and the misordered QNX image"
	@echo "make evidence      record build logs, image hashes and consoles under evidence/"
	@echo "make codelab       validate the codelab and render it into codelab/site"
	@echo "make check         run the seven release checks (tools/checks.sh)"
	@echo "make clean         remove build output"

host-test:
	$(PYTHON) tests/test_gate.py
	$(PYTHON) tests/test_run.py
	$(PYTHON) tests/test_note.py
	$(PYTHON) tests/test_codelab.py

host-claims:
	$(PYTHON) host/cordon claims

note:
	@if [ -z "$(BUNDLE)" ]; then echo "usage: make note BUNDLE=<evidence dir>"; exit 2; fi
	$(PYTHON) host/cordon note $(BUNDLE) --out $(BUNDLE)/note

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

evidence:
	sh tools/record-evidence.sh $(CONSOLE_DIR)

codelab:
	$(PYTHON) codelab/render.py --check
	$(PYTHON) codelab/render.py --out codelab/site

check:
	sh tools/checks.sh

clean:
	rm -rf target/build out codelab/site
