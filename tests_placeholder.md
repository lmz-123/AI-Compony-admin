This repository intentionally uses only Python stdlib on Python 3.11+.
Python 3.9/3.10 installs the tiny `tomli` TOML reader.

Smoke test:

```bash
python -m compileall -q ai_company_admin
python -m ai_company_admin.server --help
```
