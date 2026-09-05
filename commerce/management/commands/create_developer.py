from decouple import config
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create or update the protected DOLPHIN Developer/Super Admin account."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Developer email. Defaults to DEVELOPER_EMAIL.")
        parser.add_argument("--password", help="Developer password. Defaults to DEVELOPER_PASSWORD.")
        parser.add_argument("--first-name", help="Defaults to DEVELOPER_FIRST_NAME.")
        parser.add_argument("--last-name", help="Defaults to DEVELOPER_LAST_NAME.")
        parser.add_argument("--update-password", action="store_true", help="Update password if the account already exists.")

    @transaction.atomic
    def handle(self, *args, **options):
        email = options.get("email") or config("DEVELOPER_EMAIL", default="")
        password = options.get("password") or config("DEVELOPER_PASSWORD", default="")
        first_name = options.get("first_name") or config("DEVELOPER_FIRST_NAME", default="Admin")
        last_name = options.get("last_name") or config("DEVELOPER_LAST_NAME", default="Developer")

        if not email:
            raise CommandError("DEVELOPER_EMAIL is required. Example: $env:DEVELOPER_EMAIL='admin@dolphin.local'")
        User = get_user_model()
        user = User.objects.filter(email=email).first()
        if not user and not password:
            raise CommandError("DEVELOPER_PASSWORD is required for a new account. Example: $env:DEVELOPER_PASSWORD='your-local-password'")
        user, created = User.objects.get_or_create(email=email, defaults={"username": email.split("@")[0], "first_name": first_name, "last_name": last_name})
        user.first_name = first_name
        user.last_name = last_name
        user.role = User.Role.SUPER_ADMIN
        user.status = User.Status.ACTIVE
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        if created or options["update_password"] or not user.has_usable_password():
            user.set_password(password)
            password_message = "password set"
        else:
            password_message = "existing password kept"
        user.save()

        state = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"Developer account {state}: {user.email} ({password_message})."))
