"""Seed the local database with clearly fictional demo data.

Run from the project root:

    backend/.venv/bin/python -m scripts.seed_demo

Skips seeding if any clients already exist, so it is safe to run twice.
All names and emails are invented for testing (RFC 2606 .test domain).
"""

from app import repository
from app.db import db_session
from app.schemas import ClientCreate, MatterCreate, TaskCreate

DEMO_CLIENTS = [
    ("Acme Legal LLP", "ops@acme-legal.test"),
    ("Beacon Advisory Group", "hello@beacon-advisory.test"),
]

DEMO_MATTERS = {
    "Acme Legal LLP": [
        ("Merger diligence — Project Harbor", "open"),
        ("Employment policy review", "closed"),
    ],
    "Beacon Advisory Group": [
        ("Tax restructuring engagement", "open"),
    ],
}

DEMO_TASKS = {
    "Merger diligence — Project Harbor": [
        ("Collect target financial statements", "in_progress", "high"),
        ("Draft diligence question list", "todo", "medium"),
    ],
    "Employment policy review": [
        ("Deliver final report", "done", "medium"),
    ],
    "Tax restructuring engagement": [
        ("Schedule kickoff workshop", "todo", "low"),
    ],
}


def main() -> None:
    with db_session() as conn:
        if repository.list_clients(conn):
            print("Clients already present — nothing to seed.")
            return

        created_clients: dict[str, int] = {}
        for name, email in DEMO_CLIENTS:
            row = repository.create_client(conn, ClientCreate(name=name, email=email))
            created_clients[name] = row["id"]

        created_matters: dict[str, int] = {}
        for client_name, matters in DEMO_MATTERS.items():
            for title, status in matters:
                row = repository.create_matter(
                    conn,
                    MatterCreate(
                        client_id=created_clients[client_name], title=title, status=status
                    ),
                )
                created_matters[title] = row["id"]

        task_count = 0
        for matter_title, tasks in DEMO_TASKS.items():
            for title, status, priority in tasks:
                repository.create_task(
                    conn,
                    TaskCreate(
                        matter_id=created_matters[matter_title],
                        title=title,
                        status=status,
                        priority=priority,
                    ),
                )
                task_count += 1

        print(
            f"Seeded {len(created_clients)} clients, "
            f"{len(created_matters)} matters, {task_count} tasks (all fictional)."
        )


if __name__ == "__main__":
    main()
