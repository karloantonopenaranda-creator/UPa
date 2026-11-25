"""Lets the subscriber be started with:  python -m pubsub.subscriber --id tenant-juan --topics ..."""
from pubsub.subscriber.subscriber import main

if __name__ == "__main__":
    raise SystemExit(main())
