"""Run database migrations.

Version 7 is the first released run schema. Future releases add a step keyed by
the version being upgraded from, for example ``7: Migration(8, migrate_7_to_8)``.
"""

from cloverboard.migrations.model import Migration

RUN_MIGRATIONS: dict[int, Migration] = {}
