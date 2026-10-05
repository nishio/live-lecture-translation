"""Explicit, bounded JSON settings for launcher intervals; standard library only."""
import argparse
import json
from pathlib import Path
import sys


MAX_SETTINGS_BYTES = 16 * 1024
DEFAULT_SETTINGS = {
    "schema_version": 1,
    "translation_interval_seconds": 60,
    "analysis_interval_seconds": 120,
}


class SettingsError(ValueError):
    """The supplied settings cannot be safely used."""


def validate_settings(value):
    """Return an independent settings dict after validating the complete schema."""
    if not isinstance(value, dict):
        raise SettingsError("Settings must be a JSON object.")
    if set(value) != set(DEFAULT_SETTINGS):
        raise SettingsError("Settings require exactly schema_version, "
                            "translation_interval_seconds, and analysis_interval_seconds.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise SettingsError("Settings schema_version must be the integer 1.")
    for name in ("translation_interval_seconds", "analysis_interval_seconds"):
        if type(value[name]) is not int or not 1 <= value[name] <= 3600:
            raise SettingsError(f"{name} must be an integer from 1 to 3600 seconds.")
    return dict(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SettingsError("Duplicate settings keys are not allowed.")
        result[key] = value
    return result


def _reject_constant(value):
    raise SettingsError("Non-finite JSON numbers are not allowed.")


def load_settings(path=None):
    """Read only an explicitly supplied path, or return the unchanged defaults."""
    if path is None:
        return dict(DEFAULT_SETTINGS)
    try:
        with Path(path).expanduser().open("rb") as source:
            raw = source.read(MAX_SETTINGS_BYTES + 1)
    except OSError as exc:
        raise SettingsError("Unable to read settings file.") from exc
    if len(raw) > MAX_SETTINGS_BYTES:
        raise SettingsError("Settings file must be at most 16 KiB.")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except SettingsError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SettingsError("Settings file must contain valid UTF-8 JSON.") from exc
    return validate_settings(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path)
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.settings)
    except SettingsError as exc:
        parser.error(str(exc))
    print(settings["translation_interval_seconds"], settings["analysis_interval_seconds"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
