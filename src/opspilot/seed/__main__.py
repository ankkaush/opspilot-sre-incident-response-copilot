"""Run with: python -m opspilot.seed"""

import logging

from opspilot.db import SessionLocal
from opspilot.logging_config import configure_logging
from opspilot.seed.generator import seed_all

configure_logging()
log = logging.getLogger("opspilot.seed")

if __name__ == "__main__":
    session = SessionLocal()
    try:
        count = seed_all(session)
        log.info("seed complete", extra={"extra_fields": {"scenarios_newly_seeded": count}})
    finally:
        session.close()
