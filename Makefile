ENV ?= DEV
ENV_TARGET ?= requirements.txt

# pre-commit captures Python output and expects UTF-8 on Windows as well.
export PYTHONIOENCODING := utf-8
export UV_LINK_MODE := copy

UV_ENV_FLAGS_DEV :=
UV_ENV_FLAGS_PROD := --no-dev
UV_ENV_FLAGS = $(UV_ENV_FLAGS_$(ENV))

CHECK_ENV = $(if $(and $(filter 1,$(words $(ENV))),$(filter $(ENV),DEV PROD)),,$(error ENV must be DEV or PROD, got "$(ENV)"))
CHECK_FILE = $(if $(strip $(FILE)),,$(error FILE is not set, usage: make $@ FILE=path/to/file.py))

.PHONY: lint-all typing-all tests-all check-all vulture-all bandit-all \
        secrets-all build-exe \
        lint-file typing-file test-file tests-file check-file \
        requirements pre-commit check-commit-msg sync

# Проверить весь проект с помощью Ruff без автоматического исправления.
lint-all:
	uv run ruff check --no-fix .

# Проверить типы во всём проекте в строгом режиме.
typing-all:
	uv run --with mypy -- python -m mypy --strict .

# Найти мёртвый код с помощью vulture.
vulture-all:
	uv run --with vulture -- vulture src tests

# Проверить код на уязвимости с помощью bandit.
bandit-all:
	uv run --with bandit -- bandit -r src

# Проверить репозиторий на утечку секретов с помощью detect-secrets.
secrets-all:
	uv run --with detect-secrets -- detect-secrets scan \
		| uv run python -c "import json, sys; results = json.load(sys.stdin).get('results', {}); [print(f'{name}: {len(items)} секрет(ов)') for name, items in results.items()]; sys.exit(bool(results))"

# Запустить все тесты unittest из tests с проверкой покрытия 100%.
tests-all:
	uv run coverage run -m unittest discover -v -s tests -t .
	uv run coverage report --fail-under=100

# Проверить указанный файл FILE с помощью Ruff.
lint-file:
	$(CHECK_FILE)
	uv run ruff check --no-fix "$(FILE)"

# Проверить типы в файле FILE в строгом режиме.
typing-file:
	$(CHECK_FILE)
	uv run --with mypy -- python -m mypy --strict "$(FILE)"

# Запустить тесты из файла FILE внутри tests с проверкой покрытия 100%.
test-file:
	$(CHECK_FILE)
	uv run coverage run -m unittest discover -v -s "$(dir $(subst \,/,$(FILE)))" -t . -p "$(notdir $(subst \,/,$(FILE)))"
	uv run coverage report --fail-under=100

# Альтернативное имя цели test-file, используемое в документации.
tests-file: test-file

# Экспортировать зависимости ENV=DEV или PROD в файл ENV_TARGET.
requirements:
	$(CHECK_ENV)
	uv export $(UV_ENV_FLAGS) --format requirements.txt --output-file "$(ENV_TARGET)"

# Выполнить проверки Ruff, mypy, vulture, bandit, секреты и все тесты.
check-all: lint-all typing-all vulture-all bandit-all secrets-all tests-all

# Выполнить проверки Ruff, mypy и тесты для файла FILE.
check-file: lint-file typing-file test-file

# Запустить все настроенные проверки pre-commit для всех файлов.
pre-commit:
	uv run python -m pre_commit run --all-files

# Проверить сообщение коммита из файла, переданного hook commit-msg.
check-commit-msg:
	uv run python -m commitizen check --commit-msg-file "$(filter-out $@,$(MAKECMDGOALS))"

install-hooks:
	uv run python -m pre_commit install
	uv run python -m pre_commit install --hook-type commit-msg
	uv run python -m pre_commit install --hook-type post-commit

# Синхронизировать окружение с зависимостями ENV=DEV или PROD.
sync:
	$(CHECK_ENV)
	uv sync $(UV_ENV_FLAGS)

# Собрать самодостаточный google-sheet-max.exe с помощью Nuitka.
build-exe:
	uv run python -m nuitka --onefile --assume-yes-for-downloads \
		--include-package=google_sheet_max \
		--output-dir=dist --output-filename=google-sheet-max.exe \
		--remove-output src/google_sheet_max/__main__.py
