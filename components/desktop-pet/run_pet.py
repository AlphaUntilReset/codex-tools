"""开发环境启动入口：python run_pet.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    # Dispatch before importing Qt: the observer remains small and independent.
    from codex_pet.process_evidence import relaunch_outside_package
    if relaunch_outside_package():
        raise SystemExit(0)
    from codex_pet import diagnostics
    if len(sys.argv) == 3 and sys.argv[1] == "--exit-monitor":
        raise SystemExit(diagnostics.observe(Path(sys.argv[2])))
    session = diagnostics.begin()
    try:
        from codex_pet.app import main
        code = main()
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
        if session:
            session.finish(code)
        raise
    except BaseException:
        # Persist before interpreter finalization and retain the original traceback.
        sys.excepthook(*sys.exc_info())
        if session:
            session.finish(1, clean=False)
        raise SystemExit(1)
    else:
        if session:
            session.finish(code)
        raise SystemExit(code)
