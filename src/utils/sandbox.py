"""Sandbox for safely executing generated reasoning programs."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
import sys
import traceback
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.base import ModelRoster
    from src.models.router import ModelRouter
    from src.programs.interface import ReasoningProgram, Solution

logger = logging.getLogger(__name__)

GENERATED_DIR = Path(__file__).resolve().parents[1] / "programs" / "generated"
TIMEOUT_SECONDS = 30


def load_program(module_path: Path) -> ReasoningProgram | None:
    """Dynamically load a Python module and return its ReasoningProgram subclass instance.

    The module must define exactly one class that subclasses ReasoningProgram.
    Returns None if loading fails.
    """
    from src.programs.interface import ReasoningProgram

    if not module_path.exists():
        logger.error("Program file does not exist: %s", module_path)
        return None

    # Compile first to catch syntax errors cheaply before doing a full import
    try:
        source = module_path.read_text(encoding="utf-8")
        compile(source, str(module_path), "exec")
    except SyntaxError as exc:
        logger.error("Syntax error in %s: %s", module_path.name, exc)
        return None

    # Build a unique module name to avoid collisions in sys.modules
    module_name = f"_poetiq_generated_{module_path.stem}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            logger.error("Cannot create module spec for %s", module_path)
            return None

        module = importlib.util.module_from_spec(spec)

        # Load into a temporary sys.modules slot so relative imports inside the
        # generated file can resolve; we remove it afterwards.
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        finally:
            sys.modules.pop(module_name, None)

    except Exception as exc:
        logger.error(
            "Failed to import module %s: %s\n%s",
            module_path.name,
            exc,
            traceback.format_exc(),
        )
        return None

    # Collect all concrete ReasoningProgram subclasses defined in this module
    subclasses = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, ReasoningProgram)
        and obj is not ReasoningProgram
        and obj.__module__ == module_name
    ]

    if len(subclasses) == 0:
        logger.error(
            "No ReasoningProgram subclass found in %s", module_path.name
        )
        return None

    if len(subclasses) > 1:
        names = [cls.__name__ for cls in subclasses]
        logger.error(
            "Expected exactly one ReasoningProgram subclass in %s, found %d: %s",
            module_path.name,
            len(subclasses),
            names,
        )
        return None

    program_class = subclasses[0]

    try:
        instance = program_class()
    except Exception as exc:
        logger.error(
            "Failed to instantiate %s from %s: %s",
            program_class.__name__,
            module_path.name,
            exc,
        )
        return None

    logger.debug(
        "Loaded program '%s' from %s", getattr(instance, "name", program_class.__name__), module_path.name
    )
    return instance


async def run_program_safe(
    program: ReasoningProgram,
    problem: dict,
    models: ModelRoster,
    router: ModelRouter,
    timeout: float = TIMEOUT_SECONDS,
) -> Solution:
    """Run a program's solve() method with a timeout.

    If the program crashes, times out, or produces an error, return an error Solution
    with answer="ERROR", cost=0.0, and the error in the trace.
    """
    from src.programs.interface import Solution

    try:
        solution = await asyncio.wait_for(
            program.solve(problem, models, router),
            timeout=timeout,
        )
        return solution
    except asyncio.TimeoutError:
        logger.warning(
            "Program '%s' timed out after %.1fs",
            getattr(program, "name", type(program).__name__),
            timeout,
        )
        return Solution(answer="ERROR", cost=0.0, trace=f"Timeout after {timeout}s")
    except Exception as exc:
        tb = traceback.format_exc()
        logger.warning(
            "Program '%s' raised an exception: %s",
            getattr(program, "name", type(program).__name__),
            exc,
        )
        return Solution(answer="ERROR", cost=0.0, trace=f"Error: {exc}\n{tb}")


def validate_program(module_path: Path) -> tuple[bool, str]:
    """Validate that a generated program file has valid syntax and conforms to the interface.

    Returns (valid: bool, message: str).

    Checks performed:
    1. File exists
    2. Python syntax is valid (compile)
    3. Module can be imported
    4. Contains exactly one ReasoningProgram subclass
    5. The class has ``name`` and ``description`` attributes
    6. The class has a ``solve`` method
    """
    # --- Check 1: file exists ---
    if not module_path.exists():
        return False, f"File does not exist: {module_path}"

    if not module_path.is_file():
        return False, f"Path is not a file: {module_path}"

    # --- Check 2: valid Python syntax ---
    try:
        source = module_path.read_text(encoding="utf-8")
        compile(source, str(module_path), "exec")
    except SyntaxError as exc:
        return False, f"Syntax error: {exc}"

    # --- Check 3: module can be imported ---
    module_name = f"_poetiq_validate_{module_path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return False, "Cannot create module spec (invalid Python file?)"

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        finally:
            sys.modules.pop(module_name, None)

    except Exception as exc:
        return False, f"Import error: {exc}"

    # --- Check 4: exactly one ReasoningProgram subclass ---
    from src.programs.interface import ReasoningProgram

    subclasses = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, ReasoningProgram)
        and obj is not ReasoningProgram
        and obj.__module__ == module_name
    ]

    if len(subclasses) == 0:
        return False, "No ReasoningProgram subclass found in module"

    if len(subclasses) > 1:
        names = [cls.__name__ for cls in subclasses]
        return False, f"Expected exactly one ReasoningProgram subclass, found {len(subclasses)}: {names}"

    cls = subclasses[0]

    # --- Check 5: name and description attributes ---
    missing_attrs = [attr for attr in ("name", "description") if not hasattr(cls, attr)]
    if missing_attrs:
        return False, f"Class '{cls.__name__}' is missing required attributes: {missing_attrs}"

    # Both attributes must be non-empty strings
    for attr in ("name", "description"):
        value = getattr(cls, attr)
        if not isinstance(value, str) or not value.strip():
            return False, f"Class '{cls.__name__}' attribute '{attr}' must be a non-empty string"

    # --- Check 6: solve method ---
    if not hasattr(cls, "solve"):
        return False, f"Class '{cls.__name__}' does not define a 'solve' method"

    solve_method = getattr(cls, "solve")
    if not callable(solve_method):
        return False, f"Class '{cls.__name__}'.solve is not callable"

    if not inspect.iscoroutinefunction(solve_method):
        return False, f"Class '{cls.__name__}'.solve must be an async method"

    return True, f"OK — class '{cls.__name__}' passes all validation checks"
