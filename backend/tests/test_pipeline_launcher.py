from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.services import pipeline_launcher


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings = pipeline_launcher.get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_launch_run_spawns_subprocess():
    settings = Settings(momants_api_base_url="https://momants.example")
    with (
        patch.object(pipeline_launcher, "get_settings", return_value=settings),
        patch.object(pipeline_launcher, "is_pipeline_busy", return_value=False),
        patch.object(pipeline_launcher.subprocess, "Popen") as mock_popen,
    ):
        result = pipeline_launcher.launch_run("agent-123", "user@momants.ai")

    assert result.agent_id == "agent-123"
    assert result.initiated_by == "user@momants.ai"
    mock_popen.assert_called_once()
    cmd = mock_popen.call_args.args[0]
    assert cmd[-2:] == ["--agent-id=agent-123", "--initiated-by=user@momants.ai"]
    assert cmd[2:4] == ["app.pipeline", "run"]


def test_launch_reanalyze_spawns_subprocess():
    with (
        patch.object(pipeline_launcher, "is_pipeline_busy", return_value=False),
        patch.object(pipeline_launcher.subprocess, "Popen") as mock_popen,
    ):
        pipeline_launcher.launch_reanalyze("agent-456", "user@momants.ai")

    cmd = mock_popen.call_args.args[0]
    assert cmd[2:4] == ["app.pipeline", "reanalyze"]
    assert "--agent-id=agent-456" in cmd


def test_launch_run_requires_momants_api():
    settings = Settings(momants_api_base_url="")
    with patch.object(pipeline_launcher, "get_settings", return_value=settings):
        with pytest.raises(pipeline_launcher.PipelineConfigError):
            pipeline_launcher.launch_run("agent-123")


def test_launch_run_rejects_busy_agent():
    settings = Settings(momants_api_base_url="https://momants.example")
    with (
        patch.object(pipeline_launcher, "get_settings", return_value=settings),
        patch.object(pipeline_launcher, "is_pipeline_busy", return_value=True),
        patch.object(pipeline_launcher.subprocess, "Popen") as mock_popen,
    ):
        with pytest.raises(pipeline_launcher.PipelineBusyError):
            pipeline_launcher.launch_run("agent-123")

    mock_popen.assert_not_called()


def test_is_pipeline_busy_false_without_lock():
    mock_client = MagicMock()
    mock_client.get.return_value = None
    with patch.object(pipeline_launcher, "get_lock_client", return_value=mock_client):
        assert pipeline_launcher.is_pipeline_busy("agent-123") is False


def test_is_pipeline_busy_true_when_lock_and_jobs_running():
    mock_client = MagicMock()
    mock_client.get.return_value = "1"
    mock_db = MagicMock()
    with (
        patch.object(pipeline_launcher, "get_lock_client", return_value=mock_client),
        patch.object(pipeline_launcher, "SessionLocal", return_value=mock_db),
        patch("app.services.job_concurrency.count_running_jobs", return_value=2),
    ):
        assert pipeline_launcher.is_pipeline_busy("agent-123") is True
    mock_db.close.assert_called_once()
