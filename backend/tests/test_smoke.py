"""
冒烟测试：验证工具链和 import 链路
"""
import pytest


def test_config_importable():
    import config
    assert hasattr(config, "DEFAULT_MODEL")
    assert hasattr(config, "SUMMARY_TYPES")
    assert hasattr(config, "SERVER_PORT")


def test_schemas_importable():
    from models.schemas import JobCreate, JobStatus, InputType
    assert JobStatus.pending == "pending"
    assert InputType.text == "text"


def test_job_create_validation():
    from models.schemas import JobCreate, InputType
    job = JobCreate(input_type=InputType.text, input_text="hello")
    assert job.input_type == InputType.text
    assert job.input_text == "hello"
    assert job.do_summary is True  # 默认


def test_job_create_missing_required():
    from models.schemas import JobCreate
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        JobCreate()  # input_type 必填
