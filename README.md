https://hrone-attendance-analytics.vercel.app(FRONTENT ONLY)
# Employee Attendance & Analytics API

A FastAPI application backed by MongoDB for employee management, attendance tracking, attendance regularization, and analytics.

## Requirements

- Python 3.11+
- MongoDB instance accessible from your machine
- Dependencies listed in `requirements.txt`

## Setup

From the project root, create and activate a virtual environment if you do not already have one.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Configure the MongoDB connection using environment variables:

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_db"
```

Use the MongoDB URI and database name required by your environment. Do not commit credentials, `.env` files, or secrets to the repository.

## Run the API

From the project root:

```powershell
uvicorn app.main:app --port 8000
```

Health check: `http://localhost:8000/health`

Interactive API documentation: `http://localhost:8000/docs`

OpenAPI schema: `http://localhost:8000/openapi.json`

## Run tests

```powershell
python -m pytest -v
```

The current local test run completed with 12 passing tests. Results may vary if the code or test environment changes.

## API capabilities

- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance listing and regularization
- Employee monthly attendance analytics
- Department attendance summaries and trends
- Late-arrival leaderboard
- Query-plan inspection through the admin explain endpoint

Refer to `openapi.yaml` and `DATA_MODEL.md` for the API contract and data model.

## Frontend Dashboard

The React + Vite + TypeScript frontend is optional. The API can be run and tested independently using the interactive Swagger documentation at `http://localhost:8000/docs`.

## Design and review notes

- `REVIEW.md` documents the starter-code review and verification limitations.
- `DECISIONS.md` explains key implementation decisions, including indexes and concurrency safeguards.

## What's missing

- The frontend requires the API backend to be running and reachable to display live data. A frontend-only deployment does not provide live API functionality.
- The API capabilities are documented in `openapi.yaml`. The frontend requires a running, reachable backend to display live data.

## Submission notes

Commit the application code, tests, requirements, and documentation. Do not commit secrets, local virtual environments, or machine-specific configuration.
