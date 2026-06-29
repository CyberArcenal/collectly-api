



# -------------------------------------------------------------------
# Enhanced logging configuration with proper app/module handling
# -------------------------------------------------------------------

# Get all installed apps that belong to your project
from core.settings.components.apps import PROJECT_APPS



APP_LOGGERS = {
    app: {
        "handlers": ["console"],
        "level": "DEBUG",
        "propagate": False,  # Don't propagate to root logger
    }
    for app in PROJECT_APPS
}

# Add module-specific loggers for better granularity
MODULE_LOGGERS = {
    "api": {
        "handlers": ["console"],
        "level": "DEBUG",
        "propagate": False,
    },
    "views": {
        "handlers": ["console"],
        "level": "DEBUG",
        "propagate": False,
    },
    "serializers": {
        "handlers": ["console"],
        "level": "DEBUG",
        "propagate": False,
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    # ----------------------------------------------------------------
    # Formatters - Enhanced with more details
    # ----------------------------------------------------------------
    "formatters": {
        "rich": {
            "datefmt": "[%X]",
            "format": "%(name)s | %(levelname)s | %(message)s",
        },
        "verbose": {
            "format": "[%(asctime)s] %(name)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s"
        },
    },
    # ----------------------------------------------------------------
    # Handlers - Added fallback for non-rich environments
    # ----------------------------------------------------------------
    "handlers": {
        "console": {
            "class": "rich.logging.RichHandler",
            "formatter": "rich",
            "rich_tracebacks": True,
            "markup": True,
            "log_time_format": "[%X]",
        },
        "console_fallback": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    # ----------------------------------------------------------------
    # Root logger - Set to WARNING to avoid double logging
    # ----------------------------------------------------------------
    "root": {
        "handlers": ["console_fallback"],
        "level": "WARNING",  # Only show warnings and above globally
    },
    # ----------------------------------------------------------------
    # Loggers - Combine all configurations
    # ----------------------------------------------------------------
    "loggers": {
        # Django built-in loggers
        "django": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console"],
            "level": "ERROR",  # Reduce SQL query noise
            "propagate": False,
        },
        # Automatically inject DEBUG loggers for project apps
        **APP_LOGGERS,
        # Add module-specific loggers
        **MODULE_LOGGERS,
        # Catch-all for your project
        "barangay": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "resident": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}