import datetime
from logging import (
    Formatter,
    StreamHandler,
    FileHandler,
    Logger,
    getLogger,
    DEBUG,
    NOTSET,
    INFO,
)

from qubesbuilder.exc import QubesBuilderError

FileLogDateFmt = "%Y-%m-%d %H:%M:%S"
ConsoleLogDateFmt = "%H:%M:%S"
PrefixLogFileDataFmt = "%Y%m%dT%H%M%S"


class FileFormatter(Formatter):
    def __init__(
        self, fmt="%(asctime)s [%(name)s] %(message)s", *args, **kwargs
    ):
        super().__init__(fmt, *args, **kwargs)


class ConsoleFormatter(Formatter):
    """
    A formatter that colors messages in console.
    """

    # https://en.wikipedia.org/wiki/ANSI_escape_code
    colors = {
        "grey": "\x1b[38;5;246m",
        "green": "\x1b[32m",
        "yellow": "\x1b[93;1m",
        "red": "\x1b[91;1m",
        "cyan": "\x1b[96m",
        "reset": "\x1b[0m",
    }

    def __init__(self, fmt=None, *args, **kwargs):
        if fmt is None:
            fmt = (
                "{grey}%(asctime)s "
                "{cyan}[%(name)s] "
                "$COLOR%(message)s"
                "{reset}"
            ).format(**self.colors)
        super().__init__(fmt, *args, **kwargs)

    def format(self, record):
        result = super().format(record)
        level_color = {
            "DEBUG": "grey",
            "INFO": "reset",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red",
        }.get(record.levelname, "reset")
        result = result.replace("$COLOR", self.colors[level_color])
        return result

    def formatException(self, ei):
        result = super().formatException(ei)
        return "{red}{result}{reset}".format(result=result, **self.colors)


def create_file_handler(log_file, **kwargs):
    file_handler = FileHandler(log_file, **kwargs)
    file_handler.setLevel(DEBUG)
    file_handler.setFormatter(FileFormatter(datefmt=FileLogDateFmt))
    return file_handler


def create_console_handler(verbose, **kwargs):
    console_handler = StreamHandler(**kwargs)
    console_handler.setLevel(DEBUG if verbose else INFO)
    console_handler.setFormatter(ConsoleFormatter(datefmt=ConsoleLogDateFmt))
    return console_handler


def get_logger_name(name, plugin):
    fname = []
    if getattr(plugin, "component", None) is not None:
        fname.append(plugin.component.name)
    if (
        getattr(plugin, "dist", None) is not None
        and getattr(plugin, "template", None) is None
    ):
        fname.append(plugin.dist.distribution)
    if getattr(plugin, "template", None) is not None:
        fname.append(plugin.template.name)
    return ".".join([name] + fname)


def get_log_filename(plugin, logs_dir):
    fname = [QubesBuilderTimeStamp]
    if getattr(plugin, "component", None) is not None:
        fname.append(plugin.component.name)
    if (
        getattr(plugin, "dist", None) is not None
        and getattr(plugin, "template", None) is None
    ):
        fname.append(plugin.dist.distribution)
    if getattr(plugin, "template", None) is not None:
        fname.append(plugin.template.name)
    log_fname = "-".join(fname)
    return (logs_dir / log_fname).with_suffix(".log")


def init_logger(verbose=False, log_file=None):
    QubesBuilderLogger.setLevel(DEBUG)
    QubesBuilderLogger.set_log_file(log_file)
    QubesBuilderLogger.addHandler(create_console_handler(verbose))


def get_logger(name):
    return QubesBuilderLogger.getChild(name)


class QBLogger(Logger):
    def __init__(self, name, level=NOTSET, plugin=None, log_file=None):
        super().__init__(name=name, level=level)
        self._plugin = plugin
        # log_file will be set only if provided by cli option --log-file
        # for a global log file
        self._log_file = log_file

    def getChild(self, suffix, plugin=None):
        if plugin:
            logger = super().getChild(get_logger_name(suffix, plugin))
            logger._plugin = plugin
            # We only set logfile if it is provided by the QubesBuilderLogger
            # being the parent of all our loggers.
            if self == QubesBuilderLogger and QubesBuilderLogger._log_file:
                logger._log_file = self._log_file
            try:
                logs_dir = plugin.config.logs_dir
                logs_dir.mkdir(parents=True, exist_ok=True)

                # Check if the given logger has a log file already
                log_file = logger._log_file
                if not log_file:
                    log_file = get_log_filename(plugin, logs_dir)

                # Check if a FileHandler already exists for this logfile
                existing_log_files = [
                    fh.baseFilename
                    for fh in logger.handlers
                    if isinstance(fh, FileHandler)
                ]
                # If not FileHandler already exists, create it
                if str(log_file) not in existing_log_files:
                    logger._log_file = log_file
                    file_handler = create_file_handler(
                        logger._log_file, mode="a", delay=True
                    )
                    logger.addHandler(file_handler)
            except Exception as e:
                raise QubesBuilderError("Failed to initialize logger") from e
        else:
            logger = super().getChild(suffix)
        return logger

    def set_log_file(self, log_file):
        self._log_file = log_file

    def get_log_file(self):
        return self._log_file


Logger.manager.setLoggerClass(QBLogger)

QubesBuilderLogger: QBLogger = getLogger("qb")  # type: ignore
QubesBuilderTimeStamp = datetime.datetime.now(datetime.UTC).strftime(
    PrefixLogFileDataFmt
)
