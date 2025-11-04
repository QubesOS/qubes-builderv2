import tempfile
from pathlib import Path

import pytest
import logging
from qubesbuilder.config import Config
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.log import (
    FileFormatter,
    ConsoleFormatter,
    create_file_handler,
    create_console_handler,
    QubesBuilderLogger,
    init_logger,
)


@pytest.fixture
def logger_name():
    return "test_logger"


@pytest.fixture
def log_file(tmp_path):
    return tmp_path / "test.log"


@pytest.fixture
def config(tmp_path):
    with tempfile.NamedTemporaryFile("w", dir=tmp_path) as config_file_main:
        config_file_main.write(
            f"""
artifacts-dir: {tmp_path}/artifacts

executor:
  type: docker
  options:
    image: "qubes-builder-fedora:latest"

components:
  - linux-utils

distributions:
  - host-fc37
  - vm-bookworm
"""
        )
        config_file_main.flush()
        return Config(config_file_main.name)


@pytest.fixture
def plugins(config):
    src_dir = (config.sources_dir / "linux-utils")
    src_dir.mkdir(parents=True)
    (src_dir / ".qubesbuilder").write_text("""
host:
  rpm:
    build:
    - rpm_spec/qubes-utils.spec
vm:
  deb:
    build:
    - debian
"""
)
    (src_dir / "version").write_text("1.2.3")
    (src_dir / "rel").write_text("4")
    return config.get_jobs(
        stages=["prep"],
        components=config.get_components(),
        distributions=config.get_distributions(),
        templates=[],
    )


@pytest.fixture
def root_logger():
    return QubesBuilderLogger


def teardown_logging():
    """Reset logging configuration to default"""
    if QubesBuilderLogger.handlers:
        logging.shutdown()
        import importlib

        importlib.reload(logging)
        QubesBuilderLogger.handlers = []
        QubesBuilderLogger.filters = []


def test_file_formatter():
    formatter = FileFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    formatted_message = formatter.format(record)
    assert "Test message" in formatted_message


def test_console_formatter():
    formatter = ConsoleFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    formatted_message = formatter.format(record)
    assert "Test message" in formatted_message


def test_create_file_handler(log_file):
    handler = create_file_handler(log_file)
    assert isinstance(handler, logging.FileHandler)
    assert handler.baseFilename == str(log_file)


def test_create_console_handler():
    handler = create_console_handler(True)
    assert isinstance(handler, logging.StreamHandler)


def test_qb_logger_initialization(root_logger):
    logger = root_logger.getChild("test_logger")
    assert logger.name == "qb.test_logger"
    assert logger.level == logging.NOTSET  # Default level


def test_qb_logger_set_log_file(root_logger, log_file, plugins):
    root_logger.set_log_file(log_file)
    logger = root_logger.getChild("test_logger", plugin=plugins[0])
    assert any(
        isinstance(handler, logging.FileHandler) for handler in logger.handlers
    )
    assert str(log_file) == str(logger.get_log_file())


def test_qb_logger_getChild(root_logger, plugins):
    logger = root_logger.getChild("test_logger", plugin=plugins[2])
    child_logger = logger.getChild("child")
    assert child_logger.name == "qb.test_logger.linux-utils.host-fc37.child"
    assert child_logger.level == logger.level


@pytest.mark.parametrize("verbose", [True, False])
def test_qb_logger_verbose_no_verbose(root_logger, plugins, capsys, verbose):
    teardown_logging()
    init_logger(verbose=verbose)
    logger = root_logger.getChild("test_logger", plugin=plugins[0])
    logger.info("Info message")
    logger.debug("Debug message")

    stdout, stderr = capsys.readouterr()
    assert "Info message" in stderr
    if verbose:
        assert "Debug message" in stderr
    else:
        assert "Debug message" not in stderr


def test_qb_logger_no_duplicate_logs(root_logger, plugins, capsys):
    teardown_logging()
    init_logger(verbose=True)
    logger = root_logger.getChild("test_logger", plugin=plugins[0])
    logger.info("Info message")

    stdout, stderr = capsys.readouterr()
    assert stderr.count("Info message") == 1
