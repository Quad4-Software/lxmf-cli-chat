.PHONY: all help test dev build lint format clean install install-python install-man uninstall

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
INSTALL ?= install

PREFIX ?= /usr/local
DESTDIR ?=
MANDIR ?= $(PREFIX)/share/man

# Set to "sudo" or "doas" when installing system-wide:
# make install ELEVATE=sudo
ELEVATE ?=

PACKAGE_NAME ?= lxmf-cli-chat
MANPAGES ?= man/lxmf-chat.1 man/lxmf-cli-chat.1

all: build

help:
	@echo "Usage: make [target] [VARIABLE=value]"
	@echo ""
	@echo "Main targets:"
	@echo "  test           Run all tests"
	@echo "  dev            Run the application in debug mode"
	@echo "  build          Build the package"
	@echo "  lint           Run linting"
	@echo "  format         Format code"
	@echo "  clean          Remove build artifacts"
	@echo "  install        Install package and man page"
	@echo "  uninstall      Uninstall package and man page"
	@echo ""
	@echo "Install variables:"
	@echo "  PREFIX=$(PREFIX)"
	@echo "  DESTDIR=$(DESTDIR)"
	@echo "  MANDIR=$(MANDIR)"
	@echo "  ELEVATE=$(ELEVATE)   (set to sudo or doas)"

test:
	poetry run pytest

dev:
	poetry run lxmf-chat --debug

build:
	poetry build

lint:
	poetry run ruff check .

format:
	poetry run ruff format .

clean:
	rm -rf dist build .pytest_cache .ruff_cache

install: install-python install-man

install-python:
	$(ELEVATE) $(PIP) install .

install-man:
	$(ELEVATE) $(INSTALL) -d "$(DESTDIR)$(MANDIR)/man1"
	@for page in $(MANPAGES); do \
		base="$$(basename "$$page")"; \
		$(ELEVATE) $(INSTALL) -m 0644 "$$page" "$(DESTDIR)$(MANDIR)/man1/$$base"; \
	done

uninstall:
	$(ELEVATE) $(PIP) uninstall -y "$(PACKAGE_NAME)"
	$(ELEVATE) rm -f "$(DESTDIR)$(MANDIR)/man1/lxmf-chat.1"
	$(ELEVATE) rm -f "$(DESTDIR)$(MANDIR)/man1/lxmf-cli-chat.1"
