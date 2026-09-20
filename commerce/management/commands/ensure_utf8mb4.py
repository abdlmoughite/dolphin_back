import re

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Convert the configured MySQL database and its tables to utf8mb4 for Arabic, mixed French/Arabic text, and emoji."

    def add_arguments(self, parser):
        parser.add_argument("--collation", default="utf8mb4_unicode_ci", help="MySQL utf8mb4 collation to apply.")

    def handle(self, *args, **options):
        if connection.vendor != "mysql":
            raise CommandError("ensure_utf8mb4 works only with MySQL.")

        collation = options["collation"]
        if not re.fullmatch(r"utf8mb4_[0-9A-Za-z_]+", collation):
            raise CommandError("Collation must be an utf8mb4 collation.")

        database_name = connection.settings_dict["NAME"]
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER DATABASE {connection.ops.quote_name(database_name)} CHARACTER SET utf8mb4 COLLATE {collation}")
            cursor.execute(
                """
                SELECT TABLE_NAME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME
                """,
                [database_name],
            )
            table_names = [row[0] for row in cursor.fetchall()]

            for table_name in table_names:
                quoted_table = connection.ops.quote_name(table_name)
                cursor.execute(f"ALTER TABLE {quoted_table} CONVERT TO CHARACTER SET utf8mb4 COLLATE {collation}")

        self.stdout.write(self.style.SUCCESS(f"Converted database '{database_name}' and {len(table_names)} tables to utf8mb4/{collation}."))
