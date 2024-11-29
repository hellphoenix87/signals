import logging

def configure_logging():
    """
    Configure a logger that includes the module, function, and line number
    for better traceability of log messages.
    """
    log_format = (
        "%(asctime)s - %(name)s - %(levelname)s - [%(module)s.%(funcName)s:%(lineno)d] - %(message)s"
    )
    logging.basicConfig(
        level=logging.ERROR,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger(__name__)
    return logger

# Initialize the logger
logger = configure_logging()
