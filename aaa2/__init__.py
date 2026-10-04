

import importlib.util as _importlib_util
import sys as _sys

# A DuckDB minden paraméteres lekérdezésnél, paraméterenként megpróbálja betölteni a pandast.
# Ha nincs telepítve, a sikertelen import minden alkalommal végigjárja a keresési utat
# (a futásidő nagyobb része erre ment); a `None` bejegyzés ezt azonnali ImportErrorra rövidíti.
if "pandas" not in _sys.modules and _importlib_util.find_spec("pandas") is None:
    _sys.modules["pandas"] = None  # type: ignore[assignment]
