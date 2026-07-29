"""Generate exactly three hidden final seeds before submissions arrive."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import uuid
from datetime import datetime
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = MODULE_DIR / "private_inputs" / "final_seeds.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the private three-seed final set"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(
            f"Refusing to replace existing seed set: {output}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)

    values: set[int] = set()
    while len(values) < 3:
        values.add(secrets.randbits(63))
    payload = {
        "seed_set_id": "final_" + uuid.uuid4().hex,
        "created_at": datetime.now().astimezone().isoformat(),
        "notice": (
            "Organizer private. Frozen before participant submissions."
        ),
        "seeds": [
            {
                "seed_id": f"hidden_seed_{index}",
                "value": value,
            }
            for index, value in enumerate(sorted(values), start=1)
        ],
    }
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    output.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    checksum_path = output.with_suffix(".sha256")
    checksum_path.write_text(
        f"{digest}  {output.name}\n",
        encoding="utf-8",
    )
    print(f"Private seed set: {output}")
    print(f"SHA-256: {digest}")
    print("Freeze these files now. Do not regenerate after submissions.")


if __name__ == "__main__":
    main()
