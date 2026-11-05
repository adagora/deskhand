import asyncio
import os
import sys
from pathlib import Path

from temporalio.client import Client

from casework.backend import SqliteBackend
from casework.model import Proposal
from orchestration.home import activities_for, build_worker, open_home


class CrashAfterCancel(SqliteBackend):
    def cancel(self, proposal: Proposal) -> None:
        super().cancel(proposal)
        os._exit(9)


async def main() -> None:
    address, root, fault = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    home = open_home(root)
    activities = activities_for(home)
    if fault == "crash-after-cancel":
        activities.desk.backend = CrashAfterCancel(root / "backend.db")
    client = await Client.connect(address)
    async with build_worker(client, activities):
        sys.stdout.write("ready\n")
        sys.stdout.flush()
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
