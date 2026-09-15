"""Keep production migrations behind the deployment release gate."""

import json
from pathlib import Path


def test_service_container_does_not_run_migrations_on_startup() -> None:
    """Service instances start Uvicorn without running database migrations."""
    dockerfile = Path("Dockerfile").read_text()
    command = next(
        line.removeprefix("CMD ")
        for line in dockerfile.splitlines()
        if line.startswith("CMD ")
    )

    assert "alembic upgrade head" not in dockerfile
    assert json.loads(command) == [
        "sh",
        "-c",
        "exec uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    ]


def test_ci_waits_for_migration_before_service_deploy() -> None:
    """CI serializes releases and waits for one fail-fast migration task."""
    workflow = Path(".github/workflows/ci.yml").read_text()
    configure = "gcloud run jobs deploy line-clockio-migrate"
    migration = "gcloud run jobs execute line-clockio-migrate"
    service = "gcloud run deploy line-clockio"

    assert configure in workflow and migration in workflow
    assert (
        workflow.index(configure) < workflow.index(migration) < workflow.index(service)
    )
    configure_block = workflow[workflow.index(configure) : workflow.index(migration)]
    execute_block = workflow[workflow.index(migration) : workflow.index(service)]
    for flag in (
        "--source .",
        '--service-account "${RUNTIME_SERVICE_ACCOUNT}"',
        '--set-cloudsql-instances "${CLOUD_SQL_CONNECTION_NAME}"',
        "--set-secrets DATABASE_URL=DATABASE_URL:latest",
        "--command alembic",
        "--args upgrade,head",
        "--tasks 1",
        "--parallelism 1",
        "--max-retries 0",
    ):
        assert flag in configure_block
    assert "--wait" in execute_block
    assert "--source ." in workflow[workflow.index(service) :]
    assert "cancel-in-progress: false" in workflow.split("  deploy:", 1)[1]
    assert "set -euo pipefail" in workflow
    assert "continue-on-error" not in workflow and "|| true" not in execute_block
    assert "600104370576-compute@developer.gserviceaccount.com" in workflow
    assert "aiotek-bot:asia-east1:line-clockio-db-new" in workflow


def test_manual_deploy_waits_for_migration_before_service_deploy() -> None:
    """Manual releases migrate using the same pushed image before rollout."""
    script = Path("deploy.sh").read_text()
    configure = "gcloud run jobs deploy line-clockio-migrate"
    migration = "gcloud run jobs execute line-clockio-migrate"
    service = 'gcloud run deploy "${SERVICE}"'

    assert configure in script and migration in script
    assert script.index('docker push "${IMAGE}"') < script.index(configure)
    assert script.index(configure) < script.index(migration) < script.index(service)
    configure_block = script[script.index(configure) : script.index(migration)]
    execute_block = script[script.index(migration) : script.index(service)]
    for flag in (
        '--image "${IMAGE}"',
        '--service-account "${RUNTIME_SERVICE_ACCOUNT}"',
        '--set-cloudsql-instances "${CLOUD_SQL_CONNECTION_NAME}"',
        "--set-secrets DATABASE_URL=DATABASE_URL:latest",
        "--command alembic",
        "--args upgrade,head",
        "--tasks 1",
        "--parallelism 1",
        "--max-retries 0",
    ):
        assert flag in configure_block
    assert "--wait" in execute_block
    assert '--image "${IMAGE}"' in script[script.index(service) :]
    assert "set -euo pipefail" in script and "||" not in execute_block
    assert "600104370576-compute@developer.gserviceaccount.com" in script
    assert "line-clockio-db-new" in script
