# The lab's commands; README.md lists them and docs/ describes each one.
VENV      := $(CURDIR)/.venv
PYTHON    ?= 3.12
INVENTORY ?= inventory/local
SECRETS   ?= vars/secrets.yml
# VSR_IMAGE=<path> overrides vsr_image from the inventory.
VSR_IMAGE ?=
# VAULT=1 prompts for the ansible-vault password of an encrypted secrets file.
VAULT     ?=
export ANSIBLE_CONFIG := $(CURDIR)/ansible.cfg
export PATH := $(VENV)/bin:$(PATH)

PLAYBOOK = ansible-playbook -i $(INVENTORY) -e lab_secrets_file=$(abspath $(SECRETS)) $(if $(VAULT),--ask-vault-pass) \
           $(if $(VSR_IMAGE),-e vsr_image=$(abspath $(VSR_IMAGE)))

.DEFAULT_GOAL := help
.PHONY: help venv lint check-inventory preflight host images vms radius generator access core enterprise coa test perf teardown

help: ## List targets
	@grep -E '^[a-z][a-z0-9_-]*:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

venv: ## Create .venv with Ansible, lint tools and collections
	uv venv $(VENV) --python $(PYTHON) --allow-existing
	uv pip install --python $(VENV)/bin/python -r requirements-dev.txt
	ansible-galaxy collection install -r collections/requirements.yml -p collections

lint: ## Run yamllint, ansible-lint and a syntax check against the example inventory
	yamllint .
	ansible-lint
	for p in playbooks/*.yml; do ansible-playbook -i inventory/example --syntax-check $$p || exit 1; done

check-inventory: ## Validate the inventory and secrets without contacting the host
	$(PLAYBOOK) playbooks/check-inventory.yml

preflight: ## Read-only host checks and cable loop test (LOOP_TEST=false to skip the loop test)
	$(PLAYBOOK) playbooks/00-preflight.yml $(if $(LOOP_TEST),-e preflight_loop_test=$(LOOP_TEST))

host: ## Prepare the Proxmox host (ALLOW_REBOOT=true lets it reboot)
	$(PLAYBOOK) playbooks/10-host.yml $(if $(ALLOW_REBOOT),-e pve_host_allow_reboot=$(ALLOW_REBOOT))

images: ## Stage the VSR image (vsr_image or VSR_IMAGE=) and the generator image on the host
	$(PLAYBOOK) playbooks/20-images.yml

vms: ## Build and start the VSR and generator VMs (REBUILD=vsr|gen|all replaces existing ones)
	$(PLAYBOOK) playbooks/30-vms.yml $(if $(REBUILD),-e lab_vms_rebuild=$(REBUILD))

radius: ## Build the FreeRADIUS container and load the subscribers (REBUILD=radius replaces it)
	$(PLAYBOOK) playbooks/40-radius.yml $(if $(REBUILD),-e lab_radius_rebuild=$(REBUILD))

generator: ## Install NIC drivers, name the link ports and build BNG Blaster on the generator
	$(PLAYBOOK) playbooks/50-generator.yml

access: ## Configure the VSR access side and bring every lab subscriber up and down
	$(PLAYBOOK) playbooks/60-access.yml

core: ## Configure the VSR as an L3VPN PE and run labelled subscriber traffic through the core
	$(PLAYBOOK) playbooks/70-core.yml

enterprise: ## Configure the static enterprise customers and run their traffic through the core
	$(PLAYBOOK) playbooks/80-enterprise.yml

coa: ## Turn on the VSR's CoA servers and test a rate-limit CoA and Disconnects
	$(PLAYBOOK) playbooks/85-coa.yml

test: ## Run the functional test suite and write results/test-<time>/summary.md
	$(PLAYBOOK) playbooks/90-test.yml

perf: ## Measure fast-path core load and RX queue spread under load (PERF_BPS, PERF_PPPOE, PERF_IPOE, PERF_SECONDS)
	$(PLAYBOOK) playbooks/95-perf.yml $(if $(PERF_BPS),-e lab_perf_bps=$(PERF_BPS)) \
	  $(if $(PERF_PPPOE),-e lab_perf_pppoe=$(PERF_PPPOE)) $(if $(PERF_IPOE),-e lab_perf_ipoe=$(PERF_IPOE)) \
	  $(if $(PERF_SECONDS),-e lab_perf_seconds=$(PERF_SECONDS))

teardown: ## Remove the lab's VMs, container and files from the host (lists them unless CONFIRM=yes; IMAGES=yes adds the images)
	$(PLAYBOOK) playbooks/99-teardown.yml $(if $(CONFIRM),-e lab_teardown_confirm=$(CONFIRM)) $(if $(IMAGES),-e lab_teardown_images=$(IMAGES))
