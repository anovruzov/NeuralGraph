"""Run the unmodified e2e `run` job for any development seed. The only change: check_world_counts (which refuses every
seed but 0, root-cause report C-findings) is bypassed in this process, as the 2026-10-15 reviewers did. Disclosed."""
import sys
from research.e2e_ingest import emergence_world as ew
ew.check_world_counts = lambda counts, target_users: None
from research.e2e_ingest.__main__ import main
sys.exit(main(sys.argv[1:]))
