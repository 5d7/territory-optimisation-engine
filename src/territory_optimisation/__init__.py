"""Public interface for the Territory Optimisation Engine."""

from .engine import (
    ACC_REQUIRED,
    AE_REQUIRED,
    Config,
    DataValidationError,
    ReviewResult,
    build_next_cycle_frames,
    build_next_cycle_input,
    download_input_template,
    export_excel,
    load_data,
    make_demo_data,
    prepare_data,
    result_tables,
    run_scenarios,
    run_territory_review,
    show_dashboard,
)

__version__ = "0.1.0"

__all__ = [
    "ACC_REQUIRED",
    "AE_REQUIRED",
    "Config",
    "DataValidationError",
    "ReviewResult",
    "build_next_cycle_frames",
    "build_next_cycle_input",
    "download_input_template",
    "export_excel",
    "load_data",
    "make_demo_data",
    "prepare_data",
    "result_tables",
    "run_scenarios",
    "run_territory_review",
    "show_dashboard",
]
