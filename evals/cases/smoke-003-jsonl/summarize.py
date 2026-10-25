"""Read JSONL from stdin and print a JSON object with count and total quantity."""

import json
import sys


def main():
    rows = json.load(sys.stdin)
    print(json.dumps({"count": len(rows), "total": sum(row["quantity"] for row in rows)}))


if __name__ == "__main__":
    main()
