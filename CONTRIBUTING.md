# Contributing to OpenReview AI

Thank you for your interest in contributing to OpenReview AI! We welcome bug reports, feature proposals, and pull requests.

---

## Development Setup

### 1. Prerequisites
- Python 3.11+
- Git
- Docker Desktop or Docker Engine (for running sandbox integration tests)

### 2. Environment Setup
```bash
# Clone the repository
git clone https://github.com/Vivek-2004V/Openreview.git
cd Openreview

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in editable mode with development dependencies
pip install -e .
pip install -r requirements.txt
```

### 3. Build the Validator Container Image
```bash
docker build -t openreview-patch-validator:latest -f Dockerfile.validator .
```

---

## Running Tests & Linters

### Run Unit Tests
```bash
pytest -q
```

### Run Live Docker Integration Tests
```bash
pytest -v tests/integration/test_docker_sandbox.py
```

### Linting & Code Formatting
```bash
ruff check src/ tests/
ruff format --check src/ tests/
```

---

## Pull Request Guidelines

1. **Security First:** Never weaken container isolation controls (`--network none`, `--cap-drop ALL`, `--read-only`, non-root user).
2. **Fail-Closed:** When an external dependency or tool is missing, fail closed gracefully without compromising security boundaries.
3. **No Secrets:** Never commit `.env`, `.pem` keys, tokens, or credential files.
4. **Clean Commits:** Use clear commit messages describing the intent and impact of changes.

