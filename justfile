set shell := ["powershell.exe", "-c"]

# Start FastAPI server with pipenv
dev:
    pipenv run uvicorn app.main:app --reload

# Run tests with pipenv
test:
    pipenv run pytest

# Format code with black using pipenv
format:
    pipenv run black .

# Install dependencies
install:
    pipenv install

# Activate pipenv shell
shell:
    pipenv shell