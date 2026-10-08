"""Zero-dependency test runner.

Installs a minimal ``pytest`` shim into ``sys.modules`` so the test files can stay
written in standard pytest style, then discovers and runs them. Works whether or not
pytest is installed.

    python scripts/run_tests.py
    python scripts/run_tests.py -k prec
    python scripts/run_tests.py -v
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import inspect
import sys
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


# ---------------------------------------------------------------------------
class _Raises:
    def __init__(self, expected: type[BaseException]) -> None:
        self.expected = expected
        self.value: BaseException | None = None

    def __enter__(self) -> _Raises:
        return self

    def __exit__(self, exc_type: type | None, exc: BaseException | None, tb: object) -> bool:
        if exc_type is None:
            raise AssertionError(f"expected {self.expected.__name__} to be raised")
        if issubclass(exc_type, self.expected):
            self.value = exc
            return True
        return False


class _Mark:
    def parametrize(self, argnames, argvalues, **_: object):
        names = (
            [n.strip() for n in argnames.split(",")]
            if isinstance(argnames, str)
            else list(argnames)
        )

        def deco(fn):
            fn._parametrize = (names, list(argvalues))
            return fn

        return deco

    def __getattr__(self, _name: str):
        def deco(fn=None, **_kw):
            return (lambda f: f) if fn is None else fn

        return deco


def _fixture(fn=None, **_kw):
    def wrap(f):
        f._is_fixture = True
        return f

    return wrap if fn is None else wrap(fn)


def _approx(value: float, rel: float = 1e-6, abs_: float = 1e-12):
    class _A:
        def __eq__(self, other: object) -> bool:
            return abs(float(other) - value) <= max(abs_, rel * abs(value))  # type: ignore[arg-type]

    return _A()


def _install_pytest_shim() -> None:
    if "pytest" in sys.modules:
        return
    mod = types.ModuleType("pytest")
    mod.raises = _Raises
    mod.mark = _Mark()
    mod.fixture = _fixture
    mod.approx = _approx
    sys.modules["pytest"] = mod


# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors: list[tuple[str, str]] = []
        self.timings: list[tuple[float, str]] = []

    def _run_one(self, label: str, fn, kwargs: dict) -> bool:
        import time as _time

        started = _time.perf_counter()
        try:
            fn(**kwargs)
        except Exception:
            self.failed += 1
            self.errors.append((label, traceback.format_exc()))
            self.timings.append((_time.perf_counter() - started, label))
            return False
        self.passed += 1
        self.timings.append((_time.perf_counter() - started, label))
        return True

    @staticmethod
    def _resolve_fixture(fn):
        val = fn()
        if inspect.isgenerator(val):
            gen = val
            try:
                return next(gen), gen
            except StopIteration:
                return None, None
        return val, None

    @staticmethod
    def _builtin_fixtures() -> dict:
        """pytest's built-in fixtures that tests commonly rely on."""
        import tempfile
        from pathlib import Path as _Path

        def tmp_path():
            with tempfile.TemporaryDirectory() as d:
                yield _Path(d)

        def tmp_path_factory():
            return tempfile.mkdtemp

        return {"tmp_path": tmp_path, "tmp_path_factory": tmp_path_factory}

    def run_module(self, path: Path, keyword: str) -> None:
        mod = importlib.import_module(path.stem)
        fixtures = {
            n: f for n, f in vars(mod).items()
            if callable(f) and getattr(f, "_is_fixture", False)
        }
        for name, fn in self._builtin_fixtures().items():
            fixtures.setdefault(name, fn)

        printed = False
        for name, fn in sorted(vars(mod).items()):
            if not name.startswith("test_") or not callable(fn):
                continue
            if keyword and keyword.lower() not in name.lower():
                continue
            if not printed:
                print(f"\n{path.name}")
                printed = True

            sig = inspect.signature(fn)
            cases: list[tuple[str, dict]] = []
            if hasattr(fn, "_parametrize"):
                names, values = fn._parametrize
                for v in values:
                    params = (
                        {names[0]: v} if len(names) == 1
                        else dict(zip(names, v, strict=False))
                    )
                    cases.append((f"{name}[{v!r}]", params))
            else:
                cases.append((name, {}))

            results: list[bool] = []
            for label, params in cases:
                kwargs = dict(params)
                gens: list = []
                resolved = True
                for pname in sig.parameters:
                    if pname in kwargs:
                        continue
                    if pname in fixtures:
                        value, gen = self._resolve_fixture(fixtures[pname])
                        if gen is not None:
                            gens.append(gen)
                        kwargs[pname] = value
                    else:
                        resolved = False
                        self.failed += 1
                        self.errors.append((label, f"cannot resolve fixture: {pname}"))
                        break
                if not resolved:
                    results.append(False)
                    continue

                ok = self._run_one(label, fn, kwargs)
                results.append(ok)

                for gen in gens:
                    with contextlib.suppress(StopIteration):
                        next(gen)

                if self.verbose:
                    print(f"    {'✓' if ok else '✗'} {label}")

            print(f"  {'✓' if all(results) else '✗'} {name}"
                  + (f"  ({len(cases)} cases)" if len(cases) > 1 else ""))

    def report(self, show_slowest: int = 0) -> int:
        if self.errors:
            print("\n" + "=" * 74)
            print("failures")
            print("=" * 74)
            for name, tb in self.errors:
                print(f"\n--- {name} ---")
                print(tb.rstrip())
        if show_slowest:
            print("\n" + "=" * 74)
            print(f"slowest {show_slowest} tests")
            print("=" * 74)
            for secs, name in sorted(self.timings, reverse=True)[:show_slowest]:
                print(f"  {secs * 1000:>8.0f} ms  {name}")
        total = self.passed + self.failed
        wall = sum(t for t, _ in self.timings)
        print("\n" + "=" * 74)
        print(f"result: {self.passed} passed, {self.failed} failed ({total} total)")
        print(f"test time: {wall:.1f}s")
        print("=" * 74)
        return 1 if self.failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="zero-dependency test runner")
    ap.add_argument("-k", "--keyword", default="", help="only run tests matching this substring")
    ap.add_argument("-v", "--verbose", action="store_true", help="show each parametrized case")
    ap.add_argument("-s", "--slowest", type=int, default=0, help="report the N slowest tests")
    args = ap.parse_args()

    _install_pytest_shim()

    files = sorted((ROOT / "tests").glob("test_*.py"))
    if not files:
        print("no test files found")
        return 1

    runner = Runner(verbose=args.verbose)
    for f in files:
        runner.run_module(f, args.keyword)
    return runner.report(show_slowest=args.slowest)


if __name__ == "__main__":
    sys.exit(main())
