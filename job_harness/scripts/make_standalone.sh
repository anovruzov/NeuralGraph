#!/usr/bin/env bash
# Build a standalone repository from this directory.
#
# In NeuralGraph this tree is both the project root and the Python package. A
# standalone repo needs them separated:
#
#     job-harness/              <- new repo root (pyproject, LICENSE, tests)
#     └── job_harness/          <- the importable package
#
# Usage: scripts/make_standalone.sh ../job-harness
set -euo pipefail

target="${1:?usage: make_standalone.sh <target-directory>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -e "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null)" ]; then
    echo "error: $target exists and is not empty" >&2
    exit 1
fi

mkdir -p "$target/job_harness"

# Files that belong at the repo root, not inside the package.
root_files=(
    LICENSE NOTICE README.md CONTRIBUTING.md pyproject.toml requirements.txt
    Dockerfile docker-compose.yml .dockerignore .gitignore .env.example
)
for file in "${root_files[@]}"; do
    [ -e "$here/$file" ] && cp -a "$here/$file" "$target/$file"
done
cp -a "$here/.github" "$target/.github"
cp -a "$here/tests" "$target/tests"
cp -a "$here/scripts" "$target/scripts"

# Everything else is the package itself.
for entry in "$here"/*; do
    name="$(basename "$entry")"
    case "$name" in
        tests|scripts|logs|resumes|profile) continue ;;
    esac
    printf '%s\n' "${root_files[@]}" | grep -qx "$name" && continue
    cp -a "$entry" "$target/job_harness/$name"
done

# A standalone package needs a real __init__.py: the namespace-package trick
# exists only to stop pytest resolving through NeuralGraph's root package.
cat > "$target/job_harness/__init__.py" <<'PYINIT'
"""Autonomous job search and application harness."""

__version__ = "1.0.0"
PYINIT

# User data directories: present but empty, as in a fresh clone.
for dir in logs resumes profile; do
    mkdir -p "$target/job_harness/$dir"
    touch "$target/job_harness/$dir/.gitkeep"
done
cp -a "$here/profile/applicant.example.json" "$target/job_harness/profile/"
cp -a "$here/profile/applicant.py" "$target/job_harness/profile/"
cp -a "$here/profile/pdf_text.py" "$target/job_harness/profile/"
[ -e "$here/profile/__init__.py" ] && cp -a "$here/profile/__init__.py" "$target/job_harness/profile/"

# tests/ sits at the root and imports the installed package, so pytest.ini
# moves into pyproject (already configured there).
rm -f "$target/job_harness/pytest.ini"

cat > "$target/.gitignore" <<'GITIGNORE'
job_harness/logs/*
!job_harness/logs/.gitkeep
**/__pycache__/
*.pyc
.pytest_cache/
.venv/
build/
dist/
*.egg-info/
.env
# Personal data: never committed.
job_harness/profile/applicant.json
job_harness/resumes/*
!job_harness/resumes/.gitkeep
GITIGNORE

echo "standalone project written to $target"
echo
echo "next:"
echo "  cd $target"
echo "  git init && git add . && git commit -m 'Initial commit'"
echo "  pip install -e '.[dev]' && python -m pytest"
